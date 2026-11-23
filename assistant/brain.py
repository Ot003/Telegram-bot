"""The assistant's conversation loop with Claude (Messages API + tool use)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import anthropic

from .config import Settings
from .storage import Storage
from .tools import ALL_TOOLS, ToolRunner, fmt_local

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 12

# Models that accept server-side refusal fallbacks (`fallbacks: "default"`).
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-fable-5-2", "claude-sonnet-5-5"}

BETAS_BASE = ["compact-2026-01-12"]
FALLBACK_BETA = "server-side-fallback-2026-07-01"


def build_system_prompt(name: str) -> str:
    # Keep this text fixed for the life of the process: it is the start of the cached
    # prefix, and Claude's preserved thinking requires the prefix to stay unchanged.
    return f"""You are {name}, a personal assistant the user talks to through Telegram. You are warm, \
direct and practical, like a capable friend who gets things done.

How to talk:
- This is a chat. Keep replies short and conversational unless the user asks for depth or the task needs it.
- Lead with the answer or the result. Skip filler like "Great question!".
- Ask a brief clarifying question only when you genuinely can't proceed; otherwise make a sensible assumption and say what you assumed.
- When a message says it was sent as a voice message, the user is speaking to you and may hear your reply read aloud: answer in natural spoken sentences, avoid tables, code blocks, long lists and URLs.

Formatting (Telegram HTML): you may use <b>, <i>, <u>, <s>, <code>, <pre>, <a href="...">, <blockquote>. \
Do not use Markdown (no **, ##, or backtick fences). Escape a literal <, > or & as &lt; &gt; &amp;. \
Plain hyphen bullets are fine.

What you can do (use tools proactively, without asking permission for routine actions):
- Remember things: when the user tells you something lasting about themselves (name, preferences, people, \
plans, routines), save it with save_memory. Memories persist across conversations.
- To-do list: add_task, list_tasks, complete_task, delete_task.
- Reminders: set_reminder sends the user a message at a given time (one-off or repeating). Times you give \
are in the user's local time zone; if you don't know their time zone yet and it matters, ask once and call set_timezone.
- Look things up: web_search for current information (news, prices, opening hours, facts that may have changed) \
and web_fetch to read a specific page. Cite sources briefly with links when you use them.
- Voice: set_voice_replies changes whether you answer with voice messages.
- You can see images and read PDFs and text files the user sends.
- For anything else (writing, planning, explaining, brainstorming, maths, code, advice) just help directly.

After using tools, confirm what you did in one short line (e.g. "Done — I'll remind you at 9:00 tomorrow."). \
Never claim you did something unless the tool call succeeded. Each user message starts with a bracketed \
line giving the current local date and time and other context; use it, but don't repeat it back."""


def serialize_content(blocks: list[Any]) -> list[dict[str, Any]]:
    """Turn SDK content blocks into plain dicts we can store and send back unchanged."""
    out = []
    for b in blocks:
        if hasattr(b, "model_dump"):
            out.append(b.model_dump(mode="json", by_alias=True, exclude_unset=True))
        else:
            out.append(b)
    return out


def _is_history_binding_error(err: anthropic.BadRequestError) -> bool:
    msg = str(getattr(err, "message", err)).lower()
    return any(k in msg for k in ("prefix", "thinking", "block_binding", "signature"))


class Brain:
    def __init__(self, settings: Settings, storage: Storage, client: anthropic.AsyncAnthropic | None = None):
        self.settings = settings
        self.storage = storage
        self.client = client or anthropic.AsyncAnthropic()
        self.tools = ToolRunner(storage, settings.default_timezone)
        self.system = [{"type": "text", "text": build_system_prompt(settings.assistant_name)}]

    # ---------- building the user's turn ----------
    def context_header(self, chat_id: int, user_name: str | None, via_voice: bool, new_conversation: bool) -> str:
        tz = self.tools.tz(chat_id)
        now = datetime.now(timezone.utc)
        parts = [f"[{fmt_local(now, tz)}, time zone {tz.key}"]
        if via_voice:
            parts.append("sent as a voice message, transcribed")
        parts[-1] += "]"
        lines = [", ".join(parts)]
        if new_conversation:
            if user_name:
                lines.append(f"[Telegram name: {user_name}]")
            memories = self.storage.list_memories(chat_id)
            if memories:
                lines.append("[What you remember about the user:\n" +
                             "\n".join(f"#{m['id']}: {m['fact']}" for m in memories) + "]")
            else:
                lines.append("[No saved memories about this user yet.]")
        for event in self.storage.pop_events(chat_id):
            lines.append(f"[Since the last message: {event}]")
        return "\n".join(lines)

    # ---------- main entry point ----------
    async def respond(
        self,
        chat_id: int,
        text: str,
        *,
        user_name: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
        via_voice: bool = False,
    ) -> str:
        if self.storage.history_length(chat_id) >= self.settings.max_history_messages:
            log.info("chat %s: history cap reached, starting a fresh conversation", chat_id)
            self.storage.clear_history(chat_id)

        new_conv = self.storage.history_length(chat_id) == 0
        header = self.context_header(chat_id, user_name, via_voice, new_conv)
        content: list[dict[str, Any]] = [{"type": "text", "text": header}]
        content += attachments or []
        content.append({"type": "text", "text": text or "(no text)"})
        self.storage.append_message(chat_id, "user", content)

        try:
            return await self._run_loop(chat_id)
        except anthropic.BadRequestError as e:
            if not _is_history_binding_error(e):
                raise
            # The stored history no longer matches what Claude expects (e.g. it was edited).
            # Start over with just this message rather than leaving the chat stuck.
            log.warning("chat %s: history rejected (%s); starting fresh", chat_id, e)
            self.storage.clear_history(chat_id)
            header = self.context_header(chat_id, user_name, via_voice, True)
            self.storage.append_message(chat_id, "user", [{"type": "text", "text": header}] + content[1:])
            return await self._run_loop(chat_id)

    async def _create(self, messages: list[dict[str, Any]]):
        kwargs: dict[str, Any] = dict(
            model=self.settings.model,
            max_tokens=16000,
            system=self.system,
            tools=ALL_TOOLS,
            messages=messages,
            output_config={"effort": self.settings.effort},
            cache_control={"type": "ephemeral"},
            context_management={"edits": [{"type": "compact_20260112"}]},
            betas=list(BETAS_BASE),
        )
        if self.settings.model in FALLBACK_MODELS:
            kwargs["betas"].append(FALLBACK_BETA)
            kwargs["fallbacks"] = "default"
        return await self.client.beta.messages.create(**kwargs)

    async def _run_loop(self, chat_id: int) -> str:
        replies: list[str] = []
        for _ in range(MAX_TOOL_ROUNDS):
            messages = self.storage.load_history(chat_id)
            resp = await self._create(messages)
            content = serialize_content(resp.content)
            if not content:
                content = [{"type": "text", "text": "(no reply)"}]
            self.storage.append_message(chat_id, "assistant", content)
            replies += [b.text for b in resp.content if b.type == "text" and b.text.strip()]

            if resp.stop_reason == "pause_turn":
                continue  # server tool (web search) still working; send the turn back to resume
            if resp.stop_reason == "refusal":
                return "\n\n".join(replies) or "Sorry, I can't help with that one."
            if resp.stop_reason == "max_tokens":
                replies.append("(I ran out of room in that reply — ask me to continue.)")
                break
            if resp.stop_reason != "tool_use":
                break

            results = []
            for block in resp.content:
                if block.type != "tool_use":
                    continue
                output, is_error = self.tools.run(chat_id, block.name, block.input)
                log.info("chat %s: tool %s(%s) -> %s", chat_id, block.name, block.input, output[:200])
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": output, "is_error": is_error})
            self.storage.append_message(chat_id, "user", results)
        else:
            replies.append("(I stopped after too many steps — tell me if you want me to keep going.)")

        return "\n\n".join(replies).strip() or "Done."
