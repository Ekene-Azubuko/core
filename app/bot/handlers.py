"""Telegram consent, commands, group message ingestion, voting and private checkout."""

import logging
from datetime import timedelta

from sqlalchemy import delete, select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity
from telegram.error import TelegramError

from app.db.models import GeneratedProduct, Group, LoreProposal, new_id, now
from app.services.voting import cast_vote

log = logging.getLogger(__name__)

INTRO = (
    "Core turns group-chat lore into physical products. With an admin's consent, "
    "I store new text messages. Mention me by @username to analyze recent messages for shared group lore. "
    "The group votes on real Arcade categories or No. Only a winning category starts "
    "generation; purchase is optional. Lore and its context are sent to Arcade after a win.\n\n"
    "Monitoring is off until an admin sends /resume. Use /pause to stop, /status for progress, "
    "and /delete_data to erase this group's locally stored data."
)


def pipeline(context):
    """Get the shared pipeline attached during application startup."""
    return context.application.bot_data["pipeline"]


async def is_admin(update, context):
    """Check the actual Telegram member before accepting group control commands."""
    chat, user = update.effective_chat, update.effective_user
    if not chat or chat.type not in ("group", "supergroup") or not user:
        return False
    member = await context.bot.get_chat_member(chat.id, user.id)
    return member.status in ("creator", "administrator")


async def require_admin(update, context):
    """Explain when a command needs an identified group administrator."""
    if await is_admin(update, context):
        return True
    await update.effective_message.reply_text("A group admin must run this command in the group.")
    return False


async def start(update, context):
    """Explain consent or create an individual checkout from a private deep link."""
    if update.effective_chat.type == "private" and context.args and context.args[0].startswith("buy_"):
        await checkout(update, context, context.args[0][4:])
        return
    await update.effective_message.reply_text(INTRO)


async def resume(update, context):
    """Enable monitoring only after an administrator explicitly consents."""
    if await require_admin(update, context):
        await pipeline(context).set_monitoring(update.effective_chat.id, True)
        await update.effective_message.reply_text("Monitoring enabled. New text messages will be stored. Mention me by @username to analyze recent messages.")


async def pause(update, context):
    """Stop collecting and analyzing while preserving saved work for resume."""
    if await require_admin(update, context):
        await pipeline(context).set_monitoring(update.effective_chat.id, False)
        await update.effective_message.reply_text(
            "Paused. New messages will not be stored or analyzed. Saved work resumes with /resume. "
            "An Arcade request already submitted may finish at Arcade.")


async def status(update, context):
    """Report this group's progress without exposing other groups' data."""
    if update.effective_chat.type not in ("group", "supergroup"):
        await update.effective_message.reply_text("Use /status in your group.")
        return
    await update.effective_message.reply_text(await pipeline(context).status(update.effective_chat.id))


async def retry(update, context):
    """Let an admin retry failed analysis or already-approved generation."""
    if await require_admin(update, context):
        changed = await pipeline(context).retry(update.effective_chat.id)
        await update.effective_message.reply_text("Retry queued." if changed else "Nothing to retry. If paused, use /resume first.")


async def delete_data(update, context):
    """Request short-lived confirmation before deleting this group's local records."""
    if not await require_admin(update, context):
        return
    core, chat_id = pipeline(context), update.effective_chat.id
    token = new_id()
    async with core.locks[chat_id], core.sessions.begin() as session:
        group = await session.get(Group, chat_id)
        if not group:
            group = Group(chat_id=chat_id)
            session.add(group)
        group.delete_token = token
        group.delete_expires_at = now() + timedelta(minutes=5)
    keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Delete this group's data", callback_data=f"d:{token}")]])
    await update.effective_message.reply_text(
        "Delete all locally stored messages, lore, votes and product records for this group and pause? "
        "This cannot erase Telegram messages or data already sent to OpenAI or Arcade. "
        "Confirm within 5 minutes.", reply_markup=keyboard)


async def callback(update, context):
    """Validate callback origin, round and permission before changing persisted state."""
    query, core = update.callback_query, pipeline(context)
    if update.effective_chat is None or update.effective_chat.type not in ("group", "supergroup"):
        await query.answer("Use these buttons in the original group.", show_alert=True)
        return
    chat_id = update.effective_chat.id
    parts = (query.data or "").split(":")
    if parts[0] == "v" and len(parts) == 4:
        try:
            round_number, choice = int(parts[2]), int(parts[3])
        except ValueError:
            await query.answer("Invalid vote.")
            return
        async with core.locks[chat_id], core.sessions.begin() as session:
            group = await session.get(Group, chat_id)
            proposal = await session.get(LoreProposal, parts[1])
            valid = (group and group.status == "monitoring" and proposal
                     and proposal.chat_id == chat_id
                     and proposal.telegram_message_id == query.message.message_id)
            accepted = valid and await cast_vote(session, proposal, query.from_user.id, round_number, choice)
        await query.answer("Vote saved. You can change it before closing." if accepted else "This vote is closed or paused.")
    elif parts[0] == "r" and len(parts) == 2:
        if not await is_admin(update, context):
            await query.answer("An admin must retry.", show_alert=True)
            return
        changed = await core.retry(chat_id, parts[1])
        await query.answer("Retry queued." if changed else "Nothing to retry; resume first if paused.")
    elif parts[0] == "d" and len(parts) == 2:
        if not await is_admin(update, context):
            await query.answer("Only an admin can confirm deletion.", show_alert=True)
            return
        async with core.locks[chat_id], core.sessions() as session:
            group = await session.get(Group, chat_id)
            valid = (group and group.delete_token == parts[1] and group.delete_expires_at > now())
        if not valid:
            await query.answer("Confirmation expired. Use /delete_data again.")
            return
        await core.set_monitoring(chat_id, False)
        async with core.locks[chat_id], core.sessions.begin() as session:
            await session.execute(delete(Group).where(Group.chat_id == chat_id))
        await query.answer("Data deleted.")
        await query.edit_message_text("This group's local data has been deleted. Monitoring is off; /resume enables it again.")
    else:
        await query.answer("This button is no longer supported.")


async def checkout(update, context, proposal_id):
    """Verify group membership and send a new buyer-specific hosted checkout privately."""
    core = pipeline(context)
    async with core.sessions() as session:
        proposal = await session.get(LoreProposal, proposal_id)
        product = await session.scalar(select(GeneratedProduct).where(GeneratedProduct.proposal_id == proposal_id))
    if not proposal or proposal.status != "generated" or not product:
        await update.effective_message.reply_text("This product is no longer available through Core.")
        return
    member = await context.bot.get_chat_member(proposal.chat_id, update.effective_user.id)
    if member.status in ("left", "kicked") or (member.status == "restricted" and not member.is_member):
        await update.effective_message.reply_text("This product belongs to a group you are not currently in.")
        return
    await update.effective_message.reply_text("Creating your checkout…")
    try:
        # A Telegram update identifies one buyer request; transport replays reuse its key.
        result = await core.arcade.checkout(product.arcade_product_id,
            f"core-{proposal_id}-{update.effective_user.id}-{update.update_id}")
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Open your checkout", url=result["checkoutUrl"])]])
        await update.effective_message.reply_text(
            "Your personal checkout is ready. Review the item, shipping, service fees and tax before paying.",
            reply_markup=keyboard)
    except Exception as error:
        log.warning("Checkout failed: %s", type(error).__name__)
        await update.effective_message.reply_text("Checkout could not be created. Tap Get yours again to retry.")


def mentions_bot(message, bot):
    """Recognize Telegram mention entities for this bot, including UTF-16 offsets."""
    for entity in message.entities or ():
        if entity.type == MessageEntity.MENTION:
            if message.parse_entity(entity).casefold() == f"@{bot.username}".casefold():
                return True
        elif entity.type == MessageEntity.TEXT_MENTION and entity.user and entity.user.id == bot.id:
            return True
    return False


async def message(update, context):
    """Store ordinary text and request analysis only when it explicitly mentions Core."""
    if update.message:
        await pipeline(context).store_message(
            update.message, analyze=mentions_bot(update.message, context.bot),
        )


async def membership(update, context):
    """Introduce Core when added and pause when removed or restricted from sending."""
    change = update.my_chat_member
    if change.chat.type not in ("group", "supergroup"):
        return
    old, new = change.old_chat_member.status, change.new_chat_member.status
    if new in ("left", "kicked", "restricted"):
        await pipeline(context).set_monitoring(change.chat.id, False)
    elif old in ("left", "kicked", "restricted"):
        await pipeline(context).set_monitoring(change.chat.id, False)
        await context.bot.send_message(change.chat.id, INTRO)


async def error_handler(update, context):
    """Log exception classes only, keeping secrets and conversation text out of logs."""
    log.error("Telegram update failed: %s", type(context.error).__name__)
    if update and update.effective_message:
        try:
            await update.effective_message.reply_text("Core could not complete that action. Please try again or check /status.")
        except TelegramError:
            pass
