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
    Application, ContextTypes,
)

from config import (
    PYROGRAM_API_ID, PYROGRAM_API_HASH, DEBUG_PRINT,
    QR_LOGIN_TIMEOUT_SECONDS, QR_LOGIN_POLL_INTERVAL,
    DRAFT_PROBE_DELAY,
)
from utils.utils import get_timestamp, typing_action
from clients.x402gate.openrouter import generate_reply, generate_response
from clients import pyrogram_client
from database import supabase
from database.users import save_session, clear_session, get_user
from system_messages import get_system_message, get_system_messages
from prompts import DRAFT_INSTRUCTION_PROMPT


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


# ====== /connect ======

@typing_action
async def on_connect(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /connect — подключение аккаунта через QR-код."""
    u = update.effective_user

    if DEBUG_PRINT:
        print(f"{get_timestamp()} [BOT] /connect from user {u.id} (@{u.username})")

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
        msg = await get_system_message(u.language_code, "connect_scan")
        await update.message.reply_photo(photo=buf, caption=msg)

        if DEBUG_PRINT:
            print(f"{get_timestamp()} [CONNECT_QR] QR sent to user {u.id}, waiting for scan...")

        # Запускаем polling в фоне, чтобы НЕ блокировать бот
        asyncio.create_task(
            _poll_qr_login(client, u.id, u.language_code, context.bot, update.effective_chat.id)
        )

    except Exception as e:
        print(f"{get_timestamp()} [BOT] ERROR connect for user {u.id}: {e}")
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
                    msg = await get_system_message(language_code, "connect_2fa_error")
                    await bot.send_message(chat_id=chat_id, text=msg)
                    await client.disconnect()
                    return
                if "Unauthorized" not in str(e):
                    raise

        if not authorized:
            msg = await get_system_message(language_code, "connect_timeout")
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

    # Только личные чаты
    if message.chat.type.value != "private":
        return

    chat_id = message.chat.id

    if DEBUG_PRINT:
        sender = message.from_user.first_name if message.from_user else "Unknown"
        print(f"{get_timestamp()} [PYROGRAM] New message for user {user_id} from {sender}: '{message.text[:50]}'")

    try:
        # Показываем пользователю что бот работает
        user = await get_user(user_id)
        lang = user.get("language_code") if user else None
        probe_text = await get_system_message(lang, "draft_typing")
        _bot_drafts[(user_id, chat_id)] = probe_text
        await pyrogram_client.set_draft(user_id, chat_id, probe_text)

        # Читаем историю чата
        history = await pyrogram_client.read_chat_history(user_id, chat_id)
        if not history:
            return

        # Генерируем ответ
        reply_text = await generate_reply(history)
        if not reply_text or not reply_text.strip():
            return

        # Устанавливаем черновик с AI-ответом
        ai_text = reply_text.strip()
        _bot_drafts[(user_id, chat_id)] = ai_text
        await pyrogram_client.set_draft(user_id, chat_id, ai_text)

    except Exception as e:
        print(f"{get_timestamp()} [PYROGRAM] ERROR processing message for user {user_id}: {e}")


# Тексты черновиков, установленные ботом: {(user_id, chat_id): text}
_bot_drafts: dict[tuple[int, int], str] = {}

# Ожидающие проверки черновики пользователя: {(user_id, chat_id): instruction}
_pending_drafts: dict[tuple[int, int], str] = {}


async def on_pyrogram_draft(user_id: int, chat_id: int, draft_text: str) -> None:
    """Вызывается при обновлении черновика — probe-based detection."""
    key = (user_id, chat_id)

    # Игнорируем черновики, установленные ботом (пробел или AI-ответ)
    bot_text = _bot_drafts.get(key)
    if bot_text is not None and draft_text == bot_text:
        _bot_drafts.pop(key, None)  # Одноразовая проверка
        return

    # Пустой черновик — ничего не делаем
    if not draft_text:
        _pending_drafts.pop(key, None)
        return

    # Пользователь набрал текст — запоминаем как инструкцию
    instruction = draft_text

    if DEBUG_PRINT:
        print(f"{get_timestamp()} [DRAFT] User text for {user_id} in chat {chat_id}: '{instruction[:80]}'")

    # Сохраняем инструкцию и ставим пробу (статус-сообщение)
    user = await get_user(user_id)
    lang = user.get("language_code") if user else None
    probe_text = await get_system_message(lang, "draft_typing")
    _pending_drafts[key] = instruction
    _bot_drafts[key] = probe_text
    await pyrogram_client.set_draft(user_id, chat_id, probe_text)

    # Ждём DRAFT_PROBE_DELAY секунд
    await asyncio.sleep(DRAFT_PROBE_DELAY)

    # Проверяем: инструкция ещё ожидает? (если пользователь вернул свой текст —
    # on_pyrogram_draft вызовется снова и перезапишет _pending_drafts → новый цикл)
    current_pending = _pending_drafts.get(key)
    if current_pending != instruction:
        # Инструкция изменилась или удалена — кто-то другой уже обрабатывает
        return

    # Пользователь вышел из чата — генерируем ответ
    _pending_drafts.pop(key, None)

    if DEBUG_PRINT:
        print(f"{get_timestamp()} [DRAFT] User left chat, processing: '{instruction[:80]}'")

    try:
        # Читаем историю чата для контекста
        history = await pyrogram_client.read_chat_history(user_id, chat_id)

        # Формируем запрос: инструкция + контекст переписки
        context_lines = []
        for msg in history:
            prefix = "You" if msg["role"] == "user" else "Them"
            context_lines.append(f"{prefix}: {msg['text']}")

        user_message = f"Instruction: {instruction}"
        if context_lines:
            user_message += "\n\nChat history:\n" + "\n".join(context_lines)

        # Генерируем ответ
        response = await generate_response(
            user_message=user_message,
            system_prompt=DRAFT_INSTRUCTION_PROMPT,
        )
        if not response or not response.strip():
            return

        # Устанавливаем черновик с AI-ответом и запоминаем
        ai_text = response.strip()
        _bot_drafts[key] = ai_text
        await pyrogram_client.set_draft(user_id, chat_id, ai_text)

        print(f"{get_timestamp()} [DRAFT] Response set as draft for user {user_id} in chat {chat_id}")

    except Exception as e:
        print(f"{get_timestamp()} [DRAFT] ERROR processing draft for user {user_id}: {e}")


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
                BotCommand("disconnect", messages.get("menu_disconnect", "Disconnect account")),
                BotCommand("status", messages.get("menu_status", "Connection status")),
            ],
            language_code=lang,
        )
        if DEBUG_PRINT:
            print(f"{get_timestamp()} [BOT] Menu commands set for language: {lang}")
    except Exception as e:
        print(f"{get_timestamp()} [BOT] ERROR setting menu for {lang}: {e}")
