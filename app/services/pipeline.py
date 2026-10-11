"""A single-process async worker with persisted work and one task per group."""

import asyncio
import logging
from collections import defaultdict
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from telegram.error import BadRequest, Forbidden, TelegramError

from app.bot.keyboards import buy_keyboard, retry_keyboard, voting_keyboard
from app.db.models import AnalysisWindow, GeneratedProduct, Group, LoreProposal, Message, WindowMessage, new_id, now
from app.services.arcade import JobFailed
from app.services.lore import extract_lore, select_categories
from app.services.voting import close_vote

log = logging.getLogger(__name__)


class Pipeline:
    def __init__(self, config, sessions, bot, openai, arcade):
        """Keep short group locks separate from slow external service calls."""
        self.config, self.sessions, self.bot = config, sessions, bot
        self.openai, self.arcade = openai, arcade
        self.locks = defaultdict(asyncio.Lock)
        self.tasks = {}
        self.worker = None

    async def store_message(self, message, analyze=False):
        """Store consented text and atomically snapshot recent messages on a mention."""
        if not message.from_user or message.from_user.is_bot or not message.text:
            return False
        async with self.locks[message.chat_id], self.sessions.begin() as session:
            group = await session.get(Group, message.chat_id)
            if (group is None or group.status != "monitoring"
                    or group.consent_at is None or message.date < group.consent_at):
                return False
            statement = insert(Message).values(
                chat_id=message.chat_id, telegram_message_id=message.message_id,
                sender_id=message.from_user.id, sender_name=message.from_user.full_name,
                text=message.text, timestamp=message.date,
                reply_to_message_id=(message.reply_to_message.message_id
                                     if message.reply_to_message else None),
            ).on_conflict_do_nothing(index_elements=[Message.chat_id, Message.telegram_message_id])
            result = await session.execute(statement)
            if result.rowcount == 1 and analyze:
                await self.request_window(session, message.chat_id)
            return result.rowcount == 1

    async def set_monitoring(self, chat_id, enabled):
        """Pause local work immediately or explicitly enable new message collection."""
        async with self.locks[chat_id], self.sessions.begin() as session:
            group = await session.get(Group, chat_id)
            if group is None:
                group = Group(chat_id=chat_id)
                session.add(group)
            group.status = "monitoring" if enabled else "paused"
            if enabled:
                # Telegram timestamps have second precision; reject older queued updates.
                group.consent_at = now().replace(microsecond=0)
        if not enabled:
            await self.cancel_chat(chat_id)

    async def cancel_chat(self, chat_id):
        """Cancel local API polling before pausing or deleting a group's records."""
        task = self.tasks.pop(chat_id, None)
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def start(self):
        """Start scanning persisted work, including unfinished work after a restart."""
        try:
            catalog = await self.arcade.categories()
            log.info("Loaded %s Arcade product categories.", len(catalog))
        except Exception as error:
            log.warning("Arcade catalog preload failed (%s); will retry on analysis.", type(error).__name__)
        self.worker = asyncio.create_task(self.run())

    async def stop(self):
        """Cancel background tasks and leave their persisted work recoverable."""
        tasks = list(self.tasks.values())
        if self.worker:
            tasks.append(self.worker)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def run(self):
        """Give each monitoring group its own nonblocking worker every two seconds."""
        while True:
            try:
                async with self.sessions() as session:
                    chat_ids = list(await session.scalars(select(Group.chat_id).where(Group.status == "monitoring")))
                for chat_id in chat_ids:
                    if chat_id not in self.tasks or self.tasks[chat_id].done():
                        self.tasks[chat_id] = asyncio.create_task(self.process_chat(chat_id))
            except Exception as error:
                log.error("Worker scan failed: %s", type(error).__name__)
            await asyncio.sleep(2)

    async def process_chat(self, chat_id):
        """Advance proposals and at most one conversation window per worker pass."""
        try:
            async with self.sessions() as session:
                group = await session.get(Group, chat_id)
                if group is None or group.status != "monitoring":
                    return
                proposals = list(await session.scalars(select(LoreProposal).where(
                    LoreProposal.chat_id == chat_id,
                ).order_by(LoreProposal.created_at)))
            for proposal in proposals:
                if proposal.status == "voting":
                    await self.advance_vote(proposal.id)
                elif proposal.status == "generating":
                    await self.generate_product(proposal.id)
                elif not proposal.notified:
                    await self.notify_result(proposal.id)
            window_id = await self.claim_window(chat_id)
            if window_id:
                await self.analyze_window(window_id)
        except Forbidden:
            await self.set_monitoring(chat_id, False)
        except Exception as error:
            # Exception messages can contain request URLs, tokens, or private text.
            log.error("Group work failed (%s): %s", chat_id, type(error).__name__)
            await asyncio.sleep(5)

    async def request_window(self, session, chat_id):
        """Snapshot up to the latest N messages; coalesce mentions while work is pending."""
        existing = await session.scalar(select(AnalysisWindow.id).where(
            AnalysisWindow.chat_id == chat_id,
            AnalysisWindow.status.in_(["analyzing", "failed"]),
        ).limit(1))
        if existing:
            return
        messages = list(await session.scalars(select(Message).where(
            Message.chat_id == chat_id,
        ).order_by(Message.timestamp.desc(), Message.telegram_message_id.desc())
          .limit(self.config.window_size)))
        if not messages:
            return
        window = AnalysisWindow(chat_id=chat_id)
        session.add(window)
        await session.flush()
        # Separate membership rows preserve overlapping windows and exact retry context.
        for message in messages:
            session.add(WindowMessage(window_id=window.id, message_id=message.id))

    async def claim_window(self, chat_id):
        """Find previously requested work without triggering analysis from message counts."""
        async with self.locks[chat_id], self.sessions() as session:
            group = await session.get(Group, chat_id)
            if not group or group.status != "monitoring":
                return None
            return await session.scalar(select(AnalysisWindow.id).where(
                AnalysisWindow.chat_id == chat_id, AnalysisWindow.status == "analyzing",
            ).order_by(AnalysisWindow.created_at).limit(1))

    async def analyze_window(self, window_id):
        """Persist grounded lore before retrieving categories and creating its vote."""
        async with self.sessions() as session:
            window = await session.get(AnalysisWindow, window_id)
            if window is None:
                return
            chat_id, lore = window.chat_id, window.lore
            message_ids = list(await session.scalars(select(WindowMessage.message_id).where(
                WindowMessage.window_id == window_id,
            )))
            # Older windows retain their original Message.window_id association.
            condition = Message.id.in_(message_ids) if message_ids else Message.window_id == window_id
            messages = list(await session.scalars(select(Message).where(
                Message.chat_id == chat_id, condition,
            ).order_by(Message.timestamp, Message.telegram_message_id)))
            recent = list(await session.scalars(select(LoreProposal.lore).where(
                LoreProposal.chat_id == chat_id,
            ).order_by(LoreProposal.created_at.desc()).limit(20)))
        try:
            if lore is None:
                formatted = [{
                    "message_id": message.telegram_message_id,
                    "speaker_id": message.sender_id, "speaker": message.sender_name,
                    "timestamp": message.timestamp.isoformat(), "text": message.text,
                    "reply_to_message_id": message.reply_to_message_id,
                } for message in messages]
                lore = await extract_lore(self.openai, self.config.openai_model, formatted, recent)
                async with self.sessions.begin() as session:
                    window = await session.get(AnalysisWindow, window_id)
                    if window is None:
                        return
                    window.lore = lore
                    if lore is None:
                        window.status = "analyzed"
                        return
            catalog = await self.arcade.categories()
            categories = await select_categories(
                self.openai, self.config.openai_model, lore,
                messages[-1].text if messages else "", catalog,
            )
            async with self.locks[chat_id], self.sessions.begin() as session:
                window = await session.get(AnalysisWindow, window_id)
                if window is None:
                    return
                session.add(LoreProposal(chat_id=chat_id, window_id=window_id,
                                         lore=lore, categories=categories))
                window.status = "analyzed"
                window.error = None
        except Exception as error:
            async with self.sessions.begin() as session:
                window = await session.get(AnalysisWindow, window_id)
                if window is None:
                    return
                window.status, window.error = "failed", type(error).__name__
            await self.bot.send_message(chat_id,
                "Lore analysis or product selection failed. Your window is saved. "
                "An admin can use /retry to try again.")

    async def advance_vote(self, proposal_id):
        """Publish a full-duration vote or persist its decision when time runs out."""
        async with self.sessions() as session:
            proposal = await session.get(LoreProposal, proposal_id)
        if not proposal or proposal.status != "voting":
            return
        if proposal.telegram_message_id is None:
            title = "Runoff vote" if proposal.round > 1 else "New lore unlocked 👀"
            message = await self.bot.send_message(proposal.chat_id,
                f"{title}\n\n{proposal.lore['lore']}\n\n{proposal.lore['explanation']}\n\n"
                f"What are we making? Vote within {self.config.vote_seconds} seconds. "
                "You can change your vote.", reply_markup=voting_keyboard(proposal))
            async with self.sessions.begin() as session:
                current = await session.get(LoreProposal, proposal_id)
                current.telegram_message_id = message.message_id
                current.closes_at = now() + timedelta(seconds=self.config.vote_seconds)
            return
        if proposal.closes_at > now():
            return
        async with self.locks[proposal.chat_id], self.sessions.begin() as session:
            current = await session.get(LoreProposal, proposal_id)
            if current.status != "voting" or current.closes_at is None or current.closes_at > now():
                return
            status = await close_vote(session, current)
        try:
            await self.bot.edit_message_reply_markup(proposal.chat_id, proposal.telegram_message_id,
                                                     reply_markup=None)
        except TelegramError:
            pass  # Server-side status and round checks still reject stale buttons.
        if status == "generating":
            await self.bot.send_message(proposal.chat_id, "The chat has spoken. Making this one real...")

    async def generate_product(self, proposal_id):
        """Resume idempotent Arcade work and persist the real product and price."""
        async with self.sessions.begin() as session:
            proposal = await session.get(LoreProposal, proposal_id)
            if proposal is None or proposal.status != "generating":
                return
            if proposal.generation_request is None:
                category = proposal.winner
                prompt = (f"Create a {category['name']} celebrating this group's inside joke: "
                          f"{proposal.lore['lore']}. Context: {proposal.lore['explanation']}. "
                          "Make a tasteful, manufacturable design appropriate to the chosen category.")[:1000]
                # Persist the exact body before sending: retries must not create another product.
                proposal.generation_request = {"requestId": new_id(), "prompt": prompt,
                                               "categoryId": category["categoryId"]}
            request, job_id, chat_id = proposal.generation_request, proposal.job_id, proposal.chat_id
        try:
            if not job_id:
                job = await self.arcade.generate(request)
                job_id = job["jobId"]
                async with self.sessions.begin() as session:
                    proposal = await session.get(LoreProposal, proposal_id)
                    proposal.job_id = job_id
            deadline = asyncio.get_running_loop().time() + self.config.job_timeout
            while True:
                job = await self.arcade.job(job_id)
                if job["status"] == "failed":
                    raise JobFailed("Arcade reported a failed job")
                if job["status"] == "succeeded":
                    product_id = job["result"]["productId"]
                    product = await self.arcade.product(product_id)
                    async with self.sessions.begin() as session:
                        proposal = await session.get(LoreProposal, proposal_id)
                        if proposal is None:
                            return
                        session.add(GeneratedProduct(
                            proposal_id=proposal_id, arcade_product_id=product["productId"],
                            category=proposal.winner, price=product["unitPrice"],
                            image_url=product.get("imageUrl"), details=product,
                        ))
                        proposal.status, proposal.error = "generated", None
                    return
                if job["status"] != "running":
                    raise ValueError("Unknown Arcade job status")
                if asyncio.get_running_loop().time() >= deadline:
                    raise TimeoutError("Arcade job is still running; retry polling")
                await asyncio.sleep(self.config.poll_seconds)
        except Exception as error:
            async with self.sessions.begin() as session:
                proposal = await session.get(LoreProposal, proposal_id)
                if proposal is None:
                    return
                proposal.status = "generation_failed"
                proposal.error = type(error).__name__
                proposal.terminal_job_failure = isinstance(error, JobFailed)
                proposal.notified = False
            log.warning("Generation needs retry (%s): %s", chat_id, type(error).__name__)

    async def notify_result(self, proposal_id):
        """Post the persisted result; Telegram failures never regenerate a product."""
        async with self.sessions() as session:
            proposal = await session.get(LoreProposal, proposal_id)
            if proposal is None or proposal.notified:
                return
            product = await session.scalar(select(GeneratedProduct).where(
                GeneratedProduct.proposal_id == proposal_id,
            ))
        if proposal.status == "generated":
            # Signed image URLs expire, so refresh the product before publishing.
            details = await self.arcade.product(product.arcade_product_id)
            price = details["unitPrice"]
            caption = (f"Your lore is officially real.\n\n{proposal.lore['lore']}\n\n"
                       f"{price['amount']} {price['currency']} per item\n"
                       f"{details.get('selectionLabel', '')[:200]}\n"
                       "Shipping, drop-ship service and tax are shown at checkout.")
            keyboard = buy_keyboard(proposal.id, self.bot.username)
            if details.get("imageUrl"):
                try:
                    await self.bot.send_photo(proposal.chat_id, details["imageUrl"],
                                              caption=caption, reply_markup=keyboard)
                except BadRequest:
                    await self.bot.send_message(proposal.chat_id, caption, reply_markup=keyboard)
            else:
                await self.bot.send_message(proposal.chat_id, caption, reply_markup=keyboard)
        elif proposal.status == "generation_failed":
            await self.bot.send_message(proposal.chat_id,
                "Arcade generation or product retrieval could not finish. No product or price "
                "has been assumed. An admin can retry; existing work will be resumed where possible.",
                reply_markup=retry_keyboard(proposal.id))
        elif proposal.status == "rejected":
            await self.bot.send_message(proposal.chat_id, "Keeping this one in the chat. No product will be generated.")
        elif proposal.status == "expired":
            await self.bot.send_message(proposal.chat_id, "Nobody voted, so this proposal expired. Nothing will be generated.")
        else:
            return
        async with self.sessions.begin() as session:
            proposal = await session.get(LoreProposal, proposal_id)
            if proposal:
                proposal.notified = True

    async def retry(self, chat_id, proposal_id=None):
        """Retry saved failures, using a new generation key only for a terminal job failure."""
        async with self.locks[chat_id], self.sessions.begin() as session:
            group = await session.get(Group, chat_id)
            if not group or group.status != "monitoring":
                return False
            changed = False
            if proposal_id is None:
                windows = list(await session.scalars(select(AnalysisWindow).where(
                    AnalysisWindow.chat_id == chat_id, AnalysisWindow.status == "failed",
                )))
                for window in windows:
                    window.status, window.error = "analyzing", None
                    changed = True
            query = select(LoreProposal).where(LoreProposal.chat_id == chat_id,
                                               LoreProposal.status == "generation_failed")
            if proposal_id:
                query = query.where(LoreProposal.id == proposal_id)
            for proposal in await session.scalars(query):
                if proposal.terminal_job_failure:
                    proposal.job_id, proposal.generation_request = None, None
                proposal.status, proposal.error, proposal.notified = "generating", None, False
                proposal.terminal_job_failure = False
                changed = True
            return changed

    async def status(self, chat_id):
        """Show collection counts and actionable persisted failures without private text."""
        async with self.sessions() as session:
            group = await session.get(Group, chat_id)
            count = await session.scalar(select(func.count()).select_from(Message).where(
                Message.chat_id == chat_id,
            ))
            windows = list(await session.scalars(select(AnalysisWindow.status).where(AnalysisWindow.chat_id == chat_id)))
            proposals = list(await session.scalars(select(LoreProposal.status).where(LoreProposal.chat_id == chat_id)))
        return (f"Monitoring: {group.status if group else 'paused'}\n"
                f"Stored messages: {count}\n"
                f"Mention @{self.bot.username} to analyze up to {self.config.window_size} recent messages.\n"
                f"Analyzing windows: {windows.count('analyzing')}\n"
                f"Failed windows: {windows.count('failed')}\n"
                f"Open votes: {proposals.count('voting')}\n"
                f"Generating: {proposals.count('generating')}\n"
                f"Generation failures: {proposals.count('generation_failed')}\n"
                "Admins: /resume, /pause, /retry, /delete_data")
