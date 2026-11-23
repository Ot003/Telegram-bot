"""The assistant's conversation loop, using Google Gemini (free tier) with function calling."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from google import genai
from google.genai import types

from .config import Settings
from .storage import Storage
from .tools import ALL_TOOLS, ToolRunner, fmt_local

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 8  # each round is one request against the free daily quota


def build_system_prompt(name: str) -> str:
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
- Look things up: web_search for current information and read_webpage for a specific link. Mention sources briefly.
- Voice: set_voice_replies changes whether you answer with voice messages.
- You can see images and read PDFs and text files the user sends.
- For anything else (writing, planning, explaining, brainstorming, maths, code, advice) just help directly.

After using tools, confirm what you did in one short line (e.g. "Done — I'll remind you at 9:00 tomorrow."). \
Never claim you did something unless the tool call succeeded. Each user message starts with a bracketed \
line giving the current local date and time and other context; use it, but don't repeat it back."""


def to_dict(content: types.Content) -> dict[str, Any]:
    # JSON mode base64-encodes bytes (images, thought signatures) so they survive storage.
    return json.loads(content.model_dump_json(exclude_none=True))


def from_dict(data: dict[str, Any]) -> types.Content:
    return types.Content.model_validate_json(json.dumps(data))


def _starts_user_turn(c: dict[str, Any]) -> bool:
    return c.get("role") == "user" and not any("function_response" in p for p in c.get("parts", []))


def trim_history(history: list[dict[str, Any]], max_items: int) -> list[dict[str, Any]]:
    """Keep the most recent messages, starting on a real user message (not a tool result)."""
    if len(history) <= max_items:
        return history
    tail = history[-max_items:]
    for i, c in enumerate(tail):
        if _starts_user_turn(c):
            return tail[i:]
    return tail[-1:]


class Brain:
    def __init__(self, settings: Settings, storage: Storage, client: genai.Client | None = None):
        self.settings = settings
        self.storage = storage
        self.client = client or genai.Client(api_key=settings.gemini_api_key or None)
        self.tools = ToolRunner(storage, settings.default_timezone)
        self.config = types.GenerateContentConfig(
            system_instruction=build_system_prompt(settings.assistant_name),
            tools=[types.Tool(function_declarations=[
                types.FunctionDeclaration(name=t["name"], description=t["description"],
                                          parameters_json_schema=t["input_schema"])
                for t in ALL_TOOLS
            ])],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

    # ---------- building the user's turn ----------
    def context_header(self, chat_id: int, user_name: str | None, via_voice: bool, new_conversation: bool) -> str:
        tz = self.tools.tz(chat_id)
        now = datetime.now(timezone.utc)
        first = f"[{fmt_local(now, tz)}, time zone {tz.key}"
        if via_voice:
            first += ", sent as a voice message, transcribed"
        lines = [first + "]"]
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

    async def respond(
        self,
        chat_id: int,
        text: str,
        *,
        user_name: str | None = None,
        attachments: list[types.Part] | None = None,
        via_voice: bool = False,
    ) -> str:
        if self.storage.history_length(chat_id) >= self.settings.max_history_messages:
            self.storage.clear_history(chat_id)
        new_conv = self.storage.history_length(chat_id) == 0
        header = self.context_header(chat_id, user_name, via_voice, new_conv)
        parts = [types.Part(text=header), *(attachments or []), types.Part(text=text or "(no text)")]
        self.storage.append_message(chat_id, "user", to_dict(types.Content(role="user", parts=parts)))
        return await self._run_loop(chat_id)

    async def _run_loop(self, chat_id: int) -> str:
        replies: list[str] = []
        for _ in range(MAX_TOOL_ROUNDS):
            history = trim_history([m["content"] for m in self.storage.load_history(chat_id)],
                                   self.settings.context_messages)
            resp = await self.client.aio.models.generate_content(
                model=self.settings.model, contents=[from_dict(c) for c in history], config=self.config,
            )
            candidate = resp.candidates[0] if resp.candidates else None
            if candidate is None or candidate.content is None or not candidate.content.parts:
                reason = getattr(candidate, "finish_reason", None) or getattr(resp, "prompt_feedback", None)
                log.warning("chat %s: empty reply (%s)", chat_id, reason)
                return "\n\n".join(replies) or "Sorry, I can't help with that one."
            self.storage.append_message(chat_id, "model", to_dict(candidate.content))
            replies += [p.text for p in candidate.content.parts if p.text and not p.thought and p.text.strip()]

            calls = resp.function_calls or []
            if not calls:
                break
            results = []
            for call in calls:
                output, is_error = await self._run_tool(chat_id, call.name, dict(call.args or {}))
                log.info("chat %s: tool %s(%s) -> %s", chat_id, call.name, call.args, output[:200])
                key = "error" if is_error else "result"
                results.append(types.Part(function_response=types.FunctionResponse(
                    id=call.id, name=call.name, response={key: output})))
            self.storage.append_message(chat_id, "user", to_dict(types.Content(role="user", parts=results)))
        else:
            replies.append("(I stopped after too many steps — tell me if you want me to keep going.)")
        return "\n\n".join(replies).strip() or "Done."

    # ---------- tools that need their own Gemini call ----------
    async def _run_tool(self, chat_id: int, name: str, args: dict[str, Any]) -> tuple[str, bool]:
        try:
            if name == "web_search":
                return await self._grounded(f"Search the web and give a concise, factual answer with source "
                                            f"links for: {args['query']}", types.Tool(google_search=types.GoogleSearch())), False
            if name == "read_webpage":
                q = args.get("question") or "Summarise the main content."
                return await self._grounded(f"Read {args['url']} and answer: {q}",
                                            types.Tool(url_context=types.UrlContext())), False
        except KeyError as e:
            return f"Missing argument {e}", True
        except Exception as e:  # network / quota problems: let the model tell the user
            log.warning("%s failed: %s", name, e)
            return f"{name} failed: {e}", True
        return self.tools.run(chat_id, name, args)

    async def _grounded(self, prompt: str, tool: types.Tool) -> str:
        resp = await self.client.aio.models.generate_content(
            model=self.settings.model, contents=prompt, config=types.GenerateContentConfig(tools=[tool]),
        )
        text = resp.text or "No results."
        sources = []
        try:
            for chunk in resp.candidates[0].grounding_metadata.grounding_chunks or []:
                if chunk.web and chunk.web.uri:
                    sources.append(f"{chunk.web.title or 'source'}: {chunk.web.uri}")
        except (AttributeError, IndexError, TypeError):
            pass
        return text + ("\n\nSources:\n" + "\n".join(sources[:5]) if sources else "")

    async def transcribe(self, audio: bytes, mime_type: str = "audio/ogg") -> str:
        resp = await self.client.aio.models.generate_content(
            model=self.settings.model,
            contents=[types.Part.from_bytes(data=audio, mime_type=mime_type),
                      "Transcribe this voice message exactly, in its original language. "
                      "Reply with only the transcript, or nothing if there is no speech."],
        )
        return (resp.text or "").strip()
