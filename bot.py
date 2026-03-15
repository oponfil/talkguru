# bot.py — Telegram-бот TalkGuru (запуск и конфигурация)

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
    Application, MessageHandler, CommandHandler,
    ContextTypes, filters,
)

from config import BOT_TOKEN, BOT_READ_TIMEOUT  # noqa: E402
from utils.utils import get_timestamp  # noqa: E402
from clients import pyrogram_client  # noqa: E402
from handlers.bot_handlers import on_start, on_text  # noqa: E402
from handlers.pyrogram_handlers import (  # noqa: E402
    on_disconnect, on_connect, on_status, handle_2fa_password,
    on_pyrogram_message, on_pyrogram_draft,
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

    print(f"{get_timestamp()} [BOT] Starting TalkGuru bot...")

    # Устанавливаем callback-и для Pyrogram
    pyrogram_client.set_message_callback(on_pyrogram_message)
    pyrogram_client.set_draft_callback(on_pyrogram_draft)

    # Создаём приложение
    app = Application.builder().token(BOT_TOKEN).read_timeout(BOT_READ_TIMEOUT).post_init(post_init).build()

    # Регистрируем обработчики
    app.add_handler(CommandHandler("start", on_start, filters=PRIVATE_ONLY_FILTER))
    app.add_handler(CommandHandler("connect", on_connect, filters=PRIVATE_ONLY_FILTER))
    app.add_handler(CommandHandler("disconnect", on_disconnect, filters=PRIVATE_ONLY_FILTER))
    app.add_handler(CommandHandler("status", on_status, filters=PRIVATE_ONLY_FILTER))
    app.add_handler(MessageHandler(PRIVATE_ONLY_FILTER & filters.TEXT & ~filters.COMMAND, handle_2fa_password), group=0)
    app.add_handler(MessageHandler(PRIVATE_ONLY_FILTER & filters.TEXT & ~filters.COMMAND, on_text), group=1)

    # Глобальный обработчик ошибок
    app.add_error_handler(on_error)

    print(f"{get_timestamp()} [BOT] Bot is running (polling mode)...")

    # Запуск polling
    app.run_polling(drop_pending_updates=True)


async def post_init(app: Application) -> None:
    """Выполняется после инициализации приложения."""
    # Меню команд (английский по умолчанию)
    await app.bot.set_my_commands([
        BotCommand("status", "Connection status"),
        BotCommand("connect", "Connect account"),
        BotCommand("disconnect", "Disconnect account"),
    ])

    # Восстанавливаем Pyrogram-сессии
    await restore_sessions(app)


if __name__ == "__main__":
    main()
