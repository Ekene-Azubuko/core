"""Environment-only application configuration."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv


def positive_int(name, default):
    """Read a positive integer setting, using its default for blank values."""
    value = int(os.getenv(name) or default)
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


@dataclass
class Config:
    telegram_token: str
    openai_key: str
    arcade_key: str
    database_url: str
    arcade_user_agent: str
    openai_model: str = "gpt-4o-mini"
    arcade_base_url: str = "https://platform.arcade.ai/v1"
    window_size: int = 100
    vote_seconds: int = 120
    poll_seconds: int = 5
    job_timeout: int = 600


def load_config():
    """Load local environment values and fail clearly on missing credentials."""
    load_dotenv()
    names = ["TELEGRAM_BOT_TOKEN", "OPENAI_API_KEY", "ARCADE_API_KEY",
             "DATABASE_URL", "ARCADE_USER_AGENT"]
    missing = [name for name in names if not os.getenv(name)]
    if missing:
        raise ValueError("Set required environment variables: " + ", ".join(missing))
    url = os.environ["DATABASE_URL"]
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            url = "postgresql+asyncpg://" + url[len(prefix):]
    if not url.startswith("postgresql+asyncpg://"):
        raise ValueError("DATABASE_URL must be a PostgreSQL URL")
    return Config(
        os.environ["TELEGRAM_BOT_TOKEN"], os.environ["OPENAI_API_KEY"],
        os.environ["ARCADE_API_KEY"], url, os.environ["ARCADE_USER_AGENT"],
        os.getenv("OPENAI_MODEL") or "gpt-4o-mini",
        os.getenv("ARCADE_BASE_URL") or "https://platform.arcade.ai/v1",
        positive_int("LORE_WINDOW_SIZE", 100),
        positive_int("VOTE_DURATION_SECONDS", 120),
        positive_int("ARCADE_POLL_SECONDS", 5),
        positive_int("ARCADE_JOB_TIMEOUT_SECONDS", 600),
    )
