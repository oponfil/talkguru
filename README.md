# DraftGuru 🦉

Open-source Telegram bot that drafts replies for you. Try it: [@DraftGuruBot](https://t.me/DraftGuruBot)

🔞 For users 18+ only.

## How It Works

1. 🔌 Connect your account via `/connect` (phone number or QR code).
2. 🦉 When you receive a private message, the bot automatically drafts a reply right in the input field.
3. ✏️ Write an instruction in the draft — the bot will rewrite it as soon as you leave the chat.

Auto-replies work in private chats only. Draft instructions work everywhere.

## Security

- By default, the bot **only writes drafts** and never sends messages on your behalf. Auto-sending is only possible when the auto-reply timer is explicitly enabled in `/settings`.
- **Messages are not stored.** The bot doesn't save conversations — chat history is fetched via Telegram API on each event and is never persisted.
- **Saved Messages** (self-chat) and **Telegram service notifications** are fully ignored — the bot doesn't read, draft, or process messages in them. Additional chats can be excluded via `IGNORED_CHAT_IDS` in `config.py`.
- Telegram sessions are encrypted with `Fernet` (`SESSION_ENCRYPTION_KEY`) before being stored in the database.

## Quick Start

### 1. Clone and install dependencies

```bash
git clone https://github.com/oponfil/draftguru.git
cd draftguru
pip install -r requirements.txt
```

### 2. Configure environment variables

```bash
cp .env.example .env
```

Fill in `.env`:
- `BOT_TOKEN` — get from [@BotFather](https://t.me/BotFather)
- `PYROGRAM_API_ID` and `PYROGRAM_API_HASH` — from [my.telegram.org](https://my.telegram.org)
- `SUPABASE_URL` and `SUPABASE_KEY` — from [Supabase Dashboard](https://supabase.com) (use the **service_role** key)
- `SESSION_ENCRYPTION_KEY` — `Fernet` key for encrypting `session_string` before storing in DB
- `EVM_PRIVATE_KEY` — private key of a Base wallet with USDC for AI payments

Generate `SESSION_ENCRYPTION_KEY`:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Optional for debugging:
- `DEBUG_PRINT=true` — verbose console logs (default `false`)
- `LOG_TO_FILE=true` — save full AI requests/responses to `logs/` for local debugging (default `false`)

Important: keep `LOG_TO_FILE` disabled in production — it logs full prompts, chat history, and model responses.

### 3. Create the database table

Run `schema.sql` in the SQL Editor of your Supabase project.

### 4. Start the bot

```bash
python bot.py
```

## Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message and quick usage guide |
| `/settings` | Settings: drafts, model (FREE/PRO), prompt, communication style, auto-reply timer, timezone |
| `/chats` | Per-chat settings: individual style, auto-reply timer, and system prompt for each chat (connected users only) |
| `/status` | Connection status |
| `/connect` | Connect Telegram account via phone or QR code (supports 2FA) |
| `/disconnect` | Disconnect account (idempotent: stops listener and clears session in DB) |

The command menu is dynamic: `/connect` and `/disconnect` are shown based on connection status. `/chats` is only visible to connected users. `/start` is not shown in the menu.

By default, `/connect` prompts for a phone number. A button below the message lets you switch to QR code. For `2FA`, the bot asks for the cloud password in a separate message. Phone number, confirmation code, and password are kept visible during authorization and automatically deleted after successful login or timeout.

**Code masking:** During authorization, the bot will ask you to enter the confirmation code with letters or spaces (e.g. `12x345`) to prevent Telegram from blocking your login attempt.

### Settings (`/settings`)

| Setting | Description | Default |
|---------|-------------|:-------:|
| **Drafts** (✏️) | Enable/disable draft instruction processing. When disabled, the bot won't edit drafts based on instructions but will continue creating auto-replies to incoming messages. | ✅ ON |
| **Model** (🤖) | AI mode: FREE (Gemini 3.1 Flash Lite) or PRO. In PRO mode, the model is selected by communication style: GPT-5.4 for most styles, Gemini 3.1 Pro Preview for seducer. | PRO |
| **Prompt** (📝) | Custom prompt: describe your persona and add instructions (max 600 chars). The AI uses this to build a *USER PROFILE & CUSTOM INSTRUCTIONS* block. **We recommend adding a self-description** — gender, age, occupation, and texting habits — so the AI mimics your style more accurately. Example: "I'm a 28 y/o guy, designer. I text short, 1–2 sentences, never use periods at the end. I swear a lot and use stickers." Applied to drafts and auto-replies. | ❌ OFF |
| **Style** (🦉/🍻/💕/💼/💰/🕵️/😈) | Communication style: Userlike, Friend, Romance, Business, Sales, Paranoid, Seducer. Sets the tone and manner of replies (including direct bot chat). | 🦉 Userlike |
| **Auto-reply** (⏰) | Auto-reply timer. If the user doesn't send the draft within the specified time, the bot sends the message itself. Options: OFF, 1 min, 5 min, 15 min, 1 hour, 16 hours. Actual delay: from base to 2×base (e.g. 16 h → 16–32 h, avg 24 h). | OFF |
| **Timezone** (🕐) | User timezone. The button shows the current time — tap to cycle through 30 popular UTC offsets (including +3:30, +4:30, +5:30, +9:30). Affects message timestamps in AI context. | UTC0 |

### Per-chat Settings (`/chats`)

The `/chats` command shows only chats where the bot has actually set a draft or replied, as well as chats with custom settings. Each chat has three buttons:

- **Prompt** (`📝`) — tap to open the prompt editor for this chat. Shows the current prompt and lets you set a new one, clear it, or cancel. Per-chat prompt is appended to the global prompt (max 300 chars).
- **Style** (`🦉 Name`) — tap to cycle through styles
- **Auto-reply** (`⏰`) — tap to cycle through auto-reply timers for this chat. The last option in the cycle is **🔇 Ignore** — fully disables drafts, auto-replies, and message polling for that chat.

Per-chat settings override the global ones from `/settings`. If a per-chat value matches the global one, the override is automatically cleared. Available only to connected users.

**Typing indicator:** While generating a reply, the bot shows a status in the chat with the active style emoji (e.g. `💕 is typing...` for Romance or `😈 is typing...` for Seducer).

**Emoji shortcut in draft:** put a style emoji in the chat draft — the bot will switch the style and generate a reply. If the chosen emoji matches your **global style** (set in `/settings`), the per-chat override will be cleared.

| Emoji | Style |
|-------|-------|
| 🦉 | Userlike |
| 🍻 | Friend |
| 💕 | Romance |
| 💼 | Business |
| 💰 | Sales |
| 🕵️ | Paranoid |
| 😈 | Seducer |

You can combine: `😈 tell her I miss her` — switches the style to Seducer and executes the instruction.

### Voice Messages and Stickers

When a voice message is received, the bot automatically transcribes it via Telegram Premium `TranscribeAudio` and generates a draft reply based on the text. Requires Telegram Premium on the connected account (or trial attempts for free users).

Stickers are processed by emoji — the bot sees the sticker's emoji in the conversation context and generates an appropriate reply.

## Deploy on Railway

1. Create a project on [Railway](https://railway.app)
2. Connect your GitHub repository
3. Add environment variables (from `.env.example`)
4. Railway will automatically detect the `Procfile` and start the bot

## Tests

```bash
pytest tests/ -v
```

All external dependencies are mocked — tests are fully offline and don't require `.env`.

Tests run automatically on GitHub on push to `main`/`dev` and on PRs (GitHub Actions).

## Secret Storage

- `SESSION_STRING` — a secret key for your account. Generation scripts don't display it in the terminal without explicit confirmation. Never share it with third parties.

## Architecture

- **bot.py** — Entry point: handler registration and bot startup
- **handlers/** — Bot commands and Pyrogram events
- **config.py** — Constants and environment variables
- **prompts.py** — AI prompts
- **system_messages.py** — System messages with auto-translation
- **clients/** — API clients (x402gate, Pyrogram)
- **logic/** — Reply generation business logic
- **database/** — Supabase queries
- **utils/** — Utilities
- **scripts/** — CLI scripts (Railway logs, session generation)
- **tests/** — Unit tests (pytest)

## Tech Stack

- **Python 3.13**
- **python-telegram-bot** — Telegram Bot API
- **Pyrogram** — Telegram Client API (reading messages, drafts)
- **x402gate.io** → OpenRouter → any model (configured in `config.py`, paid with USDC on Base)
- **Supabase** — PostgreSQL (DB)
- **Railway** — hosting

## Style Guide

See [CONTRIBUTING.md](CONTRIBUTING.md)

## License

[MIT License](LICENSE)
