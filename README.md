# Telegram Assistant

A personal assistant you talk to on Telegram. You can type to it or send voice notes, and it answers in text and, if you want, out loud. It runs on Claude (`claude-opus-5-5`).

## What it can do

- **Talk.** It keeps the thread of each conversation and replies like a person would.
- **Do tasks.** It keeps your to-do list, sets one-off or repeating reminders ("remind me every weekday at 9 to stretch"), and remembers facts about you between conversations.
- **Look things up.** It can search the web and read web pages for current information.
- **Read what you send.** Photos, PDFs and text files.
- **Voice.** It transcribes your voice notes and replies with a voice message. By default it replies in voice only when you spoke to it. Change this with `/voice always|auto|off` or just ask it to.

Commands: `/tasks`, `/reminders`, `/memory`, `/voice`, `/reset`, `/help`. Anything else, just say it.

## Setup

1. **Create the bot.** In Telegram, message [@BotFather](https://t.me/BotFather), send `/newbot`, and copy the token.
2. **Get a Claude API key** at <https://console.anthropic.com>.
3. **Configure.**
   ```bash
   cp .env.example .env    # then fill in TELEGRAM_BOT_TOKEN and ANTHROPIC_API_KEY
   ```
4. **Install and run.**
   ```bash
   pip install -r requirements.txt            # text + voice replies
   pip install -r requirements-voice.txt      # optional: understand voice notes (needs ffmpeg)
   python main.py
   ```
5. **Lock it to you.** Message your bot. If `ALLOWED_USER_IDS` is set and you're not on the list, it replies with your Telegram user id. Put that id in `ALLOWED_USER_IDS` and restart. If the list is empty, anyone who finds the bot can use it, and your API credits pay for it.

Set `DEFAULT_TIMEZONE` (for example `Europe/London`) or tell the bot where you live, so reminder times are right.

## Running modes

- `MODE=polling` (default): the bot pulls messages from Telegram. It works on any computer and needs no public URL.
- `MODE=webhook`: Telegram pushes messages to `WEBHOOK_URL/webhook`. Use this on a host with public HTTPS, such as Render, Railway or Fly. Start it with `uvicorn main:app --host 0.0.0.0 --port $PORT`. The bot registers the webhook on startup and checks Telegram's secret header on every request.

Data (conversations, memories, tasks, reminders) is kept in SQLite at `DB_PATH`. On hosts with temporary disks, point it at a persistent volume.

## How it works

| File | Role |
|---|---|
| `main.py` | FastAPI app: webhook endpoint, polling, reminder loop |
| `assistant/bot.py` | Turns Telegram messages, voice notes, files and commands into assistant turns; delivers reminders |
| `assistant/brain.py` | The Claude loop (Messages API with tool use, web search, server-side compaction, refusal fallbacks) |
| `assistant/tools.py` | The assistant's tools: memory, to-dos, reminders, time zone, voice setting |
| `assistant/voice.py` | Speech-to-text (faster-whisper, local) and text-to-speech (edge-tts) |
| `assistant/storage.py` | SQLite persistence |

Conversation history is append-only and the system prompt and tool list never change, so prompt caching keeps working. When a conversation gets long, the server compacts it automatically. Saved memories carry over into fresh conversations.

Run the tests with `pip install pytest && python -m pytest`.
