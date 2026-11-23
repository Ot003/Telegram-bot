import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from anthropic.types.beta import BetaMessage

from assistant.bot import Bot
from assistant.brain import Brain
from assistant.config import Settings
from assistant.storage import Storage
from assistant.tools import ToolRunner, next_occurrence, parse_local_datetime


def make_message(content, stop_reason):
    return BetaMessage.model_validate({
        "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
        "content": content, "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    })


class FakeMessages:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def create(self, **kwargs):
        # snapshot the messages as sent
        import copy
        self.calls.append(copy.deepcopy(kwargs))
        return self.responses.pop(0)


class FakeClient:
    def __init__(self, responses):
        self.beta = type("B", (), {})()
        self.beta.messages = FakeMessages(responses)


class FakeTelegram:
    def __init__(self):
        self.sent = []

    async def send_text(self, chat_id, text, reply_to=None):
        self.sent.append((chat_id, text))

    async def send_typing(self, chat_id, action="typing"):
        pass

    async def close(self):
        pass


@pytest.fixture
def settings(tmp_path):
    return Settings(telegram_token="x", db_path=str(tmp_path / "t.db"), default_timezone="Europe/London",
                    allowed_user_ids={42})


def test_tool_loop_sets_reminder_and_replies(settings):
    storage = Storage(settings.db_path)
    client = FakeClient([
        make_message([
            {"type": "thinking", "thinking": "", "signature": "sig123"},
            {"type": "tool_use", "id": "tu_1", "name": "set_reminder",
             "input": {"text": "Call mum", "in_minutes": 30}},
        ], "tool_use"),
        make_message([{"type": "text", "text": "Done — I'll remind you in 30 minutes."}], "end_turn"),
    ])
    brain = Brain(settings, storage, client=client)
    reply = asyncio.run(brain.respond(7, "remind me to call mum in half an hour", user_name="Sam"))

    assert "remind you" in reply
    assert storage.list_reminders(7)[0]["text"] == "Call mum"

    history = storage.load_history(7)
    assert [m["role"] for m in history] == ["user", "assistant", "user", "assistant"]
    # thinking block (with signature) is stored and sent back unchanged
    assert history[1]["content"][0] == {"type": "thinking", "thinking": "", "signature": "sig123"}
    assert history[2]["content"][0]["type"] == "tool_result"
    # first message carries the context header; the history sent is append-only
    first_call, second_call = client.beta.messages.calls
    assert "Telegram name: Sam" in first_call["messages"][0]["content"][0]["text"]
    assert second_call["messages"][:1] == first_call["messages"]
    assert first_call["system"] == second_call["system"] and first_call["tools"] == second_call["tools"]
    assert first_call["fallbacks"] == "default"


def test_tool_errors_are_reported_to_model(settings):
    runner = ToolRunner(Storage(settings.db_path), "UTC")
    out, err = runner.run(1, "complete_task", {"task_id": 99})
    assert err and "No task" in out
    out, err = runner.run(1, "set_reminder", {"text": "x", "when": "2001-01-01T09:00"})
    assert err and "past" in out
    out, err = runner.run(1, "set_timezone", {"timezone": "Mars/Base"})
    assert err


def test_time_helpers():
    tz = ZoneInfo("America/New_York")
    dt = parse_local_datetime("2026-11-24T09:00", tz)
    assert dt == datetime(2026, 11, 24, 14, 0, tzinfo=timezone.utc)
    # Friday 09:00 -> next weekday is Monday 09:00 local
    fri = parse_local_datetime("2026-11-27T09:00", tz)
    nxt = next_occurrence(fri, "weekdays", tz, fri)
    assert nxt.astimezone(tz).strftime("%a %H:%M") == "Mon 09:00"
    assert next_occurrence(fri, "none", tz, fri) is None


def test_due_reminder_is_sent_and_reported(settings):
    storage = Storage(settings.db_path)
    tg = FakeTelegram()
    bot = Bot(settings, storage=storage, telegram=tg, brain=Brain(settings, storage, client=FakeClient([])))
    past = datetime.now(timezone.utc) - timedelta(seconds=5)
    storage.add_reminder(5, "Take the bins out", past, "daily")

    assert asyncio.run(bot.fire_due_reminders()) == 1
    assert "Take the bins out" in tg.sent[0][1]
    assert storage.list_reminders(5)  # daily -> rescheduled, still active
    assert datetime.fromisoformat(storage.list_reminders(5)[0]["fire_at"]) > datetime.now(timezone.utc)
    assert "Take the bins out" in storage.pop_events(5)[0]


def test_unauthorised_user_is_turned_away(settings):
    storage = Storage(settings.db_path)
    tg = FakeTelegram()
    bot = Bot(settings, storage=storage, telegram=tg, brain=Brain(settings, storage, client=FakeClient([])))
    asyncio.run(bot.handle_update({"update_id": 1, "message": {
        "message_id": 1, "chat": {"id": 9}, "from": {"id": 999, "first_name": "X"}, "text": "hi"}}))
    assert "private assistant" in tg.sent[0][1]


def test_commands(settings):
    storage = Storage(settings.db_path)
    tg = FakeTelegram()
    bot = Bot(settings, storage=storage, telegram=tg, brain=Brain(settings, storage, client=FakeClient([])))
    storage.add_task(3, "Buy milk", None, None)
    asyncio.run(bot.handle_command(3, "/tasks"))
    asyncio.run(bot.handle_command(3, "/voice always"))
    assert "Buy milk" in tg.sent[0][1]
    assert storage.get_setting(3, "voice") == "always"
