# TalkGuru 🦉

Опенсорсный Telegram-бот, который пишет черновик ответа за вас.

## Как работает

1. 🔌 Подключите аккаунт через `/connect` (QR-код).
2. 🦉 Когда вам пишут в личный чат — бот автоматически составляет черновик ответа прямо в поле ввода.
3. ✏️ В черновике можно написать инструкцию — бот перепишет его, как только вы выйдете из чата.

Авто-ответы работают только в личных чатах. Черновики-инструкции работают везде: в личных чатах, группах и супергруппах.

## Быстрый старт

### 1. Клонировать и установить зависимости

```bash
git clone https://github.com/oponfil/talkguru.git
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
- `LOG_TO_FILE=true` — сохранять полные запросы/ответы AI в `logs/` для локальной отладки (по умолчанию `false`)

Важно: на продакшене `LOG_TO_FILE` должен оставаться выключенным, потому что в лог будут записываться полные prompt'ы, история переписки и ответы модели.

Для локализации системных сообщений:
- `SYSTEM_MESSAGE_TRANSLATION_TIMEOUT` — таймаут запроса перевода (секунды, по умолчанию `60`)
- `SYSTEM_MESSAGES_FALLBACK_TTL_SECONDS` — TTL английского fallback-кэша при сбое перевода (секунды, по умолчанию `300`)

Дополнительно:
- `BOT_READ_TIMEOUT` — таймаут чтения ответа от Telegram Bot API (секунды, по умолчанию `30`)

Примечание по логике перевода: сообщения кэшируются по языку. При временной ошибке перевода бот использует английский fallback и кэширует его на 5 минут, после чего автоматически пробует перевод снова.

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
| `/connect` | Подключить Telegram-аккаунт через QR-код (поддерживает 2FA) |
| `/disconnect` | Отключить аккаунт (идемпотентно: останавливает listener и очищает сессию в БД) |
| `/settings` | Настройки: включение/выключение драфтов, выбор модели (FREE/PRO), пользовательский промпт |

Меню команд динамическое: `/connect` и `/disconnect` показываются в зависимости от статуса подключения.

При `2FA` бот попросит cloud password отдельным сообщением в личке и сразу удалит его после попытки логина.

### Настройки (`/settings`)

| Настройка | Описание | По умолчанию |
|-----------|----------|:------------:|
| **Drafts** (✏️) | Включение/выключение обработки черновиков-инструкций. Когда выключено, бот не редактирует черновики по инструкциям, но продолжает создавать авто-ответы на входящие сообщения. Работает в личных чатах, группах и супергруппах. | ✅ ON |
| **Model** (🤖) | Выбор модели ИИ: FREE (Gemini 3.1 Flash Lite) или PRO (GPT-5.4). Применяется ко всем генерациям — автоответы, черновики и чат с ботом. | FREE |
| **Prompt** (📝) | Пользовательский системный промпт (макс. 900 символов). Добавляется к системным инструкциям для авто-ответов и черновиков. Текст промпта отображается в сообщении настроек. | Не задан |

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

## Безопасность

- `SESSION_STRING` — bearer credential. Скрипты `scripts/generate_session.py` и `scripts/generate_session_qr.py` не показывают его в терминале, пока пользователь явно не введет `YES`.
- Не храните `SESSION_STRING` в shell history, логах и чатах.

## Архитектура

- **bot.py** — Точка входа: регистрация обработчиков и запуск бота
- **handlers/** — Обработчики команд (`bot_handlers.py` — `/start`, `on_text`; `settings_handler.py` — `/settings`) и событий Pyrogram (`pyrogram_handlers.py` — `/connect`, `on_pyrogram_message`, `on_pyrogram_draft`)
- **config.py** — Все константы и переменные окружения
- **prompts.py** — Все промпты для ИИ (`build_reply_prompt`, `build_draft_prompt`)
- **system_messages.py** — Системные сообщения с переводом на язык пользователя
- **clients/** — API-клиенты (`x402gate`, `pyrogram_client`)
- **database/** — Запросы к Supabase (`upsert_user`, `get_user`, `save_session`, `update_user_settings`)
- **utils/** — Утилиты (`bot_utils`, `pyrogram_utils`, `telegram_rating`, `utils`)
- **migrations/** — SQL-миграции
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
