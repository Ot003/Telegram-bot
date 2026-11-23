"""Jarvis: a free AI assistant for Telegram.

Brain: Google Gemini (free tier, no credit card).
Talk to it with text, voice messages, or photos.
"""

import logging
import os
from collections import defaultdict, deque

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from google import genai
from google.genai import types

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("jarvis")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")
# Secret Telegram sends with every webhook call, so strangers can't fake messages.
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
# Public address of this server. Render sets RENDER_EXTERNAL_URL automatically.
PUBLIC_URL = os.environ.get("PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL", "")
# Optional: comma-separated Telegram user IDs allowed to use the bot.
ALLOWED_USERS = {u.strip() for u in os.environ.get("ALLOWED_USERS", "").split(",") if u.strip()}
HISTORY_TURNS = int(os.environ.get("HISTORY_TURNS", "20"))

SYSTEM_PROMPT = os.environ.get(
    "SYSTEM_PROMPT",
    "You are J.A.R.V.I.S., a witty, loyal and highly capable personal assistant, "
    "in the style of Tony Stark's AI. Address the user as 'sir' or 'ma'am' only if "
    "they ask you to; otherwise be warm and natural. Keep replies short and clear, "
    "because they are read on a phone. If the user sends a voice message, respond "
    "to what they said. If they send a photo, describe or help with it.",
)

TG_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
TG_FILE = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}"

app = FastAPI()
gemini = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
# Per-chat memory of recent turns (resets if the server restarts).
history: dict[int, deque] = defaultdict(lambda: deque(maxlen=HISTORY_TURNS * 2))


async def tg(method: str, **params):
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(f"{TG_API}/{method}", json=params)
        if r.status_code != 200:
            log.warning("Telegram %s failed: %s", method, r.text)
        return r.json()


async def download_file(file_id: str) -> bytes:
    info = await tg("getFile", file_id=file_id)
    path = info["result"]["file_path"]
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.get(f"{TG_FILE}/{path}")
        r.raise_for_status()
        return r.content


async def send_text(chat_id: int, text: str):
    # Telegram caps messages at 4096 characters.
    for i in range(0, len(text), 4000):
        await tg("sendMessage", chat_id=chat_id, text=text[i : i + 4000])


async def ask_gemini(chat_id: int, parts: list[types.Part]) -> str:
    user_turn = types.Content(role="user", parts=parts)
    contents = list(history[chat_id]) + [user_turn]
    resp = await gemini.aio.models.generate_content(
        model=GEMINI_MODEL,
        contents=contents,
        config=types.GenerateContentConfig(system_instruction=SYSTEM_PROMPT),
    )
    reply = (resp.text or "").strip() or "I'm afraid I have nothing to say to that."
    # Store voice/photos as a short note so memory stays small.
    stored = [p if p.text is not None else types.Part(text="[sent a voice message or photo]") for p in parts]
    history[chat_id].append(types.Content(role="user", parts=stored))
    history[chat_id].append(types.Content(role="model", parts=[types.Part(text=reply)]))
    return reply


async def handle_message(msg: dict):
    chat_id = msg["chat"]["id"]
    user_id = str(msg.get("from", {}).get("id", ""))
    text = msg.get("text") or msg.get("caption") or ""

    if ALLOWED_USERS and user_id not in ALLOWED_USERS:
        await send_text(chat_id, f"Sorry, I only work for my owner. (Your ID: {user_id})")
        return

    if text.startswith("/start"):
        await send_text(chat_id, f"Good day. J.A.R.V.I.S. online. Text me, send a voice note, or a photo.\n(Your Telegram ID: {user_id})")
        return
    if text.startswith("/reset"):
        history.pop(chat_id, None)
        await send_text(chat_id, "Memory wiped. Fresh start.")
        return
    if text.startswith("/id"):
        await send_text(chat_id, f"Your Telegram ID: {user_id}")
        return

    parts: list[types.Part] = []
    if "voice" in msg or "audio" in msg:
        media = msg.get("voice") or msg.get("audio")
        data = await download_file(media["file_id"])
        parts.append(types.Part.from_bytes(data=data, mime_type=media.get("mime_type", "audio/ogg")))
    if "photo" in msg:
        data = await download_file(msg["photo"][-1]["file_id"])  # largest size
        parts.append(types.Part.from_bytes(data=data, mime_type="image/jpeg"))
    if text:
        parts.append(types.Part(text=text))
    if not parts:
        await send_text(chat_id, "I can handle text, voice messages and photos.")
        return

    await tg("sendChatAction", chat_id=chat_id, action="typing")
    try:
        reply = await ask_gemini(chat_id, parts)
    except Exception as e:  # keep the bot alive whatever Gemini says
        log.exception("Gemini error")
        reply = f"My apologies, my brain hiccupped: {str(e)[:300]}"
    await send_text(chat_id, reply)


@app.on_event("startup")
async def register_webhook():
    if not (TELEGRAM_BOT_TOKEN and PUBLIC_URL):
        log.warning("TELEGRAM_BOT_TOKEN or PUBLIC_URL missing; webhook not registered")
        return
    params = {"url": f"{PUBLIC_URL.rstrip('/')}/webhook", "drop_pending_updates": True}
    if WEBHOOK_SECRET:
        params["secret_token"] = WEBHOOK_SECRET
    log.info("setWebhook: %s", await tg("setWebhook", **params))


@app.get("/")
def root():
    return {
        "status": "ok",
        "telegram_token_set": bool(TELEGRAM_BOT_TOKEN),
        "gemini_key_set": bool(GEMINI_API_KEY),
        "model": GEMINI_MODEL,
    }


@app.post("/webhook")
async def telegram_webhook(
    req: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
):
    if WEBHOOK_SECRET and x_telegram_bot_api_secret_token != WEBHOOK_SECRET:
        raise HTTPException(status_code=403)
    update = await req.json()
    msg = update.get("message") or update.get("edited_message")
    if msg:
        try:
            await handle_message(msg)
        except Exception:
            log.exception("Failed to handle update")
    return {"ok": True}
