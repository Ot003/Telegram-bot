# J.A.R.V.I.S. Telegram bot

A free AI assistant you use from Telegram on your phone. Text it, send voice notes, or send photos.

- **Brain:** Google Gemini free tier (no credit card)
- **Hosting:** Render free plan (no credit card)
- **Cost:** $0

## Setup (all from your phone, about 10 minutes)

1. **Telegram bot token:** In Telegram, message `@BotFather`, send `/newbot`, pick a name. Copy the token it gives you.
2. **Gemini key:** Go to https://aistudio.google.com, sign in with Google, tap **Get API key**, then **Create API key**. Copy it.
3. **Host it:** Go to https://render.com and sign up with GitHub.
   - Tap **New +**, then **Web Service**, and pick the `Telegram-bot` repo and this branch.
   - Instance type: **Free**
   - Build command: `pip install -r requirements.txt`
   - Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
   - Under **Environment Variables** add:
     - `TELEGRAM_BOT_TOKEN` = your BotFather token
     - `GEMINI_API_KEY` = your Gemini key
     - `WEBHOOK_SECRET` = any random word, like `ironman4829`
   - Tap **Deploy**.
4. Open your bot in Telegram and send `/start`. Done.

## Lock it to just you (recommended)

`/start` shows your Telegram ID. In Render, add the variable `ALLOWED_USERS` = that number and redeploy, so nobody else can use up your free quota.

## Commands

- `/start` says hi and shows your ID
- `/reset` wipes Jarvis's memory of the chat
- `/id` shows your Telegram ID

## Notes

- Render's free plan sleeps after ~15 minutes of no use. The first message after that can take up to a minute; later ones are fast.
- Memory resets when the server restarts or sleeps.
- On Gemini's free tier, Google may use your chats to improve its products.
- To change the personality, set the `SYSTEM_PROMPT` variable. To change the model, set `GEMINI_MODEL`.
