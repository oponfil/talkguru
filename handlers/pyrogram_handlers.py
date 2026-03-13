# handlers/pyrogram_handlers.py — Обработчики /connect, /disconnect, Pyrogram callback

import asyncio
import base64
import io
import traceback

import qrcode
from pyrogram import Client
from pyrogram.raw.functions.auth import ExportLoginToken
from telegram import BotCommand, Update
from telegram.ext import (
    Application, ConversationHandler, ContextTypes,
)

from config import (
    PYROGRAM_API_ID, PYROGRAM_API_HASH, DEBUG_PRINT,
    QR_LOGIN_TIMEOUT_SECONDS, QR_LOGIN_POLL_INTERVAL,
)
from utils.utils import get_timestamp, typing_action
from clients.x402gate.openrouter import generate_reply
from clients import pyrogram_client
from database import supabase
from database.users import save_session, clear_session
from system_messages import get_system_message, get_system_messages


# ====== СОСТОЯНИЯ CONVERSATION ======
CONNECT_PHONE, CONNECT_CODE, CONNECT_2FA = range(3)


# ====== /connect ======

@typing_action
async def on_connect(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработчик команды /connect — начинает подключение аккаунта."""
    u = update.effective_user

    if DEBUG_PRINT:
        print(f"{get_timestamp()} [BOT] /connect from user {u.id} (@{u.username})")

    # Проверяем, не подключён ли уже
    if pyrogram_client.is_active(u.id):
        msg = await get_system_message(u.language_code, "connect_already")
        await update.message.reply_text(msg)
        return ConversationHandler.END

    msg = await get_system_message(u.language_code, "connect_prompt_phone")
    await update.message.reply_text(msg)
    return CONNECT_PHONE


@typing_action
async def on_connect_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получает номер телефона, отправляет код."""
    u = update.effective_user
    phone = update.message.text.strip()

    try:
        client = Client(
            name=f"talkguru_{u.id}",
            api_id=PYROGRAM_API_ID,
            api_hash=PYROGRAM_API_HASH,
            phone_number=phone,
            in_memory=True,
        )
        await client.connect()
        try:
            # Сначала пытаемся стандартно (APP)
            sent_code = await client.send_code(phone)
            # Принудительно запрашиваем SMS, если это возможно
            if sent_code.next_type:
                sent_code = await client.resend_code(phone, sent_code.phone_code_hash)
        except Exception as e:
            msg_str = str(e).lower()
            if "flood" in msg_str:
                print(f"{get_timestamp()} [BOT] FloodWait in send_code: {e}")
                raise
            else:
                raise

        if DEBUG_PRINT:
            print(f"{get_timestamp()} [CONNECT] Phone: {phone}, type: {sent_code.type}, next_type: {sent_code.next_type}")

        # Сохраняем в контекст для следующего шага
        context.user_data["pyrogram_client"] = client
        context.user_data["phone"] = phone
        context.user_data["phone_code_hash"] = sent_code.phone_code_hash

        msg = await get_system_message(u.language_code, "connect_prompt_code")
        await update.message.reply_text(msg)
        return CONNECT_CODE

    except Exception as e:
        print(f"{get_timestamp()} [BOT] ERROR connect phone for user {u.id}: {e}")
        msg = await get_system_message(u.language_code, "connect_error")
        await update.message.reply_text(msg)
        return ConversationHandler.END


async def on_connect_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получает код подтверждения, авторизует пользователя."""
    u = update.effective_user
    code = update.message.text.strip()

    client = context.user_data.get("pyrogram_client")
    phone = context.user_data.get("phone")
    phone_code_hash = context.user_data.get("phone_code_hash")

    if not client or not phone:
        msg = await get_system_message(u.language_code, "connect_error")
        await update.message.reply_text(msg)
        return ConversationHandler.END

    try:
        await client.sign_in(phone, phone_code_hash, code)
    except Exception as e:
        error_name = type(e).__name__
        # Нужна 2FA
        if "password" in error_name.lower() or "two" in str(e).lower():
            msg = await get_system_message(u.language_code, "connect_prompt_2fa")
            await update.message.reply_text(msg)
            return CONNECT_2FA

        print(f"{get_timestamp()} [BOT] ERROR connect code for user {u.id}: {e}")
        await client.disconnect()
        msg = await get_system_message(u.language_code, "connect_error")
        await update.message.reply_text(msg)
        return ConversationHandler.END

    return await _finalize_connection(update, context, client)


async def on_connect_2fa(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получает пароль 2FA."""
    u = update.effective_user
    password = update.message.text.strip()
    client = context.user_data.get("pyrogram_client")

    if not client:
        msg = await get_system_message(u.language_code, "connect_error")
        await update.message.reply_text(msg)
        return ConversationHandler.END

    try:
        await client.check_password(password)
    except Exception as e:
        print(f"{get_timestamp()} [BOT] ERROR connect 2FA for user {u.id}: {e}")
        await client.disconnect()
        msg = await get_system_message(u.language_code, "connect_error")
        await update.message.reply_text(msg)
        return ConversationHandler.END

    return await _finalize_connection(update, context, client)


async def _finalize_connection(update: Update, context: ContextTypes.DEFAULT_TYPE, client) -> int:
    """Завершает подключение: сохраняет сессию, запускает слушатель."""
    u = update.effective_user

    try:
        session_string = await client.export_session_string()
        await client.disconnect()

        # Сохраняем в БД
        await save_session(u.id, session_string)

        # Запускаем слушатель
        await pyrogram_client.start_listening(u.id, session_string)

        msg = await get_system_message(u.language_code, "connect_success")
        await update.message.reply_text(msg)
        print(f"{get_timestamp()} [BOT] User {u.id} connected via Pyrogram")

    except Exception as e:
        print(f"{get_timestamp()} [BOT] ERROR finalizing connection for user {u.id}: {e}")
        msg = await get_system_message(u.language_code, "connect_error")
        await update.message.reply_text(msg)

    # Очищаем контекст
    context.user_data.pop("pyrogram_client", None)
    context.user_data.pop("phone", None)
    context.user_data.pop("phone_code_hash", None)
    return ConversationHandler.END


async def on_connect_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отмена /connect."""
    context.user_data.pop("pyrogram_client", None)
    context.user_data.pop("phone", None)
    context.user_data.pop("phone_code_hash", None)
    await update.message.reply_text("❌ Cancelled.")
    return ConversationHandler.END


# ====== /disconnect ======

@typing_action
async def on_disconnect(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /disconnect — отключает аккаунт."""
    u = update.effective_user

    if DEBUG_PRINT:
        print(f"{get_timestamp()} [BOT] /disconnect from user {u.id} (@{u.username})")

    if not pyrogram_client.is_active(u.id):
        msg = await get_system_message(u.language_code, "disconnect_not_connected")
        await update.message.reply_text(msg)
        return

    await pyrogram_client.stop_listening(u.id)
    await clear_session(u.id)

    msg = await get_system_message(u.language_code, "disconnect_success")
    await update.message.reply_text(msg)
    print(f"{get_timestamp()} [BOT] User {u.id} disconnected")


# ====== /status ======

@typing_action
async def on_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /status — показывает статус подключения."""
    u = update.effective_user

    if DEBUG_PRINT:
        print(f"{get_timestamp()} [BOT] /status from user {u.id} (@{u.username})")

    if pyrogram_client.is_active(u.id):
        msg = await get_system_message(u.language_code, "status_connected")
    else:
        msg = await get_system_message(u.language_code, "status_disconnected")

    await update.message.reply_text(msg)


# ====== /connectqr ======

@typing_action
async def on_connectqr(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /connectqr — подключение аккаунта через QR-код."""
    u = update.effective_user

    if DEBUG_PRINT:
        print(f"{get_timestamp()} [BOT] /connectqr from user {u.id} (@{u.username})")

    # Проверяем, не подключён ли уже
    if pyrogram_client.is_active(u.id):
        msg = await get_system_message(u.language_code, "connect_already")
        await update.message.reply_text(msg)
        return

    try:
        # Создаём временного клиента Pyrogram
        client = Client(
            name=f"talkguru_qr_{u.id}",
            api_id=int(PYROGRAM_API_ID),
            api_hash=PYROGRAM_API_HASH,
            in_memory=True,
            no_updates=True,
        )
        await client.connect()

        # Запрашиваем QR-токен через raw MTProto
        result = await client.invoke(
            ExportLoginToken(
                api_id=int(PYROGRAM_API_ID),
                api_hash=PYROGRAM_API_HASH,
                except_ids=[],
            )
        )

        # Формируем URL для QR-кода
        token_b64 = base64.urlsafe_b64encode(result.token).decode().rstrip("=")
        qr_url = f"tg://login?token={token_b64}"

        # Генерируем QR-изображение
        qr_img = qrcode.make(qr_url)
        buf = io.BytesIO()
        qr_img.save(buf, format="PNG")
        buf.seek(0)

        # Отправляем QR-код пользователю
        msg = await get_system_message(u.language_code, "connectqr_scan")
        await update.message.reply_photo(photo=buf, caption=msg)

        if DEBUG_PRINT:
            print(f"{get_timestamp()} [CONNECT_QR] QR sent to user {u.id}, waiting for scan...")

        # Запускаем polling в фоне, чтобы НЕ блокировать бот
        asyncio.create_task(
            _poll_qr_login(client, u.id, u.language_code, context.bot, update.effective_chat.id)
        )

    except Exception as e:
        print(f"{get_timestamp()} [BOT] ERROR connectqr for user {u.id}: {e}")
        traceback.print_exc()
        msg = await get_system_message(u.language_code, "connect_error")
        await update.message.reply_text(msg)


async def _poll_qr_login(client, user_id: int, language_code: str, bot, chat_id: int) -> None:
    """Фоновая задача: ожидает сканирования QR-кода (до 2 минут)."""
    try:
        authorized = False
        polls = QR_LOGIN_TIMEOUT_SECONDS // QR_LOGIN_POLL_INTERVAL
        for _ in range(polls):
            await asyncio.sleep(QR_LOGIN_POLL_INTERVAL)
            try:
                result = await client.invoke(
                    ExportLoginToken(
                        api_id=int(PYROGRAM_API_ID),
                        api_hash=PYROGRAM_API_HASH,
                        except_ids=[],
                    )
                )
                type_name = type(result).__name__
                if "Success" in type_name:
                    authorized = True
                    break
            except Exception as e:
                type_name = type(e).__name__
                if "SessionPasswordNeeded" in type_name:
                    msg = await get_system_message(language_code, "connectqr_2fa_error")
                    await bot.send_message(chat_id=chat_id, text=msg)
                    await client.disconnect()
                    return
                if "Unauthorized" not in str(e):
                    raise

        if not authorized:
            msg = await get_system_message(language_code, "connectqr_timeout")
            await bot.send_message(chat_id=chat_id, text=msg)
            await client.disconnect()
            return

        # Получаем session string
        session_string = await client.export_session_string()
        await client.disconnect()

        # Сохраняем в БД и запускаем слушатель
        await save_session(user_id, session_string)
        await pyrogram_client.start_listening(user_id, session_string)

        msg = await get_system_message(language_code, "connect_success")
        await bot.send_message(chat_id=chat_id, text=msg)
        print(f"{get_timestamp()} [BOT] User {user_id} connected via QR code")

    except Exception as e:
        print(f"{get_timestamp()} [BOT] ERROR _poll_qr_login for user {user_id}: {e}")
        traceback.print_exc()
        msg = await get_system_message(language_code, "connect_error")
        await bot.send_message(chat_id=chat_id, text=msg)


# ====== PYROGRAM CALLBACK ======

async def on_pyrogram_message(user_id: int, pyrogram_client_instance, message) -> None:
    """Вызывается при новом входящем сообщении в любом чате пользователя."""
    if not message.text:
        return

    # Игнорируем исходящие сообщения (наши собственные) и сообщения от ботов
    if message.outgoing:
        return
    if message.from_user and message.from_user.is_bot:
        return

    chat_id = message.chat.id

    if DEBUG_PRINT:
        sender = message.from_user.first_name if message.from_user else "Unknown"
        print(f"{get_timestamp()} [PYROGRAM] New message for user {user_id} from {sender}: '{message.text[:50]}'")

    try:
        # Читаем историю чата
        history = await pyrogram_client.read_chat_history(user_id, chat_id)
        if not history:
            return

        # Генерируем ответ
        reply_text = await generate_reply(history)
        if not reply_text or not reply_text.strip():
            return

        # Устанавливаем черновик
        await pyrogram_client.set_draft(user_id, chat_id, reply_text.strip())

    except Exception as e:
        print(f"{get_timestamp()} [PYROGRAM] ERROR processing message for user {user_id}: {e}")


# ====== ВСПОМОГАТЕЛЬНЫЕ ======

async def restore_sessions(app: Application) -> None:
    """Восстанавливает активные Pyrogram-сессии при старте бота."""
    try:
        result = supabase.table("users").select(
            "user_id, session_string"
        ).not_.is_("session_string", "null").execute()

        if not result.data:
            return

        count = 0
        for row in result.data:
            user_id = row["user_id"]
            session_string = row["session_string"]
            if session_string:
                ok = await pyrogram_client.start_listening(user_id, session_string)
                if ok:
                    count += 1

        if count > 0:
            print(f"{get_timestamp()} [BOT] Restored {count} Pyrogram session(s)")

    except Exception as e:
        print(f"{get_timestamp()} [BOT] ERROR restoring sessions: {e}")


async def update_menu_language(bot, language_code: str | None) -> None:
    """Устанавливает меню команд на языке пользователя."""
    lang = (language_code or "en").lower()
    if lang == "en":
        return  # Английский уже установлен по умолчанию

    try:
        messages = await get_system_messages(lang)
        await bot.set_my_commands(
            [
                BotCommand("start", messages.get("menu_start", "Start")),
                BotCommand("connect", messages.get("menu_connect", "Connect account")),
                BotCommand("connectqr", messages.get("menu_connectqr", "Connect via QR code")),
                BotCommand("disconnect", messages.get("menu_disconnect", "Disconnect account")),
                BotCommand("status", messages.get("menu_status", "Connection status")),
            ],
            language_code=lang,
        )
        if DEBUG_PRINT:
            print(f"{get_timestamp()} [BOT] Menu commands set for language: {lang}")
    except Exception as e:
        print(f"{get_timestamp()} [BOT] ERROR setting menu for {lang}: {e}")
