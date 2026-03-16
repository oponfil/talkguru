# handlers/pyrogram_handlers.py — Обработчики /connect, /disconnect, Pyrogram callback

import asyncio
import base64
import io
import random
import time
import traceback
from datetime import datetime, timezone

import qrcode
from pyrogram import Client
from pyrogram.raw.functions.account import GetPassword
from pyrogram.raw.functions.auth import CheckPassword, ExportLoginToken, ImportLoginToken
from pyrogram.session import Session as PyroSession
from pyrogram.session.auth import Auth
from pyrogram.utils import compute_password_check
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

from config import (
    PYROGRAM_API_ID, PYROGRAM_API_HASH, DEBUG_PRINT,
    PHONE_CODE_TIMEOUT_SECONDS,
    QR_LOGIN_TIMEOUT_SECONDS, QR_LOGIN_POLL_INTERVAL,
    DRAFT_PROBE_DELAY, DRAFT_VERIFY_DELAY, STYLE_PRO_MODELS, STICKER_FALLBACK_EMOJI,
)
from utils.utils import format_chat_history, get_timestamp, normalize_auto_reply, typing_action
from utils.bot_utils import update_user_menu
from clients.x402gate.openrouter import generate_response
from logic.reply import generate_reply
from clients import pyrogram_client
from database.users import clear_session, get_user, save_session
from system_messages import get_system_message
from prompts import build_draft_prompt
from utils.telegram_user import ensure_effective_user, upsert_effective_user


# ====== /disconnect ======

@typing_action
async def on_disconnect(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /disconnect — отключает аккаунт."""
    u = update.effective_user

    try:
        await ensure_effective_user(update)
    except Exception:
        msg = await get_system_message(u.language_code, "error")
        await update.message.reply_text(msg)
        return

    is_active = pyrogram_client.is_active(u.id)
    had_pending_2fa = await _cancel_pending_2fa(u.id)
    had_pending_phone = await _cancel_pending_phone(u.id)

    if DEBUG_PRINT:
        print(f"{get_timestamp()} [BOT] /disconnect from user {u.id} (@{u.username}, lang={u.language_code})")

    if is_active:
        stopped = await pyrogram_client.stop_listening(u.id)
        if not stopped:
            msg = await get_system_message(u.language_code, "disconnect_error")
            await update.message.reply_text(msg)
            return

    # Всегда пробуем очистить сессию в БД:
    # это делает /disconnect идемпотентным и устраняет stale-сессии после сбоев.
    cleared = await clear_session(u.id)
    if not cleared:
        msg = await get_system_message(u.language_code, "disconnect_error")
        await update.message.reply_text(msg)
        return

    if not is_active and not had_pending_2fa and not had_pending_phone:
        msg = await get_system_message(u.language_code, "status_disconnected")
        await update.message.reply_text(msg)
        return

    msg = await get_system_message(u.language_code, "disconnect_success")
    await update.message.reply_text(msg)
    await update_user_menu(context.bot, u.id, u.language_code, is_connected=False)
    print(f"{get_timestamp()} [BOT] User {u.id} disconnected")


# ====== /status ======

@typing_action
async def on_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /status — показывает статус подключения."""
    u = update.effective_user

    try:
        await ensure_effective_user(update)
    except Exception:
        msg = await get_system_message(u.language_code, "error")
        await update.message.reply_text(msg)
        return

    if DEBUG_PRINT:
        print(f"{get_timestamp()} [BOT] /status from user {u.id} (@{u.username}, lang={u.language_code})")

    if pyrogram_client.is_active(u.id):
        msg = await get_system_message(u.language_code, "status_connected")
    else:
        msg = await get_system_message(u.language_code, "status_disconnected")

    await update.message.reply_text(msg)


# ====== /connect ======

_qr_login_tasks: dict[int, asyncio.Task] = {}

# Ожидающие 2FA-пароля: {user_id: {client, language_code, bot, chat_id}}
_pending_2fa: dict[int, dict] = {}

# Ожидающие phone-логина: {user_id: {state, client, phone_number, phone_code_hash, expires_at, ...}}
# state: "awaiting_phone" | "awaiting_code" | "awaiting_2fa"
_pending_phone: dict[int, dict] = {}


def _get_chat_type(update: Update) -> str | None:
    """Возвращает тип чата Telegram как строку."""
    chat = update.effective_chat
    chat_type = getattr(chat, "type", None)
    return getattr(chat_type, "value", chat_type)


def _has_pending_2fa(user_id: int) -> bool:
    """Проверяет, ожидается ли у пользователя ввод 2FA-пароля."""
    return user_id in _pending_2fa


def _next_phone_expiry() -> float:
    """Возвращает дедлайн для текущего шага phone-flow."""
    return time.monotonic() + PHONE_CODE_TIMEOUT_SECONDS


def _put_pending_phone(user_id: int, pending: dict) -> dict:
    """Сохраняет phone-flow c обновлённым дедлайном."""
    pending["expires_at"] = _next_phone_expiry()
    _pending_phone[user_id] = pending
    return pending


def _get_phone_timeout_message_key(state: str) -> str:
    """Подбирает сообщение таймаута для состояния phone-flow."""
    if state == "awaiting_phone":
        return "connect_phone_timeout"
    return "connect_code_expired"


async def _get_pending_phone(user_id: int) -> dict | None:
    """Возвращает активный phone-flow, очищая протухшее состояние."""
    pending = _pending_phone.get(user_id)
    if pending is None:
        return None

    expires_at = pending.get("expires_at")
    if expires_at is None or time.monotonic() < expires_at:
        return pending

    await _cancel_pending_phone(user_id)
    return None


async def _cancel_pending_phone(user_id: int) -> bool:
    """Отменяет незавершённый phone-логин и очищает временный клиент."""
    pending = _pending_phone.pop(user_id, None)
    if pending is None:
        return False

    client = pending.get("client")
    if client is not None:
        await _safe_disconnect_temp_client(client, user_id)

    return True


def _get_qr_login_task(user_id: int) -> asyncio.Task | None:
    """Возвращает активную QR-задачу пользователя."""
    task = _qr_login_tasks.get(user_id)
    if task and task.done():
        _qr_login_tasks.pop(user_id, None)
        return None
    return task


async def _cancel_pending_2fa(user_id: int) -> bool:
    """Отменяет незавершённый 2FA-логин и очищает временный клиент."""
    pending = _pending_2fa.pop(user_id, None)
    if pending is None:
        return False

    client = pending.get("client")
    if client is not None:
        await _safe_disconnect_temp_client(client, user_id)

    return True


def _register_qr_login_task(user_id: int, task: asyncio.Task) -> None:
    """Регистрирует фоновую QR-задачу до её завершения."""
    _qr_login_tasks[user_id] = task

    def _cleanup(done_task: asyncio.Task) -> None:
        current_task = _qr_login_tasks.get(user_id)
        if current_task is done_task:
            _qr_login_tasks.pop(user_id, None)

    task.add_done_callback(_cleanup)


async def _safe_disconnect_temp_client(client: Client, user_id: int) -> None:
    """Пытается корректно отключить временный QR-клиент."""
    try:
        await client.disconnect()
    except Exception as e:
        if DEBUG_PRINT:
            print(f"{get_timestamp()} [CONNECT_QR] Cleanup disconnect failed for user {user_id}: {e}")

@typing_action
async def on_connect(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /connect — подключение аккаунта (сначала телефон, кнопка QR)."""
    u = update.effective_user

    if DEBUG_PRINT:
        print(f"{get_timestamp()} [BOT] /connect from user {u.id} (@{u.username}, lang={u.language_code})")

    # Проверяем, не подключён ли уже
    if pyrogram_client.is_active(u.id):
        msg = await get_system_message(u.language_code, "connect_already")
        await update.message.reply_text(msg)
        return

    if _get_qr_login_task(u.id) is not None:
        msg = await get_system_message(u.language_code, "connect_in_progress")
        await update.message.reply_text(msg)
        return

    if _has_pending_2fa(u.id):
        msg = await get_system_message(u.language_code, "connect_in_progress")
        await update.message.reply_text(msg)
        return

    if await _get_pending_phone(u.id) is not None:
        msg = await get_system_message(u.language_code, "connect_in_progress")
        await update.message.reply_text(msg)
        return

    try:
        # /connect должен работать даже без предварительного /start
        if not await upsert_effective_user(update):
            msg = await get_system_message(u.language_code, "connect_error")
            await update.message.reply_text(msg)
            return

        # Регистрируем phone-flow (ожидание номера)
        _put_pending_phone(u.id, {
            "state": "awaiting_phone",
            "language_code": u.language_code,
            "chat_id": update.effective_chat.id,
        })

        # Отправляем приглашение ввести номер с кнопкой QR
        msg = await get_system_message(u.language_code, "connect_phone_prompt")
        qr_label = await get_system_message(u.language_code, "connect_phone_btn_qr")
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(qr_label, callback_data="connect:qr")]
        ])
        await update.message.reply_text(msg, reply_markup=keyboard)

    except Exception as e:
        print(f"{get_timestamp()} [BOT] ERROR connect for user {u.id}: {e}")
        traceback.print_exc()
        await _cancel_pending_phone(u.id)
        try:
            msg = await get_system_message(u.language_code, "connect_error")
            await update.message.reply_text(msg)
        except Exception:
            pass


async def _start_qr_flow(
    user_id: int, language_code: str, bot: object, chat_id: int,
) -> None:
    """Запускает QR-flow: создаёт клиент, генерирует QR, запускает polling."""
    client: Client | None = None
    try:
        client = Client(
            name=f"draftguru_qr_{user_id}",
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
        msg = await get_system_message(language_code, "connect_scan")
        await bot.send_photo(chat_id=chat_id, photo=buf, caption=msg)

        if DEBUG_PRINT:
            print(f"{get_timestamp()} [CONNECT_QR] QR sent to user {user_id}, waiting for scan...")

        # Запускаем polling в фоне
        task = asyncio.create_task(
            _poll_qr_login(client, user_id, language_code, bot, chat_id)
        )
        _register_qr_login_task(user_id, task)
        client = None

    except Exception as e:
        print(f"{get_timestamp()} [BOT] ERROR QR flow for user {user_id}: {e}")
        traceback.print_exc()
        msg = await get_system_message(language_code, "connect_error")
        await bot.send_message(chat_id=chat_id, text=msg)
        if client is not None:
            await _safe_disconnect_temp_client(client, user_id)


async def on_connect_qr_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback кнопки 'QR-код' — переключает на QR-flow."""
    query = update.callback_query
    await query.answer()

    u = update.effective_user

    # Отменяем phone-flow
    await _cancel_pending_phone(u.id)

    # Проверяем, не подключён ли уже
    if pyrogram_client.is_active(u.id):
        msg = await get_system_message(u.language_code, "connect_already")
        await query.edit_message_text(msg)
        return

    if _get_qr_login_task(u.id) is not None:
        msg = await get_system_message(u.language_code, "connect_in_progress")
        await query.edit_message_text(msg)
        return

    if _has_pending_2fa(u.id):
        msg = await get_system_message(u.language_code, "connect_in_progress")
        await query.edit_message_text(msg)
        return

    await _start_qr_flow(
        u.id, u.language_code, context.bot, update.effective_chat.id,
    )


# ====== Phone flow text handlers ======


async def handle_connect_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Диспетчер текстовых сообщений для phone-flow и QR 2FA."""
    u = update.effective_user

    # QR 2FA (существующий flow) — приоритет
    if _has_pending_2fa(u.id):
        await handle_2fa_password(update, context)
        return  # handle_2fa_password поднимает ApplicationHandlerStop

    # Phone-flow
    pending = _pending_phone.get(u.id)
    if pending is not None:
        expires_at = pending.get("expires_at")
        if expires_at is not None and time.monotonic() >= expires_at:
            await _cancel_pending_phone(u.id)
            msg = await get_system_message(
                pending.get("language_code"),
                _get_phone_timeout_message_key(pending.get("state", "")),
            )
            await context.bot.send_message(chat_id=pending.get("chat_id"), text=msg)
            raise ApplicationHandlerStop

    pending = await _get_pending_phone(u.id)
    if pending is None:
        return  # Нет активного flow — пропускаем, on_text обработает

    state = pending["state"]
    if state == "awaiting_phone":
        await _handle_phone_number(update, context, pending)
    elif state == "awaiting_code":
        await _handle_phone_code(update, context, pending)
    elif state == "awaiting_2fa":
        await _handle_phone_2fa(update, context, pending)
    else:
        return  # Неизвестное состояние — пропускаем


async def _handle_phone_number(
    update: Update, context: ContextTypes.DEFAULT_TYPE, pending: dict,
) -> None:
    """Обработка ввода номера телефона."""
    u = update.effective_user
    phone_number = (update.message.text or "").strip()
    language_code = pending["language_code"]

    # Удаляем сообщение с номером для безопасности
    try:
        await update.message.delete()
    except Exception:
        pass

    client: Client | None = None
    try:
        # Создаём временного клиента Pyrogram
        client = Client(
            name=f"draftguru_phone_{u.id}",
            api_id=int(PYROGRAM_API_ID),
            api_hash=PYROGRAM_API_HASH,
            in_memory=True,
            no_updates=True,
        )
        await client.connect()

        # Отправляем код подтверждения
        sent_code = await client.send_code(phone_number)

        # Переводим в состояние ожидания кода
        _put_pending_phone(u.id, {
            "state": "awaiting_code",
            "client": client,
            "phone_number": phone_number,
            "phone_code_hash": sent_code.phone_code_hash,
            "language_code": language_code,
            "chat_id": pending["chat_id"],
        })

        msg = await get_system_message(language_code, "connect_code_prompt")
        await context.bot.send_message(chat_id=pending["chat_id"], text=msg)

        if DEBUG_PRINT:
            print(f"{get_timestamp()} [CONNECT_PHONE] Code sent to user {u.id}")

    except Exception as e:
        error_name = type(e).__name__

        if "PhoneNumberInvalid" in error_name:
            _put_pending_phone(u.id, pending)
            msg = await get_system_message(language_code, "connect_phone_invalid")
            await context.bot.send_message(chat_id=pending["chat_id"], text=msg)
            # Остаёмся в awaiting_phone — пользователь может ввести повторно
            if client is not None:
                await _safe_disconnect_temp_client(client, u.id)
            raise ApplicationHandlerStop

        if "FloodWait" in error_name:
            seconds = getattr(e, "value", getattr(e, "x", 0))
            msg = await get_system_message(language_code, "connect_flood_wait")
            msg = msg.replace("{seconds}", str(seconds))
            await context.bot.send_message(chat_id=pending["chat_id"], text=msg)
            _pending_phone.pop(u.id, None)
            if client is not None:
                await _safe_disconnect_temp_client(client, u.id)
            raise ApplicationHandlerStop

        print(f"{get_timestamp()} [CONNECT_PHONE] ERROR send_code for user {u.id}: {e}")
        traceback.print_exc()
        msg = await get_system_message(language_code, "connect_error")
        await context.bot.send_message(chat_id=pending["chat_id"], text=msg)
        _pending_phone.pop(u.id, None)
        if client is not None:
            await _safe_disconnect_temp_client(client, u.id)

    raise ApplicationHandlerStop


async def _handle_phone_code(
    update: Update, context: ContextTypes.DEFAULT_TYPE, pending: dict,
) -> None:
    """Обработка ввода кода подтверждения."""
    u = update.effective_user
    code = (update.message.text or "").strip()
    client: Client = pending["client"]
    language_code = pending["language_code"]
    chat_id = pending["chat_id"]

    # Удаляем сообщение с кодом для безопасности
    try:
        await update.message.delete()
    except Exception:
        pass

    try:
        result = await client.sign_in(
            phone_number=pending["phone_number"],
            phone_code_hash=pending["phone_code_hash"],
            phone_code=code,
        )

        # Успешная авторизация
        user_obj = getattr(result, "user", result)
        if hasattr(user_obj, "id"):
            await client.storage.user_id(user_obj.id)
            await client.storage.is_bot(getattr(user_obj, "bot", False))

        await _finalize_phone_login(u.id, client, language_code, context.bot, chat_id)

    except Exception as e:
        error_name = type(e).__name__

        if "SessionPasswordNeeded" in error_name:
            # 2FA требуется — переводим в awaiting_2fa
            _put_pending_phone(u.id, {**_pending_phone[u.id], "state": "awaiting_2fa"})
            msg = await get_system_message(language_code, "connect_2fa_prompt")
            await context.bot.send_message(chat_id=chat_id, text=msg)
            if DEBUG_PRINT:
                print(f"{get_timestamp()} [CONNECT_PHONE] 2FA required for user {u.id}")
            raise ApplicationHandlerStop

        if "PhoneCodeInvalid" in error_name:
            _put_pending_phone(u.id, pending)
            msg = await get_system_message(language_code, "connect_code_invalid")
            await context.bot.send_message(chat_id=chat_id, text=msg)
            # Остаёмся в awaiting_code
            raise ApplicationHandlerStop

        if "PhoneCodeExpired" in error_name:
            msg = await get_system_message(language_code, "connect_code_expired")
            await context.bot.send_message(chat_id=chat_id, text=msg)
            _pending_phone.pop(u.id, None)
            await _safe_disconnect_temp_client(client, u.id)
            raise ApplicationHandlerStop

        print(f"{get_timestamp()} [CONNECT_PHONE] ERROR sign_in for user {u.id}: {e}")
        traceback.print_exc()
        msg = await get_system_message(language_code, "connect_error")
        await context.bot.send_message(chat_id=chat_id, text=msg)
        _pending_phone.pop(u.id, None)
        await _safe_disconnect_temp_client(client, u.id)

    raise ApplicationHandlerStop


async def _handle_phone_2fa(
    update: Update, context: ContextTypes.DEFAULT_TYPE, pending: dict,
) -> None:
    """Обработка ввода 2FA-пароля в phone-flow."""
    u = update.effective_user
    password = (update.message.text or "").strip()
    client: Client = pending["client"]
    language_code = pending["language_code"]
    chat_id = pending["chat_id"]

    # Удаляем сообщение с паролем для безопасности
    try:
        await update.message.delete()
    except Exception:
        pass

    try:
        # Получаем SRP-параметры и проверяем пароль
        pwd = await client.invoke(GetPassword())
        r = await client.invoke(
            CheckPassword(password=compute_password_check(pwd, password))
        )

        if DEBUG_PRINT:
            print(f"{get_timestamp()} [CONNECT_PHONE] 2FA password accepted for user {u.id}")

        user_obj = getattr(r, "user", None)
        if user_obj:
            await client.storage.user_id(user_obj.id)
            await client.storage.is_bot(getattr(user_obj, "bot", False))

        await _finalize_phone_login(u.id, client, language_code, context.bot, chat_id)

    except Exception as e:
        error_name = type(e).__name__

        if "PasswordHashInvalid" in error_name:
            print(f"{get_timestamp()} [CONNECT_PHONE] Wrong 2FA password for user {u.id}")
            _put_pending_phone(u.id, pending)
            msg = await get_system_message(language_code, "connect_2fa_wrong_password")
            await context.bot.send_message(chat_id=chat_id, text=msg)
            # Остаёмся в awaiting_2fa
            raise ApplicationHandlerStop

        print(f"{get_timestamp()} [CONNECT_PHONE] 2FA error for user {u.id}: {e}")
        traceback.print_exc()
        msg = await get_system_message(language_code, "connect_2fa_error")
        await context.bot.send_message(chat_id=chat_id, text=msg)
        _pending_phone.pop(u.id, None)
        await _safe_disconnect_temp_client(client, u.id)

    raise ApplicationHandlerStop


async def _finalize_phone_login(
    user_id: int, client: Client, language_code: str, bot: object, chat_id: int,
) -> None:
    """Завершает phone-логин: сохраняет сессию и запускает listener."""
    try:
        session_string = await client.export_session_string()

        saved = await save_session(user_id, session_string)
        if not saved:
            msg = await get_system_message(language_code, "connect_error")
            await bot.send_message(chat_id=chat_id, text=msg)
            return

        started = await pyrogram_client.start_listening(user_id, session_string)
        if not started:
            await clear_session(user_id)
            msg = await get_system_message(language_code, "connect_error")
            await bot.send_message(chat_id=chat_id, text=msg)
            return

        msg = await get_system_message(language_code, "connect_success")
        await bot.send_message(chat_id=chat_id, text=msg)
        await update_user_menu(bot, user_id, language_code, is_connected=True)
        if DEBUG_PRINT:
            print(f"{get_timestamp()} [BOT] User {user_id} connected via phone")

    finally:
        _pending_phone.pop(user_id, None)
        await _safe_disconnect_temp_client(client, user_id)


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
                if DEBUG_PRINT:
                    print(f"{get_timestamp()} [CONNECT_QR] Poll result for user {user_id}: {type_name}")
                if "Success" in type_name:
                    authorized = True
                    break
                if "MigrateTo" in type_name:
                    # Аккаунт на другом DC — переключаемся и импортируем токен
                    dc_id = result.dc_id
                    token = result.token
                    print(f"{get_timestamp()} [CONNECT_QR] Migrating user {user_id} to DC {dc_id}")
                    try:
                        # Переключаем сессию на правильный DC (паттерн из Pyrogram)
                        await client.session.stop()
                        await client.storage.dc_id(dc_id)
                        await client.storage.auth_key(
                            await Auth(client, dc_id, await client.storage.test_mode()).create()
                        )
                        client.session = PyroSession(
                            client,
                            dc_id,
                            await client.storage.auth_key(),
                            await client.storage.test_mode(),
                        )
                        await client.session.start()

                        migration_result = await client.invoke(
                            ImportLoginToken(token=token),
                        )
                        migration_type = type(migration_result).__name__
                        print(f"{get_timestamp()} [CONNECT_QR] Migration result for user {user_id}: {migration_type}")
                        if "Success" in migration_type or "Authorization" in migration_type:
                            # Сохраняем данные авторизации в storage
                            auth = getattr(migration_result, "authorization", migration_result)
                            user_obj = getattr(auth, "user", None)
                            if user_obj:
                                await client.storage.user_id(user_obj.id)
                                await client.storage.is_bot(getattr(user_obj, "bot", False))
                            authorized = True
                            break
                    except Exception as migrate_err:
                        print(f"{get_timestamp()} [CONNECT_QR] Migration error for user {user_id}: {migrate_err}")
            except Exception as e:
                type_name = type(e).__name__
                if "SessionPasswordNeeded" in type_name:
                    # Сохраняем клиент для ожидания пароля
                    _pending_2fa[user_id] = {
                        "client": client,
                        "language_code": language_code,
                        "bot": bot,
                        "chat_id": chat_id,
                    }
                    msg = await get_system_message(language_code, "connect_2fa_prompt")
                    await bot.send_message(chat_id=chat_id, text=msg)
                    print(f"{get_timestamp()} [CONNECT_QR] 2FA required for user {user_id}, waiting for password")
                    return  # НЕ отключаем client — он нужен для check_password
                if "Unauthorized" in str(e):
                    if DEBUG_PRINT:
                        print(f"{get_timestamp()} [CONNECT_QR] Waiting for scan (user {user_id}): {e}")
                else:
                    raise

        if not authorized:
            msg = await get_system_message(language_code, "connect_timeout")
            await bot.send_message(chat_id=chat_id, text=msg)
            return

        # Получаем session string
        session_string = await client.export_session_string()

        # Сохраняем в БД и запускаем слушатель
        saved = await save_session(user_id, session_string)
        if not saved:
            msg = await get_system_message(language_code, "connect_error")
            await bot.send_message(chat_id=chat_id, text=msg)
            return

        started = await pyrogram_client.start_listening(user_id, session_string)
        if not started:
            await clear_session(user_id)
            msg = await get_system_message(language_code, "connect_error")
            await bot.send_message(chat_id=chat_id, text=msg)
            return

        msg = await get_system_message(language_code, "connect_success")
        await bot.send_message(chat_id=chat_id, text=msg)
        await update_user_menu(bot, user_id, language_code, is_connected=True)
        print(f"{get_timestamp()} [BOT] User {user_id} connected via QR code")

    except Exception as e:
        print(f"{get_timestamp()} [BOT] ERROR _poll_qr_login for user {user_id}: {e}")
        traceback.print_exc()
        msg = await get_system_message(language_code, "connect_error")
        await bot.send_message(chat_id=chat_id, text=msg)
    finally:
        # Не отключаем клиент, если он передан в _pending_2fa (ожидает 2FA-пароль)
        if user_id not in _pending_2fa:
            await _safe_disconnect_temp_client(client, user_id)


async def handle_2fa_password(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик ввода 2FA-пароля после QR-сканирования."""
    u = update.effective_user
    pending = _pending_2fa.pop(u.id, None)

    if pending is None:
        return  # Нет ожидающей 2FA-сессии — пропускаем, on_text обработает

    client: Client = pending["client"]
    language_code = pending["language_code"]
    password = update.message.text

    # Удаляем сообщение с паролем из чата для безопасности
    try:
        await update.message.delete()
    except Exception:
        pass  # Бот может не иметь прав на удаление

    try:
        # Получаем SRP-параметры через MTProto
        pwd = await client.invoke(GetPassword())

        # Compute SRP check via Pyrogram
        r = await client.invoke(
            CheckPassword(
                password=compute_password_check(pwd, password),
            )
        )

        print(f"{get_timestamp()} [CONNECT_QR] 2FA password accepted for user {u.id}")

        # Сохраняем данные авторизации
        user_obj = getattr(r, "user", None)
        if user_obj:
            await client.storage.user_id(user_obj.id)
            await client.storage.is_bot(getattr(user_obj, "bot", False))

        # Получаем session string
        session_string = await client.export_session_string()

        # Сохраняем в БД и запускаем слушатель
        saved = await save_session(u.id, session_string)
        if not saved:
            msg = await get_system_message(language_code, "connect_error")
            await context.bot.send_message(chat_id=update.effective_chat.id, text=msg)
            raise ApplicationHandlerStop

        started = await pyrogram_client.start_listening(u.id, session_string)
        if not started:
            await clear_session(u.id)
            msg = await get_system_message(language_code, "connect_error")
            await context.bot.send_message(chat_id=update.effective_chat.id, text=msg)
            raise ApplicationHandlerStop

        msg = await get_system_message(language_code, "connect_success")
        await context.bot.send_message(chat_id=update.effective_chat.id, text=msg)
        await update_user_menu(context.bot, u.id, language_code, is_connected=True)
        print(f"{get_timestamp()} [BOT] User {u.id} connected via QR code + 2FA")

    except ApplicationHandlerStop:
        raise  # Пробрасываем дальше

    except Exception as e:
        error_name = type(e).__name__

        if "PasswordHashInvalid" in error_name:
            # Неправильный пароль — даём попробовать ещё раз (не отключаем клиент)
            print(f"{get_timestamp()} [CONNECT_QR] Wrong 2FA password for user {u.id}")
            _pending_2fa[u.id] = pending  # Возвращаем в ожидание
            msg = await get_system_message(language_code, "connect_2fa_wrong_password")
            await context.bot.send_message(chat_id=update.effective_chat.id, text=msg)
            raise ApplicationHandlerStop

        print(f"{get_timestamp()} [CONNECT_QR] 2FA error for user {u.id}: {e}")
        traceback.print_exc()
        msg = await get_system_message(language_code, "connect_2fa_error")
        await context.bot.send_message(chat_id=update.effective_chat.id, text=msg)
    finally:
        # Не отключаем клиент, если он вернулся в _pending_2fa (повтор пароля)
        if u.id not in _pending_2fa:
            await _safe_disconnect_temp_client(client, u.id)

    # Останавливаем обработку — on_text НЕ должен видеть пароль.
    # Happy path тоже попадает сюда: пароль не должен дойти до on_text.
    raise ApplicationHandlerStop


# ====== PYROGRAM CALLBACK ======

# Дедупликация: Pyrogram может доставить один update через MessageHandler и RawUpdateHandler
# одновременно. {(user_id, chat_id): {msg_id, ...}}
_processed_incoming_ids: dict[tuple, set[int]] = {}
_PROCESSED_INCOMING_MAX = 50  # макс. размер set на чат


async def _verify_draft_delivery(user_id: int, chat_id: int, expected_text: str) -> None:
    """Повторно отправляет AI-черновик через DRAFT_VERIFY_DELAY секунд.

    Проверки перед re-push:
    1. _bot_drafts уже очищен (пользователь удалил / регенерация) — пропускаем.
    2. Фактический draft на сервере отличается от expected — пользователь
       отредактировал, не перезаписываем.
    """
    try:
        await asyncio.sleep(DRAFT_VERIFY_DELAY)

        key = (user_id, chat_id)
        # Пользователь уже очистил/отправил черновик или началась регенерация
        if _bot_drafts.get(key) != expected_text:
            return

        # Проверяем фактический draft на сервере — если пользователь
        # отредактировал, а on_pyrogram_draft задержался, не перезаписываем
        actual = await pyrogram_client.get_draft(user_id, chat_id)
        if actual is not None and actual != expected_text:
            if DEBUG_PRINT:
                print(
                    f"{get_timestamp()} [DRAFT] Skipping re-push for user {user_id} "
                    f"in chat {chat_id}: user edited draft"
                )
            return

        await pyrogram_client.set_draft(user_id, chat_id, expected_text)
        if DEBUG_PRINT:
            print(
                f"{get_timestamp()} [DRAFT] Re-pushed draft for user {user_id} "
                f"in chat {chat_id}: {len(expected_text)} chars"
            )
    except Exception:
        pass  # не ломаем основной поток

async def on_pyrogram_message(user_id: int, pyrogram_client_instance, message) -> None:
    """Вызывается при новом входящем сообщении в любом чате пользователя."""
    text = message.text
    transcribed_voice = False

    # Голосовое сообщение → транскрибируем
    if not text and message.voice:
        text = await pyrogram_client.transcribe_voice(
            user_id, message.chat.id, message.id
        )
        if text:
            transcribed_voice = True
            print(f"{get_timestamp()} [PYROGRAM] Voice transcribed for user {user_id} in chat {message.chat.id}: {len(text)} chars")

    # Стикер → используем эмодзи как текстовое представление
    if not text and message.sticker:
        emoji = message.sticker.emoji or STICKER_FALLBACK_EMOJI
        text = emoji

    if not text:
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

    # Saved Messages (чат с самим собой) — пропускаем
    if chat_id == user_id:
        return

    key = (user_id, chat_id)

    # Запоминаем ID последнего обработанного сообщения для polling
    msg_id = getattr(message, "id", None)
    if isinstance(msg_id, int):
        _last_seen_msg_id[key] = max(msg_id, _last_seen_msg_id.get(key, 0))

        # Дедупликация: пропускаем если уже обработали
        seen = _processed_incoming_ids.setdefault(key, set())
        if msg_id in seen:
            if DEBUG_PRINT:
                print(f"{get_timestamp()} [PYROGRAM] Duplicate message {msg_id} for user {user_id} in chat {chat_id}, skipping")
            return
        seen.add(msg_id)
        # Чистим старые ID
        if len(seen) > _PROCESSED_INCOMING_MAX:
            oldest = sorted(seen)[:len(seen) - _PROCESSED_INCOMING_MAX]
            seen.difference_update(oldest)

    # Читаем пользователя и настройки одним запросом
    user = await get_user(user_id)
    user_settings = (user or {}).get("settings") or {}
    lang = (user or {}).get("language_code")

    if DEBUG_PRINT:
        sender = message.from_user.first_name if message.from_user else "Unknown"
        print(
            f"{get_timestamp()} [PYROGRAM] New message for user {user_id} "
            f"from {sender} in chat {chat_id}: {len(text)} chars"
        )

    # Лок: если уже генерируем ответ для этого чата — ставим флаг и уходим.
    # Когда текущая генерация закончится, она увидит флаг и перегенерирует.
    _cancel_auto_reply(key)
    _bot_drafts.pop(key, None)
    if _reply_locks.get(key):
        _reply_pending[key] = True
        if DEBUG_PRINT:
            print(f"{get_timestamp()} [PYROGRAM] Reply locked for user {user_id} in chat {chat_id}, queued")
        return

    _reply_locks[key] = True
    try:
        # Показываем пользователю что бот работает
        probe_text = await get_system_message(lang, "draft_typing")
        _bot_draft_echoes[key] = probe_text
        await pyrogram_client.set_draft(user_id, chat_id, probe_text)

        # Читаем историю чата
        history = await pyrogram_client.read_chat_history(user_id, chat_id)
        if transcribed_voice:
            sender = message.from_user
            # Normalize date to UTC (Pyrogram may return naive local time)
            voice_date = message.date
            if isinstance(voice_date, datetime):
                voice_date = voice_date.astimezone(timezone.utc)
            history.append({
                "role": "other",
                "text": text,
                "date": voice_date,
                "name": sender.first_name if sender else None,
                "last_name": sender.last_name if sender else None,
                "username": sender.username if sender else None,
            })
        if not history:
            return

        # Информация об участниках для контекста AI
        opponent = message.from_user
        opponent_info = {
            "first_name": opponent.first_name,
            "last_name": opponent.last_name,
            "username": opponent.username,
            "language_code": opponent.language_code,
            "is_premium": opponent.is_premium,
        } if opponent else None

        # Генерируем ответ
        kwargs: dict = {}
        style = user_settings.get("style")
        if user_settings.get("pro_model"):
            pro_model = STYLE_PRO_MODELS.get(style)
            if pro_model is None:
                print(f"{get_timestamp()} [PYROGRAM] WARNING: style {style!r} not in STYLE_PRO_MODELS, using default")
                pro_model = STYLE_PRO_MODELS[None]
            kwargs["model"] = pro_model
        custom_prompt = user_settings.get("custom_prompt", "")
        tz_offset = user_settings.get("tz_offset", 0) or 0
        reply_text = await generate_reply(history, user, opponent_info, custom_prompt=custom_prompt, style=style, tz_offset=tz_offset, **kwargs)
        if not reply_text or not reply_text.strip():
            return

        # Устанавливаем черновик с AI-ответом
        ai_text = reply_text.strip()
        _bot_drafts[key] = ai_text
        _bot_draft_echoes[key] = ai_text
        await pyrogram_client.set_draft(user_id, chat_id, ai_text)

        print(f"{get_timestamp()} [PYROGRAM] Reply set as draft for user {user_id} in chat {chat_id}")
        asyncio.create_task(_verify_draft_delivery(user_id, chat_id, ai_text))

        # Запускаем таймер автоответа
        auto_reply = normalize_auto_reply(user_settings.get("auto_reply"))
        if auto_reply:
            _schedule_auto_reply(user_id, chat_id, ai_text, auto_reply)

    except Exception as e:
        print(f"{get_timestamp()} [PYROGRAM] ERROR processing message for user {user_id}: {e}")
    finally:
        _reply_locks.pop(key, None)
        if _reply_pending.pop(key, None):
            asyncio.create_task(_regenerate_reply(user_id, chat_id))


async def _regenerate_reply(user_id: int, chat_id: int) -> None:
    """Перегенерирует ответ на актуальной истории после снятия лока.

    Вызывается когда во время генерации пришли новые сообщения.
    Использует тот же лок для предотвращения параллельных вызовов.
    """
    key = (user_id, chat_id)

    # Проверяем лок (на случай гонки)
    if _reply_locks.get(key):
        _reply_pending[key] = True
        return

    _reply_locks[key] = True
    try:
        user = await get_user(user_id)
        user_settings = (user or {}).get("settings") or {}
        lang = (user or {}).get("language_code")

        # Показываем пробу (и обновляем _bot_drafts, чтобы _verify_draft_delivery
        # от предыдущего ответа не сделала ложный retry)
        probe_text = await get_system_message(lang, "draft_typing")
        _bot_drafts[key] = probe_text
        _bot_draft_echoes[key] = probe_text
        await pyrogram_client.set_draft(user_id, chat_id, probe_text)

        # Читаем актуальную историю
        history = await pyrogram_client.read_chat_history(user_id, chat_id)
        if not history:
            return

        # Извлекаем opponent из последнего входящего сообщения
        opponent_info = None
        for msg in reversed(history):
            if msg["role"] == "other" and msg.get("name"):
                opponent_info = {
                    "first_name": msg.get("name"),
                    "last_name": msg.get("last_name"),
                    "username": msg.get("username"),
                }
                break

        # Генерируем ответ
        kwargs: dict = {}
        style = user_settings.get("style")
        if user_settings.get("pro_model"):
            pro_model = STYLE_PRO_MODELS.get(style)
            if pro_model is None:
                print(f"{get_timestamp()} [PYROGRAM] WARNING: style {style!r} not in STYLE_PRO_MODELS, using default")
                pro_model = STYLE_PRO_MODELS[None]
            kwargs["model"] = pro_model
        custom_prompt = user_settings.get("custom_prompt", "")
        tz_offset = user_settings.get("tz_offset", 0) or 0
        reply_text = await generate_reply(
            history, user, opponent_info,
            custom_prompt=custom_prompt, style=style, tz_offset=tz_offset,
            **kwargs,
        )
        if not reply_text or not reply_text.strip():
            return

        ai_text = reply_text.strip()
        _bot_drafts[key] = ai_text
        _bot_draft_echoes[key] = ai_text
        await pyrogram_client.set_draft(user_id, chat_id, ai_text)

        print(f"{get_timestamp()} [PYROGRAM] Reply re-generated for user {user_id} in chat {chat_id}")
        asyncio.create_task(_verify_draft_delivery(user_id, chat_id, ai_text))

        auto_reply = normalize_auto_reply(user_settings.get("auto_reply"))
        if auto_reply:
            _schedule_auto_reply(user_id, chat_id, ai_text, auto_reply)

    except Exception as e:
        print(f"{get_timestamp()} [PYROGRAM] ERROR re-generating reply for user {user_id}: {e}")
    finally:
        _reply_locks.pop(key, None)
        if _reply_pending.pop(key, None):
            asyncio.create_task(_regenerate_reply(user_id, chat_id))


# Тексты черновиков, установленные ботом: {(user_id, chat_id): text}
_bot_drafts: dict[tuple[int, int], str] = {}

# Эхо от set_draft, которое нужно один раз проигнорировать: {(user_id, chat_id): text}
_bot_draft_echoes: dict[tuple[int, int], str] = {}

# Ожидающие проверки черновики пользователя: {(user_id, chat_id): instruction}
_pending_drafts: dict[tuple[int, int], str] = {}

# Активные таймеры автоответа: {(user_id, chat_id): asyncio.Task}
_auto_reply_tasks: dict[tuple[int, int], asyncio.Task] = {}

# Лок на генерацию ответа: {(user_id, chat_id): True}
_reply_locks: dict[tuple[int, int], bool] = {}

# Флаг «пришло новое сообщение во время генерации»: {(user_id, chat_id): True}
_reply_pending: dict[tuple[int, int], bool] = {}

# ID последнего обработанного входящего сообщения: {(user_id, chat_id): message_id}
_last_seen_msg_id: dict[tuple[int, int], int] = {}


def _cancel_auto_reply(key: tuple[int, int]) -> None:
    """Отменяет активный таймер автоответа для чата."""
    task = _auto_reply_tasks.pop(key, None)
    if task and not task.done():
        task.cancel()


def _schedule_auto_reply(user_id: int, chat_id: int, text: str, base_seconds: int) -> None:
    """Запускает таймер автоответа, отменяя предыдущий."""
    key = (user_id, chat_id)
    _cancel_auto_reply(key)
    task = asyncio.create_task(_auto_reply_worker(user_id, chat_id, text, base_seconds))
    _auto_reply_tasks[key] = task


async def _auto_reply_worker(user_id: int, chat_id: int, text: str, base_seconds: int) -> None:
    """Ждёт таймаут и отправляет сообщение, если черновик не изменился."""
    key = (user_id, chat_id)
    try:
        delay = base_seconds + random.uniform(0, base_seconds)
        await asyncio.sleep(delay)

        # Проверяем: черновик всё ещё наш?
        if _bot_drafts.get(key) != text:
            return

        sent = await pyrogram_client.send_message(user_id, chat_id, text)
        if sent:
            _bot_drafts.pop(key, None)
            _bot_draft_echoes.pop(key, None)
            print(f"{get_timestamp()} [AUTO-REPLY] Sent for user {user_id} in chat {chat_id} after {delay:.0f}s")

    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"{get_timestamp()} [AUTO-REPLY] ERROR for user {user_id} in chat {chat_id}: {e}")
    finally:
        _auto_reply_tasks.pop(key, None)


async def on_pyrogram_draft(user_id: int, chat_id: int, draft_text: str) -> None:
    """Вызывается при обновлении черновика — probe-based detection."""
    key = (user_id, chat_id)

    # Saved Messages (чат с самим собой) — пропускаем
    if chat_id == user_id:
        return

    # Игнорируем черновики, установленные ботом (пробел или AI-ответ)
    bot_echo_text = _bot_draft_echoes.get(key)
    if bot_echo_text is not None and draft_text == bot_echo_text:
        _bot_draft_echoes.pop(key, None)
        return

    # Пустой черновик — ничего не делаем
    if not draft_text:
        _pending_drafts.pop(key, None)
        _bot_drafts.pop(key, None)
        _cancel_auto_reply(key)
        return

    # Проверяем настройки пользователя одним запросом
    user = await get_user(user_id)
    user_settings = (user or {}).get("settings") or {}
    lang = (user or {}).get("language_code")
    if not user_settings.get("drafts_enabled", True):
        if DEBUG_PRINT:
            print(f"{get_timestamp()} [PYROGRAM] Drafts disabled for user {user_id}, skipping draft")
        return

    # Пользователь набрал текст — запоминаем как инструкцию
    instruction = draft_text
    _cancel_auto_reply(key)
    _bot_drafts.pop(key, None)

    if DEBUG_PRINT:
        print(
            f"{get_timestamp()} [DRAFT] User updated draft for {user_id} "
            f"in chat {chat_id}: {len(instruction)} chars"
        )

    # Сохраняем инструкцию и ставим пробу (статус-сообщение)
    probe_text = await get_system_message(lang, "draft_typing")
    _pending_drafts[key] = instruction
    _bot_draft_echoes[key] = probe_text
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
        print(
            f"{get_timestamp()} [DRAFT] Processing pending draft for {user_id} "
            f"in chat {chat_id}: {len(instruction)} chars"
        )

    try:
        # Читаем историю чата для контекста
        history = await pyrogram_client.read_chat_history(user_id, chat_id)

        # Формируем запрос: инструкция + контекст переписки

        # Определяем профиль оппонента из истории
        opponent_info = None
        for msg in history:
            if msg["role"] == "other" and msg.get("name"):
                opponent_info = {
                    "first_name": msg.get("name"),
                    "last_name": msg.get("last_name"),
                    "username": msg.get("username"),
                }
                break

        user_message = ""
        if history:
            tz_offset = user_settings.get("tz_offset", 0) or 0
            user_message = format_chat_history(history, user, opponent_info, tz_offset=tz_offset)
            user_message += "\n\n"
        user_message += f"INSTRUCTION: {instruction}"

        # Генерируем ответ
        style = user_settings.get("style")
        gen_kwargs: dict = {
            "user_message": user_message,
            "system_prompt": build_draft_prompt(
                has_history=bool(history),
                custom_prompt=user_settings.get("custom_prompt", ""),
                style=style,
            ),
        }
        if user_settings.get("pro_model"):
            pro_model = STYLE_PRO_MODELS.get(style)
            if pro_model is None:
                print(f"{get_timestamp()} [DRAFT] WARNING: style {style!r} not in STYLE_PRO_MODELS, using default")
                pro_model = STYLE_PRO_MODELS[None]
            gen_kwargs["model"] = pro_model
        response = await generate_response(**gen_kwargs)
        if not response or not response.strip():
            return

        # Устанавливаем черновик с AI-ответом и запоминаем
        ai_text = response.strip()
        _bot_drafts[key] = ai_text
        _bot_draft_echoes[key] = ai_text
        await pyrogram_client.set_draft(user_id, chat_id, ai_text)

        print(f"{get_timestamp()} [DRAFT] Response set as draft for user {user_id} in chat {chat_id}")
        asyncio.create_task(_verify_draft_delivery(user_id, chat_id, ai_text))

        # Запускаем таймер автоответа
        auto_reply = normalize_auto_reply(user_settings.get("auto_reply"))
        if auto_reply:
            _schedule_auto_reply(user_id, chat_id, ai_text, auto_reply)

    except Exception as e:
        print(f"{get_timestamp()} [DRAFT] ERROR processing draft for user {user_id}: {e}")


async def poll_missed_messages(user_id: int) -> int:
    """Проверяет приватные чаты пользователя на пропущенные сообщения.

    Находит входящие сообщения, которые не были обработаны on_pyrogram_message
    (например, Telegram не доставил update). Для каждого такого сообщения
    триггерит on_pyrogram_message.

    Returns:
        Количество найденных пропущенных сообщений.
    """
    if not pyrogram_client.is_active(user_id):
        return 0

    client = pyrogram_client._active_clients.get(user_id)
    chat_ids = await pyrogram_client.get_private_dialogs(user_id)
    found = 0

    for chat_id in chat_ids:
        # Saved Messages — пропускаем
        if chat_id == user_id:
            continue

        key = (user_id, chat_id)

        # Пропускаем чаты, где уже идёт генерация или стоит бот-черновик
        if _reply_locks.get(key) or _bot_drafts.get(key):
            continue

        msg = await pyrogram_client.get_last_incoming(user_id, chat_id)
        if not msg:
            continue

        # Уже обрабатывали — пропускаем
        if msg.id <= _last_seen_msg_id.get(key, 0):
            continue

        # Нет текста, голосового или стикера — пропускаем
        if not msg.text and not msg.voice and not msg.sticker:
            continue

        if msg.from_user and msg.from_user.is_bot:
            continue

        if DEBUG_PRINT:
            print(
                f"{get_timestamp()} [POLL] Missed message found for user {user_id} "
                f"in chat {chat_id}: msg_id={msg.id}"
            )
        found += 1

        await on_pyrogram_message(user_id, client, msg)

    return found

