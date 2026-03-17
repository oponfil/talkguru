# config.py — Константы и настройки DraftGuru

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
EVM_WALLET_LOW_BALANCE_WARN = 10.0  # Порог предупреждения о низком балансе USDC на кошельке ($)
EVM_PRIVATE_KEY = os.getenv("EVM_PRIVATE_KEY", "")

# ====== ЯЗЫК ======
DEFAULT_LANGUAGE_CODE = "en"  # Язык по умолчанию (ISO 639-1)

# ====== МОДЕЛЬ ИИ ======
LLM_MODEL = "google/gemini-3.1-flash-lite-preview"  # FREE-модель (по умолчанию)
# PRO-модель для каждого стиля. Используется при pro_model=True.
STYLE_PRO_MODELS: dict[str, str] = {
    "userlike": "openai/gpt-5.4",
    "friend": "openai/gpt-5.4",
    "romance": "openai/gpt-5.4",
    "seducer": "google/gemini-3.1-pro-preview",
    "business": "openai/gpt-5.4",
    "sales": "openai/gpt-5.4",
    "paranoid": "openai/gpt-5.4",
}
# Уровень reasoning для конкретных моделей (minimal/low/medium/high).
# Если модели нет в словаре — используется "medium" по умолчанию.
MODEL_REASONING_EFFORT: dict[str, str] = {
    "google/gemini-3.1-pro-preview": "low",
}

# ====== RETRY ======
RETRY_ATTEMPTS = 2  # Количество повторных попыток
RETRY_DELAY = 2.0  # Базовая задержка (секунды)
RETRY_EXPONENTIAL_BASE = 2.0  # База экспоненциальной задержки

# ====== ЛОКАЛИЗАЦИЯ ======
SYSTEM_MESSAGE_TRANSLATION_TIMEOUT = 60
SYSTEM_MESSAGES_FALLBACK_TTL_SECONDS = 300.0

# ====== КОНТЕКСТ ======
MAX_CONTEXT_MESSAGES = 40  # Макс. кол-во сообщений из чата для контекста

# ====== QR LOGIN ======
QR_LOGIN_TIMEOUT_SECONDS = 120  # Таймаут ожидания сканирования QR-кода (секунды)
QR_LOGIN_POLL_INTERVAL = 2  # Интервал проверки сканирования (секунды)

# ====== PHONE LOGIN ======
PHONE_CODE_TIMEOUT_SECONDS = 120  # Таймаут на ввод кода при phone-логине (секунды)

# ====== DRAFT INTERACTION ======
DRAFT_PROBE_DELAY = 2  # Секунды ожидания после пробы (draft_typing)
DRAFT_VERIFY_DELAY = 3  # Секунды до проверки доставки AI-черновика
POLL_MISSED_INTERVAL = 60  # Интервал проверки пропущенных сообщений (секунды)
POLL_MISSED_DIALOGS_LIMIT = 10  # Кол-во последних приватных чатов для проверки

# ====== TELEGRAM BOT ======
BOT_READ_TIMEOUT = 30  # Таймаут чтения ответа от Telegram API (секунды)

# ====== НАСТРОЙКИ ======
CUSTOM_PROMPT_MAX_LENGTH = 600  # Макс. длина пользовательского промпта (символы)

# ====== ГОЛОСОВЫЕ СООБЩЕНИЯ ======
VOICE_TRANSCRIPTION_TIMEOUT = 60  # Таймаут ожидания транскрипции (секунды)

# ====== СТИКЕРЫ ======
STICKER_FALLBACK_EMOJI = "□"  # Fallback для стикеров без привязанного эмодзи (U+25A1)

# ====== АВТООТВЕТ ======
# {секунды: ключ сообщения} — None = выключено (по умолчанию)
AUTO_REPLY_OPTIONS: dict[int | None, str] = {
    None: "settings_auto_reply_off",
    60: "settings_auto_reply_1m",
    300: "settings_auto_reply_5m",
    900: "settings_auto_reply_15m",
    3600: "settings_auto_reply_1h",
    57600: "settings_auto_reply_16h",
}

# ====== СТИЛЬ ОБЩЕНИЯ ======

# Маппинг emoji → стиль (единый источник правды для emoji↔style)
EMOJI_TO_STYLE: dict[str, str] = {
    "🦉": "userlike",
    "🍻": "friend",
    "💕": "romance",
    "💼": "business",
    "💰": "sales",
    "🕵️": "paranoid",
    "😈": "seducer",
}

# Стиль по умолчанию — первый в EMOJI_TO_STYLE
DEFAULT_STYLE: str = next(iter(EMOJI_TO_STYLE.values()))

# Обратный маппинг стиль → emoji
STYLE_TO_EMOJI: dict[str, str] = {v: k for k, v in EMOJI_TO_STYLE.items()}

# {значение: ключ сообщения}
def _style_msg_key(style: str) -> str:
    return f"settings_style_{style}"

# Отображаемое имя стиля (из ключа)
def style_display_name(style: str) -> str:
    return style.title()

STYLE_OPTIONS: dict[str, str] = {
    style: _style_msg_key(style) for style in EMOJI_TO_STYLE.values()
}

# Количество чатов в /styles
CHAT_STYLES_DIALOGS_LIMIT = 10

# ====== ЧАСОВОЙ ПОЯС ======
# 30 популярных UTC-смещений (часы); дробные: +3.5 Иран, +4.5 Афганистан,
# +5.5 Индия, +9.5 Центральная Австралия
TIMEZONE_OFFSETS: list[float] = [
    -12, -11, -10, -9, -8, -7, -6, -5, -4, -3, -2, -1,
    0, 1, 2, 3, 3.5, 4, 4.5, 5, 5.5,
    6, 7, 8, 9, 9.5, 10, 11, 12, 13,
]
