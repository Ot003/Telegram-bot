"""Glue: turns Telegram updates into assistant turns, and fires reminders."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import html
import logging
from collections import OrderedDict, defaultdict
from datetime import datetime, timezone
from typing import Any

import anthropic

from .brain import Brain
from .config import Settings
from .storage import Storage
from .telegram import Telegram
from .tools import fmt_local, next_occurrence, user_tz
from .voice import Voice, VoiceUnavailable

log = logging.getLogger(__name__)

MAX_INLINE_FILE_BYTES = 20 * 1024 * 1024  # Telegram bots can download up to 20 MB
TEXT_MIME_PREFIXES = ("text/",)
TEXT_MIME_TYPES = {"application/json", "application/xml", "application/x-yaml", "application/csv"}
IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}

HELP = """Hi! I'm {name}, your assistant. Just talk to me — type or send a voice note.

I can:
• answer questions and look things up on the web
• keep your to-do list
• set reminders ("remind me to call mum at 6pm")
• remember things about you
• read photos, PDFs and text files
• reply with voice

Commands:
/tasks — your to-do list
/reminders — upcoming reminders
/memory — what I remember about you
/voice always|auto|off — when I reply with voice
/reset — start a fresh conversation (memories, tasks and reminders are kept)"""


class Bot:
    def __init__(self, settings: Settings, storage: Storage | None = None, telegram: Telegram | None = None,
                 brain: Brain | None = None, voice: Voice | None = None):
        self.settings = settings
        self.storage = storage or Storage(settings.db_path)
        self.tg = telegram or Telegram(settings.telegram_token)
        self.brain = brain or Brain(settings, self.storage)
        self.voice = voice or Voice(settings.tts_voice, settings.whisper_model)
        self._chat_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._seen_updates: OrderedDict[int, None] = OrderedDict()
        self._tasks: set[asyncio.Task] = set()

    # ---------- entry points ----------
    def submit(self, update: dict[str, Any]) -> None:
        """Handle an update in the background (so webhooks return immediately)."""
        uid = update.get("update_id")
        if uid is not None:
            if uid in self._seen_updates:
                return  # Telegram re-delivered it
            self._seen_updates[uid] = None
            while len(self._seen_updates) > 1000:
                self._seen_updates.popitem(last=False)
        task = asyncio.create_task(self._safe_handle(update))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _safe_handle(self, update: dict[str, Any]) -> None:
        try:
            await self.handle_update(update)
        except Exception:
            log.exception("Failed to handle update %s", update.get("update_id"))

    async def poll_forever(self) -> None:
        await self.tg.call("deleteWebhook")
        log.info("Polling Telegram for messages…")
        offset = None
        while True:
            try:
                updates = await self.tg.get_updates(offset)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("getUpdates failed: %s; retrying in 5s", e)
                await asyncio.sleep(5)
                continue
            for u in updates:
                offset = u["update_id"] + 1
                self.submit(u)

    # ---------- message handling ----------
    def allowed(self, user_id: int | None) -> bool:
        return not self.settings.allowed_user_ids or user_id in self.settings.allowed_user_ids

    async def handle_update(self, update: dict[str, Any]) -> None:
        msg = update.get("message")
        if not msg:
            return
        chat_id = msg["chat"]["id"]
        user = msg.get("from") or {}
        if not self.allowed(user.get("id")):
            await self.tg.send_text(
                chat_id,
                f"Sorry, this is a private assistant. (Your Telegram user id is <code>{user.get('id')}</code> — "
                "the owner can add it to ALLOWED_USER_IDS.)",
            )
            return

        text = (msg.get("text") or msg.get("caption") or "").strip()
        if text.startswith("/") and await self.handle_command(chat_id, text):
            return

        async with self._chat_locks[chat_id]:
            await self._converse(chat_id, msg, user, text)

    async def _converse(self, chat_id: int, msg: dict[str, Any], user: dict[str, Any], text: str) -> None:
        typing = asyncio.create_task(self._keep_typing(chat_id))
        try:
            via_voice = False
            attachments: list[dict[str, Any]] = []

            voice_obj = msg.get("voice") or msg.get("audio") or msg.get("video_note")
            if voice_obj:
                try:
                    audio = await self.tg.download_file(voice_obj["file_id"])
                    heard = await self.voice.transcribe(audio)
                except VoiceUnavailable as e:
                    log.warning("%s", e)
                    await self.tg.send_text(chat_id, "I can't listen to voice messages yet on this server — "
                                                     "could you type it instead?")
                    return
                if not heard:
                    await self.tg.send_text(chat_id, "Sorry, I couldn't make out anything in that voice message.")
                    return
                via_voice = True
                text = f"{text}\n{heard}".strip()

            attachments, note = await self._collect_attachments(msg)
            if note:
                text = f"{text}\n{note}".strip()
            if not text and not attachments:
                await self.tg.send_text(chat_id, "I can read text, voice notes, photos, PDFs and text files — "
                                                 "that type of message isn't something I can open yet.")
                return

            try:
                reply = await self.brain.respond(
                    chat_id, text, user_name=user.get("first_name"), attachments=attachments, via_voice=via_voice
                )
            except anthropic.RateLimitError:
                reply = "I'm a bit overloaded right now — try again in a minute."
            except anthropic.APIConnectionError:
                reply = "I couldn't reach my brain just now (network problem). Please try again."
            except anthropic.AuthenticationError:
                log.error("Anthropic API key is missing or invalid")
                reply = "I'm not set up correctly (my Anthropic API key is missing or invalid)."
            except anthropic.APIStatusError as e:
                log.error("Claude API error %s: %s", e.status_code, e.message)
                reply = "Something went wrong on my side. Please try again."
        finally:
            typing.cancel()

        await self.tg.send_text(chat_id, reply, reply_to=msg.get("message_id") if via_voice else None)
        await self._maybe_speak(chat_id, reply, via_voice)

    async def _collect_attachments(self, msg: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
        blocks: list[dict[str, Any]] = []
        notes: list[str] = []
        if msg.get("photo"):
            photo = max(msg["photo"], key=lambda p: p.get("file_size", 0))
            data = await self.tg.download_file(photo["file_id"])
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                       "data": base64.b64encode(data).decode()}})
        doc = msg.get("document")
        if doc:
            mime = doc.get("mime_type") or ""
            name = doc.get("file_name") or "file"
            if doc.get("file_size", 0) > MAX_INLINE_FILE_BYTES:
                notes.append(f"(The user sent a file '{name}' that is too large for me to open.)")
            elif mime == "application/pdf":
                data = await self.tg.download_file(doc["file_id"])
                blocks.append({"type": "document", "title": name,
                               "source": {"type": "base64", "media_type": "application/pdf",
                                          "data": base64.b64encode(data).decode()}})
            elif mime in IMAGE_MIME_TYPES:
                data = await self.tg.download_file(doc["file_id"])
                blocks.append({"type": "image", "source": {"type": "base64", "media_type": mime,
                                                           "data": base64.b64encode(data).decode()}})
            elif mime.startswith(TEXT_MIME_PREFIXES) or mime in TEXT_MIME_TYPES:
                data = await self.tg.download_file(doc["file_id"])
                body = data.decode("utf-8", errors="replace")
                blocks.append({"type": "text", "text": f"<file name=\"{name}\">\n{body}\n</file>"})
            else:
                notes.append(f"(The user sent a file '{name}' of type {mime or 'unknown'}, which I can't open.)")
        if msg.get("location"):
            loc = msg["location"]
            notes.append(f"(The user shared their location: {loc['latitude']}, {loc['longitude']}.)")
        return blocks, "\n".join(notes)

    async def _maybe_speak(self, chat_id: int, reply: str, via_voice: bool) -> None:
        mode = self.storage.get_setting(chat_id, "voice", "auto")
        if mode == "off" or (mode == "auto" and not via_voice):
            return
        try:
            await self.tg.send_typing(chat_id, "record_voice")
            audio, filename, mime = await self.voice.synthesize(reply)
            await self.tg.send_voice(chat_id, audio, filename, mime)
        except VoiceUnavailable as e:
            log.info("No voice reply: %s", e)
        except Exception:
            log.exception("Voice reply failed")

    async def _keep_typing(self, chat_id: int) -> None:
        with contextlib.suppress(asyncio.CancelledError):
            while True:
                await self.tg.send_typing(chat_id)
                await asyncio.sleep(4.5)

    # ---------- slash commands ----------
    async def handle_command(self, chat_id: int, text: str) -> bool:
        cmd, _, arg = text.partition(" ")
        cmd = cmd.split("@")[0].lower()
        arg = arg.strip().lower()
        tz = user_tz(self.storage, chat_id, self.settings.default_timezone)

        if cmd in ("/start", "/help"):
            await self.tg.send_text(chat_id, html.escape(HELP.format(name=self.settings.assistant_name)))
        elif cmd == "/reset":
            async with self._chat_locks[chat_id]:
                self.storage.clear_history(chat_id)
            await self.tg.send_text(chat_id, "Fresh start! I still remember your saved memories, tasks and reminders.")
        elif cmd == "/tasks":
            tasks = self.storage.list_tasks(chat_id)
            lines = [f"#{t['id']} {html.escape(t['title'])}" + (f" <i>(due {html.escape(t['due'])})</i>" if t["due"] else "")
                     for t in tasks]
            await self.tg.send_text(chat_id, "<b>To-do</b>\n" + "\n".join(lines) if lines else "Your to-do list is empty.")
        elif cmd == "/reminders":
            rows = self.storage.list_reminders(chat_id)
            lines = [f"#{r['id']} {fmt_local(datetime.fromisoformat(r['fire_at']), tz)} — {html.escape(r['text'])}"
                     + ("" if r["repeat"] == "none" else f" <i>({r['repeat']})</i>") for r in rows]
            await self.tg.send_text(chat_id, "<b>Reminders</b>\n" + "\n".join(lines) if lines else "No upcoming reminders.")
        elif cmd == "/memory":
            mems = self.storage.list_memories(chat_id)
            lines = [f"#{m['id']} {html.escape(m['fact'])}" for m in mems]
            await self.tg.send_text(chat_id, "<b>What I remember</b>\n" + "\n".join(lines) if lines
                                    else "I haven't saved anything about you yet.")
        elif cmd == "/voice":
            if arg not in ("always", "auto", "off"):
                current = self.storage.get_setting(chat_id, "voice", "auto")
                await self.tg.send_text(chat_id, f"Voice replies are <b>{current}</b>. Use /voice always, /voice auto "
                                                 "(only when you send voice) or /voice off.")
            else:
                self.storage.set_setting(chat_id, "voice", arg)
                await self.tg.send_text(chat_id, f"Voice replies: <b>{arg}</b>.")
        else:
            return False  # unknown command: let the assistant handle it as text
        return True

    # ---------- reminders ----------
    async def reminder_loop(self, interval: float = 15.0) -> None:
        while True:
            try:
                await self.fire_due_reminders()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Reminder loop error")
            await asyncio.sleep(interval)

    async def fire_due_reminders(self) -> int:
        now = datetime.now(timezone.utc)
        due = self.storage.due_reminders(now)
        for r in due:
            chat_id = r["chat_id"]
            tz = user_tz(self.storage, chat_id, self.settings.default_timezone)
            try:
                await self.tg.send_text(chat_id, f"⏰ <b>Reminder:</b> {html.escape(r['text'])}")
            except Exception:
                log.exception("Could not deliver reminder %s", r["id"])
                continue
            fire_at = datetime.fromisoformat(r["fire_at"])
            self.storage.reschedule_reminder(r["id"], next_occurrence(fire_at, r["repeat"], tz, now))
            self.storage.add_event(chat_id, f"you sent reminder #{r['id']} at {fmt_local(now, tz)}: {r['text']!r}")
            if self.storage.get_setting(chat_id, "voice", "auto") == "always":
                await self._maybe_speak(chat_id, f"Reminder: {r['text']}", True)
        return len(due)

    async def close(self) -> None:
        for t in list(self._tasks):
            t.cancel()
        await self.tg.close()
