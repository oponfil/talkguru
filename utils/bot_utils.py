# utils/bot_utils.py — Утилиты для Telegram Bot API

from telegram import BotCommand, BotCommandScopeChat

from config import DEBUG_PRINT
from utils.utils import get_timestamp
from system_messages import get_system_messages


async def update_user_menu(bot, user_id: int, language_code: str | None, is_connected: bool) -> None:
    """Устанавливает меню команд для конкретного пользователя.

    Показывает connect или disconnect в зависимости от статуса подключения.
    """
    lang = (language_code or "en").lower()

    try:
        messages = await get_system_messages(lang)

        commands = [
            BotCommand("settings", messages.get("menu_settings", "Settings")),
        ]

        if is_connected:
            commands.append(BotCommand("styles", messages.get("menu_styles", "Chat styles")))

        commands.append(BotCommand("status", messages.get("menu_status", "Connection status")))

        if is_connected:
            commands.append(BotCommand("disconnect", messages.get("menu_disconnect", "Disconnect account")))
        else:
            commands.append(BotCommand("connect", messages.get("menu_connect", "Connect account")))

        await bot.set_my_commands(
            commands,
            scope=BotCommandScopeChat(chat_id=user_id),
        )

        if DEBUG_PRINT:
            conn_status = "connected" if is_connected else "disconnected"
            print(f"{get_timestamp()} [BOT] Menu set for user {user_id} ({conn_status}, lang={lang})")
    except Exception as e:
        print(f"{get_timestamp()} [BOT] ERROR setting menu for user {user_id}: {e}")
