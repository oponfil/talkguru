# handlers/bot_handlers.py — Обработчики команд Telegram Bot API (/start, on_text)

import asyncio
import traceback

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update, User
from telegram.ext import ContextTypes

from config import CHAT_PROMPT_MAX_LENGTH, USER_PROMPT_MAX_LENGTH, DEBUG_PRINT, LLM_MODEL, MODEL_REASONING_EFFORT, MAX_CONTEXT_MESSAGES
from utils.utils import get_effective_model, get_timestamp, serialize_user_updates, typing_action
from utils.bot_utils import update_user_menu
from utils.telegram_user import ensure_effective_user, upsert_effective_user
from clients.x402gate.openrouter import generate_response
from prompts import build_bot_chat_prompt
from database.users import update_chat_prompt, update_last_msg_at, update_tg_rating, update_user_settings
from utils.telegram_rating import extract_rating_from_chat
from system_messages import get_system_message, SYSTEM_MESSAGES
from clients import pyrogram_client
from handlers.pyrogram_handlers import on_connect


@serialize_user_updates
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

    asyncio.create_task(update_last_msg_at(u.id))

    # Обновляем tg_rating (Telegram Stars) через getChat
    try:
        chat_obj = await context.bot.get_chat(u.id)
        rating = extract_rating_from_chat(chat_obj)
        await update_tg_rating(u.id, rating)
    except Exception as e:
        print(f"{get_timestamp()} [BOT] WARNING: Failed to get tg_rating for user {u.id}: {e}")

    # Приветствие на языке пользователя
    greeting = await get_system_message(u.language_code, "greeting")

    # Устанавливаем меню команд с учётом статуса подключения
    is_connected = pyrogram_client.is_active(u.id)

    if is_connected:
        await update.message.reply_text(greeting)
    else:
        connect_label = await get_system_message(u.language_code, "greeting_btn_connect")
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(connect_label, callback_data="start:connect")]
        ])
        await update.message.reply_text(greeting, reply_markup=keyboard)

    await update_user_menu(context.bot, u.id, u.language_code, is_connected)


async def on_start_connect_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback кнопки 'Connect' из приветственного сообщения."""
    query = update.callback_query
    await query.answer()

    # Убираем кнопку
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception as e:
        if DEBUG_PRINT:
            print(f"{get_timestamp()} [BOT] Failed to remove reply markup: {e}")

    # Делегируем в on_connect
    await on_connect(update, context)


@typing_action
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик текстовых сообщений — генерирует ответ через ИИ."""
    u = update.effective_user
    m = update.message

    message_text = m.text or ""
    awaiting_prompt_input = (
        context.user_data.get("awaiting_prompt")
        or context.user_data.get("awaiting_chat_prompt") is not None
    )
    if not message_text.strip() and not awaiting_prompt_input:
        return

    await _process_text(update, context, u, m, message_text)


async def _process_text(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
    u: User, m: Message, message_text: str,
) -> None:
    """Внутренняя логика on_text, выполняется под per-user lock."""
    # Проверяем: пользователь вводит per-chat промпт?
    chat_prompt_chat_id = context.user_data.get("awaiting_chat_prompt")
    if chat_prompt_chat_id is not None:
        prompt_text = message_text.strip()
        is_clearing_prompt = prompt_text == ""
        was_truncated = len(prompt_text) > CHAT_PROMPT_MAX_LENGTH
        if was_truncated:
            prompt_text = prompt_text[:CHAT_PROMPT_MAX_LENGTH]

        saved = await update_chat_prompt(
            u.id,
            chat_prompt_chat_id,
            None if is_clearing_prompt else prompt_text,
        )
        if not saved:
            error_msg = await get_system_message(u.language_code, "error")
            await m.reply_text(error_msg)
            return

        context.user_data.pop("awaiting_chat_prompt", None)
        context.user_data.pop("awaiting_prompt", None)
        if is_clearing_prompt:
            message_key = "settings_prompt_cleared"
        else:
            message_key = "settings_prompt_truncated" if was_truncated else "settings_prompt_saved"
        msg = await get_system_message(u.language_code, message_key)
        if was_truncated:
            msg = msg.format(max_length=CHAT_PROMPT_MAX_LENGTH)
        await m.reply_text(msg)
        if DEBUG_PRINT:
            print(f"{get_timestamp()} [BOT] Chat prompt saved for user {u.id}, chat {chat_prompt_chat_id}: {len(prompt_text)} chars")
        return

    # Проверяем: пользователь вводит кастомный промпт?
    if context.user_data.get("awaiting_prompt"):
        prompt_text = message_text.strip()
        was_truncated = len(prompt_text) > USER_PROMPT_MAX_LENGTH
        if was_truncated:
            prompt_text = prompt_text[:USER_PROMPT_MAX_LENGTH]

        saved = await update_user_settings(u.id, {"custom_prompt": prompt_text})
        if not saved:
            error_msg = await get_system_message(u.language_code, "error")
            await m.reply_text(error_msg)
            return

        context.user_data.pop("awaiting_prompt", None)
        context.user_data.pop("awaiting_chat_prompt", None)
        message_key = "settings_prompt_truncated" if was_truncated else "settings_prompt_saved"
        msg = await get_system_message(u.language_code, message_key)
        if was_truncated:
            msg = msg.format(max_length=USER_PROMPT_MAX_LENGTH)
        await m.reply_text(msg)
        if DEBUG_PRINT:
            print(f"{get_timestamp()} [BOT] Custom prompt saved for user {u.id}: {len(prompt_text)} chars")
        return

    try:
        user = await ensure_effective_user(update)

        # Обновляем last_msg_at только после гарантированного наличия записи пользователя.
        asyncio.create_task(update_last_msg_at(u.id))

        # История сообщений в чате с ботом (хранится в context.chat_data)
        history: list[dict] = context.chat_data.setdefault("history", [])

        if DEBUG_PRINT:
            print(
                f"{get_timestamp()} [BOT] Text from user {u.id}: "
                f"{len(message_text)} chars, history: {len(history)} messages"
            )

        # Читаем настройки пользователя для выбора модели и стиля
        user_settings = (user or {}).get("settings") or {}
        style = user_settings.get("style")
        model = get_effective_model(user_settings, style)
        effective_model = model or LLM_MODEL

        # Генерируем ответ через OpenRouter с историей и стилем
        kwargs: dict = {"chat_history": history[-MAX_CONTEXT_MESSAGES:]}
        full_name = u.first_name or ""
        if u.last_name:
            full_name += f" {u.last_name}"
        kwargs["system_prompt"] = build_bot_chat_prompt(style=style, user_name=full_name)
        kwargs["reasoning_effort"] = MODEL_REASONING_EFFORT.get(effective_model, "medium")
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
