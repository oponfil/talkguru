# system_messages.py — Системные сообщения с механизмом перевода на язык пользователя

import asyncio
import json
import re
import time
from typing import Dict

from clients.x402gate.openrouter import generate_response
from config import (
    CHAT_PROMPT_MAX_LENGTH,
    USER_PROMPT_MAX_LENGTH,
    DEFAULT_LANGUAGE_CODE,
    DEBUG_PRINT,
    EMOJI_TO_STYLE,
    style_display_name,
    SYSTEM_MESSAGES_FALLBACK_TTL_SECONDS,
    SYSTEM_MESSAGE_TRANSLATION_TIMEOUT,
)
from prompts import TRANSLATE_MESSAGES_PROMPT
from utils.utils import get_timestamp


# ====== Системные сообщения (на английском, переводятся для пользователя) ======
SYSTEM_MESSAGES = {
    # — General —
    "greeting": (
        "👋 Hey! I'm DraftGuru — an open-source Telegram bot that safely helps you reply to messages using AI.\n\n"
        "How to use:\n"
        "1. 🔌 Connect your account via /connect\n"
        "2. 🦉 When someone messages you, I will automatically compose a draft reply in your text input field.\n"
        "3. ✏️ Don't like the draft? Write an instruction inside it, and I'll rewrite it when you close the chat.\n\n"
        "🔐 Privacy & Security:\n"
        "• I only write drafts. I do NOT send messages automatically (unless you configure a timer).\n"
        "• I NEVER store your chat history.\n"
        "• I completely ignore your 'Saved Messages' chat.\n\n"
        "💻 GitHub: https://github.com/oponfil/draftguru\n"
        "🔞 For users 18+ only."
    ),
    "error": "⚠️ An error occurred. Please try again later.",
    "greeting_btn_connect": "🔌 Connect Account",

    # — Connect / Disconnect —
    "connect_success": "✅ Account connected! I'll now suggest replies to your incoming messages as drafts.",
    "connect_error": "🔌 Failed to connect your account. Please try again with /connect.",
    "connect_already": "✅ Your account is already connected.",
    "connect_in_progress": "⏳ Connection is already in progress. Please finish the current QR login or wait for it to expire.",
    "disconnect_success": "🔌 Account disconnected. I'll no longer write drafts for you.",
    "disconnect_error": "⚠️ Failed to fully disconnect your account. Please try /disconnect again.",
    "connect_scan": "📱 Open Telegram on your phone → Settings → Devices → Link Desktop Device.\n\nScan this QR code with your phone camera. You have 2 minutes.",
    "connect_timeout": "⏰ QR code expired. Please try /connect again.",
    "connect_2fa_error": "🔐 Failed to complete 2FA login. Please try /connect again.",
    "connect_2fa_prompt": "🔐 Your account has a cloud password (2FA). Please send your cloud password as a message.\n\n⚠️ The password will be used once for login and will NOT be stored:",
    "connect_2fa_wrong_password": "❌ Wrong password. Please try again — send your cloud password as a message:",

    # — Connect: Phone flow —
    "connect_phone_prompt": "📱 Send your phone number in international format (e.g. +1234567890).\n\n⚠️ The number will be used once for login and will NOT be stored.",
    "connect_phone_btn_qr": "📷 Connect via QR code",
    "connect_code_prompt": "📲 Enter the confirmation code you received from Telegram.\n\n⚠️ IMPORTANT: Add any character or space anywhere in the code.\nExample: 12-345 or 1234 5",
    "connect_code_invalid": "❌ Invalid code. Please try again.\nRemember: add any character or space in the code (e.g. 12-345):",
    "connect_code_no_separator": "⚠️ It looks like you entered the code without separators.\nNext time add any character or space in the code (e.g. 12-345).",
    "connect_code_blocked": "🚫 Telegram has blocked this code. Please try /connect again.",
    "connect_code_expired": "⏰ Code expired. Please try /connect again.",
    "connect_phone_invalid": "❌ Invalid phone number. Please send your number in international format (e.g. +1234567890):",
    "connect_phone_confirm": "📱 You entered: {phone_number}\n\nIs this number correct?",
    "connect_phone_btn_confirm": "✅ Yes, that's correct",
    "connect_phone_btn_cancel": "❌ No, re-enter",
    "connect_btn_cancel": "❌ Cancel",
    "connect_phone_timeout": "⏰ Login timed out. Please try /connect again.",
    "connect_flood_wait": "⏳ Too many attempts. Please wait {seconds} seconds and try /connect again.",

    # — Status —
    "status_connected": "✅ Your account is connected. I'm suggesting replies to your incoming messages as drafts.",
    "status_disconnected": "🔌 Your account is not connected. Use /connect to connect.",

    # — Menu —
    "menu_start": "Start",
    "menu_connect": "Connect account",
    "menu_disconnect": "Disconnect account",
    "menu_status": "Connection status",
    "menu_settings": "Settings",

    # — Drafts —
    "draft_typing": "{emoji} is typing...",

    # — Settings —
    "settings_title": "⚙️ Settings\nTap buttons to change.",
    "settings_drafts_on": "✏️ Draft editing: ✅ ON",
    "settings_drafts_off": "✏️ Draft editing: ❌ OFF",
    "settings_model_free": "🤖 Model: FREE",
    "settings_model_pro": "🤖 Model: ⭐ PRO",
    "settings_prompt_set": "📝 Prompt: ✅ ON",
    "settings_prompt_empty": "📝 Prompt: ❌ OFF",
    "settings_prompt_saved": "✅ Prompt saved!",
    "settings_prompt_truncated": "⚠️ Prompt was too long, so I saved only the first {max_length} characters.",
    "settings_prompt_cleared": "🧹 Prompt cleared.",
    "settings_prompt_current": f"📝 Global prompt (all chats):\n«{{prompt}}»\n\nSend a new prompt to replace it (Max length: {USER_PROMPT_MAX_LENGTH} chars):",
    "settings_prompt_no_prompt": f"📝 Global prompt (all chats): not set.\n\nSend a prompt to set it (Max length: {USER_PROMPT_MAX_LENGTH} chars):",
    # — Auto-reply labels (base, without prefix) —
    "auto_reply_prefix": "Auto-reply",
    "auto_reply_off": "✅ OFF",
    "auto_reply_1m": "⚠️ 1 min",
    "auto_reply_5m": "⚠️ 5 min",
    "auto_reply_15m": "⚠️ 15 min",
    "auto_reply_1h": "⚠️ 1 hour",
    "auto_reply_16h": "⚠️ 16 hours",
    "auto_reply_ignore": "🔇 Ignore",
    "settings_timezone_back": "🕐 Time",

    # — Settings: Style — (генерируется из EMOJI_TO_STYLE)
    **{
        f"settings_style_{style or 'userlike'}": f"{emoji} Style: {style_display_name(style)}"
        for emoji, style in EMOJI_TO_STYLE.items()
    },

    # — Chats (per-chat settings) —
    "menu_chats": "Chat settings",
    "chats_title": "🎭 Chats\nTap to change prompt, style, or auto-reply timer.",
    "chats_not_connected": "🔌 Connect your account first with /connect.",
    "chats_no_chats": "No active chats found. Start a conversation first.",
    "chats_prompt_set": "📝: ✅ ON",
    "chats_prompt_empty": "📝: ❌ OFF",


    # — Chats: per-chat prompt —
    "chats_prompt_current": f"📝 Prompt for {{chat_name}}:\n«{{prompt}}»\n\nSend a new prompt to replace it (Max length: {CHAT_PROMPT_MAX_LENGTH} chars):",
    "chats_prompt_no_prompt": f"📝 Prompt for {{chat_name}}: not set.\n\nSend a prompt to set it (Max length: {CHAT_PROMPT_MAX_LENGTH} chars):",

    # — Prompt editor buttons —
    "prompt_cancel": "❌ Cancel",
    "prompt_clear": "🗑 Clear",
}


# ====== Кэш переводов ======
_messages_cache: Dict[str, Dict[str, str]] = {DEFAULT_LANGUAGE_CODE: SYSTEM_MESSAGES}
_messages_locks: Dict[str, asyncio.Lock] = {}
# Для fallback-кэша (английские тексты при сбое перевода) храним время жизни по языку.
# Если язык есть в этом словаре — значит в _messages_cache лежит временный fallback.
_fallback_cache_expiry: Dict[str, float] = {}
# Временный fallback живет ограниченное время: после TTL пробуем перевод заново.
# Значение TTL задается в config.py.


def _get_cached_messages(lang: str) -> Dict[str, str] | None:
    """Возвращает кэш языка, учитывая TTL для fallback-значений."""
    cached = _messages_cache.get(lang)
    if cached is None:
        return None

    # Для обычного (успешного) перевода TTL не применяется:
    # он хранится в кэше до рестарта процесса.
    fallback_expires_at = _fallback_cache_expiry.get(lang)
    if fallback_expires_at is not None and time.monotonic() >= fallback_expires_at:
        # Fallback протух — удаляем его, чтобы следующий запрос повторил перевод.
        _messages_cache.pop(lang, None)
        _fallback_cache_expiry.pop(lang, None)
        return None

    return cached


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

        result_text = await asyncio.wait_for(
            generate_response(prompt, system_prompt=None),
            timeout=SYSTEM_MESSAGE_TRANSLATION_TIMEOUT,
        )

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

    except asyncio.TimeoutError:
        print(
            f"{get_timestamp()} [TRANSLATE] Translation to {language_code} "
            f"timed out after {SYSTEM_MESSAGE_TRANSLATION_TIMEOUT}s"
        )
        return None
    except Exception as e:
        print(f"{get_timestamp()} [TRANSLATE] Error translating to {language_code}: {e}")
        return None


async def get_system_messages(language_code: str | None) -> Dict[str, str]:
    """Возвращает все системные сообщения на указанном языке (с кэшированием)."""
    lang = (language_code or DEFAULT_LANGUAGE_CODE).lower()

    # Быстрый путь: если перевод/фоллбек уже есть в кэше.
    cached = _get_cached_messages(lang)
    if cached is not None:
        return cached

    # Лок на язык предотвращает штурм LLM:
    # параллельные запросы одного языка не запускают дублирующий перевод.
    lock = _messages_locks.setdefault(lang, asyncio.Lock())
    async with lock:
        # Double-check после захвата лока
        cached = _get_cached_messages(lang)
        if cached is not None:
            return cached

        keys = list(SYSTEM_MESSAGES.keys())
        source_values = [SYSTEM_MESSAGES[k] for k in keys]
        translated_values = await translate_messages(source_values, lang)

        if translated_values is None:
            # Быстро деградируем до английского и кэшируем fallback на ограниченное
            # время, чтобы избежать постоянных вызовов LLM при временной деградации.
            # Важно: fallback НЕ вечный — после TTL перевод будет запрошен повторно.
            fallback_messages = dict(zip(keys, source_values))
            _messages_cache[lang] = fallback_messages
            _fallback_cache_expiry[lang] = time.monotonic() + SYSTEM_MESSAGES_FALLBACK_TTL_SECONDS
            return fallback_messages

        # Успешный перевод кэшируем как "нормальный" без срока годности fallback.
        translated_dict = dict(zip(keys, translated_values))
        _messages_cache[lang] = translated_dict
        _fallback_cache_expiry.pop(lang, None)
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
