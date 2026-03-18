# bot.py — Telegram-бот DraftGuru (запуск и конфигурация)

import asyncio
import os
import traceback

# Отключаем буферизацию
os.environ["PYTHONUNBUFFERED"] = "1"

import logging

# Отключаем лишние логи от библиотек ДО импорта telegram
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("httpcore").setLevel(logging.ERROR)
logging.getLogger("telegram").setLevel(logging.ERROR)

from telegram import BotCommand  # noqa: E402
from telegram.ext import (  # noqa: E402
    Application, MessageHandler, CommandHandler, CallbackQueryHandler,
    ContextTypes, filters,
)

from config import BOT_TOKEN, BOT_READ_TIMEOUT, POLL_MISSED_INTERVAL, DEBUG_PRINT  # noqa: E402
from utils.utils import get_timestamp  # noqa: E402
from clients import pyrogram_client  # noqa: E402
from handlers.bot_handlers import on_start, on_start_connect_callback, on_text  # noqa: E402
from handlers.pyrogram_handlers import (  # noqa: E402
    on_disconnect, on_connect, on_status, handle_connect_text,
    on_connect_qr_callback, on_confirm_phone_callback, on_cancel_phone_callback, on_connect_cancel_callback,
    on_pyrogram_message, on_pyrogram_draft,
    poll_missed_messages,
)
from handlers.settings_handler import on_settings, on_settings_callback  # noqa: E402
from handlers.styles_handler import (  # noqa: E402
    on_auto_reply_callback, on_chat_prompt_callback, on_chat_prompt_cancel_callback,
    on_chat_prompt_clear_callback, on_chats, on_chats_callback,
)
from utils.pyrogram_utils import restore_sessions  # noqa: E402

PRIVATE_ONLY_FILTER = filters.ChatType.PRIVATE


# ====== ОБРАБОТЧИК ОШИБОК ======

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Глобальный обработчик ошибок."""
    print(f"{get_timestamp()} [BOT] ERROR: {context.error}")
    traceback.print_exc()


# ====== ЗАПУСК ======

def main() -> None:
    """Точка входа — запуск бота в polling-режиме."""
    if not BOT_TOKEN:
        print("❌ BOT_TOKEN не задан! Установите его в .env")
        return

    print(f"{get_timestamp()} [BOT] Starting DraftGuru bot...")

    # Устанавливаем callback-и для Pyrogram
    pyrogram_client.set_message_callback(on_pyrogram_message)
    pyrogram_client.set_draft_callback(on_pyrogram_draft)

    # Создаём приложение
    app = Application.builder().token(BOT_TOKEN).read_timeout(BOT_READ_TIMEOUT).concurrent_updates(True).post_init(post_init).build()

    # Регистрируем обработчики
    app.add_handler(CommandHandler("start", on_start, filters=PRIVATE_ONLY_FILTER))
    app.add_handler(CommandHandler("connect", on_connect, filters=PRIVATE_ONLY_FILTER))
    app.add_handler(CommandHandler("settings", on_settings, filters=PRIVATE_ONLY_FILTER))
    app.add_handler(CommandHandler("chats", on_chats, filters=PRIVATE_ONLY_FILTER))
    app.add_handler(CallbackQueryHandler(on_settings_callback, pattern=r"^settings:"))
    app.add_handler(CallbackQueryHandler(on_chats_callback, pattern=r"^chats:"))
    app.add_handler(CallbackQueryHandler(on_auto_reply_callback, pattern=r"^autoreply:"))
    app.add_handler(CallbackQueryHandler(on_chat_prompt_callback, pattern=r"^chatprompt:"))
    app.add_handler(CallbackQueryHandler(on_chat_prompt_cancel_callback, pattern=r"^chatprompt_cancel:"))
    app.add_handler(CallbackQueryHandler(on_chat_prompt_clear_callback, pattern=r"^chatprompt_clear:"))
    app.add_handler(CommandHandler("status", on_status, filters=PRIVATE_ONLY_FILTER))
    app.add_handler(CommandHandler("disconnect", on_disconnect, filters=PRIVATE_ONLY_FILTER))
    app.add_handler(CallbackQueryHandler(on_connect_qr_callback, pattern=r"^connect:qr$"))
    app.add_handler(CallbackQueryHandler(on_confirm_phone_callback, pattern=r"^connect:confirm_phone$"))
    app.add_handler(CallbackQueryHandler(on_cancel_phone_callback, pattern=r"^connect:cancel_phone$"))
    app.add_handler(CallbackQueryHandler(on_connect_cancel_callback, pattern=r"^connect:cancel$"))
    app.add_handler(CallbackQueryHandler(on_start_connect_callback, pattern=r"^start:connect$"))
    app.add_handler(MessageHandler(PRIVATE_ONLY_FILTER & filters.TEXT & ~filters.COMMAND, handle_connect_text), group=0)
    app.add_handler(MessageHandler(PRIVATE_ONLY_FILTER & filters.TEXT & ~filters.COMMAND, on_text), group=1)

    # Глобальный обработчик ошибок
    app.add_error_handler(on_error)

    print(f"{get_timestamp()} [BOT] Bot is running (polling mode)...")

    # Запуск polling
    app.run_polling(drop_pending_updates=True)


async def post_init(app: Application) -> None:
    """Выполняется после инициализации приложения."""
    # Глобальное меню (без connect/disconnect — они устанавливаются per-user)
    await app.bot.set_my_commands([
        BotCommand("settings", "Settings"),
        BotCommand("status", "Connection status"),
        BotCommand("connect", "Connect account"),
    ])

    # Восстанавливаем Pyrogram-сессии
    await restore_sessions(app)

    # Запускаем фоновый polling пропущенных сообщений
    global _poll_task
    _poll_task = asyncio.create_task(_poll_missed_loop())


_poll_task: asyncio.Task | None = None


async def _poll_missed_loop() -> None:
    """Фоновый цикл проверки пропущенных сообщений для всех активных пользователей."""
    while True:
        await asyncio.sleep(POLL_MISSED_INTERVAL)
        try:
            active_users = pyrogram_client.get_active_user_ids()
            for user_id in active_users:
                found = await poll_missed_messages(user_id)
                if found and DEBUG_PRINT:
                    print(f"{get_timestamp()} [POLL] Found {found} missed message(s) for user {user_id}")
        except Exception as e:
            print(f"{get_timestamp()} [POLL] ERROR: {e}")


if __name__ == "__main__":
    main()
