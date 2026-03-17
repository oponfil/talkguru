# handlers/styles_handler.py — Обработчик команды /chats (per-chat стили и автоответ)

import asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from clients import pyrogram_client
from config import (
    AUTO_REPLY_OPTIONS,
    CHAT_STYLES_DIALOGS_LIMIT,
    DEBUG_PRINT,
    DEFAULT_STYLE,
    STYLE_OPTIONS,
    STYLE_TO_EMOJI,
)
from database.users import get_user, update_chat_auto_reply, update_chat_style, update_last_msg_at
from handlers.pyrogram_handlers import get_replied_chats
from system_messages import SYSTEM_MESSAGES, get_system_message
from utils.utils import get_effective_auto_reply, get_effective_style, get_timestamp, normalize_auto_reply, typing_action



def _style_emoji(style: str | None) -> str:
    """Возвращает emoji для стиля."""
    return STYLE_TO_EMOJI.get(style, "🦉")


# Лейблы авто-ответа для /chats — единый источник: SYSTEM_MESSAGES + AUTO_REPLY_OPTIONS.
# Убираем " Auto-reply" для компактности кнопок.
_CHAT_AR_LABELS: dict[int | None, str] = {
    seconds: SYSTEM_MESSAGES[msg_key].replace(" Auto-reply", "")
    for seconds, msg_key in AUTO_REPLY_OPTIONS.items()
}


def _auto_reply_label(seconds: int | None) -> str:
    """Формирует метку таймера автоответа (из SYSTEM_MESSAGES, без 'Auto-reply')."""
    return _CHAT_AR_LABELS.get(seconds, "⏰")


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
    dialogs: list[dict],
    chat_styles: dict,
    user_settings: dict,
    global_style: str | None = None,
) -> InlineKeyboardMarkup:
    """Формирует inline-клавиатуру со списком чатов: стиль + автоответ."""
    keyboard = []
    for d in dialogs:
        chat_id = d["chat_id"]
        # Стиль
        style = chat_styles.get(str(chat_id))
        if style is None:
            style = global_style
        emoji = _style_emoji(style)
        name = _chat_display_name(d)

        # Автоответ
        auto_reply = get_effective_auto_reply(user_settings, chat_id)
        ar_label = _auto_reply_label(auto_reply)

        style_btn = InlineKeyboardButton(
            f"{emoji} {name}", callback_data=f"chats:{chat_id}",
        )
        ar_btn = InlineKeyboardButton(
            ar_label or "⏰", callback_data=f"autoreply:{chat_id}",
        )
        keyboard.append([style_btn, ar_btn])
    return InlineKeyboardMarkup(keyboard)


def _get_relevant_dialogs(
    all_dialogs: list[dict], user_settings: dict, user_id: int,
) -> list[dict]:
    """Фильтрует диалоги: только чаты, где бот ответил или есть кастомная настройка."""
    replied = get_replied_chats(user_id)
    styled_ids = set(int(k) for k in (user_settings.get("chat_styles") or {}))
    ar_ids = set(int(k) for k in (user_settings.get("chat_auto_replies") or {}))
    relevant_ids = replied | styled_ids | ar_ids
    dialogs = [d for d in all_dialogs if d["chat_id"] in relevant_ids]
    return dialogs[:CHAT_STYLES_DIALOGS_LIMIT]


@typing_action
async def on_chats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /chats — показывает per-chat настройки."""
    u = update.effective_user

    asyncio.create_task(update_last_msg_at(u.id))

    # Проверяем подключение
    if not pyrogram_client.is_active(u.id):
        msg = await get_system_message(u.language_code, "chats_not_connected")
        await update.message.reply_text(msg)
        return

    # Читаем настройки
    user = await get_user(u.id)
    user_settings = (user or {}).get("settings") or {}
    chat_styles = user_settings.get("chat_styles") or {}

    # Получаем широкий список диалогов для фильтрации
    all_dialogs = await pyrogram_client.get_dialog_info(
        u.id, limit=CHAT_STYLES_DIALOGS_LIMIT * 10,
    )

    dialogs = _get_relevant_dialogs(all_dialogs, user_settings, u.id)

    if not dialogs:
        msg = await get_system_message(u.language_code, "chats_no_chats")
        await update.message.reply_text(msg)
        return

    # Сохраняем dialogs в user_data для callback
    context.user_data["chats_dialogs"] = dialogs

    title = await get_system_message(u.language_code, "chats_title")
    global_style = user_settings.get("style")
    keyboard = _build_styles_keyboard(dialogs, chat_styles, user_settings, global_style)
    await update.message.reply_text(title, reply_markup=keyboard)

    if DEBUG_PRINT:
        print(f"{get_timestamp()} [BOT] /chats from user {u.id}, {len(dialogs)} chats")


async def _refresh_keyboard(
    query, u, context, updated_settings: dict,
) -> None:
    """Обновляет клавиатуру /chats после изменения настроек."""
    dialogs = context.user_data.get("chats_dialogs") or []
    if not dialogs:
        all_dialogs = await pyrogram_client.get_dialog_info(
            u.id, limit=CHAT_STYLES_DIALOGS_LIMIT * 10,
        )
        dialogs = _get_relevant_dialogs(all_dialogs, updated_settings, u.id)

    chat_styles = updated_settings.get("chat_styles") or {}
    global_style = updated_settings.get("style")
    keyboard = _build_styles_keyboard(dialogs, chat_styles, updated_settings, global_style)
    title = await get_system_message(u.language_code, "chats_title")
    await query.edit_message_text(text=title, reply_markup=keyboard)


async def on_chats_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик нажатия inline-кнопок /chats — циклическое переключение стиля."""
    query = update.callback_query
    u = update.effective_user
    await query.answer()

    # Извлекаем chat_id из callback_data "chats:123456"
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

    await _refresh_keyboard(query, u, context, updated_settings)

    if DEBUG_PRINT:
        print(f"{get_timestamp()} [BOT] Style for chat {chat_id} changed to {next_value!r} by user {u.id}")


async def on_auto_reply_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик нажатия кнопки автоответа — циклическое переключение таймера для чата."""
    query = update.callback_query
    u = update.effective_user
    await query.answer()

    # Извлекаем chat_id из callback_data "autoreply:123456"
    try:
        chat_id = int(query.data.split(":")[1])
    except (IndexError, ValueError):
        return

    # Читаем текущие настройки
    user = await get_user(u.id)
    user_settings = (user or {}).get("settings") or {}

    # Текущий auto_reply для этого чата
    current = get_effective_auto_reply(user_settings, chat_id)

    # Глобальный auto_reply
    global_auto_reply = normalize_auto_reply(user_settings.get("auto_reply"))

    # Циклически переключаем
    options = list(AUTO_REPLY_OPTIONS)
    idx = options.index(current) if current in options else 0
    next_value = options[(idx + 1) % len(options)]

    # Если совпадает с глобальным — сбрасываем per-chat override (= None)
    override_value = None if next_value == global_auto_reply else next_value

    # Сохраняем
    updated_settings = await update_chat_auto_reply(u.id, chat_id, override_value)
    if updated_settings is None:
        error_msg = await get_system_message(u.language_code, "error")
        await query.edit_message_text(text=error_msg)
        return

    await _refresh_keyboard(query, u, context, updated_settings)

    if DEBUG_PRINT:
        print(f"{get_timestamp()} [BOT] Auto-reply for chat {chat_id} changed to {next_value!r} by user {u.id}")
