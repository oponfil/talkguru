# handlers/bot_handlers.py — Обработчики команд Telegram Bot API (/start, on_text)

import traceback

from telegram import Update
from telegram.ext import ContextTypes

from config import DEBUG_PRINT, MAX_CONTEXT_MESSAGES
from utils.utils import get_timestamp, typing_action
from utils.bot_utils import update_menu_language
from clients.x402gate.openrouter import generate_response
from database.users import upsert_user, update_last_msg_at, update_tg_rating
from utils.telegram_rating import extract_rating_from_chat
from system_messages import get_system_message, SYSTEM_MESSAGES


@typing_action
async def on_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /start."""
    u = update.effective_user

    if DEBUG_PRINT:
        print(f"{get_timestamp()} [BOT] /start from user {u.id} (@{u.username})")

    # Сохраняем пользователя в БД
    await upsert_user(
        user_id=u.id,
        username=u.username,
        first_name=u.first_name,
        last_name=u.last_name,
        is_bot=u.is_bot,
        is_premium=bool(u.is_premium),
        language_code=u.language_code,
    )

    # Обновляем tg_rating (Telegram Stars) через getChat
    try:
        chat_obj = await context.bot.get_chat(u.id)
        rating = extract_rating_from_chat(chat_obj)
        await update_tg_rating(u.id, rating)
    except Exception as e:
        print(f"{get_timestamp()} [BOT] WARNING: Failed to get tg_rating for user {u.id}: {e}")

    # Приветствие на языке пользователя
    greeting = await get_system_message(u.language_code, "greeting")
    await update.message.reply_text(greeting)

    # Устанавливаем меню команд на языке пользователя
    await update_menu_language(context.bot, u.language_code)


@typing_action
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик текстовых сообщений — генерирует ответ через ИИ."""
    u = update.effective_user
    m = update.message

    message_text = m.text or ""
    if not message_text.strip():
        return

    # Обновляем last_msg_at
    await update_last_msg_at(u.id)

    # История сообщений в чате с ботом (хранится в context.chat_data)
    history: list[dict] = context.chat_data.setdefault("history", [])

    if DEBUG_PRINT:
        print(
            f"{get_timestamp()} [BOT] Text from user {u.id}: "
            f"{len(message_text)} chars, history: {len(history)} messages"
        )

    try:
        # Генерируем ответ через OpenRouter с историей
        response_text = await generate_response(
            message_text,
            chat_history=history[-MAX_CONTEXT_MESSAGES:],
        )

        # Сохраняем в историю
        history.append({"role": "user", "content": message_text})
        history.append({"role": "assistant", "content": response_text})

        # Обрезаем историю, чтобы не разрастался
        if len(history) > MAX_CONTEXT_MESSAGES:
            del history[: len(history) - MAX_CONTEXT_MESSAGES]

        # Отправляем ответ
        await m.reply_text(response_text)

        if DEBUG_PRINT:
            print(f"{get_timestamp()} [BOT] Response sent to user {u.id}")

    except Exception as e:
        print(f"{get_timestamp()} [BOT] ERROR generating response for user {u.id}: {e}")
        traceback.print_exc()
        error_msg = await get_system_message(u.language_code, "error")
        await m.reply_text(error_msg or SYSTEM_MESSAGES["error"])
