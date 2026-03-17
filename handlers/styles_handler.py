# handlers/styles_handler.py — Обработчик команды /styles (per-chat стили)

import asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from clients import pyrogram_client
from config import (
    CHAT_STYLES_DIALOGS_LIMIT,
    DEBUG_PRINT,
    DEFAULT_STYLE,
    STYLE_OPTIONS,
    STYLE_TO_EMOJI,
)
from database.users import get_user, update_chat_style, update_last_msg_at
from system_messages import get_system_message
from utils.utils import get_effective_style, get_timestamp, typing_action


def _style_emoji(style: str | None) -> str:
    """Возвращает emoji для стиля."""
    return STYLE_TO_EMOJI.get(style, "🦉")


def _chat_display_name(dialog_info: dict) -> str:
    """Формирует отображаемое имя чата."""
    # Группы/супергруппы — title
    title = dialog_info.get("title", "")
    if title:
        return title
    # Приватные чаты — first_name + last_name
    name = dialog_info.get("first_name", "")
    last = dialog_info.get("last_name")
    if last:
        name += f" {last}"
    return name or dialog_info.get("username", "") or "???"


def _build_styles_keyboard(
    dialogs: list[dict], chat_styles: dict, global_style: str | None = None,
) -> InlineKeyboardMarkup:
    """Формирует inline-клавиатуру со списком чатов и их стилями."""
    keyboard = []
    for d in dialogs:
        chat_id = d["chat_id"]
        style = chat_styles.get(str(chat_id))
        if style is None:
            style = global_style
        emoji = _style_emoji(style)
        name = _chat_display_name(d)
        label = f"{emoji} {name}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"styles:{chat_id}")])
    return InlineKeyboardMarkup(keyboard)


@typing_action
async def on_styles(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /styles — показывает per-chat стили."""
    u = update.effective_user

    asyncio.create_task(update_last_msg_at(u.id))

    # Проверяем подключение
    if not pyrogram_client.is_active(u.id):
        msg = await get_system_message(u.language_code, "styles_not_connected")
        await update.message.reply_text(msg)
        return

    # Читаем настройки
    user = await get_user(u.id)
    user_settings = (user or {}).get("settings") or {}
    chat_styles = user_settings.get("chat_styles") or {}

    # Получаем список последних диалогов
    dialogs = await pyrogram_client.get_dialog_info(
        u.id, limit=CHAT_STYLES_DIALOGS_LIMIT,
    )

    if not dialogs:
        msg = await get_system_message(u.language_code, "styles_no_chats")
        await update.message.reply_text(msg)
        return

    # Сохраняем dialogs в user_data для callback
    context.user_data["styles_dialogs"] = dialogs

    title = await get_system_message(u.language_code, "styles_title")
    global_style = user_settings.get("style")
    keyboard = _build_styles_keyboard(dialogs, chat_styles, global_style)
    await update.message.reply_text(title, reply_markup=keyboard)

    if DEBUG_PRINT:
        print(f"{get_timestamp()} [BOT] /styles from user {u.id}, {len(dialogs)} chats")


async def on_styles_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик нажатия inline-кнопок /styles — циклическое переключение стиля."""
    query = update.callback_query
    u = update.effective_user
    await query.answer()

    # Извлекаем chat_id из callback_data "styles:123456"
    try:
        chat_id = int(query.data.split(":")[1])
    except (IndexError, ValueError):
        return

    # Читаем текущие настройки
    user = await get_user(u.id)
    user_settings = (user or {}).get("settings") or {}

    # Текущий стиль для этого чата
    current = get_effective_style(user_settings, chat_id)
    
    # Глобальный стиль
    global_style = user_settings.get("style") or DEFAULT_STYLE

    # Циклически переключаем
    options = list(STYLE_OPTIONS)
    idx = options.index(current) if current in options else 0
    next_value = options[(idx + 1) % len(options)]

    # Если выбранный стиль совпадает с глобальным — сбрасываем override для чата (= None)
    override_value = None if next_value == global_style else next_value

    # Сохраняем
    updated_settings = await update_chat_style(u.id, chat_id, override_value)
    if updated_settings is None:
        error_msg = await get_system_message(u.language_code, "error")
        await query.edit_message_text(text=error_msg)
        return

    # Обновляем клавиатуру
    dialogs = context.user_data.get("styles_dialogs") or []
    if not dialogs:
        dialogs = await pyrogram_client.get_dialog_info(
            u.id, limit=CHAT_STYLES_DIALOGS_LIMIT,
        )

    chat_styles = updated_settings.get("chat_styles") or {}
    global_style = updated_settings.get("style")
    keyboard = _build_styles_keyboard(dialogs, chat_styles, global_style)
    title = await get_system_message(u.language_code, "styles_title")
    await query.edit_message_text(text=title, reply_markup=keyboard)

    if DEBUG_PRINT:
        print(f"{get_timestamp()} [BOT] Style for chat {chat_id} changed to {next_value!r} by user {u.id}")
