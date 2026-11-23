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
    # On Render, RENDER_EXTERNAL_URL is set automatically, so nothing needs typing.
    webhook_url: str = field(default_factory=lambda: (
        os.environ.get("WEBHOOK_URL") or os.environ.get("RENDER_EXTERNAL_URL", "")).rstrip("/"))
    webhook_secret: str = field(default_factory=lambda: os.environ.get("WEBHOOK_SECRET", ""))
    # "webhook" (Telegram pushes updates to /webhook) or "polling" (bot pulls updates; no public URL needed)
    mode: str = field(default_factory=lambda: os.environ.get(
        "MODE", "webhook" if os.environ.get("RENDER_EXTERNAL_URL") else "polling").lower())
    # Free hosts (e.g. Render) put the app to sleep after ~15 idle minutes, which would stop
    # reminders. When set, the app pings its own public URL every 10 minutes to stay awake.
    keep_awake: bool = field(default_factory=lambda: os.environ.get(
        "KEEP_AWAKE", "1" if os.environ.get("RENDER_EXTERNAL_URL") else "0") == "1")
    allowed_user_ids: set[int] = field(
        default_factory=lambda: _int_set(os.environ.get("ALLOWED_USER_IDS", ""))
    )
    # Free key from https://aistudio.google.com/apikey (no credit card needed)
    gemini_api_key: str = field(
        default_factory=lambda: os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")
    )
    # "-latest" alias always points at Google's current Flash-Lite model, which has the
    # most generous free daily quota. Use gemini-flash-latest for a smarter, lower-quota model.
    model: str = field(default_factory=lambda: os.environ.get("GEMINI_MODEL", "gemini-flash-lite-latest"))
    assistant_name: str = field(default_factory=lambda: os.environ.get("ASSISTANT_NAME", "Ava"))
    default_timezone: str = field(default_factory=lambda: os.environ.get("DEFAULT_TIMEZONE", "UTC"))
    db_path: str = field(default_factory=lambda: os.environ.get("DB_PATH", "assistant.db"))
    tts_voice: str = field(default_factory=lambda: os.environ.get("TTS_VOICE", "en-US-AriaNeural"))
    # How many recent messages Gemini sees each turn (older ones stay in the database).
    context_messages: int = field(default_factory=lambda: int(os.environ.get("CONTEXT_MESSAGES", "40")))
    # Start a fresh conversation after this many stored messages (long-term memories survive).
    max_history_messages: int = field(
        default_factory=lambda: int(os.environ.get("MAX_HISTORY_MESSAGES", "400"))
    )


settings = Settings()
