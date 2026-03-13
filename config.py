# config.py — Константы и настройки TalkGuru

import os
from dotenv import load_dotenv
load_dotenv()


# ====== TELEGRAM ======
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
if not BOT_TOKEN:
    print("⚠️  WARNING: BOT_TOKEN не задан!")

# ====== ОТЛАДКА ======
DEBUG_PRINT = os.getenv("DEBUG_PRINT", "False").lower() in ("true", "1", "yes")

# ====== БАЗА ДАННЫХ (Supabase) ======
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
if not SUPABASE_URL or not SUPABASE_KEY:
    print("⚠️  WARNING: SUPABASE_URL или SUPABASE_KEY не заданы!")

# ====== x402gate.io (оплата USDC на Base) ======
X402GATE_URL = "https://x402gate.io"
X402GATE_TIMEOUT = 300  # Таймаут запроса (секунды)
X402GATE_PREPAID_TOPUP_AMOUNT = 0.5  # Сумма пополнения prepaid ($)
X402GATE_PREPAID_MIN_BALANCE = 0.10  # Минимальный порог баланса ($)
EVM_PRIVATE_KEY = os.getenv("EVM_PRIVATE_KEY", "")

# ====== ЯЗЫК ======
DEFAULT_LANGUAGE_CODE = "en"  # Язык по умолчанию (ISO 639-1)

# ====== МОДЕЛЬ ИИ ======
CHAT_MODEL = "google/gemini-3.1-flash-lite-preview"  # Основная модель через OpenRouter
TRANSLATION_MODEL = "openai/gpt-5-nano"  # Модель для перевода системных сообщений

# ====== RETRY ======
RETRY_ATTEMPTS = 3  # Количество повторных попыток
RETRY_DELAY = 2.0  # Базовая задержка (секунды)
RETRY_EXPONENTIAL_BASE = 2.0  # База экспоненциальной задержки
