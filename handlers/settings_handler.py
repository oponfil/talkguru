# handlers/settings_handler.py — Обработчик команды /settings

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config import AUTO_REPLY_OPTIONS, DEBUG_PRINT, STYLE_OPTIONS
from database.users import get_user_settings, update_user_settings
from system_messages import get_system_message, get_system_messages
from utils.utils import get_timestamp, normalize_auto_reply, typing_action


def _build_settings_keyboard(settings: dict, messages: dict) -> InlineKeyboardMarkup:
    """Формирует InlineKeyboard с текущими настройками пользователя."""
    drafts_enabled = settings.get("drafts_enabled", True)
    pro_model = settings.get("pro_model", False)
    has_prompt = bool(settings.get("custom_prompt"))

    drafts_label = messages.get("settings_drafts_on") if drafts_enabled else messages.get("settings_drafts_off")
    model_label = messages.get("settings_model_pro") if pro_model else messages.get("settings_model_free")
    prompt_label = messages.get("settings_prompt_set") if has_prompt else messages.get("settings_prompt_empty")
    auto_reply = normalize_auto_reply(settings.get("auto_reply"))
    auto_label = messages.get(AUTO_REPLY_OPTIONS.get(auto_reply, "settings_auto_off"))
    style_label = messages.get(STYLE_OPTIONS.get(settings.get("style"), "settings_style_userlike"))

    keyboard = [
        [InlineKeyboardButton(drafts_label, callback_data="settings:drafts")],
        [InlineKeyboardButton(model_label, callback_data="settings:model")],
        [InlineKeyboardButton(prompt_label, callback_data="settings:prompt")],
        [InlineKeyboardButton(style_label, callback_data="settings:style")],
        [InlineKeyboardButton(auto_label, callback_data="settings:auto_reply")],
    ]
    return InlineKeyboardMarkup(keyboard)



def _build_settings_text(title: str, settings: dict) -> str:
    """Формирует текст сообщения настроек с превью промпта."""
    custom_prompt = settings.get("custom_prompt", "")
    if not custom_prompt:
        return title

    return f"{title}\n\n📝 «{custom_prompt}»"


async def _send_settings_error(query, language_code: str | None) -> None:
    """Отправляет сообщение об ошибке сохранения настроек."""
    error_msg = await get_system_message(language_code, "error")
    await query.edit_message_text(text=error_msg)


@typing_action
async def on_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /settings — показывает настройки с Inline-кнопками."""
    u = update.effective_user

    settings = await get_user_settings(u.id)
    title = await get_system_message(u.language_code, "settings_title")

    messages = await get_system_messages(u.language_code)

    text = _build_settings_text(title, settings)
    keyboard = _build_settings_keyboard(settings, messages)
    await update.message.reply_text(text, reply_markup=keyboard)

    if DEBUG_PRINT:
        print(f"{get_timestamp()} [BOT] /settings from user {u.id}")


async def on_settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик нажатия Inline-кнопок настроек."""
    query = update.callback_query
    u = update.effective_user
    action = query.data

    await query.answer()

    settings = await get_user_settings(u.id)

    if action == "settings:drafts":
        current = settings.get("drafts_enabled", True)
        updated = await update_user_settings(u.id, {"drafts_enabled": not current})
        if not updated:
            await _send_settings_error(query, u.language_code)
            return
    elif action == "settings:model":
        current = settings.get("pro_model", False)
        updated = await update_user_settings(u.id, {"pro_model": not current})
        if not updated:
            await _send_settings_error(query, u.language_code)
            return
    elif action == "settings:prompt":
        # Если промпт уже установлен — очищаем, иначе запрашиваем ввод
        if settings.get("custom_prompt"):
            updated = await update_user_settings(u.id, {"custom_prompt": ""})
            if not updated:
                await _send_settings_error(query, u.language_code)
                return
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
        updated = await update_user_settings(u.id, {"auto_reply": next_value})
        if not updated:
            await _send_settings_error(query, u.language_code)
            return
    elif action == "settings:style":
        current = settings.get("style")
        options = list(STYLE_OPTIONS)
        idx = options.index(current) if current in options else 0
        next_value = options[(idx + 1) % len(options)]
        updated = await update_user_settings(u.id, {"style": next_value})
        if not updated:
            await _send_settings_error(query, u.language_code)
            return
    else:
        return

    # Перечитываем обновлённые настройки
    updated_settings = await get_user_settings(u.id)

    messages = await get_system_messages(u.language_code)

    keyboard = _build_settings_keyboard(updated_settings, messages)
    title = messages.get("settings_title", "⚙️ Settings")
    text = _build_settings_text(title, updated_settings)

    await query.edit_message_text(text=text, reply_markup=keyboard)

    if DEBUG_PRINT:
        print(f"{get_timestamp()} [BOT] Settings updated by user {u.id}: {action}")
