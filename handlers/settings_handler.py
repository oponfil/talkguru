# handlers/settings_handler.py — Обработчик команды /settings

from datetime import datetime, timedelta, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config import AUTO_REPLY_OPTIONS, DEBUG_PRINT, STYLE_OPTIONS, TIMEZONE_OFFSETS
from database.users import update_user_settings
from system_messages import get_system_message, get_system_messages
from utils.telegram_user import ensure_effective_user
from utils.utils import get_timestamp, normalize_auto_reply, typing_action


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
    return f"🕐 {time_str} (UTC{_format_tz_offset(offset)})"


def _build_settings_keyboard(settings: dict, messages: dict) -> InlineKeyboardMarkup:
    """Формирует InlineKeyboard с текущими настройками пользователя."""
    drafts_enabled = settings.get("drafts_enabled", True)
    pro_model = settings.get("pro_model", False)
    has_prompt = bool(settings.get("custom_prompt"))

    drafts_label = messages.get("settings_drafts_on") if drafts_enabled else messages.get("settings_drafts_off")
    model_label = messages.get("settings_model_pro") if pro_model else messages.get("settings_model_free")
    prompt_label = messages.get("settings_prompt_set") if has_prompt else messages.get("settings_prompt_empty")
    auto_reply = normalize_auto_reply(settings.get("auto_reply"))
    auto_label = messages.get(AUTO_REPLY_OPTIONS.get(auto_reply, "settings_auto_reply_off"))
    style_label = messages.get(STYLE_OPTIONS.get(settings.get("style"), "settings_style_userlike"))

    tz_offset = settings.get("tz_offset", 0) or 0
    tz_label = _build_timezone_label(tz_offset)

    keyboard = [
        [InlineKeyboardButton(drafts_label, callback_data="settings:drafts")],
        [InlineKeyboardButton(model_label, callback_data="settings:model")],
        [InlineKeyboardButton(prompt_label, callback_data="settings:prompt")],
        [InlineKeyboardButton(style_label, callback_data="settings:style")],
        [InlineKeyboardButton(auto_label, callback_data="settings:auto_reply")],
        [
            InlineKeyboardButton("⏪", callback_data="settings:timezone_back"),
            InlineKeyboardButton(f"{tz_label} ⏩", callback_data="settings:timezone"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def _escape_markdown_v2(text: str) -> str:
    """Экранирует спецсимволы для Telegram MarkdownV2."""
    special_chars = r"_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in special_chars else c for c in text)


def _build_settings_text(title: str, settings: dict) -> tuple[str, str | None]:
    """Формирует текст сообщения настроек с превью промпта.

    Returns:
        (text, parse_mode) — parse_mode задаётся только при strikethrough.
    """
    custom_prompt = settings.get("custom_prompt", "")
    cleared_prompt = settings.get("_cleared_prompt", "")

    if cleared_prompt:
        escaped_title = _escape_markdown_v2(title)
        escaped_prompt = _escape_markdown_v2(cleared_prompt)
        text = f"{escaped_title}\n\n📝 ~«{escaped_prompt}»~"
        return text, "MarkdownV2"

    if not custom_prompt:
        return title, None

    return f"{title}\n\n📝 «{custom_prompt}»", None


async def _send_settings_error(query, language_code: str | None) -> None:
    """Отправляет сообщение об ошибке сохранения настроек."""
    error_msg = await get_system_message(language_code, "error")
    await query.edit_message_text(text=error_msg)


@typing_action
async def on_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /settings — показывает настройки с Inline-кнопками."""
    u = update.effective_user

    try:
        user = await ensure_effective_user(update)
    except Exception:
        error_msg = await get_system_message(u.language_code, "error")
        await update.message.reply_text(error_msg)
        return

    settings = user.get("settings") or {}
    title = await get_system_message(u.language_code, "settings_title")

    messages = await get_system_messages(u.language_code)

    text, parse_mode = _build_settings_text(title, settings)
    keyboard = _build_settings_keyboard(settings, messages)
    await update.message.reply_text(text, reply_markup=keyboard, parse_mode=parse_mode)

    if DEBUG_PRINT:
        print(f"{get_timestamp()} [BOT] /settings from user {u.id}")


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

    if action == "settings:drafts":
        current = settings.get("drafts_enabled", True)
        updated_settings = await update_user_settings(
            u.id,
            {"drafts_enabled": not current},
            current_settings=settings,
        )
        if updated_settings is None:
            await _send_settings_error(query, u.language_code)
            return
    elif action == "settings:model":
        current = settings.get("pro_model", False)
        updated_settings = await update_user_settings(
            u.id,
            {"pro_model": not current},
            current_settings=settings,
        )
        if updated_settings is None:
            await _send_settings_error(query, u.language_code)
            return
    elif action == "settings:prompt":
        # Если промпт уже установлен — очищаем, иначе запрашиваем ввод
        if settings.get("custom_prompt"):
            old_prompt = settings["custom_prompt"]
            updated_settings = await update_user_settings(
                u.id,
                {"custom_prompt": ""},
                current_settings=settings,
            )
            if updated_settings is None:
                await _send_settings_error(query, u.language_code)
                return
            # Передаём удалённый промпт для отображения в сообщении
            updated_settings["_cleared_prompt"] = old_prompt
        else:
            # Ставим флаг ожидания промпта
            context.user_data["awaiting_prompt"] = True
            msg = await get_system_message(u.language_code, "settings_prompt_enter")
            await query.edit_message_text(text=msg)
            if DEBUG_PRINT:
                print(f"{get_timestamp()} [BOT] Awaiting custom prompt from user {u.id}")
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
    text, parse_mode = _build_settings_text(title, updated_settings)

    await query.edit_message_text(text=text, reply_markup=keyboard, parse_mode=parse_mode)

    if DEBUG_PRINT:
        print(f"{get_timestamp()} [BOT] Settings updated by user {u.id}: {action}")

