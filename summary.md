# Core — current application

## Current pipeline

Implemented: admin consent → Telegram text → PostgreSQL storage → configurable
window → GPT structured lore → actual Arcade categories plus No → persisted inline
votes → approved Arcade generation → real image/unit price → private buyer checkout.
Live end-to-end verification awaits credentials. Verification uses real PostgreSQL
with mocked Telegram/OpenAI/Arcade services.

Monitoring defaults off. `/start` explains; only an admin can `/resume`, `/pause`,
`/retry` or confirm `/delete_data`. Only new ordinary text during monitoring is
stored. Commands, edits, bots and queued messages predating consent are excluded.

A worker scans every two seconds with one task per group. Short locks serialize
storage, window claims and votes; slow external calls do not hold them. A PostgreSQL
session advisory lock prevents a second worker. Use one poller and a direct or
session-pooled database connection.

Exactly X messages are assigned to a chronological window. GPT receives speaker,
timestamp, text, message/reply IDs and the latest 20 proposals. Structured output
must cite IDs inside the window. Null consumes a window without a proposal. Failed
windows retain messages and block later windows until an admin retries.

Arcade supplies categories dynamically. Core picks up to three distinct category
groups, preferring print and sorting alphabetically. One vote per member/round can
change until the deadline. Highest count wins; No wins ties; category ties create
a fresh runoff with tied categories and No; zero votes expire. Old-round votes do
not count in a new round.

Only a category winner starts generation. The exact idempotent request is saved
before `patterns/create`, then the persisted job ID is polled. The documented
combined workflow with categoryId makes print or jewelry with Arcade-selected
options. A real `unitPrice` is required; null pricing becomes a retry state. Fresh
retrieval renews signed image URLs before posting. Get yours opens a private chat,
verifies group membership and creates a buyer-specific quantity-one checkout with
dropShip=true. No payments are submitted by Core.

## Important files

- `app/main.py`: polling, lifecycle, handler registration and single-worker lock.
- `app/config.py`: environment configuration and positive integer validation.
- `app/bot/handlers.py`: consent, admin controls, ingestion, callbacks, private checkout.
- `app/bot/keyboards.py`: round-specific voting, retries and purchase deep links.
- `app/services/pipeline.py`: collection, window/proposal transitions, workers, retries,
  product generation/delivery, pause and recovery.
- `app/services/lore.py`: structured OpenAI output, grounded prompt, evidence validation.
- `app/services/arcade.py`: documented HTTP integration, transient retries, categories,
  generation, jobs, product quotes and checkout.
- `app/services/voting.py`: atomic vote upserts, counting and runoffs.
- `app/db/models.py`: six SQLAlchemy models and UTC timestamp helpers.
- `app/db/session.py`: asyncpg engine, SQLAlchemy sessions and table creation.
- `tests/test_core.py`: PostgreSQL workflow tests with external services mocked.
- `tests/test_arcade.py`: Arcade HTTP contract tests with a mock transport.
- `README.md`: setup, BotFather, variables, workflow, tests and manual demo.
- `env.example`: placeholders only; real `.env` is gitignored.

## Database

Startup creates missing tables; there are no schema migrations.

- `groups`: Telegram chat ID; paused/monitoring; consent time; expiring deletion token.
- `messages`: group ID, Telegram message ID, sender ID/name, text, timestamp, reply ID,
  window ID. Unique `(chat_id, telegram_message_id)`. Null window means collecting.
- `analysis_windows`: group ID, analyzing/analyzed/failed, saved lore, sanitized error.
- `lore_proposals`: group/window, lore, categories, round/deadline, Telegram post,
  winner, exact generation request, job ID, error and notification flag. Statuses:
  voting, rejected, expired, generating, generated, generation_failed.
- `votes`: proposal, round, member, choice index (`-1` is No), timestamp. Unique per
  proposal/round/member; changing a vote is an upsert.
- `generated_products`: proposal, Arcade product ID, category, price, image URL,
  full retrieved product details and creation time.

Foreign-key cascades remove all child records when a group is deleted. Deletion
leaves monitoring off. Payment data and checkout URLs are not stored locally.

## External services

- Telegram: long polling, admin/member checks, consent commands, inline votes,
  product posts, private checkout. Disable privacy mode and make the bot an admin.
- OpenAI: Responses parse/Pydantic schema with store=False. Receives consented
  windows and recent lore; provider retention policies still apply. No scores/embeddings.
- Arcade: platform.arcade.ai (not arcade.dev); POST categories/list, patterns/create,
  jobs/get, products/get, checkouts/create. Bearer auth and identifying User-Agent.
  Contract checked at https://docs.arcade.ai/openapi.json and official quickstarts.
  Null prices are never invented; short-lived image URLs are refreshed.
- PostgreSQL: durable pipeline data and worker lock. No queue, Redis or HTTP server.

## Environment variables

Required: TELEGRAM_BOT_TOKEN, OPENAI_API_KEY, ARCADE_API_KEY, DATABASE_URL,
ARCADE_USER_AGENT (integration name/contact).

Optional: OPENAI_MODEL (gpt-4o-mini), ARCADE_BASE_URL (https://platform.arcade.ai/v1),
LORE_WINDOW_SIZE (100), VOTE_DURATION_SECONDS (120), ARCADE_POLL_SECONDS (5),
ARCADE_JOB_TIMEOUT_SECONDS (600). Blank optional values use defaults.
TEST_DATABASE_URL is test-only and enables the full PostgreSQL suite.

## Current state

**Working (implemented and verified with mocked external services)**

- Consent, isolated/deduplicated storage, window thresholds, structured lore/no-lore.
- Real API category client, persisted proposals, changed votes, No ties, runoffs, expiry.
- Vote-gated generation, idempotent requests, saved jobs, retries/restart recovery.
- Real-price/image retrieval, result posts and private per-buyer checkouts.
- Pause/removal controls, confirmed cascading deletion and sanitized error logs.
- Full suite: 30 tests passed against isolated local PostgreSQL, including in-flight
  pause cancellation, admin consent, cross-group callbacks and private checkout access.
- Dependency consistency, syntax compilation, function docstrings and secret-pattern
  checks passed. No real .env was created and no credentials were added.

**In progress**

- Live demo requires provider credentials and a Telegram group; implementation and
  local verification are complete.

**Not implemented**

- Payment/order tracking, user-selected variants, provider-side data deletion,
  cancelling submitted external jobs, schema migrations, group ID migration, hosting.
- No frontend, scoring, embeddings, analytics or profiles.

**Known issues / limitations**

- No live provider call or purchase is verified; credentials are not included.
- Single process. Failed windows hold later windows until `/retry`.
- Slow per-group generation can delay later result announcements; ingestion and
  other groups continue independently.
- Vote deadlines continue while paused; resume resolves overdue votes.
- Local cancellation cannot undo requests already submitted to external providers.
- A crash between Telegram send and DB commit can duplicate a post. Message/round
  validation prevents stale vote buttons affecting a different active vote.
- Repeated category ties mean more runoffs, never an invented winner.
- Reliable Telegram membership checks need the bot to be a group administrator.
- Checkout may add shipping, drop-ship fees and tax to the displayed unit price.
