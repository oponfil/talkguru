# config.py — Константы и настройки TalkGuru

import os

from dotenv import load_dotenv

load_dotenv()


# ====== TELEGRAM ======
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
if not BOT_TOKEN:
    print("⚠️  WARNING: BOT_TOKEN не задан!")

# ====== PYROGRAM (Client API) ======
PYROGRAM_API_ID = int(os.getenv("PYROGRAM_API_ID", "0"))
PYROGRAM_API_HASH = os.getenv("PYROGRAM_API_HASH", "")
if not PYROGRAM_API_ID or not PYROGRAM_API_HASH:
    print("⚠️  WARNING: PYROGRAM_API_ID или PYROGRAM_API_HASH не заданы!")

# ====== ОТЛАДКА ======
DEBUG_PRINT = os.getenv("DEBUG_PRINT", "False").lower() in ("true", "1", "yes")
LOG_TO_FILE = os.getenv("LOG_TO_FILE", "False").lower() in ("true", "1", "yes")

# ====== БАЗА ДАННЫХ (Supabase) ======
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
if not SUPABASE_URL or not SUPABASE_KEY:
    print("⚠️  WARNING: SUPABASE_URL или SUPABASE_KEY не заданы!")

# ====== ШИФРОВАНИЕ СЕССИЙ ======
SESSION_ENCRYPTION_KEY = os.getenv("SESSION_ENCRYPTION_KEY", "")
if not SESSION_ENCRYPTION_KEY:
    print("⚠️  WARNING: SESSION_ENCRYPTION_KEY не задан! Шифрование сессий недоступно.")

# ====== x402gate.io (оплата USDC на Base) ======
X402GATE_URL = "https://x402gate.io"
X402GATE_TIMEOUT = 120  # Таймаут запроса (секунды)
X402GATE_PREPAID_TOPUP_AMOUNT = 0.5  # Сумма пополнения prepaid ($)
X402GATE_PREPAID_MIN_BALANCE = 0.10  # Минимальный порог баланса ($)
EVM_PRIVATE_KEY = os.getenv("EVM_PRIVATE_KEY", "")

# ====== ЯЗЫК ======
DEFAULT_LANGUAGE_CODE = "en"  # Язык по умолчанию (ISO 639-1)

# ====== МОДЕЛЬ ИИ ======
LLM_MODEL = "google/gemini-3.1-flash-lite-preview"  # FREE-модель (по умолчанию)
LLM_MODEL_PRO = "openai/gpt-5.4"  # PRO-модель

# ====== RETRY ======
RETRY_ATTEMPTS = 2  # Количество повторных попыток
RETRY_DELAY = 2.0  # Базовая задержка (секунды)
RETRY_EXPONENTIAL_BASE = 2.0  # База экспоненциальной задержки

# ====== ЛОКАЛИЗАЦИЯ ======
SYSTEM_MESSAGE_TRANSLATION_TIMEOUT = 60
SYSTEM_MESSAGES_FALLBACK_TTL_SECONDS = 300.0

# ====== КОНТЕКСТ ======
MAX_CONTEXT_MESSAGES = 100  # Макс. кол-во сообщений из чата для контекста

# ====== QR LOGIN ======
QR_LOGIN_TIMEOUT_SECONDS = 120  # Таймаут ожидания сканирования QR-кода (секунды)
QR_LOGIN_POLL_INTERVAL = 2  # Интервал проверки сканирования (секунды)

# ====== DRAFT INTERACTION ======
DRAFT_PROBE_DELAY = 2  # Секунды ожидания после пробы (draft_typing)

# ====== TELEGRAM BOT ======
BOT_READ_TIMEOUT = 30  # Таймаут чтения ответа от Telegram API (секунды)

# ====== НАСТРОЙКИ ======
CUSTOM_PROMPT_MAX_LENGTH = 900  # Макс. длина пользовательского промпта (символы)

# ====== ГОЛОСОВЫЕ СООБЩЕНИЯ ======
VOICE_TRANSCRIPTION_TIMEOUT = 60  # Таймаут ожидания транскрипции (секунды)

# ====== АВТООТВЕТ ======
# {секунды: ключ сообщения} — None = выключено (по умолчанию)
AUTO_REPLY_OPTIONS: dict[int | None, str] = {
    None: "settings_auto_off",
    60: "settings_auto_1m",
    300: "settings_auto_5m",
    900: "settings_auto_15m",
    3600: "settings_auto_1h",
    57600: "settings_auto_16h",
}

# ====== СТИЛЬ ОБЩЕНИЯ ======
# {значение: ключ сообщения} — None = под пользователя (по умолчанию)
STYLE_OPTIONS: dict[str | None, str] = {
    None: "settings_style_userlike",
    "flirt": "settings_style_flirt",
    "business": "settings_style_business",
    "sales": "settings_style_sales",
    "friend": "settings_style_friend",
}
