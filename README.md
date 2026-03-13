# TalkGuru 🗣️

Опенсорсный Telegram-бот, помогающий писать ответы на сообщения.

Использует **Gemini 3.1 Flash** через OpenRouter и [x402gate.io](https://x402gate.io) (оплата USDC на Base).

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
- `SUPABASE_URL` и `SUPABASE_KEY` — из [Supabase Dashboard](https://supabase.com) (используйте **service_role** ключ)
- `EVM_PRIVATE_KEY` — приватный ключ кошелька Base с USDC для оплаты AI

### 3. Создать таблицу в Supabase

Выполните `schema.sql` в SQL Editor вашего Supabase проекта.

### 4. Запустить

```bash
python bot.py
```

## Деплой на Railway

1. Создайте проект на [Railway](https://railway.app)
2. Подключите GitHub-репозиторий
3. Добавьте переменные окружения (из `.env.example`)
4. Railway автоматически обнаружит `Procfile` и запустит бота

## Архитектура

```
bot.py              ← Telegram handlers (polling)
config.py           ← Константы и env
clients/x402gate/   ← Клиент x402gate.io (Base/EVM, OpenRouter)
database/           ← Supabase (таблица users)
```

## Стек

- **Python 3.13**
- **python-telegram-bot** — Telegram Bot API
- **x402gate.io** → OpenRouter → Gemini 3.1 Flash
- **Supabase** — PostgreSQL (БД)
- **Railway** — хостинг

## Стайлгайд

См. [CONTRIBUTING.md](CONTRIBUTING.md)

## Лицензия

MIT
