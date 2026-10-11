# Core Agent Instructions

Core is a hackathon MVP. Optimize for simplicity, readability, and getting the end-to-end pipeline working.

Do not overengineer this project.

## General coding philosophy

Always choose the simplest implementation that works.

Prefer:

* straightforward Python
* small functions
* obvious control flow
* readable variable names
* direct API calls
* simple database operations
* minimal dependencies

Avoid abstractions unless they clearly make the code easier to understand.

A new developer should be able to open a file and understand what it does without tracing through several layers of abstractions.

## Code documentation

Always write a brief docstring for every function.

Docstrings should explain what the function does in one or two short sentences.

Example:

```python
async def save_message(message):
    """Save an incoming Telegram message to the database."""
```

For functions with behavior that is not obvious from the name, briefly explain the important behavior:

```python
async def check_message_threshold(chat_id):
    """Check whether a group has enough new messages to trigger lore analysis."""
```

Keep docstrings concise. Do not write large documentation blocks for simple functions.

Add comments when code would otherwise be unclear.

Comments should explain:

* why a non-obvious decision is being made
* unusual API behavior
* important state transitions
* edge-case handling
* logic that would take effort to infer from the code

For example:

```python
# "No" wins ties so Core never generates a product
# unless the group clearly chooses to make one.
if no_votes == max_votes:
    return "no"
```

Do not add comments that simply restate obvious code.

Bad:

```python
# Increment count by one
count += 1
```

Good:

```python
# Only count messages that have not already been included
# in a previous lore-analysis window.
count = get_unanalyzed_message_count(chat_id)
```

The goal is readable code, not heavily annotated code.

## Keep the code simple

Do not introduce:

* design patterns for their own sake
* dependency injection frameworks
* repository patterns
* factory patterns
* complicated inheritance
* generic base classes
* unnecessary interfaces
* event buses
* plugin systems
* microservices
* Redis
* Celery
* Kafka
* complex background job infrastructure
* agent frameworks
* vector databases
* unnecessary caching

If something can be implemented clearly with a function, use a function.

If something can be implemented clearly in one file, do not split it across several files just to create architectural layers.

## No type checking

Do not add static type checking.

Do not introduce:

* mypy
* pyright
* strict typing configurations
* complicated Python type annotations
* generic types
* protocols

Simple type hints are acceptable only when they make a function easier to understand, but they are not required.

Do not spend development time resolving type-checker issues.

## Do not write tests

This is a hackathon MVP.

Do not create or maintain:

* unit tests
* integration tests
* test fixtures
* mock frameworks
* test databases
* pytest configuration
* test coverage tooling

Do not spend time writing automated tests unless explicitly instructed to do so.

Instead, manually verify the actual pipeline while developing.

Prioritize testing the real user flow:

Telegram message
→ message storage
→ message threshold reached
→ GPT lore extraction
→ Telegram vote
→ vote result
→ Arcade generation
→ product returned
→ checkout

## Keep SQL simple

Do not write complex SQL queries.

Prefer simple operations such as:

* insert one record
* fetch records for a chat
* fetch recent messages
* count unanalyzed messages
* update a status
* fetch votes for a proposal
* delete records belonging to a chat

Avoid:

* complicated joins
* nested queries
* CTEs
* window functions
* database triggers
* stored procedures
* complex aggregations
* clever SQL optimizations

If data can be fetched with a simple query and processed clearly in Python, prefer that approach.

The expected scale is hackathon/demo scale.

Optimize for understandable code, not database performance.

## External APIs

Core integrates with:

* Telegram Bot API
* OpenAI API
* Arcade API

Keep each external integration easy to locate and understand.

Do not scatter API calls throughout unrelated files.

Before implementing or changing an external API integration, consult the current official documentation rather than guessing endpoint behavior.

Never invent API response fields.

Handle failures simply and clearly.

## Environment variables

All secrets and environment-specific configuration must come from environment variables.

Use `env.example` to document required variables.

`env.example` must contain placeholders only.

Never commit:

* API keys
* Telegram bot tokens
* database credentials
* secrets

The real `.env` must remain gitignored.

## Preserve the Core product flow

Do not change the fundamental product behavior unless explicitly instructed.

The pipeline is:

Telegram group messages
→ collect X messages
→ send message window to GPT
→ GPT identifies strongest group lore
→ retrieve valid Arcade product categories
→ present product options + No to Telegram group
→ group votes
→ majority determines result
→ if No wins, stop
→ if product category wins, call Arcade
→ generate physical product
→ retrieve product image and real price
→ post product back to Telegram
→ provide checkout

There is no numeric lore-scoring system.

GPT identifies the lore.

Humans decide whether the lore should become a product.

Arcade should not generate a product before the group votes for a product category.

## Avoid unnecessary features

Do not add features that are not required for the current pipeline.

In particular, do not introduce:

* embeddings
* semantic search
* lore scoring
* vector storage
* recommendation engines
* dashboards
* separate frontend applications
* user profiles
* analytics systems
* elaborate authentication
* long-term personalization
* complicated group-member modeling

Finish the core pipeline first.

## Work incrementally

Make small changes.

After implementing a meaningful step, manually verify that the existing pipeline still works.

Do not rewrite working parts of the project simply to make them more architecturally sophisticated.

When fixing a bug, make the smallest reasonable change that fixes it.

## summary.md

`summary.md` must always describe how the current application works.

Treat `summary.md` as required project documentation and the fastest way for another developer or coding agent to understand the repository.

Read `summary.md` before making substantial changes.

After every meaningful change to the application, update `summary.md`.

Do not leave `summary.md` describing behavior that no longer matches the code.

At minimum, `summary.md` should explain:

### Current pipeline

Describe the current end-to-end flow from:

Telegram message
→ storage
→ lore detection
→ voting
→ Arcade
→ checkout

Only describe functionality that actually exists.

Clearly distinguish between:

* implemented
* partially implemented
* not yet implemented

### Important files

List the important files and briefly explain what each one does.

Example:

`app/bot/handlers.py`
Receives Telegram messages and commands.

`app/services/lore.py`
Sends conversation windows to OpenAI and extracts lore.

`app/services/arcade.py`
Handles Arcade categories, generation, product retrieval, and checkout.

Keep this section synchronized with the actual repository structure.

### Database

Explain:

* what is stored
* the important tables/models
* important statuses
* how messages are associated with Telegram groups

Keep this explanation simple.

### External services

Document how the application currently uses:

* Telegram
* OpenAI
* Arcade
* PostgreSQL

Include which parts of the pipeline depend on each service.

Do not put secrets or API keys in `summary.md`.

### Environment variables

List the environment variable names currently required by the application and explain what each controls.

Do not include actual secret values.

### Current state

Maintain a short section containing:

**Working**

* functionality that currently works end to end

**In progress**

* functionality currently being built

**Not implemented**

* planned functionality that does not exist yet

**Known issues**

* bugs, limitations, hacks, or assumptions another developer should know about

Keep this accurate.

## When finishing a task

Before considering a development task complete:

1. Make sure the code is as simple as reasonably possible.
2. Make sure every function has a brief docstring.
3. Add comments to any code whose purpose or reasoning would otherwise be unclear.
4. Remove unnecessary or redundant comments.
5. Remove unnecessary complexity introduced during implementation.
6. Manually verify the affected flow where practical.
7. Check that no secrets were committed.
8. Update `env.example` if configuration changed.
9. Update `summary.md` to reflect the current pipeline.
10. Make sure `summary.md` does not claim unfinished functionality is working.

The goal is not production-grade architecture.

The goal is a small, understandable, working implementation of Core's complete Telegram → lore → vote → Arcade → checkout experience.
