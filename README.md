# Aegis Sentinel Website

Corporate multilingual website for **Aegis Sentinel Global Cybersecurity Solutions**.

## Project structure

```
/aegis-sentinel-website
├── index.html
├── /assets
│   ├── /css
│   ├── /js
│   ├── /images
│   └── /locales
├── /server
│   ├── server.js
│   └── package.json
├── /pages
└── README.md
```

## Frontend usage

Open `index.html` in a browser or serve the repo with a static server.

Language switching uses JSON files in `assets/locales/` and updates text with `data-i18n` keys.

## Backend setup (forms + Telegram + Stripe)

1. Install dependencies:

```
cd server
npm install
```

2. Create a `.env` file in `server/` with the following:

```
PORT=3000
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_SECURE=false
SMTP_USER=mailer@example.com
SMTP_PASS=your_password
CONTACT_TO=security@aegissentinel.com

BOT_TOKEN=your_telegram_bot_token
CHAT_ID=your_telegram_chat_id

STRIPE_SECRET_KEY=sk_test_xxx
```

3. Start the server:

```
npm start
```

### Telegram bot setup

1. In Telegram, open **@BotFather** and run `/newbot`.
2. Copy the `BOT_TOKEN` provided.
3. Create a group or open a private chat with your bot.
4. Add the bot to the group (if using a group) and send a message.
5. Fetch the chat ID by visiting:

```
https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates
```

Look for `chat.id` in the response and set it as `CHAT_ID`.

### Stripe setup

- Use your **publishable key** in `assets/js/payment.js` (`Stripe("YOUR_PUBLISHABLE_KEY")`).
- Use your **secret key** in `server/.env` as `STRIPE_SECRET_KEY`.

## Notes

- Contact forms POST to `/api/contact` and `/api/telegram`.
- Payment page uses Stripe Elements and expects `/api/create-payment-intent`.
