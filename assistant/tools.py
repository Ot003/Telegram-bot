"""Tools the assistant can use, plus their implementations.

Everything here runs in this process. Web search and page reading are declared here
too but executed by brain.py, because they need their own call to Gemini.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .storage import Storage

REPEAT_OPTIONS = ["none", "daily", "weekdays", "weekly"]


def _schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or []}


# Order and content are fixed for the life of the process: changing the tool list
# mid-conversation would confuse the model and break context caching.
CUSTOM_TOOLS: list[dict[str, Any]] = [
    {
        "name": "save_memory",
        "description": "Save a lasting fact about the user (preferences, people, projects, routines) so you "
                       "remember it in future conversations. Use it whenever you learn something worth keeping.",
        "input_schema": _schema({"fact": {"type": "string", "description": "One self-contained fact."}}, ["fact"]),
    },
    {
        "name": "list_memories",
        "description": "List everything saved about the user, with ids.",
        "input_schema": _schema({}),
    },
    {
        "name": "forget_memory",
        "description": "Delete a saved memory by id (use list_memories first if you need the id).",
        "input_schema": _schema({"memory_id": {"type": "integer"}}, ["memory_id"]),
    },
    {
        "name": "add_task",
        "description": "Add an item to the user's to-do list.",
        "input_schema": _schema({
            "title": {"type": "string"},
            "notes": {"type": "string", "description": "Optional details."},
            "due": {"type": "string", "description": "Optional due date/time in the user's local time, ISO 8601, "
                                                      "e.g. 2026-11-24 or 2026-11-24T17:00."},
        }, ["title"]),
    },
    {
        "name": "list_tasks",
        "description": "Show the user's to-do list.",
        "input_schema": _schema({"include_done": {"type": "boolean", "description": "Include completed items."}}),
    },
    {
        "name": "complete_task",
        "description": "Mark a to-do item as done.",
        "input_schema": _schema({"task_id": {"type": "integer"}}, ["task_id"]),
    },
    {
        "name": "delete_task",
        "description": "Remove a to-do item entirely.",
        "input_schema": _schema({"task_id": {"type": "integer"}}, ["task_id"]),
    },
    {
        "name": "set_reminder",
        "description": "Schedule a reminder message that you will send the user at a specific time. Give either "
                       "`when` (local time) or `in_minutes` (relative). The text is what the user will see.",
        "input_schema": _schema({
            "text": {"type": "string", "description": "Reminder text, written to the user."},
            "when": {"type": "string", "description": "Local date-time, ISO 8601, e.g. 2026-11-24T09:00."},
            "in_minutes": {"type": "number", "description": "Minutes from now, for relative reminders."},
            "repeat": {"type": "string", "enum": REPEAT_OPTIONS, "description": "Default none."},
        }, ["text"]),
    },
    {
        "name": "list_reminders",
        "description": "List upcoming reminders with ids.",
        "input_schema": _schema({}),
    },
    {
        "name": "cancel_reminder",
        "description": "Cancel a reminder by id.",
        "input_schema": _schema({"reminder_id": {"type": "integer"}}, ["reminder_id"]),
    },
    {
        "name": "set_timezone",
        "description": "Set the user's time zone (IANA name such as Europe/London or America/New_York). "
                       "Do this as soon as you learn where the user is.",
        "input_schema": _schema({"timezone": {"type": "string"}}, ["timezone"]),
    },
    {
        "name": "web_search",
        "description": "Search the web (Google) for current information: news, prices, opening hours, "
                       "weather, sports, anything that may have changed. Returns a summary with source links.",
        "input_schema": _schema({"query": {"type": "string", "description": "What to look up."}}, ["query"]),
    },
    {
        "name": "read_webpage",
        "description": "Read a specific web page and answer a question about it.",
        "input_schema": _schema({
            "url": {"type": "string"},
            "question": {"type": "string", "description": "What you want to know from the page."},
        }, ["url"]),
    },
    {
        "name": "set_voice_replies",
        "description": "Change when you answer with a voice message: 'always', 'auto' (only when the user sent "
                       "voice) or 'off'.",
        "input_schema": _schema({"mode": {"type": "string", "enum": ["always", "auto", "off"]}}, ["mode"]),
    },
]

ALL_TOOLS = CUSTOM_TOOLS


class ToolError(Exception):
    """A problem the model should see and can fix (bad id, bad date, ...)."""


def user_tz(storage: Storage, chat_id: int, default: str) -> ZoneInfo:
    name = storage.get_setting(chat_id, "timezone", default) or "UTC"
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def parse_local_datetime(value: str, tz: ZoneInfo) -> datetime:
    """Parse ISO 8601; naive values are taken as the user's local time. Returns aware UTC."""
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as e:
        raise ToolError(f"Could not parse date/time {value!r}; use ISO 8601 like 2026-11-24T09:00.") from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(timezone.utc)


def next_occurrence(fire_at_utc: datetime, repeat: str, tz: ZoneInfo, now_utc: datetime) -> datetime | None:
    """Next fire time for a repeating reminder, stepping in local time so DST is respected."""
    if repeat == "none":
        return None
    local = fire_at_utc.astimezone(tz)
    while True:
        if repeat == "daily":
            local = local + timedelta(days=1)
        elif repeat == "weekly":
            local = local + timedelta(weeks=1)
        elif repeat == "weekdays":
            local = local + timedelta(days=1)
            while local.weekday() >= 5:
                local = local + timedelta(days=1)
        else:
            return None
        # re-attach tz so wall-clock time stays the same across DST changes
        local = local.replace(tzinfo=None).replace(tzinfo=tz)
        nxt = local.astimezone(timezone.utc)
        if nxt > now_utc:
            return nxt


def fmt_local(dt_utc: datetime, tz: ZoneInfo) -> str:
    return dt_utc.astimezone(tz).strftime("%a %d %b %Y, %H:%M")


class ToolRunner:
    def __init__(self, storage: Storage, default_tz: str):
        self.storage = storage
        self.default_tz = default_tz
        self._handlers: dict[str, Callable[[int, dict[str, Any]], Any]] = {
            "save_memory": self.save_memory,
            "list_memories": self.list_memories,
            "forget_memory": self.forget_memory,
            "add_task": self.add_task,
            "list_tasks": self.list_tasks,
            "complete_task": self.complete_task,
            "delete_task": self.delete_task,
            "set_reminder": self.set_reminder,
            "list_reminders": self.list_reminders,
            "cancel_reminder": self.cancel_reminder,
            "set_timezone": self.set_timezone,
            "set_voice_replies": self.set_voice_replies,
        }

    def run(self, chat_id: int, name: str, args: dict[str, Any]) -> tuple[str, bool]:
        """Execute a tool. Returns (result_text, is_error)."""
        handler = self._handlers.get(name)
        if handler is None:
            return f"Unknown tool {name!r}.", True
        try:
            result = handler(chat_id, args or {})
        except ToolError as e:
            return str(e), True
        except (KeyError, TypeError, ValueError) as e:
            return f"Invalid input for {name}: {e}", True
        return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False), False

    def tz(self, chat_id: int) -> ZoneInfo:
        return user_tz(self.storage, chat_id, self.default_tz)

    # ---- memories ----
    def save_memory(self, chat_id: int, a: dict[str, Any]) -> str:
        fact = str(a["fact"]).strip()
        if not fact:
            raise ToolError("fact is empty")
        return f"Saved memory #{self.storage.add_memory(chat_id, fact)}."

    def list_memories(self, chat_id: int, a: dict[str, Any]) -> Any:
        return self.storage.list_memories(chat_id) or "No memories saved yet."

    def forget_memory(self, chat_id: int, a: dict[str, Any]) -> str:
        if not self.storage.delete_memory(chat_id, int(a["memory_id"])):
            raise ToolError(f"No memory with id {a['memory_id']}.")
        return "Forgotten."

    # ---- tasks ----
    def add_task(self, chat_id: int, a: dict[str, Any]) -> str:
        title = str(a["title"]).strip()
        if not title:
            raise ToolError("title is empty")
        task_id = self.storage.add_task(chat_id, title, a.get("notes"), a.get("due"))
        return f"Added task #{task_id}."

    def list_tasks(self, chat_id: int, a: dict[str, Any]) -> Any:
        tasks = self.storage.list_tasks(chat_id, bool(a.get("include_done")))
        return [dict(t, done=bool(t["done"])) for t in tasks] or "The to-do list is empty."

    def complete_task(self, chat_id: int, a: dict[str, Any]) -> str:
        if not self.storage.complete_task(chat_id, int(a["task_id"])):
            raise ToolError(f"No task with id {a['task_id']}.")
        return "Marked done."

    def delete_task(self, chat_id: int, a: dict[str, Any]) -> str:
        if not self.storage.delete_task(chat_id, int(a["task_id"])):
            raise ToolError(f"No task with id {a['task_id']}.")
        return "Deleted."

    # ---- reminders ----
    def set_reminder(self, chat_id: int, a: dict[str, Any]) -> str:
        text = str(a["text"]).strip()
        repeat = a.get("repeat") or "none"
        if repeat not in REPEAT_OPTIONS:
            raise ToolError(f"repeat must be one of {REPEAT_OPTIONS}")
        tz = self.tz(chat_id)
        now = datetime.now(timezone.utc)
        if a.get("in_minutes") is not None:
            fire_at = now + timedelta(minutes=float(a["in_minutes"]))
        elif a.get("when"):
            fire_at = parse_local_datetime(str(a["when"]), tz)
        else:
            raise ToolError("Give either `when` or `in_minutes`.")
        if fire_at <= now:
            raise ToolError(f"That time ({fmt_local(fire_at, tz)}) is in the past.")
        rid = self.storage.add_reminder(chat_id, text, fire_at, repeat)
        suffix = "" if repeat == "none" else f", repeating {repeat}"
        return f"Reminder #{rid} set for {fmt_local(fire_at, tz)} ({tz.key}){suffix}."

    def list_reminders(self, chat_id: int, a: dict[str, Any]) -> Any:
        tz = self.tz(chat_id)
        rows = self.storage.list_reminders(chat_id)
        if not rows:
            return "No upcoming reminders."
        return [
            {"id": r["id"], "text": r["text"], "at": fmt_local(datetime.fromisoformat(r["fire_at"]), tz),
             "repeat": r["repeat"]}
            for r in rows
        ]

    def cancel_reminder(self, chat_id: int, a: dict[str, Any]) -> str:
        if not self.storage.cancel_reminder(chat_id, int(a["reminder_id"])):
            raise ToolError(f"No active reminder with id {a['reminder_id']}.")
        return "Cancelled."

    # ---- settings ----
    def set_timezone(self, chat_id: int, a: dict[str, Any]) -> str:
        name = str(a["timezone"]).strip()
        try:
            ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ToolError(f"Unknown time zone {name!r}; use an IANA name like Europe/Paris.") from e
        self.storage.set_setting(chat_id, "timezone", name)
        return f"Time zone set to {name}. Local time there is {fmt_local(datetime.now(timezone.utc), ZoneInfo(name))}."

    def set_voice_replies(self, chat_id: int, a: dict[str, Any]) -> str:
        mode = a["mode"]
        if mode not in ("always", "auto", "off"):
            raise ToolError("mode must be always, auto or off")
        self.storage.set_setting(chat_id, "voice", mode)
        return f"Voice replies: {mode}."
