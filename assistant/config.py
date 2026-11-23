"""Configuration, read from environment variables (or a local .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader so the bot runs without extra dependencies."""
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _int_set(value: str) -> set[int]:
    return {int(v) for v in value.replace(" ", "").split(",") if v}


@dataclass(frozen=True)
class Settings:
    telegram_token: str = field(default_factory=lambda: os.environ.get("TELEGRAM_BOT_TOKEN", ""))
    # Public HTTPS base URL of this server, e.g. https://my-bot.onrender.com
    # When set (and MODE=webhook), the bot registers <WEBHOOK_URL>/webhook with Telegram.
    webhook_url: str = field(default_factory=lambda: os.environ.get("WEBHOOK_URL", "").rstrip("/"))
    webhook_secret: str = field(default_factory=lambda: os.environ.get("WEBHOOK_SECRET", ""))
    # "webhook" (Telegram pushes updates to /webhook) or "polling" (bot pulls updates; no public URL needed)
    mode: str = field(default_factory=lambda: os.environ.get("MODE", "polling").lower())
    allowed_user_ids: set[int] = field(
        default_factory=lambda: _int_set(os.environ.get("ALLOWED_USER_IDS", ""))
    )
    model: str = field(default_factory=lambda: os.environ.get("CLAUDE_MODEL", "claude-opus-5-5"))
    effort: str = field(default_factory=lambda: os.environ.get("CLAUDE_EFFORT", "medium"))
    assistant_name: str = field(default_factory=lambda: os.environ.get("ASSISTANT_NAME", "Ava"))
    default_timezone: str = field(default_factory=lambda: os.environ.get("DEFAULT_TIMEZONE", "UTC"))
    db_path: str = field(default_factory=lambda: os.environ.get("DB_PATH", "assistant.db"))
    tts_voice: str = field(default_factory=lambda: os.environ.get("TTS_VOICE", "en-US-AriaNeural"))
    whisper_model: str = field(default_factory=lambda: os.environ.get("WHISPER_MODEL", "base"))
    # Start a fresh conversation after this many stored messages (long-term memories survive).
    max_history_messages: int = field(
        default_factory=lambda: int(os.environ.get("MAX_HISTORY_MESSAGES", "400"))
    )


settings = Settings()
