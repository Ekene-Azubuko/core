"""Compact callback buttons; category IDs never need to fit in callback data."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def voting_keyboard(proposal):
    """Show current category choices and No, with a round-specific callback."""
    rows = [[InlineKeyboardButton(category["name"][:60], callback_data=
             f"v:{proposal.id}:{proposal.round}:{index}")]
            for index, category in enumerate(proposal.categories)]
    rows.append([InlineKeyboardButton("No, keep it in the chat", callback_data=
                 f"v:{proposal.id}:{proposal.round}:-1")])
    return InlineKeyboardMarkup(rows)


def retry_keyboard(proposal_id):
    """Offer an admin-controlled retry after a generation failure."""
    return InlineKeyboardMarkup([[InlineKeyboardButton("Retry", callback_data=f"r:{proposal_id}")]])


def buy_keyboard(proposal_id, bot_username):
    """Open a private chat so each buyer gets their own checkout link."""
    return InlineKeyboardMarkup([[InlineKeyboardButton(
        "Get yours", url=f"https://t.me/{bot_username}?start=buy_{proposal_id}",
    )]])
