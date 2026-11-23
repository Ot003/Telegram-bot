"""Thin async client for the Telegram Bot API."""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

log = logging.getLogger(__name__)

MAX_MESSAGE_LEN = 4000  # Telegram's hard limit is 4096


def split_message(text: str, limit: int = MAX_MESSAGE_LEN) -> list[str]:
    """Split long text on paragraph/line boundaries so each piece fits in one message."""
    chunks: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        chunks.append(text)
    return chunks


def strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("&quot;", '"')


class TelegramError(RuntimeError):
    pass


class Telegram:
    def __init__(self, token: str):
        self.base = f"https://api.telegram.org/bot{token}"
        self.file_base = f"https://api.telegram.org/file/bot{token}"
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0))

    async def close(self) -> None:
        await self.http.aclose()

    async def call(self, method: str, timeout: float | None = None, files: dict | None = None, **params: Any) -> Any:
        kwargs: dict[str, Any] = {}
        if timeout is not None:
            kwargs["timeout"] = timeout
        if files:
            resp = await self.http.post(f"{self.base}/{method}", data=params, files=files, **kwargs)
        else:
            resp = await self.http.post(f"{self.base}/{method}", json=params, **kwargs)
        data = resp.json()
        if not data.get("ok"):
            raise TelegramError(f"{method} failed: {data.get('description')}")
        return data["result"]

    async def send_text(self, chat_id: int, text: str, reply_to: int | None = None) -> None:
        """Send text as Telegram HTML; fall back to plain text if the markup is rejected."""
        for i, chunk in enumerate(split_message(text or "…")):
            params: dict[str, Any] = {"chat_id": chat_id, "text": chunk, "parse_mode": "HTML",
                                      "link_preview_options": {"is_disabled": True}}
            if reply_to and i == 0:
                params["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
            try:
                await self.call("sendMessage", **params)
            except TelegramError as e:
                log.info("HTML send failed (%s); retrying as plain text", e)
                params.pop("parse_mode")
                params["text"] = strip_html(chunk)
                await self.call("sendMessage", **params)

    async def send_typing(self, chat_id: int, action: str = "typing") -> None:
        try:
            await self.call("sendChatAction", chat_id=chat_id, action=action)
        except Exception:  # cosmetic only
            pass

    async def send_voice(self, chat_id: int, audio: bytes, filename: str, mime: str) -> None:
        method, field = ("sendVoice", "voice") if mime == "audio/ogg" else ("sendAudio", "audio")
        await self.call(method, files={field: (filename, audio, mime)}, chat_id=str(chat_id))

    async def download_file(self, file_id: str) -> bytes:
        info = await self.call("getFile", file_id=file_id)
        resp = await self.http.get(f"{self.file_base}/{info['file_path']}")
        resp.raise_for_status()
        return resp.content

    async def get_updates(self, offset: int | None, timeout: int = 50) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"timeout": timeout, "allowed_updates": ["message"]}
        if offset is not None:
            params["offset"] = offset
        return await self.call("getUpdates", timeout=timeout + 15, **params)
