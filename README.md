# Core

Core turns shared Telegram group jokes into physical products:

**Consent → stored text → @mention → GPT lore → category/No vote → Arcade product → private checkout.**

The application is implemented and verified with PostgreSQL and mocked external
services. A live demo still requires your Telegram, OpenAI and Arcade credentials.
No live generation or purchase has been verified in this repository.

## Setup

Requires Python 3.10+ and PostgreSQL 14+.

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp env.example .env
```

For local storage, start Docker Desktop and set `POSTGRES_USER=core`,
`POSTGRES_DB=core` and your chosen `POSTGRES_PASSWORD` in `.env`. Set
`DATABASE_URL=postgresql+asyncpg://core:YOUR_PASSWORD@127.0.0.1:5433/core`
(URL-encode special characters in the URL password). Then run:

```sh
docker compose up -d --wait
docker compose ps
```

PostgreSQL 17 stores data in `.data/postgres/` inside the project root via a bind
mount. This directory is gitignored and survives container recreation. Only
localhost port 5433 is exposed. Use `docker compose stop` to stop it and
`docker compose up -d --wait` to start it again. Do not delete `.data/postgres/`
unless you intend to erase the database. Changing initialization variables after
the first start does not change existing database credentials.

The old native `.postgres/` directory, if present, is separate and is not
automatically migrated. Tables are created when Core starts. Hosted PostgreSQL
also works; use a direct/session-pooled connection, not transaction pooling,
because Core uses a session advisory lock.

Fill `.env` locally; never commit it. `env.example` contains blank placeholders only.

| Variable | Purpose / default |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Required; BotFather token |
| `OPENAI_API_KEY` | Required; OpenAI project key |
| `ARCADE_API_KEY` | Required; Arcade physical-product API key |
| `DATABASE_URL` | Required; PostgreSQL connection URL |
| `POSTGRES_USER` | Docker database user; default `core` |
| `POSTGRES_DB` | Docker database name; default `core` |
| `POSTGRES_PASSWORD` | Required for Docker initialization; match the URL password |
| `ARCADE_USER_AGENT` | Required; integration name/contact, e.g. `Core/0.1 (YOUR_CONTACT)` |
| `OPENAI_MODEL` | Structured-output-capable model; default `gpt-5.6-luna` |
| `ARCADE_BASE_URL` | Default `https://platform.arcade.ai/v1` |
| `LORE_WINDOW_SIZE` | Maximum recent messages per mention; default `100` |
| `VOTE_DURATION_SECONDS` | Default `120`; use `30` for demos |
| `ARCADE_POLL_SECONDS` | Default `5` |
| `ARCADE_JOB_TIMEOUT_SECONDS` | Default `600`; retry resumes the same job |

Blank optional values use defaults. Numeric settings must be positive integers.
Arcade keys come from [Arcade Studio](https://studio.arcade.ai/settings/api-keys).
This is the **arcade.ai physical-product API**, not arcade.dev agent tooling.

## Telegram setup and running

1. Open **@BotFather**, run `/newbot`, and put its token in `.env`.
2. Run `/setprivacy`, choose Core, and select **Disable**. Remove and re-add the
   bot if it was already in the group so the setting takes effect.
3. Add Core to your test group and make it an administrator. This makes Telegram
   membership checks reliable; it also needs permission to send messages/photos.
4. Start Core:

   ```sh
   python -m app.main
   ```

5. Read the introduction, then have an admin send `/resume` to consent.

Run one process per bot/database. Long polling needs no public HTTP server,
webhook, FastAPI, Redis, Celery or frontend. Do not run another poller or configure
a competing Telegram webhook with the same token.

| Command | Behavior |
| --- | --- |
| `/start` | Explain consent; does not enable monitoring |
| `/resume` | Admin enables new-message collection and resumes saved work |
| `/pause` | Admin stops collection, local analysis, voting and job polling |
| `/status` | Show this group's counts and failure states |
| `/retry` | Admin retries failures for analysis or already-approved generation |
| `/delete_data` | Admin confirms local deletion with a button within five minutes |

Commands, edits, non-text messages and bot messages are excluded. Messages before
consent or during pauses are not stored. Each window includes speaker IDs/names,
text, timestamps, message IDs and reply relationships. OpenAI receives consented
windows plus recent lore with `store=False`; provider retention policies still
apply. Only selected lore/context goes to Arcade after a category wins.

Pause preserves saved windows and votes. Vote deadlines are wall-clock times;
overdue votes resolve after resume. Already-submitted external requests may finish
at their provider after a pause or deletion. `/delete_data` removes local records,
not Telegram history or provider-side data/products, and leaves monitoring off.

## Flow and architecture

1. PostgreSQL deduplicates messages by `(chat_id, telegram_message_id)`.
2. Text is stored continuously while monitoring. An explicit @mention snapshots
   up to `LORE_WINDOW_SIZE` latest messages, including the mention, even if fewer
   are available. The background worker processes saved requests every two seconds.
   Ordinary messages never trigger analysis. Per-group locks stay short; slow APIs do not block ingestion.
   One task runs per group; a database advisory lock prevents a second worker.
3. OpenAI Responses structured output supplies grounded lore or null. Evidence IDs
   must be in the window. The latest 20 proposals discourage repetitive suggestions.
   A no-lore window is consumed; failed analysis stays saved for `/retry`.
4. Arcade `categories/list` supplies real categories. Core chooses at most three
   distinct category groups, preferring print and sorting names alphabetically.
5. Inline votes allow one changeable vote per member per round. Highest count wins
   (plurality, as specified). No wins ties; zero votes expire. A category tie runs
   another round with the tied categories and No. Old votes/buttons cannot affect it.
6. Only a category win calls `patterns/create` with `categoryId`. This documented
   combined workflow makes both print and jewelry products with Arcade-selected
   options. Core sends no jewelry-incompatible model/color/reference fields.
7. The exact idempotent request is persisted before submission. The saved job is
   polled with `jobs/get`. Lost responses and timeouts resume existing work; only
   a confirmed terminal failure and admin retry create a fresh generation request.
8. `products/get` retrieves a real single-unit price, selection and fresh image URL.
   Null pricing produces a retry state rather than a fabricated quote. A failed
   image post falls back to a text post with the real price and purchase button.
9. **Get yours** opens a private bot chat. Core checks membership and creates a
   separate checkout for that buyer request using quantity one and `dropShip: true`.
   URLs are not assumed reusable or posted in the group. The buyer reviews and pays
   on Arcade's hosted page. Core does not submit payment or store card/address data.

Displayed unit prices exclude shipping, drop-ship service and tax charged at
checkout. The actual product selection is shown; user-chosen variants are not implemented.

| File | Responsibility |
| --- | --- |
| `app/main.py` | Startup, polling, handlers, worker lock and shutdown |
| `app/config.py` | Environment configuration |
| `app/bot/handlers.py` | Consent, controls, ingestion, callbacks, private checkout |
| `app/bot/keyboards.py` | Voting, retry and purchase buttons |
| `app/services/pipeline.py` | Windows, durable workflow, worker and recovery |
| `app/services/lore.py` | OpenAI schema, prompt and evidence validation |
| `app/services/arcade.py` | Arcade HTTP API, categories, jobs, prices and checkout |
| `app/services/voting.py` | Vote upserts, counting and runoffs |
| `app/db/models.py` | Seven SQLAlchemy models and cascading deletion |
| `app/db/session.py` | Async PostgreSQL sessions and table creation |
| `tests/` | Standard-library tests with mocked external services |
| `summary.md` | Current implementation and known limitations |

## Verification

The explicitly requested tests use standard-library `unittest`, without pytest or
type-checking dependencies. Run the API-contract and voting checks:

```sh
python -m unittest discover -s tests -v
```

Database tests are **skipped** without `TEST_DATABASE_URL`. Run the full suite with
a separate disposable PostgreSQL database:

```sh
createdb core_test
export TEST_DATABASE_URL='postgresql+asyncpg://YOUR_USER:YOUR_PASSWORD@YOUR_HOST:5432/core_test'
python -m unittest discover -s tests -v
```

Each database test creates and drops only its randomly named schema; the test DB
user needs schema-creation permission. Telegram, OpenAI and Arcade are mocked.
Tests create no real products or checkouts.

Manual demo after configuring real credentials:

1. Set window size to 10 and vote duration to 30, then restart Core.
2. Verify messages before `/resume` do not increase `/status` counts.
3. Enable monitoring; have members chat, then @mention the bot. Confirm ordinary
   messages alone never trigger analysis and a mention works with fewer than 10.
4. Confirm grounded lore and actual category buttons appear. Vote No and verify
   no generation. Exercise a No tie and category runoff.
5. Establish new lore, @mention Core again, and choose a category. Wait for the real
   image/price. Open Get yours from two accounts and verify separate private checkouts.
6. Pause/resume and restart during a job; confirm saved work resumes.
7. Confirm `/delete_data` clears the group and turns monitoring off.

Real generation may consume provider credits. Payment is a separate deliberate
action on Arcade's hosted checkout page.

## MVP limits

- Single process; no schema migrations or deployment automation.
- Live provider credentials and a real Telegram group were unavailable during build.
- A crash between Telegram send and database commit may duplicate a post. Persisted
  message/round checks prevent stale buttons from changing the active vote.
- Failed windows block later windows until `/retry`. Work is sequential per group,
  so a slow job can delay later result announcements; other groups and ingestion continue.
- Group-to-supergroup ID migration is not automatic. Pause/delete before migrating
  and enable in the new group.
- Category ties may keep producing runoffs; Core never invents a winner.
- No order tracking, user-selected variants, provider-side deletion or cancellation
  of already-submitted external jobs.

## Official references

Checked October 11, 2026:

- [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [Telegram Bot API](https://core.telegram.org/bots/api)
- [python-telegram-bot lifecycle](https://docs.python-telegram-bot.org/en/stable/telegram.ext.application.html)
- [Arcade quickstart](https://docs.arcade.ai/quickstart)
- [Arcade OpenAPI schema](https://docs.arcade.ai/openapi.json)
- [Arcade idempotency](https://docs.arcade.ai/idempotency)
- [Arcade checkout API](https://docs.arcade.ai/api-reference/checkouts/create-a-checkout)

### Mention-triggered windows

Only explicit Telegram mentions of this bot trigger a new window; mentioning another
user, a plain reply, or editing an older message does not. Commands remain excluded
from message collection. Previous messages may appear in multiple windows, and recent
lore history still discourages repetition. Each window is fixed at mention time.
Mentions while analysis is pending or failed do not queue additional windows; failed
analysis requires an admin `/retry`. Previously saved work still resumes after restart.
The new `window_messages` table is created automatically on startup; existing data
and legacy window associations are preserved. Restart the bot to activate this change.
