# Putting the bot online for free (Render)

This gets the bot running 24/7 at no cost. You only need a phone or computer browser.

## What you need first
1. **Telegram bot token.** In BotFather: `/mybots` → your bot → **API Token**.
2. **Free Gemini key.** Go to <https://aistudio.google.com/apikey> → **Create API key**.
3. **This code on your GitHub** in the `Telegram-bot` repository.

## Steps
1. Go to <https://render.com> and tap **Get Started**. Sign up with **GitHub**, using the same account that has `Telegram-bot`. The free plan shouldn't ask for a card.
2. In the Render dashboard, tap **New +** → **Blueprint**.
3. Pick the **Telegram-bot** repository. If you don't see it, tap **Configure account** and give Render access to it.
4. Render reads the setup file (`render.yaml`) and asks for three values:
   - `TELEGRAM_BOT_TOKEN`: paste the BotFather token.
   - `GEMINI_API_KEY`: paste the Gemini key.
   - `ALLOWED_USER_IDS`: leave blank for now.
5. Tap **Apply** or **Deploy**. Wait about 5 minutes until it says **Live**.
6. Message your bot on Telegram, say "hi", and it answers.
7. Lock the bot to you. Open the service in Render → **Environment** → set `ALLOWED_USER_IDS` to your Telegram user id. To find your id, message **@userinfobot** on Telegram and it replies with it. Save, and Render restarts the bot.

## Good to know
- **Time zone.** The default is `America/New_York`. Change `DEFAULT_TIMEZONE` under **Environment**, or just tell the bot where you live.
- **Staying awake.** Free Render apps normally sleep after 15 quiet minutes. The bot pings itself every 10 minutes so reminders still go off. Render's free 750 hours a month covers a full month of one app.
- **Saved data.** Free Render has no permanent disk. Memories, tasks and reminders are wiped when Render restarts or redeploys the app. Ask Claude to add a free database if you want them kept for good.
- **Free usage limits.** Gemini's free tier caps how many messages you can send per day. If the bot hits the cap, it tells you to try again later.
