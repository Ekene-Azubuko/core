"""Run one Telegram polling process and its lightweight background worker."""

import logging

from openai import AsyncOpenAI
from sqlalchemy import text
from telegram import Update
from telegram.ext import Application, CallbackQueryHandler, ChatMemberHandler, CommandHandler, MessageHandler, filters

from app.bot import handlers
from app.config import load_config
from app.db.session import connect_database, create_tables
from app.services.arcade import Arcade
from app.services.pipeline import Pipeline


async def startup(application):
    """Initialize PostgreSQL and integrations, refusing a second worker on this database."""
    config = application.bot_data["config"]
    engine, sessions = connect_database(config.database_url)
    application.bot_data["engine"] = engine
    connection = await engine.connect()
    # A session advisory lock lasts until this dedicated connection closes.
    acquired = await connection.scalar(text("SELECT pg_try_advisory_lock(716031482)"))
    if not acquired:
        await connection.close()
        raise RuntimeError("Another Core process is using this database; run only one worker")
    application.bot_data["worker_lock"] = connection
    await create_tables(engine)
    openai = AsyncOpenAI(api_key=config.openai_key, timeout=60, max_retries=2)
    arcade = Arcade(config)
    core = Pipeline(config, sessions, application.bot, openai, arcade)
    application.bot_data["pipeline"] = core
    await core.start()


async def stop(application):
    """Stop background work before Telegram and HTTP clients shut down."""
    core = application.bot_data.get("pipeline")
    if core:
        await core.stop()


async def shutdown(application):
    """Close external clients, release the worker lock and dispose database connections."""
    core = application.bot_data.get("pipeline")
    if core:
        await core.openai.close()
        await core.arcade.close()
    connection = application.bot_data.get("worker_lock")
    if connection:
        await connection.close()
    engine = application.bot_data.get("engine")
    if engine:
        await engine.dispose()


def build_application(config):
    """Register handlers with sequential updates and independent background API work."""
    app = (Application.builder().token(config.telegram_token)
           .post_init(startup).post_stop(stop).post_shutdown(shutdown).build())
    app.bot_data["config"] = config
    for name in ("start", "resume", "pause", "status", "delete_data", "retry"):
        app.add_handler(CommandHandler(name, getattr(handlers, name)))
    app.add_handler(CallbackQueryHandler(handlers.callback))
    app.add_handler(ChatMemberHandler(handlers.membership, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & filters.TEXT & ~filters.COMMAND, handlers.message))
    app.add_error_handler(handlers.error_handler)
    return app


def main():
    """Load environment configuration and run Telegram long polling until interrupted."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # HTTP request logs can include the Telegram token in the URL.
    for name in ("httpx", "httpcore", "openai", "telegram"):
        logging.getLogger(name).setLevel(logging.WARNING)
    build_application(load_config()).run_polling(
        allowed_updates=[Update.MESSAGE, Update.CALLBACK_QUERY, Update.MY_CHAT_MEMBER],
        drop_pending_updates=False,
    )


if __name__ == "__main__":
    main()
