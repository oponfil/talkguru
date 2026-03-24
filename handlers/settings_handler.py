# handlers/settings_handler.py — Обработчик команды /settings

import asyncio

from datetime import datetime, timedelta, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config import AUTO_REPLY_OPTIONS, DEBUG_PRINT, STYLE_OPTIONS, TIMEZONE_OFFSETS
from database.users import update_last_msg_at, update_user_settings
from dashboard import stats as dash_stats
from system_messages import get_system_message, get_system_messages
from utils.telegram_user import ensure_effective_user
from handlers.connect_handler import clear_pending_input
from utils.utils import (
    get_effective_pro_model,
    get_timestamp,
    normalize_auto_reply,
    serialize_user_updates,
    typing_action,
)


def _format_tz_offset(offset: float) -> str:
    """Форматирует UTC-смещение в строку: '+5:30', '-3', '0'."""
    if offset == 0:
        return "0"
    sign = "+" if offset > 0 else "-"
    abs_offset = abs(offset)
    hours = int(abs_offset)
    minutes = int((abs_offset - hours) * 60)
    if minutes:
        return f"{sign}{hours}:{minutes:02d}"
    return f"{sign}{hours}"


def _build_timezone_label(offset: float) -> str:
    """Формирует текст кнопки часового пояса с текущим временем."""
    now_utc = datetime.now(timezone.utc)
    local_time = now_utc + timedelta(hours=offset)
    time_str = local_time.strftime("%H:%M")
    return f"{time_str} (UTC{_format_tz_offset(offset)})"


def _build_settings_keyboard(settings: dict, messages: dict) -> InlineKeyboardMarkup:
    """Формирует InlineKeyboard с текущими настройками пользователя."""
    pro_model = get_effective_pro_model(settings)
    has_prompt = bool(settings.get("custom_prompt"))

    model_label = messages.get("settings_model_pro") if pro_model else messages.get("settings_model_free")
    prompt_label = messages.get("settings_prompt_set") if has_prompt else messages.get("settings_prompt_empty")
    auto_reply = normalize_auto_reply(settings.get("auto_reply"))
    ar_key = AUTO_REPLY_OPTIONS.get(auto_reply, "auto_reply_off")
    ar_base = messages.get(ar_key, "")
    if ar_key == "auto_reply_ignore":
        auto_label = ar_base
    else:
        ar_prefix = messages.get("auto_reply_prefix", "⏰ Auto-reply:")
        auto_label = f"{ar_prefix} {ar_base}"
    style_label = messages.get(STYLE_OPTIONS.get(settings.get("style"), "settings_style_userlike"))

    tz_offset = settings.get("tz_offset", 0) or 0
    tz_label = _build_timezone_label(tz_offset)

    keyboard = [
        [InlineKeyboardButton(model_label, callback_data="settings:model")],
        [InlineKeyboardButton(style_label, callback_data="settings:style")],
        [InlineKeyboardButton(prompt_label, callback_data="settings:prompt")],
        [InlineKeyboardButton(auto_label, callback_data="settings:auto_reply")],
        [
            InlineKeyboardButton(f"⏪ {messages.get('settings_timezone_back', '🕐 Time')}", callback_data="settings:timezone_back"),
            InlineKeyboardButton(f"{tz_label} ⏩", callback_data="settings:timezone"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


async def _send_settings_error(query, language_code: str | None) -> None:
    """Отправляет сообщение об ошибке сохранения настроек."""
    error_msg = await get_system_message(language_code, "error")
    await query.edit_message_text(text=error_msg)


@serialize_user_updates
@typing_action
async def on_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /settings — показывает настройки с Inline-кнопками."""
    u = update.effective_user
    await clear_pending_input(context, u.id, context.bot)

    try:
        user = await ensure_effective_user(update)
    except Exception:
        error_msg = await get_system_message(u.language_code, "error")
        await update.message.reply_text(error_msg)
        return

    asyncio.create_task(update_last_msg_at(u.id))

    settings = user.get("settings") or {}
    title = await get_system_message(u.language_code, "settings_title")

    messages = await get_system_messages(u.language_code)

    keyboard = _build_settings_keyboard(settings, messages)
    await update.message.reply_text(title, reply_markup=keyboard)

    if DEBUG_PRINT:
        print(f"{get_timestamp()} [BOT] /settings from user {u.id}")
    dash_stats.record_command("/settings")


@serialize_user_updates
async def on_settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик нажатия Inline-кнопок настроек."""
    query = update.callback_query
    u = update.effective_user
    action = query.data

    await query.answer()

    try:
        user = await ensure_effective_user(update)
    except Exception:
        await _send_settings_error(query, u.language_code)
        return

    settings = user.get("settings") or {}
    if not action.startswith("settings:prompt"):
        await clear_pending_input(context, u.id, context.bot)

    if action == "settings:model":
        current = get_effective_pro_model(settings)
        updated_settings = await update_user_settings(
            u.id,
            {"pro_model": not current},
            current_settings=settings,
        )
        if updated_settings is None:
            await _send_settings_error(query, u.language_code)
            return
    elif action == "settings:prompt":
        # Показываем текущий промпт + кнопки Cancel / Clear
        custom_prompt = settings.get("custom_prompt", "")
        messages = await get_system_messages(u.language_code)
        if custom_prompt:
            msg = messages.get("settings_prompt_current", "").format(prompt=custom_prompt)
        else:
            msg = messages.get("settings_prompt_no_prompt", "")

        buttons = [[InlineKeyboardButton(messages.get("prompt_cancel", "❌ Cancel"), callback_data="settings:prompt_cancel")]]
        if custom_prompt:
            buttons[0].append(InlineKeyboardButton(messages.get("prompt_clear", "🗑 Clear"), callback_data="settings:prompt_clear"))

        await clear_pending_input(context, u.id, context.bot)
        context.user_data["awaiting_prompt"] = True
        await query.edit_message_text(text=msg, reply_markup=InlineKeyboardMarkup(buttons))
        if DEBUG_PRINT:
            print(f"{get_timestamp()} [BOT] Prompt editor opened for user {u.id}")
        return
    elif action == "settings:prompt_cancel":
        # Отмена — убираем awaiting и восстанавливаем меню настроек
        await clear_pending_input(context, u.id, context.bot)
        updated_settings = settings
    elif action == "settings:prompt_clear":
        # Очистка промпта
        await clear_pending_input(context, u.id, context.bot)
        updated_settings = await update_user_settings(
            u.id,
            {"custom_prompt": ""},
            current_settings=settings,
        )
        if updated_settings is None:
            await _send_settings_error(query, u.language_code)
            return
    elif action == "settings:auto_reply":
        current = normalize_auto_reply(settings.get("auto_reply"))
        options = list(AUTO_REPLY_OPTIONS)
        idx = options.index(current)
        next_value = options[(idx + 1) % len(options)]
        updated_settings = await update_user_settings(
            u.id,
            {"auto_reply": next_value},
            current_settings=settings,
        )
        if updated_settings is None:
            await _send_settings_error(query, u.language_code)
            return
    elif action == "settings:style":
        current = settings.get("style")
        options = list(STYLE_OPTIONS)
        idx = options.index(current) if current in options else 0
        next_value = options[(idx + 1) % len(options)]
        updated_settings = await update_user_settings(
            u.id,
            {"style": next_value},
            current_settings=settings,
        )
        if updated_settings is None:
            await _send_settings_error(query, u.language_code)
            return
    elif action in ("settings:timezone", "settings:timezone_back"):
        current = settings.get("tz_offset", 0) or 0
        try:
            idx = TIMEZONE_OFFSETS.index(current)
        except ValueError:
            idx = TIMEZONE_OFFSETS.index(0)
        step = 1 if action == "settings:timezone" else -1
        next_value = TIMEZONE_OFFSETS[(idx + step) % len(TIMEZONE_OFFSETS)]
        updated_settings = await update_user_settings(
            u.id,
            {"tz_offset": next_value},
            current_settings=settings,
        )
        if updated_settings is None:
            await _send_settings_error(query, u.language_code)
            return
    else:
        return

    messages = await get_system_messages(u.language_code)

    keyboard = _build_settings_keyboard(updated_settings, messages)
    title = messages.get("settings_title", "⚙️ Settings")

    await query.edit_message_text(text=title, reply_markup=keyboard)

    if DEBUG_PRINT:
        print(f"{get_timestamp()} [BOT] Settings updated by user {u.id}: {action}")
