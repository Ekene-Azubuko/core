# Core companion app: teammate implementation guide

## Goal

Build a separate app where users can sign up, connect their Telegram identity,
and browse the products Core has generated for their groups over time.

The Telegram bot remains responsible for collecting messages, extracting lore,
running votes, generating products and creating Arcade checkouts. The companion
app provides accounts and a group product gallery.

When Core sends a buyer their Arcade checkout link, it should also offer a link
to that group's gallery. Adding this link is a small future change owned by the
bot maintainer, not the app team.

**This document is a build contract, not an implemented integration.** The current
repository has no website, signup system, web API, Telegram identity-linking flow
or app-link button. Teammates can build the app independently using sample data
until the live integration is connected.

## Ownership: keep changes separate

| Location | Owner | Rule |
| --- | --- | --- |
| `app/**` | Bot maintainer | Read for context; do not edit, move, rename or import into the companion app |
| `tests/**` | Bot maintainer | Do not modify existing bot tests |
| Root `requirements.txt`, `env.example`, `.gitignore` | Bot maintainer | Request changes; do not edit for the companion app |
| Root `README.md`, `summary.md`, `AGENTS.md` | Bot maintainer | Keep app documentation in its own folder |
| `core-app.md` | Shared handoff contract | Coordinate changes rather than editing concurrently |
| `companion/**` | App team | All new app source, dependencies, configuration, assets and documentation |

Use `companion/` even if your framework normally names its source directory `app/`.
For example, `companion/src/app/` is fine; root `app/` is reserved for the bot.

Suggested layout; adapt the internal folders to your chosen stack:

```text
core/
├── app/                       # Existing bot — do not touch
├── tests/                     # Existing bot tests — do not touch
├── core-app.md                # This handoff
└── companion/                 # App team's entire implementation
    ├── README.md              # App setup, architecture and run commands
    ├── summary.md             # What the companion app currently implements
    ├── .gitignore             # App secrets, dependencies and build output
    ├── env.example            # App configuration placeholders only
    ├── src/                   # UI and server routes
    ├── db/                    # App-owned schema/migrations only
    └── fixtures/              # Clearly fictional gallery data for development
```

The companion app must have its own startup command and dependency manifest.
Starting or deploying it must not start the Telegram bot or run bot migrations.
Do not turn the repository into a shared workspace or reorganize it to accommodate
the new framework. One simple full-stack app is sufficient.

This feature is explicitly authorized despite the original MVP's exclusion of a
frontend and profiles. The existing guidance to keep implementation simple still
applies. No agent framework, queue, vector database or analytics system is needed.

## Git workflow

1. Use a separate clone or Git worktree and an app feature branch. Separate
   branches in the same working directory do not isolate simultaneous file edits.
2. Keep the app PR scoped to `companion/**`. Coordinate any change to this document.
3. Before opening a PR, inspect `git diff --name-only <base-branch>...HEAD` and
   confirm no bot-owned files changed. Replace the placeholder with the actual base.
4. Ask the bot maintainer for integration changes using a short handoff note in
   `companion/README.md` or the PR description. Do not implement them under `app/`.
5. Do not commit `.env`, credentials, database dumps or real chat data. Add ignore
   rules inside `companion/.gitignore` before generating local secrets/build files.

File ownership prevents routine conflicts; it does not guarantee zero conflicts.
Agree on the public gallery URL and data contract before connecting live data.

## User experience to build

1. A user receives a Core product in Telegram and opens its private purchase flow.
2. Alongside their Arcade checkout link, they see **View your group's creations**.
3. The app opens a route such as `/groups/<group-id>`.
4. If signed out, the user signs up or signs in. Preserve the destination through
   login using an internal path, not an arbitrary external redirect URL.
5. The user connects a verified Telegram identity if not already linked.
6. The server checks that this Telegram identity currently belongs to the group.
7. The user sees that group's generated products, newest first, and can save the
   group to **My groups** for later visits.

Minimum screens:

- Sign up, sign in and sign out.
- Connect Telegram identity, with a clear failure/retry state.
- My groups: groups the user has added and can still access.
- Group gallery: product image, lore title, generation date, category and price.
- Product detail, either a page or a simple expanded card.
- Empty gallery, unavailable product/image, deleted group and access-denied states.

Showing a group in My groups means it is bookmarked, not that the user owns it.
For the first version, users add groups by following Core's group gallery link.
Do not promise automatic discovery of every Telegram group a person belongs to.

Keep purchase buttons pointed at Core's existing private bot purchase flow:

```text
https://t.me/<bot-username>?start=buy_<proposal-id>
```

The bot already verifies membership and creates a separate checkout for the buyer.
The app must not store or share another person's Arcade checkout URL, generate
products, rerun votes or create a second checkout integration for this MVP.

## Proposed integration: server-side read-only product access

Use the app's own backend to read the minimum bot data needed for the gallery,
with a dedicated read-only database credential provisioned by the bot maintainer.
The browser never receives database credentials or queries the bot database directly.

Keep two distinct data responsibilities:

- **Bot data:** existing PostgreSQL records; bot-owned, read-only to the app.
- **App data:** accounts, linked Telegram identity and saved groups; app-owned in
  a separate database or separately permissioned schema.

App migrations must never target bot tables or run against the bot's default schema.
Do not add columns, foreign keys, triggers or tables to the bot-owned schema.
Do not reuse the bot's database-owner credential. The maintainer should grant only
the required columns, or expose narrow read-only views with the same information.

Implement a small server-side function in `companion/` to fetch gallery data.
Initially return fixtures in exactly the agreed response shape; later replace the
function's reads with the provisioned database access. No shared Python imports or
runtime dependency on `app/` is needed.

### Existing records and relationships

These names reflect the current `app/db/models.py`; they are not a promise that
teammates may modify that schema.

| Table | Gallery fields | Relationship / caveat |
| --- | --- | --- |
| `groups` | `chat_id`, `status`, `created_at` | No stored group title, photo or membership list |
| `lore_proposals` | `id`, `chat_id`, `status`, `lore` | Associates lore and its generated product with a group |
| `generated_products` | `proposal_id`, `arcade_product_id`, `category`, `price`, `image_url`, `created_at` | `proposal_id` references `lore_proposals.id`; there is no direct `chat_id` here |

For one authorized group, fetch its `generated` proposals, then their product rows
using the proposal IDs. Combine them in straightforward application code. Paginate
the results rather than loading all groups or all historical products at once.

Only show an item when a generated proposal and its product record both exist.
Do not treat rejected, expired, voting, generating or failed proposals as products.
For ordering, use the product's creation time, not the earlier proposal time.

Do not read or expose `messages`, `analysis_windows`, `votes`, generation request
payloads, error internals or group deletion tokens. A gallery does not need private
conversation history or voting identities. Select explicit fields, not entire rows.

### Proposed gallery response

The following is a suggested app-owned response, **not an existing bot endpoint**.
All values below are fictional examples, not real Arcade products or prices.

```json
{
  "group": {
    "id": "-1001234567890",
    "display_name": "Saved group"
  },
  "products": [
    {
      "proposal_id": "example-proposal-id",
      "arcade_product_id": "example-product-id",
      "lore_title": "We're so back",
      "lore_explanation": "The group adopted this phrase as its motto.",
      "category_name": "Example category",
      "image_url": null,
      "price": { "amount": "48.00", "currency": "USD" },
      "created_at": "2026-10-11T18:00:00Z",
      "purchase_url": "https://t.me/EXAMPLE_BOT?start=buy_example-proposal-id"
    }
  ],
  "next_cursor": null
}
```

Mapping: `lore_title` and `lore_explanation` come from the proposal's `lore` JSON;
`category_name` comes from product `category.name`; `price` uses the saved
`amount`/`currency` object. Keep Telegram IDs as strings in URLs and JSON. Keep
price amounts as decimal strings, with their currency, rather than float arithmetic.

`display_name` is app-owned: use a user-set label or a neutral fallback until a
verified title source is connected. Never imply a group title exists in bot storage.

The stored price is a historical quote, not a current checkout total. Label it as
the price when generated; shipping, service charges and tax may differ at checkout.
Stored Arcade image URLs can expire. Render a proper unavailable-image state.
Fresh image/price retrieval is a separate maintainer-provided capability to agree
later; do not request broad Arcade credentials or invent an undocumented refresh API.

## Accounts and group authorization

An app account is not proof of Telegram group membership. Neither a URL containing
a chat ID, a forwarded bot link, a matching display name nor a manually entered
Telegram user ID grants access.

The app team owns signup, sessions, account recovery and Telegram account linking.
Use a supported authentication flow and verify identity on the server. Before
implementing Telegram or an auth provider integration, consult its current official
documentation. Do not invent login payloads, signatures or membership responses.

For live group access, the app team and bot maintainer must agree on a server-side
membership verification capability. Its conceptual contract is:

```text
Input: authenticated app user with verified Telegram identity + requested group ID
Output: allow or deny based on current group membership
```

This capability is **not currently exposed by the bot**. The bot maintainer owns
the choice of supplying a narrow membership-verification service or explicitly
provisioning server-only Telegram access to the companion backend. Do not assume
access to the bot token. Never start another Telegram poller or change its webhook.

Until verification is connected, use fictional groups in development and keep
real group galleries inaccessible. Read-only database credentials alone do not
authorize any user to see any group's products.

On every protected gallery/product request, verify the app session, linked identity
and current membership. Derive the product's group server-side. Deny access if
verification fails or is unavailable; do not trust an old saved-group record.
Serve private responses without public/shared caching and do not expose group
content in unauthenticated page metadata or previews.

Suggested app-only records, kept minimal:

- Account record from the chosen authentication system.
- Verified Telegram identity linked uniquely to one app account.
- Saved group: app account ID, Telegram chat ID, optional display label, saved time.

Do not build a parallel copy of Telegram messages or a permanent duplicate product
archive. Query the source records so bot deletion removes gallery availability.
If a group or product no longer exists, remove/hide its saved reference and show an
unavailable state. Paused monitoring is not deletion: existing products can remain
visible to verified members while the group is paused.

## Bot maintainer's integration handoff

The app team supplies the deployed base URL and a stable route such as
`/groups/<group-id>`. The bot maintainer then makes the small bot-owned changes:

1. Provision read-only gallery data access and the agreed membership verification.
2. Add an optional companion base URL to bot configuration and its root env example.
3. Add **View your group's creations** alongside **Open your checkout** in the
   private checkout response. An additional gallery button on group product posts
   is optional and should be coordinated separately.
4. Preserve the existing checkout button, membership check and generation workflow.
5. Leave bot behavior unchanged when the companion URL is unset. App availability
   must not be a prerequisite for product generation or checkout.
6. Update bot documentation after the actual integration is implemented.

Do not include checkout URLs, credentials or authentication tokens in the gallery
link. A group ID selects a destination; the app still requires authorization.

## App configuration and independent development

Document app-only variables in `companion/env.example`, with placeholders only.
Exact names can follow your framework; likely needs include the public app URL,
app database URL, auth/session secrets, public bot username and server-only read-only
Core database URL. Add membership-verification settings only after agreeing on
the real integration. Do not copy the bot's `.env` into the app.

Build in this order:

1. Create the isolated folder, startup command, ignore rules and app README.
2. Build signup/signin and gallery screens using fictional fixtures.
3. Implement app-owned saved groups and verified Telegram identity linking.
4. Connect live membership verification and restricted data access together.
5. Hand the gallery URL to the bot maintainer for the small link integration.
6. Manually verify the complete Telegram → login → authorized gallery → bot purchase flow.

## Acceptance checklist

- App PR changes only app-owned files and runs independently of the bot.
- Users can sign up, sign in, sign out and return to their original gallery link.
- A new account cannot view real group products until identity and membership verify.
- Changing the group/product ID or forwarding a link cannot expose another group's data.
- My groups shows only saved groups the user may currently access.
- Gallery shows actual generated products newest first, with pagination and empty states.
- Historical prices are labeled; missing/expired images have a fallback.
- Purchase returns to the existing bot flow rather than sharing a checkout.
- Bot `/delete_data` makes the corresponding live gallery unavailable.
- Paused groups retain historical galleries for authorized members.
- Secrets stay server-side and outside Git; development fixtures contain no real chats.
- The bot still works when the companion app is stopped or not configured.
- `companion/README.md` and `companion/summary.md` distinguish implemented work,
  fixture-only behavior and pending integration.
