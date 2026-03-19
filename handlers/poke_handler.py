# handlers/poke_handler.py — Команда /poke: проактивные черновики по всем чатам

import asyncio
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import ContextTypes

from config import (
    ACTIVE_CHATS_LIMIT,
    DEBUG_PRINT,
    IGNORED_CHAT_IDS,
    POKE_FOLLOW_UP_TIMEOUT,
    STYLE_TO_EMOJI,
)
from clients import pyrogram_client
from database.users import get_user, update_last_msg_at
from handlers.pyrogram_handlers import (
    _bot_drafts, _bot_draft_echoes, _reply_locks,
    _generate_reply_for_chat, _is_user_typing,
)
from system_messages import get_system_message
from utils.utils import (
    get_effective_style,
    get_timestamp,
    is_chat_ignored,
    serialize_user_updates,
    typing_action,
)
from utils.telegram_user import ensure_effective_user


@serialize_user_updates
@typing_action
async def on_poke(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик /poke — сканирует чаты и создаёт черновики."""
    u = update.effective_user
    language_code = u.language_code

    try:
        await ensure_effective_user(update)
    except Exception:
        msg = await get_system_message(language_code, "error")
        await update.message.reply_text(msg)
        return

    asyncio.create_task(update_last_msg_at(u.id))

    if not pyrogram_client.is_active(u.id):
        msg = await get_system_message(language_code, "status_disconnected")
        await update.message.reply_text(msg)
        return

    if DEBUG_PRINT:
        print(f"{get_timestamp()} [POKE] /poke from user {u.id}")

    # Получаем список чатов
    chat_ids = await pyrogram_client.get_private_dialogs(u.id, limit=ACTIVE_CHATS_LIMIT)

    user = await get_user(u.id)
    user_settings = (user or {}).get("settings") or {}
    lang = (user or {}).get("language_code")

    now = datetime.now(tz=timezone.utc)
    checked = 0
    drafts = 0

    for chat_id in chat_ids:
        # Global ignore
        if chat_id == u.id or chat_id in IGNORED_CHAT_IDS:
            continue

        # Per-user ignore
        if is_chat_ignored(user_settings, chat_id):
            continue

        key = (u.id, chat_id)

        checked += 1

        # Уже есть черновик или идёт генерация — пропускаем
        if _reply_locks.get(key) or _bot_drafts.get(key):
            continue

        # Пустой чат или ошибка чтения — нечего анализировать
        last_msg = await pyrogram_client.get_last_message(u.id, chat_id)
        if not last_msg:
            continue

        if last_msg.outgoing:
            # Наше сообщение — проверяем таймаут follow-up
            msg_date = last_msg.date
            if isinstance(msg_date, datetime):
                if msg_date.tzinfo is None:
                    msg_date = msg_date.replace(tzinfo=timezone.utc)
                age = (now - msg_date).total_seconds()
                if age < POKE_FOLLOW_UP_TIMEOUT:
                    continue
            else:
                continue
        else:
            # Входящее от оппонента — нужен черновик
            # Пропускаем сообщения от ботов
            if last_msg.from_user and last_msg.from_user.is_bot:
                continue

        # Если пользователь уже печатает свой черновик, не перезаписываем его.
        if await _is_user_typing(u.id, chat_id):
            continue

        # Ставим пробу и запускаем генерацию параллельно
        style = get_effective_style(user_settings, chat_id)
        style_emoji = STYLE_TO_EMOJI.get(style, "🦉")
        probe_text = (await get_system_message(lang, "draft_typing")).format(emoji=style_emoji)
        _bot_draft_echoes[key] = probe_text
        await pyrogram_client.set_draft(u.id, chat_id, probe_text)

        drafts += 1
        asyncio.create_task(
            _generate_reply_for_chat(u.id, chat_id, user, user_settings, lang)
        )

        if DEBUG_PRINT:
            direction = "follow-up" if last_msg.outgoing else "unanswered"
            print(f"{get_timestamp()} [POKE] Generating {direction} draft for user {u.id} in chat {chat_id}")

    # Итоговое сообщение с конкретными цифрами
    result_key = "poke_result" if drafts else "poke_result_none"
    result_msg = await get_system_message(language_code, result_key)
    await update.message.reply_text(result_msg.format(checked=checked, drafts=drafts))
