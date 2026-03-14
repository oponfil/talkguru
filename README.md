# TalkGuru 🗣️

Опенсорсный Telegram-бот, помогающий писать ответы на сообщения.

Использует **Gemini 3.1 Flash** через OpenRouter и [x402gate.io](https://x402gate.io) (оплата USDC на Base).

## Возможности

- 💬 **Ответы на сообщения** — отправьте текст, бот предложит ответ
- 🔗 **Подключение аккаунта** (`/connect`) — бот читает входящие и предлагает ответ как черновик (только личные чаты)
- 🌐 **Мультиязычность** — интерфейс и статусы переводятся на язык пользователя
- 📝 **Черновики** — ответ появляется в поле ввода, отправляете сами
- 🦉 **Probe-детекция** — бот определяет, вышел ли пользователь из чата, через статус «🦉 is typing...»
- ✏️ **Драфт-инструкции** — напишите инструкцию в черновик любого чата (личного, группы, канала), бот обработает

## Быстрый старт

### 1. Клонировать и установить зависимости

```bash
git clone https://github.com/your-username/TalkGuru.git
cd TalkGuru
pip install -r requirements.txt
```

### 2. Настроить переменные окружения

```bash
cp .env.example .env
```

Заполните `.env`:
- `BOT_TOKEN` — получите у [@BotFather](https://t.me/BotFather)
- `PYROGRAM_API_ID` и `PYROGRAM_API_HASH` — из [my.telegram.org](https://my.telegram.org)
- `SUPABASE_URL` и `SUPABASE_KEY` — из [Supabase Dashboard](https://supabase.com) (используйте **service_role** ключ)
- `EVM_PRIVATE_KEY` — приватный ключ кошелька Base с USDC для оплаты AI

### 3. Создать таблицу в Supabase

Выполните `schema.sql` в SQL Editor вашего Supabase проекта.

### 4. Запустить

```bash
python bot.py
```

## Команды бота

| Команда | Описание |
|---------|----------|
| `/start` | Начать |
| `/connect` | Подключить Telegram-аккаунт через QR-код |
| `/disconnect` | Отключить аккаунт |
| `/status` | Статус подключения |

## Деплой на Railway

1. Создайте проект на [Railway](https://railway.app)
2. Подключите GitHub-репозиторий
3. Добавьте переменные окружения (из `.env.example`)
4. Railway автоматически обнаружит `Procfile` и запустит бота

## Тесты

```bash
pytest tests/ -v
```

Все внешние зависимости замоканы — тесты полностью офлайновые и не требуют `.env`.

Тесты автоматически запускаются на GitHub при push в `main`/`dev` и при PR (GitHub Actions).

## Архитектура

- **bot.py** — Telegram-обработчики (`/start`, `on_text`), запуск бота
- **handlers/** — Обработчики команд и событий Pyrogram (`/connect`, `on_pyrogram_message`, `on_pyrogram_draft`)
- **config.py** — Все константы и переменные окружения
- **prompts.py** — Все промпты для ИИ
- **system_messages.py** — Системные сообщения с переводом на язык пользователя
- **clients/** — API-клиенты (`x402gate`, `pyrogram_client`)
- **database/** — Запросы к Supabase (`upsert_user`, `get_user`, `save_session`)
- **utils/** — Утилиты (`get_timestamp`, `extract_rating_from_chat`)
- **tests/** — Unit-тесты (pytest)

## Стек

- **Python 3.13**
- **python-telegram-bot** — Telegram Bot API
- **Pyrogram** — Telegram Client API (чтение сообщений, черновики)
- **x402gate.io** → OpenRouter → Gemini 3.1 Flash
- **Supabase** — PostgreSQL (БД)
- **Railway** — хостинг

## Стайлгайд

См. [CONTRIBUTING.md](CONTRIBUTING.md)

## Лицензия

MIT
