"""Exercise real PostgreSQL persistence with mocked external services."""

import asyncio
import os
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

from app.bot import handlers
from app.config import Config
from app.db.models import AnalysisWindow, Base, GeneratedProduct, Group, LoreProposal, Message, Vote, WindowMessage, now
from app.services.lore import Lore, LoreResult, extract_lore
from app.services.pipeline import Pipeline
from app.services.voting import cast_vote, outcome

CATEGORIES = [
    {"categoryId": "test-print-a", "name": "Test print A", "world": "print"},
    {"categoryId": "test-print-b", "name": "Test print B", "world": "print"},
]
LORE = {"lore": "We're so back", "type": "recurring_phrase",
        "explanation": "Two members adopt the same group motto.", "evidence_message_ids": [1, 2]}
PRODUCT = {"productId": "test-product", "unitPrice": {"amount": "48.00", "currency": "USD"},
           "imageUrl": "https://example.com/product.png", "selectionLabel": "Test selection"}


class VotingRules(unittest.TestCase):
    def test_bot_mentions(self):
        """Recognize only the bot's exact entity, including text following an emoji."""
        from telegram import Chat, Message as TelegramMessage, MessageEntity, User
        bot = SimpleNamespace(username="CoreBot", id=99)
        for text, entities, expected in [
            ("👀 @cOrEbOt", [MessageEntity("mention", 3, 8)], True),
            ("@OtherBot", [MessageEntity("mention", 0, 9)], False),
            ("@CoreBotExtra", [MessageEntity("mention", 0, 13)], False),
            ("@CoreBot", [], False),
        ]:
            message = TelegramMessage(1, now(), Chat(-100, "supergroup"),
                                      from_user=User(1, "User", False), text=text, entities=entities)
            self.assertEqual(handlers.mentions_bot(message, bot), expected)

    def test_outcomes(self):
        """Check plurality, No ties, runoffs and empty votes."""
        cases = [([], ("expired", [])), ([-1, -1, 0], ("rejected", [-1])),
                 ([-1, 0], ("rejected", [-1])), ([0, 0, 1, -1], ("generating", [0])),
                 ([0, 1], ("runoff", [0, 1]))]
        for choices, expected in cases:
            with self.subTest(choices=choices):
                self.assertEqual(outcome(choices), expected)


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "Set TEST_DATABASE_URL to an isolated PostgreSQL database")
class PipelineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        """Give each test its own PostgreSQL schema and mocked API clients."""
        self.schema = "core_test_" + uuid4().hex
        self.engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
        async with self.engine.begin() as connection:
            await connection.execute(CreateSchema(self.schema))
        self.engine = self.engine.execution_options(schema_translate_map={None: self.schema})
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        config = Config("test", "test", "test", "test", "Core-test", window_size=2, vote_seconds=10)
        self.bot = SimpleNamespace(username="core_test_bot", send_message=AsyncMock(
            return_value=SimpleNamespace(message_id=900)), send_photo=AsyncMock(),
            edit_message_reply_markup=AsyncMock(), get_chat_member=AsyncMock(
                return_value=SimpleNamespace(status="administrator")))
        self.openai = SimpleNamespace(responses=SimpleNamespace(parse=AsyncMock(
            return_value=SimpleNamespace(output_parsed=SimpleNamespace(lore=Lore(**LORE), category_ids=[item["categoryId"] for item in CATEGORIES])))))
        self.arcade = SimpleNamespace(
            categories=AsyncMock(return_value=CATEGORIES),
            generate=AsyncMock(return_value={"jobId": "test-job"}),
            job=AsyncMock(return_value={"status": "succeeded", "result": {"productId": "test-product"}}),
            product=AsyncMock(return_value=PRODUCT),
            checkout=AsyncMock(return_value={"checkoutId": "123", "checkoutUrl": "https://example.com/checkout"}),
        )
        self.core = Pipeline(config, self.sessions, self.bot, self.openai, self.arcade)
        await self.core.set_monitoring(-100, True)

    async def asyncTearDown(self):
        """Remove only the randomly named schema owned by this test."""
        await self.core.stop()
        async with self.engine.begin() as connection:
            await connection.execute(DropSchema(self.schema, cascade=True))
        await self.engine.dispose()

    def message(self, message_id, chat_id=-100, bot=False):
        """Make a Telegram-shaped text message without network calls."""
        return SimpleNamespace(chat_id=chat_id, message_id=message_id, text="We're so back",
                               from_user=SimpleNamespace(id=message_id, full_name="Test member", is_bot=bot),
                               date=now(), reply_to_message=SimpleNamespace(message_id=1) if message_id == 2 else None)

    async def count(self, model):
        """Count persisted rows for assertions."""
        async with self.sessions() as session:
            return await session.scalar(select(func.count()).select_from(model))

    async def proposal(self):
        """Drive message storage through structured extraction into a published vote."""
        await self.core.store_message(self.message(1))
        await self.core.store_message(self.message(2), analyze=True)
        window_id = await self.core.claim_window(-100)
        await self.core.analyze_window(window_id)
        async with self.sessions() as session:
            proposal = await session.scalar(select(LoreProposal))
        await self.core.advance_vote(proposal.id)
        async with self.sessions() as session:
            return await session.get(LoreProposal, proposal.id)

    async def vote_and_close(self, proposal, choices):
        """Record one vote per member then advance the persisted vote deadline."""
        async with self.sessions.begin() as session:
            current = await session.get(LoreProposal, proposal.id)
            for user, choice in enumerate(choices, start=10):
                await cast_vote(session, current, user, current.round, choice)
            current.closes_at = now() - timedelta(seconds=1)
        await self.core.advance_vote(proposal.id)
        async with self.sessions() as session:
            return await session.get(LoreProposal, proposal.id)

    async def test_collection_duplicate_isolation_and_bot(self):
        """Persist full metadata once, ignore bots, and isolate identical IDs across chats."""
        self.assertTrue(await self.core.store_message(self.message(2)))
        self.assertFalse(await self.core.store_message(self.message(2)))
        self.assertFalse(await self.core.store_message(self.message(3, bot=True)))
        self.assertFalse(await self.core.store_message(self.message(2, chat_id=-200)))
        await self.core.set_monitoring(-200, True)
        self.assertTrue(await self.core.store_message(self.message(2, chat_id=-200)))
        self.assertEqual(await self.count(Message), 2)
        async with self.sessions() as session:
            message = await session.scalar(select(Message).where(Message.chat_id == -100))
            self.assertEqual(message.reply_to_message_id, 1)
            self.assertEqual(message.sender_name, "Test member")

    async def test_mentions_and_duplicate_claims(self):
        """A mention creates one durable window shared by concurrent worker checks."""
        await self.core.store_message(self.message(1))
        self.assertIsNone(await self.core.claim_window(-100))
        await self.core.store_message(self.message(2), analyze=True)
        claims = await asyncio.gather(self.core.claim_window(-100), self.core.claim_window(-100))
        self.assertEqual(claims[0], claims[1])
        self.assertEqual(await self.count(AnalysisWindow), 1)

    async def test_no_lore_marks_window_analyzed(self):
        """A no-lore response consumes its window without retrieving Arcade categories."""
        self.openai.responses.parse.return_value = SimpleNamespace(output_parsed=LoreResult(lore=None))
        await self.core.store_message(self.message(1))
        await self.core.store_message(self.message(2), analyze=True)
        await self.core.analyze_window(await self.core.claim_window(-100))
        async with self.sessions() as session:
            self.assertEqual((await session.scalar(select(AnalysisWindow))).status, "analyzed")
        self.assertEqual(await self.count(LoreProposal), 0)
        self.arcade.categories.assert_not_awaited()

    async def test_messages_alone_never_trigger(self):
        """Exceeding the context limit stores messages without requesting any analysis."""
        for number in range(1, 6):
            await self.core.store_message(self.message(number))
        await self.core.process_chat(-100)
        self.assertEqual(await self.count(Message), 5)
        self.assertEqual(await self.count(AnalysisWindow), 0)
        self.openai.responses.parse.assert_not_awaited()

    async def test_short_fixed_snapshots_and_duplicate_mentions(self):
        """Mentions use latest context, preserve old snapshots, and deduplicate updates."""
        self.openai.responses.parse.return_value = SimpleNamespace(output_parsed=LoreResult(lore=None))
        await self.core.store_message(self.message(1), analyze=True)
        first = await self.core.claim_window(-100)
        await self.core.analyze_window(first)
        await self.core.store_message(self.message(2))
        await self.core.store_message(self.message(3), analyze=True)
        second = await self.core.claim_window(-100)
        await self.core.store_message(self.message(4))
        await self.core.analyze_window(second)
        import json
        sent = json.loads(self.openai.responses.parse.call_args.kwargs['input'][1]['content'])
        self.assertEqual([m['message_id'] for m in sent['messages']], [2, 3])
        self.assertFalse(await self.core.store_message(self.message(3), analyze=True))
        self.assertEqual(await self.count(AnalysisWindow), 2)
        async with self.sessions() as session:
            self.assertEqual(len(list(await session.scalars(select(WindowMessage).where(
                WindowMessage.window_id == first)))), 1)

    async def test_proposal_creation(self):
        """A grounded result produces actual supplied categories plus a timed vote."""
        proposal = await self.proposal()
        self.assertEqual(proposal.categories, CATEGORIES)
        self.assertEqual(proposal.lore, LORE)
        self.assertGreater(proposal.closes_at, now())
        self.arcade.generate.assert_not_awaited()

    async def test_user_can_change_vote(self):
        """A second choice replaces the first without adding a second active vote."""
        proposal = await self.proposal()
        async with self.sessions.begin() as session:
            self.assertTrue(await cast_vote(session, proposal, 42, 1, 0))
            self.assertTrue(await cast_vote(session, proposal, 42, 1, -1))
        self.assertEqual(await self.count(Vote), 1)
        async with self.sessions() as session:
            self.assertEqual((await session.scalar(select(Vote))).choice, -1)

    async def test_no_wins_without_generation(self):
        """A No plurality stops at rejection and cannot call generation."""
        proposal = await self.vote_and_close(await self.proposal(), [-1, -1, 0])
        await self.core.generate_product(proposal.id)
        self.assertEqual(proposal.status, "rejected")
        self.arcade.generate.assert_not_awaited()

    async def test_no_wins_tie(self):
        """A tied No decision rejects the proposal conservatively."""
        proposal = await self.vote_and_close(await self.proposal(), [-1, 0])
        self.assertEqual(proposal.status, "rejected")
        self.arcade.generate.assert_not_awaited()

    async def test_category_wins_and_generates(self):
        """A category win drives the real orchestration into a persisted product."""
        proposal = await self.vote_and_close(await self.proposal(), [0, 0, -1])
        self.assertEqual(proposal.status, "generating")
        await self.core.generate_product(proposal.id)
        self.arcade.generate.assert_awaited_once()
        self.assertEqual(self.arcade.generate.call_args.args[0]["categoryId"], CATEGORIES[0]["categoryId"])
        async with self.sessions() as session:
            self.assertEqual((await session.get(LoreProposal, proposal.id)).status, "generated")
            product = await session.scalar(select(GeneratedProduct))
            self.assertEqual(product.price, PRODUCT["unitPrice"])
        await self.core.notify_result(proposal.id)
        self.bot.send_photo.assert_awaited_once()

    async def test_runoff_does_not_reuse_old_votes(self):
        """Category ties create a new round that rejects old callbacks and votes."""
        proposal = await self.vote_and_close(await self.proposal(), [0, 1])
        self.assertEqual(proposal.status, "voting")
        self.assertEqual(proposal.round, 2)
        self.arcade.generate.assert_not_awaited()
        await self.core.advance_vote(proposal.id)
        async with self.sessions.begin() as session:
            proposal = await session.get(LoreProposal, proposal.id)
            self.assertFalse(await cast_vote(session, proposal, 42, 1, 0))
            self.assertTrue(await cast_vote(session, proposal, 42, 2, 1))
            proposal.closes_at = now() - timedelta(seconds=1)
        await self.core.advance_vote(proposal.id)
        async with self.sessions() as session:
            proposal = await session.get(LoreProposal, proposal.id)
            self.assertEqual(proposal.winner, CATEGORIES[1])

    async def test_zero_votes_expire(self):
        """An empty round expires without any product-generation request."""
        proposal = await self.vote_and_close(await self.proposal(), [])
        self.assertEqual(proposal.status, "expired")
        self.arcade.generate.assert_not_awaited()

    async def test_terminal_failure_allows_new_attempt(self):
        """A confirmed failed job needs an explicit retry before a new generation."""
        proposal = await self.vote_and_close(await self.proposal(), [0])
        self.arcade.job.return_value = {"status": "failed"}
        await self.core.generate_product(proposal.id)
        async with self.sessions() as session:
            current = await session.get(LoreProposal, proposal.id)
            self.assertEqual(current.status, "generation_failed")
            original = current.generation_request["requestId"]
        self.assertTrue(await self.core.retry(-100, proposal.id))
        self.arcade.job.return_value = {"status": "succeeded", "result": {"productId": "test-product"}}
        await self.core.generate_product(proposal.id)
        self.assertNotEqual(self.arcade.generate.call_args.args[0]["requestId"], original)

    async def test_uncertain_failure_reuses_request(self):
        """A lost HTTP response reuses exactly the saved generation body on retry."""
        proposal = await self.vote_and_close(await self.proposal(), [0])
        self.arcade.generate.side_effect = TimeoutError()
        await self.core.generate_product(proposal.id)
        original = self.arcade.generate.call_args.args[0]
        await self.core.retry(-100, proposal.id)
        self.arcade.generate.side_effect = None
        await self.core.generate_product(proposal.id)
        self.assertEqual(self.arcade.generate.call_args.args[0], original)

    async def test_product_failure_resumes_job_without_regeneration(self):
        """Missing pricing retries product retrieval without creating another product."""
        proposal = await self.vote_and_close(await self.proposal(), [0])
        self.arcade.product.side_effect = ValueError("price unavailable")
        await self.core.generate_product(proposal.id)
        self.assertEqual(await self.count(GeneratedProduct), 0)
        await self.core.retry(-100, proposal.id)
        self.arcade.product.side_effect = None
        await self.core.generate_product(proposal.id)
        self.arcade.generate.assert_awaited_once()

    async def test_pause_resume_and_old_messages(self):
        """Paused groups store no new messages and old queued messages need fresh consent."""
        await self.core.set_monitoring(-100, False)
        self.assertFalse(await self.core.store_message(self.message(1)))
        self.assertIsNone(await self.core.claim_window(-100))
        await self.core.set_monitoring(-100, True)
        old = self.message(1)
        old.date = now() - timedelta(days=1)
        self.assertFalse(await self.core.store_message(old))
        self.assertTrue(await self.core.store_message(self.message(2)))

    async def test_failed_window_can_retry(self):
        """A failed extraction retains its messages and needs an explicit retry."""
        await self.core.store_message(self.message(1))
        await self.core.store_message(self.message(2), analyze=True)
        window = await self.core.claim_window(-100)
        self.openai.responses.parse.side_effect = TimeoutError()
        await self.core.analyze_window(window)
        self.assertIsNone(await self.core.claim_window(-100))
        self.assertTrue(await self.core.retry(-100))
        self.openai.responses.parse.side_effect = None
        await self.core.analyze_window(window)
        self.assertEqual(await self.count(LoreProposal), 1)

    async def test_evidence_must_belong_to_window(self):
        """Reject a fabricated evidence ID even when the LLM schema is valid."""
        with self.assertRaises(ValueError):
            await extract_lore(self.openai, "test", [{"message_id": 7}], [])

    async def test_restart_uses_persisted_job(self):
        """A replacement process resumes an existing job without another create call."""
        proposal = await self.vote_and_close(await self.proposal(), [0])
        async with self.sessions.begin() as session:
            current = await session.get(LoreProposal, proposal.id)
            current.job_id = "saved-job"
            current.generation_request = {"requestId": "saved", "prompt": "saved", "categoryId": "test-print-a"}
        replacement = Pipeline(self.core.config, self.sessions, self.bot, self.openai, self.arcade)
        await replacement.generate_product(proposal.id)
        self.arcade.generate.assert_not_awaited()
        self.arcade.job.assert_awaited_with("saved-job")

    async def test_deletion_cascades_and_isolates_groups(self):
        """Group deletion removes all child records but leaves other groups untouched."""
        proposal = await self.vote_and_close(await self.proposal(), [0])
        await self.core.generate_product(proposal.id)
        await self.core.set_monitoring(-200, True)
        await self.core.store_message(self.message(1, -200))
        async with self.sessions.begin() as session:
            await session.execute(delete(Group).where(Group.chat_id == -100))
        for model in (AnalysisWindow, LoreProposal, Vote, GeneratedProduct):
            self.assertEqual(await self.count(model), 0)
        self.assertEqual(await self.count(Message), 1)

    async def test_checkout_is_private_and_buyer_specific(self):
        """Different buyers create separate checkouts and receive links only in DMs."""
        proposal = await self.vote_and_close(await self.proposal(), [0])
        await self.core.generate_product(proposal.id)
        context = SimpleNamespace(application=SimpleNamespace(bot_data={"pipeline": self.core}), bot=self.bot)
        for user_id in (11, 12):
            update = SimpleNamespace(effective_user=SimpleNamespace(id=user_id), update_id=user_id,
                                     effective_message=SimpleNamespace(reply_text=AsyncMock()))
            await handlers.checkout(update, context, proposal.id)
            self.assertEqual(update.effective_message.reply_text.await_count, 2)
        keys = [call.args[1] for call in self.arcade.checkout.call_args_list]
        self.assertNotEqual(keys[0], keys[1])

    async def test_slow_analysis_does_not_block_ingestion_and_pause_cancels(self):
        """Ingestion continues during OpenAI work, while pausing stops local analysis."""
        entered = asyncio.Event()

        async def slow_response(**kwargs):
            """Keep the external request pending until the group pauses."""
            entered.set()
            await asyncio.Event().wait()

        self.openai.responses.parse.side_effect = slow_response
        await self.core.store_message(self.message(1))
        await self.core.store_message(self.message(2), analyze=True)
        task = asyncio.create_task(self.core.process_chat(-100))
        self.core.tasks[-100] = task
        await asyncio.wait_for(entered.wait(), 2)
        self.assertTrue(await asyncio.wait_for(self.core.store_message(self.message(3)), 1))
        await self.core.set_monitoring(-100, False)
        self.assertTrue(task.cancelled())
        self.assertEqual(await self.count(Message), 3)
        self.assertEqual(await self.count(LoreProposal), 0)
        self.arcade.categories.assert_not_awaited()

    async def test_non_admin_cannot_enable_monitoring(self):
        """An ordinary member cannot provide group consent on behalf of an admin."""
        await self.core.set_monitoring(-100, False)
        self.bot.get_chat_member.return_value = SimpleNamespace(status="member")
        update = SimpleNamespace(effective_chat=SimpleNamespace(id=-100, type="supergroup"),
                                 effective_user=SimpleNamespace(id=42),
                                 effective_message=SimpleNamespace(reply_text=AsyncMock()))
        context = SimpleNamespace(application=SimpleNamespace(bot_data={"pipeline": self.core}), bot=self.bot)
        await handlers.resume(update, context)
        async with self.sessions() as session:
            self.assertEqual((await session.get(Group, -100)).status, "paused")

    async def test_callback_cannot_vote_from_another_group(self):
        """A copied callback cannot write a vote for another group's proposal."""
        proposal = await self.proposal()
        await self.core.set_monitoring(-200, True)
        query = SimpleNamespace(data=f"v:{proposal.id}:1:0", from_user=SimpleNamespace(id=42),
                                message=SimpleNamespace(message_id=900), answer=AsyncMock())
        update = SimpleNamespace(effective_chat=SimpleNamespace(id=-200, type="supergroup"), callback_query=query)
        context = SimpleNamespace(application=SimpleNamespace(bot_data={"pipeline": self.core}), bot=self.bot)
        await handlers.callback(update, context)
        self.assertEqual(await self.count(Vote), 0)

    async def test_confirmed_deletion_through_handler(self):
        """Deletion needs a valid admin confirmation and leaves monitoring disabled."""
        await self.proposal()
        context = SimpleNamespace(application=SimpleNamespace(bot_data={"pipeline": self.core}), bot=self.bot)
        update = SimpleNamespace(effective_chat=SimpleNamespace(id=-100, type="supergroup"),
                                 effective_user=SimpleNamespace(id=42),
                                 effective_message=SimpleNamespace(reply_text=AsyncMock()))
        await handlers.delete_data(update, context)
        self.assertEqual(await self.count(Message), 2)
        async with self.sessions() as session:
            token = (await session.get(Group, -100)).delete_token
        query = SimpleNamespace(data=f"d:{token}", answer=AsyncMock(), edit_message_text=AsyncMock())
        update.callback_query = query
        await handlers.callback(update, context)
        self.assertEqual(await self.count(Message), 0)
        self.assertEqual(await self.count(Group), 0)
        self.assertFalse(await self.core.store_message(self.message(3)))

    async def test_paused_worker_does_not_generate(self):
        """A scheduled worker that starts after pause cannot submit approved work."""
        await self.vote_and_close(await self.proposal(), [0])
        await self.core.set_monitoring(-100, False)
        await self.core.process_chat(-100)
        self.arcade.generate.assert_not_awaited()

    async def test_non_member_cannot_create_checkout(self):
        """A forwarded purchase deep link does not grant access to another group."""
        proposal = await self.vote_and_close(await self.proposal(), [0])
        await self.core.generate_product(proposal.id)
        self.bot.get_chat_member.return_value = SimpleNamespace(status="left")
        update = SimpleNamespace(effective_user=SimpleNamespace(id=42), update_id=123,
                                 effective_message=SimpleNamespace(reply_text=AsyncMock()))
        context = SimpleNamespace(application=SimpleNamespace(bot_data={"pipeline": self.core}), bot=self.bot)
        await handlers.checkout(update, context, proposal.id)
        self.arcade.checkout.assert_not_awaited()
