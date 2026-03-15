# TalkGuru 🦉

Опенсорсный Telegram-бот, который пишет черновик ответа за вас.

## Как работает

1. 🔌 Подключите аккаунт через `/connect` (QR-код).
2. 🦉 Когда вам пишут в личный чат — бот автоматически составляет черновик ответа прямо в поле ввода.
3. ✏️ В черновике можно написать инструкцию — бот перепишет его, как только вы выйдете из чата.

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
- `SESSION_ENCRYPTION_KEY` — ключ `Fernet` для шифрования `session_string` перед сохранением в БД
- `EVM_PRIVATE_KEY` — приватный ключ кошелька Base с USDC для оплаты AI

Сгенерировать `SESSION_ENCRYPTION_KEY` можно так:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Опционально для отладки:
- `DEBUG_PRINT=true` — подробные логи в консоли (по умолчанию `false`)
- `LOG_TO_FILE=true` — сохранять запросы/ответы AI в `logs/` (по умолчанию `false`)

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
| `/status` | Статус подключения |
| `/connect` | Подключить Telegram-аккаунт через QR-код |
| `/disconnect` | Отключить аккаунт |

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

- **bot.py** — Точка входа: регистрация обработчиков и запуск бота
- **handlers/** — Обработчики команд (`bot_handlers.py` — `/start`, `on_text`) и событий Pyrogram (`pyrogram_handlers.py` — `/connect`, `on_pyrogram_message`, `on_pyrogram_draft`)
- **config.py** — Все константы и переменные окружения
- **prompts.py** — Все промпты для ИИ
- **system_messages.py** — Системные сообщения с переводом на язык пользователя
- **clients/** — API-клиенты (`x402gate`, `pyrogram_client`)
- **database/** — Запросы к Supabase (`upsert_user`, `get_user`, `save_session`)
- **utils/** — Утилиты (`bot_utils`, `pyrogram_utils`, `telegram_rating`, `utils`)
- **tests/** — Unit-тесты (pytest)

## Стек

- **Python 3.13**
- **python-telegram-bot** — Telegram Bot API
- **Pyrogram** — Telegram Client API (чтение сообщений, черновики)
- **x402gate.io** → OpenRouter → любая модель (задаётся в `config.py`, оплата USDC на Base)
- **Supabase** — PostgreSQL (БД)
- **Railway** — хостинг

## Стайлгайд

См. [CONTRIBUTING.md](CONTRIBUTING.md)

## Лицензия

MIT
