# handlers/bot_handlers.py — Обработчики команд Telegram Bot API (/start, on_text)

import traceback

from telegram import Update
from telegram.ext import ContextTypes

from config import CUSTOM_PROMPT_MAX_LENGTH, DEBUG_PRINT, STYLE_PRO_MODELS, MAX_CONTEXT_MESSAGES
from utils.utils import get_timestamp, typing_action
from utils.bot_utils import update_user_menu
from utils.telegram_user import ensure_effective_user, upsert_effective_user
from clients.x402gate.openrouter import generate_response
from database.users import update_last_msg_at, update_tg_rating, update_user_settings
from utils.telegram_rating import extract_rating_from_chat
from system_messages import get_system_message, SYSTEM_MESSAGES
from clients import pyrogram_client


@typing_action
async def on_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /start."""
    u = update.effective_user

    if DEBUG_PRINT:
        print(f"{get_timestamp()} [BOT] /start from user {u.id} (@{u.username})")

    # Сохраняем пользователя в БД
    if not await upsert_effective_user(update):
        error_msg = await get_system_message(u.language_code, "error")
        await update.message.reply_text(error_msg or SYSTEM_MESSAGES["error"])
        return

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

    # Устанавливаем меню команд с учётом статуса подключения
    is_connected = pyrogram_client.is_active(u.id)
    await update_user_menu(context.bot, u.id, u.language_code, is_connected)


@typing_action
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик текстовых сообщений — генерирует ответ через ИИ."""
    u = update.effective_user
    m = update.message

    message_text = m.text or ""
    if not message_text.strip():
        return

    # Проверяем: пользователь вводит кастомный промпт?
    if context.user_data.get("awaiting_prompt"):
        prompt_text = message_text.strip()
        was_truncated = len(prompt_text) > CUSTOM_PROMPT_MAX_LENGTH
        if was_truncated:
            prompt_text = prompt_text[:CUSTOM_PROMPT_MAX_LENGTH]

        saved = await update_user_settings(u.id, {"custom_prompt": prompt_text})
        if not saved:
            error_msg = await get_system_message(u.language_code, "error")
            await m.reply_text(error_msg)
            return

        context.user_data.pop("awaiting_prompt", None)
        message_key = "settings_prompt_truncated" if was_truncated else "settings_prompt_saved"
        msg = await get_system_message(u.language_code, message_key)
        await m.reply_text(msg)
        if DEBUG_PRINT:
            print(f"{get_timestamp()} [BOT] Custom prompt saved for user {u.id}: {len(prompt_text)} chars")
        return

    try:
        user = await ensure_effective_user(update)

        # Обновляем last_msg_at только после гарантированного наличия записи пользователя.
        await update_last_msg_at(u.id)

        # История сообщений в чате с ботом (хранится в context.chat_data)
        history: list[dict] = context.chat_data.setdefault("history", [])

        if DEBUG_PRINT:
            print(
                f"{get_timestamp()} [BOT] Text from user {u.id}: "
                f"{len(message_text)} chars, history: {len(history)} messages"
            )

        # Читаем настройки пользователя для выбора модели
        user_settings = (user or {}).get("settings") or {}
        model = STYLE_PRO_MODELS[None] if user_settings.get("pro_model") else None

        # Генерируем ответ через OpenRouter с историей
        kwargs: dict = {"chat_history": history[-MAX_CONTEXT_MESSAGES:]}
        if model:
            kwargs["model"] = model
        response_text = await generate_response(message_text, **kwargs)

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
