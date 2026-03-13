# bot.py — Telegram-бот TalkGuru (обработчики событий и запуск)

import os
import traceback

# Отключаем буферизацию
os.environ["PYTHONUNBUFFERED"] = "1"

import logging

# Отключаем лишние логи от библиотек ДО импорта telegram
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("httpcore").setLevel(logging.ERROR)
logging.getLogger("telegram").setLevel(logging.ERROR)

from telegram import Update  # noqa: E402
from telegram.ext import Application, MessageHandler, CommandHandler, ContextTypes, filters  # noqa: E402

from config import BOT_TOKEN, DEBUG_PRINT  # noqa: E402
from utils.utils import get_timestamp  # noqa: E402
from clients.x402gate.openrouter import generate_response  # noqa: E402
from database.users import upsert_user, update_last_msg_at, update_tg_rating  # noqa: E402
from utils.telegram_rating import extract_rating_from_chat  # noqa: E402
from system_messages import get_system_message, SYSTEM_MESSAGES  # noqa: E402


# ====== ОБРАБОТЧИКИ СОБЫТИЙ ======

async def on_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /start."""
    u = update.effective_user

    if DEBUG_PRINT:
        print(f"{get_timestamp()} [BOT] /start from user {u.id} (@{u.username})")

    # Сохраняем пользователя в БД
    await upsert_user(
        user_id=u.id,
        username=u.username,
        first_name=u.first_name,
        last_name=u.last_name,
        is_bot=u.is_bot,
        is_premium=bool(u.is_premium),
        language_code=u.language_code,
    )

    # Обновляем tg_rating (Telegram Stars) через getChat
    try:
        chat_obj = await context.bot.get_chat(u.id)
        rating = extract_rating_from_chat(chat_obj)
        await update_tg_rating(u.id, rating)
    except Exception as e:
        print(f"{get_timestamp()} [BOT] WARNING: Failed to get tg_rating for user {u.id}: {e}")

    # Приветствие на языке пользователя
    greeting = await get_system_message(u.language_code, "greeting")
    await update.message.reply_text(greeting)


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик текстовых сообщений — генерирует ответ через ИИ."""
    u = update.effective_user
    c = update.effective_chat
    m = update.message

    message_text = m.text or ""
    if not message_text.strip():
        return

    if DEBUG_PRINT:
        try:
            print(f"{get_timestamp()} [BOT] Text from user {u.id}: '{message_text[:100]}'")
        except UnicodeEncodeError:
            print(f"{get_timestamp()} [BOT] Text from user {u.id}: [unicode text]")

    # Обновляем last_msg_at
    await update_last_msg_at(u.id)

    # Индикатор набора текста
    await context.bot.send_chat_action(chat_id=c.id, action="typing")

    try:
        # Генерируем ответ через OpenRouter (Gemini 3.1 Flash)
        response_text = await generate_response(message_text)

        # Отправляем ответ
        await m.reply_text(response_text)

        if DEBUG_PRINT:
            print(f"{get_timestamp()} [BOT] Response sent to user {u.id}")

    except Exception as e:
        print(f"{get_timestamp()} [BOT] ERROR generating response for user {u.id}: {e}")
        traceback.print_exc()
        error_msg = await get_system_message(u.language_code, "error")
        await m.reply_text(error_msg or SYSTEM_MESSAGES["error"])


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

    # Создаём приложение
    app = Application.builder().token(BOT_TOKEN).build()

    # Регистрируем обработчики
    app.add_handler(CommandHandler("start", on_start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    # Глобальный обработчик ошибок
    app.add_error_handler(on_error)

    print(f"{get_timestamp()} [BOT] Bot is running (polling mode)...")

    # Запуск polling
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
