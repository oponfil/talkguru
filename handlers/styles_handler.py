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
from database.users import get_user, update_chat_auto_reply, update_chat_prompt, update_chat_style, update_last_msg_at
from handlers.pyrogram_handlers import get_replied_chats
from system_messages import get_system_message, get_system_messages
from utils.utils import (
    clear_pending_input,
    get_effective_auto_reply,
    get_effective_style,
    get_timestamp,
    normalize_auto_reply,
    serialize_user_updates,
    typing_action,
)



def _style_emoji(style: str | None) -> str:
    """Возвращает emoji для стиля."""
    return STYLE_TO_EMOJI.get(style, "🦉")


def _auto_reply_label(seconds: int | None, messages: dict) -> str:
    """Формирует локализованную метку таймера автоответа для /chats."""
    ar_key = AUTO_REPLY_OPTIONS.get(seconds, "auto_reply_off")
    ar_base = messages.get(ar_key, "")
    if ar_key == "auto_reply_ignore":
        return ar_base or "🔇"
    return f"⏰: {ar_base}" if ar_base else "⏰"


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
    messages: dict,
    global_style: str | None = None,
) -> InlineKeyboardMarkup:
    """Формирует inline-клавиатуру со списком чатов: стиль + автоответ + промпт."""
    chat_prompts = user_settings.get("chat_prompts") or {}
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
        ar_label = _auto_reply_label(auto_reply, messages)

        # Per-chat промпт
        has_prompt = bool(chat_prompts.get(str(chat_id)))
        prompt_label = messages.get(
            "chats_prompt_set" if has_prompt else "chats_prompt_empty",
            "📝",
        )

        style_btn = InlineKeyboardButton(
            f"{emoji} {name}", callback_data=f"chats:{chat_id}",
        )
        ar_btn = InlineKeyboardButton(
            ar_label or "⏰", callback_data=f"autoreply:{chat_id}",
        )
        prompt_btn = InlineKeyboardButton(
            prompt_label, callback_data=f"chatprompt:{chat_id}",
        )
        keyboard.append([prompt_btn, style_btn, ar_btn])
    return InlineKeyboardMarkup(keyboard)


def _get_relevant_dialogs(
    all_dialogs: list[dict], user_settings: dict, user_id: int,
) -> list[dict]:
    """Фильтрует диалоги: только чаты, где бот ответил или есть кастомная настройка."""
    replied = get_replied_chats(user_id)
    styled_ids = set(int(k) for k in (user_settings.get("chat_styles") or {}))
    ar_ids = set(int(k) for k in (user_settings.get("chat_auto_replies") or {}))
    prompt_ids = set(int(k) for k in (user_settings.get("chat_prompts") or {}))
    relevant_ids = replied | styled_ids | ar_ids | prompt_ids
    dialogs = [d for d in all_dialogs if d["chat_id"] in relevant_ids]
    # Чаты с per-chat auto-reply/ignore — сверху
    dialogs.sort(key=lambda d: d["chat_id"] not in ar_ids)
    return dialogs[:CHAT_STYLES_DIALOGS_LIMIT]


@serialize_user_updates
@typing_action
async def on_chats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /chats — показывает per-chat настройки."""
    u = update.effective_user
    await clear_pending_input(context, u.id, context.bot)

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

    messages = await get_system_messages(u.language_code)
    title = messages.get("chats_title", "🎭 Chats")
    global_style = user_settings.get("style")
    keyboard = _build_styles_keyboard(dialogs, chat_styles, user_settings, messages, global_style)
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
    else:
        dialogs = _get_relevant_dialogs(dialogs, updated_settings, u.id)

    context.user_data["chats_dialogs"] = dialogs

    chat_styles = updated_settings.get("chat_styles") or {}
    global_style = updated_settings.get("style")
    messages = await get_system_messages(u.language_code)
    keyboard = _build_styles_keyboard(dialogs, chat_styles, updated_settings, messages, global_style)
    title = messages.get("chats_title", "🎭 Chats")
    await query.edit_message_text(text=title, reply_markup=keyboard)


@serialize_user_updates
async def on_chats_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик нажатия inline-кнопок /chats — циклическое переключение стиля."""
    query = update.callback_query
    u = update.effective_user
    await query.answer()
    await clear_pending_input(context, u.id, context.bot)

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


@serialize_user_updates
async def on_auto_reply_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик нажатия кнопки автоответа — циклическое переключение таймера для чата."""
    query = update.callback_query
    u = update.effective_user
    await query.answer()
    await clear_pending_input(context, u.id, context.bot)

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

    # Если совпадает с глобальным — сбрасываем per-chat override (= None).
    # Иначе сохраняем: для OFF используем 0 (sentinel), т.к. None = сброс.
    if next_value == global_auto_reply:
        override_value = None
    elif next_value is None:
        override_value = 0  # 0 = явно OFF
    else:
        override_value = next_value

    # Сохраняем
    updated_settings = await update_chat_auto_reply(u.id, chat_id, override_value)
    if updated_settings is None:
        error_msg = await get_system_message(u.language_code, "error")
        await query.edit_message_text(text=error_msg)
        return

    await _refresh_keyboard(query, u, context, updated_settings)

    if DEBUG_PRINT:
        print(f"{get_timestamp()} [BOT] Auto-reply for chat {chat_id} changed to {next_value!r} by user {u.id}")


@serialize_user_updates
async def on_chat_prompt_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик нажатия кнопки per-chat промпта — показ текущего + ввод нового."""
    query = update.callback_query
    u = update.effective_user
    await query.answer()

    # Извлекаем chat_id из callback_data "chatprompt:123456"
    try:
        chat_id = int(query.data.split(":")[1])
    except (IndexError, ValueError):
        return

    # Читаем текущие настройки
    user = await get_user(u.id)
    user_settings = (user or {}).get("settings") or {}
    chat_prompts = user_settings.get("chat_prompts") or {}
    current_prompt = chat_prompts.get(str(chat_id), "")

    # Имя чата из сохранённых диалогов
    dialogs = context.user_data.get("chats_dialogs") or []
    chat_name = "???"
    for d in dialogs:
        if d["chat_id"] == chat_id:
            chat_name = _chat_display_name(d)
            break

    messages = await get_system_messages(u.language_code)
    if current_prompt:
        msg = messages.get("chats_prompt_current", "").format(chat_name=chat_name, prompt=current_prompt)
    else:
        msg = messages.get("chats_prompt_no_prompt", "").format(chat_name=chat_name)

    buttons = [[InlineKeyboardButton(messages.get("prompt_cancel", "❌ Cancel"), callback_data=f"chatprompt_cancel:{chat_id}")]]
    if current_prompt:
        buttons[0].append(InlineKeyboardButton(messages.get("prompt_clear", "🗑 Clear"), callback_data=f"chatprompt_clear:{chat_id}"))

    await clear_pending_input(context, u.id, context.bot)
    context.user_data["awaiting_chat_prompt"] = chat_id
    await query.edit_message_text(text=msg, reply_markup=InlineKeyboardMarkup(buttons))

    if DEBUG_PRINT:
        print(f"{get_timestamp()} [BOT] Chat prompt editor opened for chat {chat_id} by user {u.id}")


@serialize_user_updates
async def on_chat_prompt_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Отмена редактирования per-chat промпта — возврат к списку чатов."""
    query = update.callback_query
    u = update.effective_user
    await query.answer()

    await clear_pending_input(context, u.id, context.bot)

    user = await get_user(u.id)
    user_settings = (user or {}).get("settings") or {}
    await _refresh_keyboard(query, u, context, user_settings)


@serialize_user_updates
async def on_chat_prompt_clear_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Очистка per-chat промпта и возврат к списку чатов."""
    query = update.callback_query
    u = update.effective_user
    await query.answer()

    await clear_pending_input(context, u.id, context.bot)

    try:
        chat_id = int(query.data.split(":")[1])
    except (IndexError, ValueError):
        return

    updated_settings = await update_chat_prompt(u.id, chat_id, None)
    if updated_settings is None:
        error_msg = await get_system_message(u.language_code, "error")
        await query.edit_message_text(text=error_msg)
        return

    await _refresh_keyboard(query, u, context, updated_settings)

    if DEBUG_PRINT:
        print(f"{get_timestamp()} [BOT] Chat prompt cleared for chat {chat_id} by user {u.id}")

