# system_messages.py — Системные сообщения с механизмом перевода на язык пользователя

import asyncio
import json
import re
from typing import Dict

from clients.x402gate.openrouter import generate_response
from config import DEFAULT_LANGUAGE_CODE, DEBUG_PRINT
from prompts import TRANSLATE_MESSAGES_PROMPT
from utils.utils import get_timestamp


# ====== Системные сообщения (на английском, переводятся для пользователя) ======
SYSTEM_MESSAGES = {
    "greeting": "👋 Hey! I'm TalkGuru — an assistant that helps you write responses to messages.\n\nJust send me a text, and I'll suggest a reply!",
    "error": "⚠️ An error occurred. Please try again later.",
    "connect_success": "✅ Account connected! I'll now suggest replies to your incoming messages as drafts.",
    "connect_error": "🔌 Failed to connect your account. Please try again with /connect.",
    "connect_already": "✅ Your account is already connected.",
    "disconnect_success": "🔌 Account disconnected. I'll no longer suggest replies.",
    "connect_scan": "📱 Open Telegram on your phone → Settings → Devices → Link Desktop Device.\n\nScan this QR code with your phone camera. You have 2 minutes.",
    "connect_timeout": "⏰ QR code expired. Please try /connect again.",
    "connect_2fa_error": "🔐 Your account has 2FA enabled. QR login doesn't support 2FA yet. Please try again later.",
    "status_connected": "✅ Your account is connected. I'm suggesting replies to your incoming messages as drafts.",
    "status_disconnected": "🔌 Your account is not connected. Use /connect to connect.",
    "menu_start": "Start",
    "menu_connect": "Connect account",
    "menu_disconnect": "Disconnect account",
    "menu_status": "Connection status",
    "draft_typing": "🦉 is typing...",
}


# ====== Кэш переводов ======
_messages_cache: Dict[str, Dict[str, str]] = {DEFAULT_LANGUAGE_CODE: SYSTEM_MESSAGES}
_messages_locks: Dict[str, asyncio.Lock] = {}


async def translate_messages(messages: list[str], language_code: str) -> list[str] | None:
    """Переводит список строк на указанный язык через OpenRouter (x402gate).

    Args:
        messages: Список строк для перевода
        language_code: Код языка (ISO 639-1)

    Returns:
        Список переведённых строк или None при ошибке
    """
    if language_code == DEFAULT_LANGUAGE_CODE or not messages:
        return messages

    try:
        messages_json = json.dumps(messages, ensure_ascii=False, indent=2)
        prompt = TRANSLATE_MESSAGES_PROMPT.format(
            language_code=language_code,
            message_count=len(messages),
            messages_json=messages_json,
        )

        result_text = await generate_response(prompt, system_prompt=None)

        # Убираем markdown-обёртку (```json ... ```)
        if result_text.startswith("```"):
            result_text = re.sub(r'^```(?:json)?\s*\n?', '', result_text)
            result_text = re.sub(r'\n?\s*```\s*$', '', result_text)

        translated = json.loads(result_text)

        if isinstance(translated, list) and len(translated) == len(messages) and all(isinstance(s, str) for s in translated):
            if DEBUG_PRINT:
                print(f"{get_timestamp()} [TRANSLATE] Translated {len(messages)} messages to {language_code}")
            return translated

        print(f"{get_timestamp()} [TRANSLATE] Invalid response: expected {len(messages)} items, got {len(translated) if isinstance(translated, list) else 'non-list'}")
        return None

    except Exception as e:
        print(f"{get_timestamp()} [TRANSLATE] Error translating to {language_code}: {e}")
        return None


async def get_system_messages(language_code: str | None) -> Dict[str, str]:
    """Возвращает все системные сообщения на указанном языке (с кэшированием)."""
    lang = (language_code or DEFAULT_LANGUAGE_CODE).lower()

    if lang in _messages_cache:
        return _messages_cache[lang]

    lock = _messages_locks.setdefault(lang, asyncio.Lock())
    async with lock:
        # Double-check после захвата лока
        if lang in _messages_cache:
            return _messages_cache[lang]

        keys = list(SYSTEM_MESSAGES.keys())
        source_values = [SYSTEM_MESSAGES[k] for k in keys]
        translated_values = await translate_messages(source_values, lang)

        if translated_values is None:
            # Ошибка перевода — возвращаем оригиналы, не кэшируем
            return dict(zip(keys, source_values))

        translated_dict = dict(zip(keys, translated_values))
        _messages_cache[lang] = translated_dict
        return translated_dict


async def get_system_message(language_code: str | None, key: str) -> str:
    """Возвращает одно системное сообщение по ключу на языке пользователя."""
    messages = await get_system_messages(language_code)
    if key in messages and messages[key]:
        return messages[key]

    # Fallback на английский
    default_messages = await get_system_messages(DEFAULT_LANGUAGE_CODE)
    if key in default_messages and default_messages[key]:
        return default_messages[key]

    return SYSTEM_MESSAGES.get(key, "")
