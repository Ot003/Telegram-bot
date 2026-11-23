"""SQLite persistence: conversation history, memories, tasks, reminders, per-chat settings."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id, id);

CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    fact TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    notes TEXT,
    due TEXT,
    done INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    fire_at TEXT NOT NULL,          -- UTC ISO timestamp
    repeat TEXT NOT NULL DEFAULT 'none',
    active INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(active, fire_at);

CREATE TABLE IF NOT EXISTS chat_settings (
    chat_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (chat_id, key)
);

-- Things that happened between turns (e.g. a reminder fired) that the assistant
-- should hear about with the user's next message.
CREATE TABLE IF NOT EXISTS pending_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    text TEXT NOT NULL
);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Storage:
    def __init__(self, path: str):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def _exec(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _all(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    # ---- conversation history (append-only) ----
    def load_history(self, chat_id: int) -> list[dict[str, Any]]:
        rows = self._all("SELECT role, content FROM messages WHERE chat_id=? ORDER BY id", (chat_id,))
        return [{"role": r["role"], "content": json.loads(r["content"])} for r in rows]

    def append_message(self, chat_id: int, role: str, content: Any) -> None:
        self._exec(
            "INSERT INTO messages (chat_id, role, content) VALUES (?, ?, ?)",
            (chat_id, role, json.dumps(content)),
        )

    def history_length(self, chat_id: int) -> int:
        return self._all("SELECT COUNT(*) AS n FROM messages WHERE chat_id=?", (chat_id,))[0]["n"]

    def clear_history(self, chat_id: int) -> None:
        self._exec("DELETE FROM messages WHERE chat_id=?", (chat_id,))

    # ---- memories ----
    def add_memory(self, chat_id: int, fact: str) -> int:
        return self._exec(
            "INSERT INTO memories (chat_id, fact, created_at) VALUES (?, ?, ?)",
            (chat_id, fact, utcnow().isoformat()),
        ).lastrowid

    def list_memories(self, chat_id: int) -> list[dict[str, Any]]:
        return self._all("SELECT id, fact, created_at FROM memories WHERE chat_id=? ORDER BY id", (chat_id,))

    def delete_memory(self, chat_id: int, memory_id: int) -> bool:
        return self._exec("DELETE FROM memories WHERE chat_id=? AND id=?", (chat_id, memory_id)).rowcount > 0

    # ---- tasks ----
    def add_task(self, chat_id: int, title: str, notes: str | None, due: str | None) -> int:
        return self._exec(
            "INSERT INTO tasks (chat_id, title, notes, due, created_at) VALUES (?, ?, ?, ?, ?)",
            (chat_id, title, notes, due, utcnow().isoformat()),
        ).lastrowid

    def list_tasks(self, chat_id: int, include_done: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT id, title, notes, due, done FROM tasks WHERE chat_id=?"
        if not include_done:
            sql += " AND done=0"
        return self._all(sql + " ORDER BY done, (due IS NULL), due, id", (chat_id,))

    def complete_task(self, chat_id: int, task_id: int) -> bool:
        return self._exec("UPDATE tasks SET done=1 WHERE chat_id=? AND id=?", (chat_id, task_id)).rowcount > 0

    def delete_task(self, chat_id: int, task_id: int) -> bool:
        return self._exec("DELETE FROM tasks WHERE chat_id=? AND id=?", (chat_id, task_id)).rowcount > 0

    # ---- reminders ----
    def add_reminder(self, chat_id: int, text: str, fire_at_utc: datetime, repeat: str = "none") -> int:
        return self._exec(
            "INSERT INTO reminders (chat_id, text, fire_at, repeat) VALUES (?, ?, ?, ?)",
            (chat_id, text, fire_at_utc.isoformat(), repeat),
        ).lastrowid

    def list_reminders(self, chat_id: int) -> list[dict[str, Any]]:
        return self._all(
            "SELECT id, text, fire_at, repeat FROM reminders WHERE chat_id=? AND active=1 ORDER BY fire_at",
            (chat_id,),
        )

    def cancel_reminder(self, chat_id: int, reminder_id: int) -> bool:
        return self._exec(
            "UPDATE reminders SET active=0 WHERE chat_id=? AND id=? AND active=1", (chat_id, reminder_id)
        ).rowcount > 0

    def due_reminders(self, now_utc: datetime) -> list[dict[str, Any]]:
        return self._all(
            "SELECT id, chat_id, text, fire_at, repeat FROM reminders WHERE active=1 AND fire_at<=? ORDER BY fire_at",
            (now_utc.isoformat(),),
        )

    def reschedule_reminder(self, reminder_id: int, next_fire_utc: datetime | None) -> None:
        if next_fire_utc is None:
            self._exec("UPDATE reminders SET active=0 WHERE id=?", (reminder_id,))
        else:
            self._exec("UPDATE reminders SET fire_at=? WHERE id=?", (next_fire_utc.isoformat(), reminder_id))

    # ---- per-chat settings ----
    def get_setting(self, chat_id: int, key: str, default: str | None = None) -> str | None:
        rows = self._all("SELECT value FROM chat_settings WHERE chat_id=? AND key=?", (chat_id, key))
        return rows[0]["value"] if rows else default

    def set_setting(self, chat_id: int, key: str, value: str) -> None:
        self._exec(
            "INSERT INTO chat_settings (chat_id, key, value) VALUES (?, ?, ?) "
            "ON CONFLICT(chat_id, key) DO UPDATE SET value=excluded.value",
            (chat_id, key, value),
        )

    # ---- pending events ----
    def add_event(self, chat_id: int, text: str) -> None:
        self._exec("INSERT INTO pending_events (chat_id, text) VALUES (?, ?)", (chat_id, text))

    def pop_events(self, chat_id: int) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, text FROM pending_events WHERE chat_id=? ORDER BY id", (chat_id,)
            ).fetchall()
            self._conn.execute("DELETE FROM pending_events WHERE chat_id=?", (chat_id,))
            self._conn.commit()
        return [r["text"] for r in rows]
