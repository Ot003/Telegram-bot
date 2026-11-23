"""Telegram assistant server.

Run locally (no public URL needed):   python main.py            (MODE=polling, the default)
Run behind a public HTTPS URL:        MODE=webhook WEBHOOK_URL=https://... uvicorn main:app
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Request

from assistant.bot import Bot
from assistant.config import settings

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("main")

bot: Bot | None = None
webhook_secret = settings.webhook_secret or secrets.token_urlsafe(32)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global bot
    if not settings.telegram_token:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN (from @BotFather) in the environment or .env file.")
    if not settings.gemini_api_key:
        raise RuntimeError("Set GEMINI_API_KEY (free from https://aistudio.google.com/apikey).")
    if not settings.allowed_user_ids:
        log.warning("ALLOWED_USER_IDS is empty: ANYONE who finds this bot can use it (and your free quota).")

    bot = Bot(settings)
    background = [asyncio.create_task(bot.reminder_loop())]

    if settings.mode == "webhook":
        if not settings.webhook_url:
            raise RuntimeError("MODE=webhook needs WEBHOOK_URL (your server's public https:// address).")
        await bot.tg.call("setWebhook", url=f"{settings.webhook_url}/webhook", secret_token=webhook_secret,
                          allowed_updates=["message"], drop_pending_updates=False)
        log.info("Webhook registered at %s/webhook", settings.webhook_url)
    else:
        background.append(asyncio.create_task(bot.poll_forever()))

    me = await bot.tg.call("getMe")
    log.info("%s is running as @%s (%s mode)", settings.assistant_name, me.get("username"), settings.mode)
    try:
        yield
    finally:
        for task in background:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await bot.close()


app = FastAPI(lifespan=lifespan)


@app.get("/")
def root():
    return {"status": "ok", "mode": settings.mode}


@app.post("/webhook")
async def telegram_webhook(req: Request, x_telegram_bot_api_secret_token: str | None = Header(default=None)):
    if not secrets.compare_digest(x_telegram_bot_api_secret_token or "", webhook_secret):
        raise HTTPException(status_code=403, detail="bad secret")
    update = await req.json()
    assert bot is not None
    bot.submit(update)  # process in the background; Telegram wants a fast 200
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
