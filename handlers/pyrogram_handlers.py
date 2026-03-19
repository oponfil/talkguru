# handlers/pyrogram_handlers.py — Обработчики /connect, /disconnect, Pyrogram callback

import asyncio
import base64
import io
import random
import re
import time
import traceback
from collections import defaultdict
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
    DRAFT_PROBE_DELAY, DRAFT_VERIFY_DELAY, DEFAULT_STYLE, STYLE_TO_EMOJI, STICKER_FALLBACK_EMOJI,
    EMOJI_TO_STYLE, IGNORED_CHAT_IDS,
)
from utils.utils import (
    format_chat_history,
    get_effective_auto_reply,
    get_effective_drafts,
    get_effective_model,
    get_effective_prompt,
    get_effective_style,
    get_timestamp,
    is_chat_ignored,
    keep_typing,
    serialize_user_updates,
    typing_action,
)
from utils.bot_utils import update_user_menu
from clients.x402gate.openrouter import generate_response
from logic.reply import generate_reply
from clients import pyrogram_client
from database.users import clear_session, get_user, save_session, update_chat_style, update_last_msg_at
from system_messages import get_system_message
from prompts import build_draft_prompt
from utils.telegram_user import ensure_effective_user, upsert_effective_user


# ====== /disconnect ======

@serialize_user_updates
@typing_action
async def on_disconnect(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /disconnect — показывает предупреждение с подтверждением."""
    u = update.effective_user

    try:
        await ensure_effective_user(update)
    except Exception:
        msg = await get_system_message(u.language_code, "error")
        await update.message.reply_text(msg)
        return

    asyncio.create_task(update_last_msg_at(u.id))

    is_active = pyrogram_client.is_active(u.id)
    has_pending_2fa = u.id in _pending_2fa
    has_pending_phone = u.id in _pending_phone

    if DEBUG_PRINT:
        print(f"{get_timestamp()} [BOT] /disconnect from user {u.id} (@{u.username}, lang={u.language_code})")

    # Если нет активного подключения и нет pending-процессов — нечего отключать
    if not is_active and not has_pending_2fa and not has_pending_phone:
        # Всегда чистим stale-сессию в БД (идемпотентность)
        cleared = await clear_session(u.id)
        message_key = "status_disconnected" if cleared else "disconnect_error"
        msg = await get_system_message(u.language_code, message_key)
        await update.message.reply_text(msg)
        return

    # Показываем предупреждение с кнопками подтверждения
    msg = await get_system_message(u.language_code, "disconnect_confirm")
    confirm_label = await get_system_message(u.language_code, "disconnect_btn_confirm")
    cancel_label = await get_system_message(u.language_code, "disconnect_btn_cancel")
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(confirm_label, callback_data="disconnect:confirm"),
            InlineKeyboardButton(cancel_label, callback_data="disconnect:cancel"),
        ],
    ])
    await update.message.reply_text(msg, reply_markup=keyboard)


@serialize_user_updates
async def on_disconnect_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback кнопки 'Да, отключить' — выполняет отключение."""
    query = update.callback_query
    await query.answer()
    u = update.effective_user

    # Убираем кнопки
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    # Отменяем pending-процессы
    await cancel_pending_2fa(u.id)
    await cancel_pending_phone(u.id, bot=context.bot)

    is_active = pyrogram_client.is_active(u.id)

    if is_active:
        stopped = await pyrogram_client.stop_listening(u.id)
        if not stopped:
            msg = await get_system_message(u.language_code, "disconnect_error")
            await context.bot.send_message(chat_id=query.message.chat_id, text=msg)
            return

    cleared = await clear_session(u.id)
    if not cleared:
        msg = await get_system_message(u.language_code, "disconnect_error")
        await context.bot.send_message(chat_id=query.message.chat_id, text=msg)
        return

    msg = await get_system_message(u.language_code, "disconnect_success")
    await context.bot.send_message(chat_id=query.message.chat_id, text=msg)
    await update_user_menu(context.bot, u.id, u.language_code, is_connected=False)
    print(f"{get_timestamp()} [BOT] User {u.id} disconnected")


@serialize_user_updates
async def on_disconnect_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback кнопки 'Отмена' — убираем кнопки."""
    query = update.callback_query
    await query.answer()

    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass


# ====== /status ======

@serialize_user_updates
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

    asyncio.create_task(update_last_msg_at(u.id))

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


async def _get_pending_phone(user_id: int, bot: object | None = None) -> dict | None:
    """Возвращает активный phone-flow, очищая протухшее состояние."""
    pending = _pending_phone.get(user_id)
    if pending is None:
        return None

    expires_at = pending.get("expires_at")
    if expires_at is None or time.monotonic() < expires_at:
        return pending

    await cancel_pending_phone(user_id, bot=bot)
    return None


async def _delete_sensitive_messages(bot: object, chat_id: int, msg_ids: list[int]) -> None:
    """Удаляет собранные чувствительные сообщения (номер, код, пароль)."""
    for mid in msg_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=mid)
        except Exception as e:
            print(f"{get_timestamp()} [CONNECT_PHONE] Failed to delete message {mid}: {e}")


async def cancel_pending_phone(
    user_id: int, bot: object | None = None, client: Client | None = None,
) -> bool:
    """Отменяет незавершённый phone-логин, удаляет sensitive messages и чистит клиент."""
    pending = _pending_phone.pop(user_id, None)

    # Удаляем чувствительные сообщения при отмене/таймауте
    if pending is not None and bot is not None:
        msg_ids = pending.get("sensitive_msg_ids") or []
        chat_id = pending.get("chat_id")
        if msg_ids and chat_id:
            await _delete_sensitive_messages(bot, chat_id, msg_ids)

    pending_client = pending.get("client") if pending is not None else None
    client_to_disconnect = pending_client or client
    if client_to_disconnect is not None:
        await _safe_disconnect_temp_client(client_to_disconnect, user_id)

    return pending is not None


def _get_qr_login_task(user_id: int) -> asyncio.Task | None:
    """Возвращает активную QR-задачу пользователя."""
    task = _qr_login_tasks.get(user_id)
    if task and task.done():
        _qr_login_tasks.pop(user_id, None)
        return None
    return task


async def cancel_pending_2fa(user_id: int) -> bool:
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

@serialize_user_updates
@typing_action
async def on_connect(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /connect — подключение аккаунта (сначала телефон, кнопка QR)."""
    u = update.effective_user
    chat_id = update.effective_chat.id

    # reply helper: работает и при вызове через команду, и через callback
    async def reply(text: str, **kw: object) -> object:
        if update.message is not None:
            return await update.message.reply_text(text, **kw)
        else:
            return await context.bot.send_message(chat_id=chat_id, text=text, **kw)

    asyncio.create_task(update_last_msg_at(u.id))

    if DEBUG_PRINT:
        print(f"{get_timestamp()} [BOT] /connect from user {u.id} (@{u.username}, lang={u.language_code})")

    # Проверяем, не подключён ли уже
    if pyrogram_client.is_active(u.id):
        msg = await get_system_message(u.language_code, "connect_already")
        await reply(msg)
        return

    if _get_qr_login_task(u.id) is not None:
        msg = await get_system_message(u.language_code, "connect_in_progress")
        await reply(msg)
        return

    if _has_pending_2fa(u.id):
        msg = await get_system_message(u.language_code, "connect_in_progress")
        await reply(msg)
        return

    if await _get_pending_phone(u.id, bot=context.bot) is not None:
        msg = await get_system_message(u.language_code, "connect_in_progress")
        await reply(msg)
        return

    try:
        # /connect должен работать даже без предварительного /start
        if not await upsert_effective_user(update):
            msg = await get_system_message(u.language_code, "connect_error")
            await reply(msg)
            return

        # Регистрируем phone-flow (ожидание номера)
        _put_pending_phone(u.id, {
            "state": "awaiting_phone",
            "language_code": u.language_code,
            "chat_id": chat_id,
        })

        # Отправляем приглашение ввести номер с кнопкой QR
        msg = await get_system_message(u.language_code, "connect_phone_prompt")
        qr_label = await get_system_message(u.language_code, "connect_phone_btn_qr")
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(qr_label, callback_data="connect:qr")]
        ])
        sent = await reply(msg, reply_markup=keyboard)
        # Запоминаем ID приглашения для удаления при завершении flow
        if sent and hasattr(sent, "message_id"):
            pending = _pending_phone.get(u.id)
            if pending is not None:
                pending.setdefault("sensitive_msg_ids", []).append(sent.message_id)

    except Exception as e:
        print(f"{get_timestamp()} [BOT] ERROR connect for user {u.id}: {e}")
        traceback.print_exc()
        await cancel_pending_phone(u.id, bot=context.bot)
        try:
            msg = await get_system_message(u.language_code, "connect_error")
            await reply(msg)
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
        cancel_label = await get_system_message(language_code, "connect_btn_cancel")
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(cancel_label, callback_data="connect:cancel")]
        ])
        await bot.send_photo(chat_id=chat_id, photo=buf, caption=msg, reply_markup=keyboard)

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


@serialize_user_updates
async def on_connect_qr_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback кнопки 'QR-код' — переключает на QR-flow."""
    query = update.callback_query
    await query.answer()

    u = update.effective_user

    # Отменяем phone-flow
    await cancel_pending_phone(u.id, bot=context.bot)

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


@serialize_user_updates
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
            await cancel_pending_phone(u.id, bot=context.bot)
            msg = await get_system_message(
                pending.get("language_code"),
                _get_phone_timeout_message_key(pending.get("state", "")),
            )
            await context.bot.send_message(chat_id=pending.get("chat_id"), text=msg)
            raise ApplicationHandlerStop

    pending = await _get_pending_phone(u.id, bot=context.bot)
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
    """Обработка ввода номера телефона — валидируем и показываем подтверждение."""
    u = update.effective_user
    raw_input = (update.message.text or "").strip()
    # Нормализация: извлекаем только цифры, добавляем "+"
    # Принимает любой формат: +1 234 567 890, +49-123-4567, (+7) 999...
    digits = re.sub(r"\D", "", raw_input)
    phone_number = f"+{digits}"
    language_code = pending["language_code"]
    chat_id = pending["chat_id"]

    # Базовая валидация: минимум 7 цифр (E.164 minimum)
    if len(digits) < 7:
        # Сохраняем ID сообщения с номером для отложенного удаления (privacy)
        sensitive_msg_ids = list(pending.get("sensitive_msg_ids") or [])
        sensitive_msg_ids.append(update.message.message_id)
        _put_pending_phone(u.id, {
            **pending,
            "sensitive_msg_ids": sensitive_msg_ids,
        })
        # Невалидный номер — просим ввести заново, остаёмся в awaiting_phone
        msg = await get_system_message(language_code, "connect_phone_invalid")
        sent = await context.bot.send_message(chat_id=chat_id, text=msg)
        sensitive_msg_ids.append(sent.message_id)
        raise ApplicationHandlerStop

    # Собираем ID сообщения для отложенного удаления
    sensitive_msg_ids = list(pending.get("sensitive_msg_ids") or [])
    sensitive_msg_ids.append(update.message.message_id)

    # Переводим в состояние ожидания подтверждения (номер сохраняем)
    _put_pending_phone(u.id, {
        "state": "awaiting_confirm",
        "phone_number": phone_number,
        "language_code": language_code,
        "chat_id": chat_id,
        "sensitive_msg_ids": sensitive_msg_ids,
    })

    # Показываем нормализованный номер и кнопки Да / Нет (в один ряд)
    msg = await get_system_message(language_code, "connect_phone_confirm")
    msg = msg.replace("{phone_number}", phone_number)
    confirm_label = await get_system_message(language_code, "connect_phone_btn_confirm")
    cancel_label = await get_system_message(language_code, "connect_phone_btn_cancel")
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(confirm_label, callback_data="connect:confirm_phone"),
            InlineKeyboardButton(cancel_label, callback_data="connect:cancel_phone"),
        ],
    ])
    sent = await context.bot.send_message(chat_id=chat_id, text=msg, reply_markup=keyboard)
    # Запоминаем ID бот-сообщения для удаления при завершении flow
    sensitive_msg_ids.append(sent.message_id)

    raise ApplicationHandlerStop


@serialize_user_updates
async def on_confirm_phone_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback кнопки 'Да' — отправляем код подтверждения."""
    query = update.callback_query
    await query.answer()

    u = update.effective_user
    pending = await _get_pending_phone(u.id, bot=context.bot)

    if pending is None or pending.get("state") != "awaiting_confirm":
        msg = await get_system_message(u.language_code, "connect_phone_timeout")
        await query.edit_message_text(msg)
        return

    phone_number = pending["phone_number"]
    language_code = pending["language_code"]
    chat_id = pending["chat_id"]
    client: Client | None = None

    # confirm message уже отслеживается из _handle_phone_number
    sensitive_msg_ids = list(pending.get("sensitive_msg_ids") or [])

    # Убираем кнопки — оставляем только текст
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    async with keep_typing(context.bot, chat_id):
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
                "chat_id": chat_id,
                "sensitive_msg_ids": sensitive_msg_ids,
            })

            msg = await get_system_message(language_code, "connect_code_prompt")
            cancel_label = await get_system_message(language_code, "connect_btn_cancel")
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(cancel_label, callback_data="connect:cancel")]
            ])
            sent = await context.bot.send_message(chat_id=chat_id, text=msg, reply_markup=keyboard)
            # Запоминаем ID сообщения с просьбой ввести код
            sensitive_msg_ids.append(sent.message_id)

            if DEBUG_PRINT:
                print(f"{get_timestamp()} [CONNECT_PHONE] Code sent to user {u.id}")

        except Exception as e:
            error_name = type(e).__name__

            if "PhoneNumberInvalid" in error_name:
                # Возвращаем в awaiting_phone — пользователь может ввести повторно
                _put_pending_phone(u.id, {
                    "state": "awaiting_phone",
                    "language_code": language_code,
                    "chat_id": chat_id,
                    "sensitive_msg_ids": pending.get("sensitive_msg_ids") or [],
                })
                msg = await get_system_message(language_code, "connect_phone_invalid")
                await context.bot.send_message(chat_id=chat_id, text=msg)
                if client is not None:
                    await _safe_disconnect_temp_client(client, u.id)
                return

            if "FloodWait" in error_name:
                seconds = getattr(e, "value", getattr(e, "x", 0))
                msg = await get_system_message(language_code, "connect_flood_wait")
                msg = msg.replace("{seconds}", str(seconds))
                await context.bot.send_message(chat_id=chat_id, text=msg)
                await cancel_pending_phone(u.id, bot=context.bot, client=client)
                return

            print(f"{get_timestamp()} [CONNECT_PHONE] ERROR send_code for user {u.id}: {e}")
            traceback.print_exc()
            msg = await get_system_message(language_code, "connect_error")
            await context.bot.send_message(chat_id=chat_id, text=msg)
            await cancel_pending_phone(u.id, bot=context.bot, client=client)


@serialize_user_updates
async def on_cancel_phone_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback кнопки 'Нет' — возвращаем пользователя к вводу номера."""
    query = update.callback_query
    await query.answer()

    u = update.effective_user
    pending = await _get_pending_phone(u.id, bot=context.bot)

    if pending is None or pending.get("state") != "awaiting_confirm":
        msg = await get_system_message(u.language_code, "connect_phone_timeout")
        await query.edit_message_text(msg)
        return

    language_code = pending["language_code"]
    chat_id = pending["chat_id"]

    # Возвращаем в awaiting_phone (сохраняем sensitive_msg_ids)
    _put_pending_phone(u.id, {
        "state": "awaiting_phone",
        "language_code": language_code,
        "chat_id": chat_id,
        "sensitive_msg_ids": pending.get("sensitive_msg_ids") or [],
    })

    # Просим ввести номер заново
    msg = await get_system_message(language_code, "connect_phone_prompt")
    qr_label = await get_system_message(language_code, "connect_phone_btn_qr")
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(qr_label, callback_data="connect:qr")]
    ])
    await query.edit_message_text(msg, reply_markup=keyboard)


@serialize_user_updates
async def on_connect_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback кнопки 'Отмена' — отменяет текущий connect-flow (QR или phone)."""
    query = update.callback_query
    await query.answer()

    u = update.effective_user
    language_code = u.language_code

    # Отменяем QR-flow
    qr_task = _get_qr_login_task(u.id)
    if qr_task is not None:
        qr_task.cancel()
        _qr_login_tasks.pop(u.id, None)

    # Отменяем 2FA-flow
    await cancel_pending_2fa(u.id)

    # Отменяем phone-flow (удаляет чувствительные сообщения)
    await cancel_pending_phone(u.id, bot=context.bot)

    # Убираем кнопку
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    msg = await get_system_message(language_code, "connect_phone_timeout")
    await context.bot.send_message(chat_id=update.effective_chat.id, text=msg)


async def _handle_phone_code(
    update: Update, context: ContextTypes.DEFAULT_TYPE, pending: dict,
) -> None:
    """Обработка ввода кода подтверждения."""
    u = update.effective_user
    raw_code = (update.message.text or "").strip()
    # Убираем всё кроме цифр — пользователь вводит код с любыми разделителями
    # (буквы, пробелы, дефисы и т.д.), чтобы Telegram не распознал login code.
    code = re.sub(r"\D", "", raw_code)
    client: Client = pending["client"]
    language_code = pending["language_code"]
    chat_id = pending["chat_id"]

    # Собираем ID сообщения для отложенного удаления
    sensitive_msg_ids = list(pending.get("sensitive_msg_ids") or [])
    sensitive_msg_ids.append(update.message.message_id)
    pending["sensitive_msg_ids"] = sensitive_msg_ids

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

        await _finalize_phone_login(u.id, client, language_code, context.bot, chat_id, sensitive_msg_ids=pending.get("sensitive_msg_ids"))

    except Exception as e:
        error_name = type(e).__name__

        if "SessionPasswordNeeded" in error_name:
            # 2FA требуется — переводим в awaiting_2fa
            _put_pending_phone(u.id, {**_pending_phone[u.id], "state": "awaiting_2fa"})
            msg = await get_system_message(language_code, "connect_2fa_prompt")
            sent = await context.bot.send_message(chat_id=chat_id, text=msg)
            _pending_phone[u.id].setdefault("sensitive_msg_ids", []).append(sent.message_id)
            if DEBUG_PRINT:
                print(f"{get_timestamp()} [CONNECT_PHONE] 2FA required for user {u.id}")
            raise ApplicationHandlerStop

        if "PhoneCodeInvalid" in error_name:
            # Неверный код — остаёмся в awaiting_code, даём попробовать ещё раз
            print(f"{get_timestamp()} [CONNECT_PHONE] WARNING: invalid code for user {u.id}")
            _put_pending_phone(u.id, pending)
            msg = await get_system_message(language_code, "connect_code_invalid")
            sent = await context.bot.send_message(chat_id=chat_id, text=msg)
            pending.setdefault("sensitive_msg_ids", []).append(sent.message_id)
            if raw_code.isdigit():
                hint = await get_system_message(language_code, "connect_code_no_separator")
                sent_hint = await context.bot.send_message(chat_id=chat_id, text=hint)
                pending.setdefault("sensitive_msg_ids", []).append(sent_hint.message_id)
            raise ApplicationHandlerStop

        if "PhoneCodeExpired" in error_name:
            print(f"{get_timestamp()} [CONNECT_PHONE] WARNING: code expired for user {u.id}")
            # Хинт про разделители трекаем для удаления; expired/blocked оставляем (ссылка /connect)
            if raw_code.isdigit():
                hint = await get_system_message(language_code, "connect_code_no_separator")
                sent_hint = await context.bot.send_message(chat_id=chat_id, text=hint)
                pending.setdefault("sensitive_msg_ids", []).append(sent_hint.message_id)
                blocked = await get_system_message(language_code, "connect_code_blocked")
                await context.bot.send_message(chat_id=chat_id, text=blocked)
            else:
                msg = await get_system_message(language_code, "connect_code_expired")
                await context.bot.send_message(chat_id=chat_id, text=msg)
            await cancel_pending_phone(u.id, bot=context.bot)
            raise ApplicationHandlerStop

        print(f"{get_timestamp()} [CONNECT_PHONE] ERROR sign_in for user {u.id}: {e}")
        traceback.print_exc()
        msg = await get_system_message(language_code, "connect_error")
        sent_err = await context.bot.send_message(chat_id=chat_id, text=msg)
        pending.setdefault("sensitive_msg_ids", []).append(sent_err.message_id)
        await cancel_pending_phone(u.id, bot=context.bot)

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

    # Собираем ID сообщения для отложенного удаления
    sensitive_msg_ids = list(pending.get("sensitive_msg_ids") or [])
    sensitive_msg_ids.append(update.message.message_id)
    pending["sensitive_msg_ids"] = sensitive_msg_ids

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

        await _finalize_phone_login(u.id, client, language_code, context.bot, chat_id, sensitive_msg_ids=pending.get("sensitive_msg_ids"))

    except Exception as e:
        error_name = type(e).__name__

        if "PasswordHashInvalid" in error_name:
            print(f"{get_timestamp()} [CONNECT_PHONE] Wrong 2FA password for user {u.id}")
            _put_pending_phone(u.id, pending)
            msg = await get_system_message(language_code, "connect_2fa_wrong_password")
            sent = await context.bot.send_message(chat_id=chat_id, text=msg)
            pending.setdefault("sensitive_msg_ids", []).append(sent.message_id)
            # Остаёмся в awaiting_2fa
            raise ApplicationHandlerStop

        print(f"{get_timestamp()} [CONNECT_PHONE] 2FA error for user {u.id}: {e}")
        traceback.print_exc()
        msg = await get_system_message(language_code, "connect_2fa_error")
        sent_err = await context.bot.send_message(chat_id=chat_id, text=msg)
        pending.setdefault("sensitive_msg_ids", []).append(sent_err.message_id)
        await cancel_pending_phone(u.id, bot=context.bot)

    raise ApplicationHandlerStop


async def _finalize_phone_login(
    user_id: int, client: Client, language_code: str, bot: object, chat_id: int,
    sensitive_msg_ids: list[int] | None = None,
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
        # Удаляем чувствительные сообщения после завершения (успех или ошибка)
        if sensitive_msg_ids:
            await _delete_sensitive_messages(bot, chat_id, sensitive_msg_ids)


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
                        if "SessionPasswordNeeded" in type(migrate_err).__name__:
                            _pending_2fa[user_id] = {
                                "client": client,
                                "language_code": language_code,
                                "bot": bot,
                                "chat_id": chat_id,
                            }
                            msg = await get_system_message(language_code, "connect_2fa_prompt")
                            await bot.send_message(chat_id=chat_id, text=msg)
                            print(f"{get_timestamp()} [CONNECT_QR] 2FA required after migration for user {user_id}")
                            return  # НЕ отключаем client — нужен для check_password
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

        # Сохраняем user_id/is_bot из результата авторизации (без этого struct.pack падает)
        auth = getattr(result, "authorization", result)
        user_obj = getattr(auth, "user", None)
        if user_obj:
            await client.storage.user_id(user_obj.id)
            await client.storage.is_bot(getattr(user_obj, "bot", False))

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


async def _is_user_typing(user_id: int, chat_id: int) -> bool:
    """Проверяет, печатает ли пользователь (есть не-бот-черновик)."""
    key = (user_id, chat_id)
    existing = await pyrogram_client.get_draft(user_id, chat_id)
    if existing and existing.strip() and _bot_drafts.get(key) != existing:
        if DEBUG_PRINT:
            print(
                f"{get_timestamp()} [PYROGRAM] User is typing in chat {chat_id}, "
                f"skipping generation for user {user_id}"
            )
        return True
    return False

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

    # Global ignore: Saved Messages + системные чаты (777000 и т.д.) — без обращения к БД
    if chat_id == user_id or chat_id in IGNORED_CHAT_IDS:
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

    # Per-user ignore: пользователь пометил чат как 🔇 в /chats (из БД)
    if is_chat_ignored(user_settings, chat_id):
        return

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

    # Если пользователь сейчас печатает — не трогаем чат.
    # Генерация запустится позже через on_pyrogram_draft, когда пользователь уйдёт.
    if await _is_user_typing(user_id, chat_id):
        return

    if _reply_locks.get(key):
        _reply_pending[key] = True
        if DEBUG_PRINT:
            print(f"{get_timestamp()} [PYROGRAM] Reply locked for user {user_id} in chat {chat_id}, queued")
        return

    _reply_locks[key] = True
    try:
        # Показываем пользователю что бот работает
        style = get_effective_style(user_settings, chat_id)
        style_emoji = STYLE_TO_EMOJI.get(style, "🦉")
        probe_text = (await get_system_message(lang, "draft_typing")).format(emoji=style_emoji)
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
        style = get_effective_style(user_settings, chat_id)
        model = get_effective_model(user_settings, style)
        if model:
            kwargs["model"] = model
        custom_prompt = get_effective_prompt(user_settings, chat_id)
        tz_offset = user_settings.get("tz_offset", 0) or 0
        reply_text = await generate_reply(history, user, opponent_info, custom_prompt=custom_prompt, style=style, tz_offset=tz_offset, **kwargs)
        if not reply_text or not reply_text.strip():
            return

        # Устанавливаем черновик с AI-ответом
        ai_text = reply_text.strip()
        _bot_drafts[key] = ai_text
        _bot_draft_echoes[key] = ai_text
        await pyrogram_client.set_draft(user_id, chat_id, ai_text)
        _track_replied_chat(user_id, chat_id)

        print(f"{get_timestamp()} [PYROGRAM] Reply set as draft for user {user_id} in chat {chat_id}")
        asyncio.create_task(_verify_draft_delivery(user_id, chat_id, ai_text))

        # Запускаем таймер автоответа
        _maybe_schedule_auto_reply(user_settings, user_id, chat_id, ai_text)

    except Exception as e:
        print(f"{get_timestamp()} [PYROGRAM] ERROR processing message for user {user_id}: {e}")
    finally:
        _reply_locks.pop(key, None)
        if _reply_pending.pop(key, None):
            asyncio.create_task(_regenerate_reply(user_id, chat_id))

async def _generate_reply_for_chat(
    user_id: int, chat_id: int,
    user: dict | None, user_settings: dict, lang: str | None,
) -> None:
    """Генерирует ответ для чата (используется emoji-шорткатом и другими).

    Предполагает, что draft_typing проба уже установлена.
    """
    key = (user_id, chat_id)
    draft_replaced = False

    if _reply_locks.get(key):
        _reply_pending[key] = True
        return

    _reply_locks[key] = True
    try:
        async def _clear_probe_draft() -> None:
            """Убирает probe-черновик, если финальный ответ так и не был установлен."""
            if draft_replaced:
                return
            _bot_drafts.pop(key, None)
            _bot_draft_echoes.pop(key, None)
            await pyrogram_client.set_draft(user_id, chat_id, "")

        history = await pyrogram_client.read_chat_history(user_id, chat_id)
        if not history:
            await _clear_probe_draft()
            return

        # Определяем оппонента из истории
        opponent_info = None
        for msg in reversed(history):
            if msg["role"] == "other" and msg.get("name"):
                opponent_info = {
                    "first_name": msg.get("name"),
                    "last_name": msg.get("last_name"),
                    "username": msg.get("username"),
                }
                break

        kwargs: dict = {}
        style = get_effective_style(user_settings, chat_id)
        model = get_effective_model(user_settings, style)
        if model:
            kwargs["model"] = model
        custom_prompt = get_effective_prompt(user_settings, chat_id)
        tz_offset = user_settings.get("tz_offset", 0) or 0
        reply_text = await generate_reply(
            history, user, opponent_info,
            custom_prompt=custom_prompt, style=style, tz_offset=tz_offset,
            **kwargs,
        )
        if not reply_text or not reply_text.strip():
            await _clear_probe_draft()
            return

        ai_text = reply_text.strip()
        _bot_drafts[key] = ai_text
        _bot_draft_echoes[key] = ai_text
        await pyrogram_client.set_draft(user_id, chat_id, ai_text)
        _track_replied_chat(user_id, chat_id)
        draft_replaced = True

        print(f"{get_timestamp()} [DRAFT] Emoji reply set as draft for user {user_id} in chat {chat_id}")
        asyncio.create_task(_verify_draft_delivery(user_id, chat_id, ai_text))

        _maybe_schedule_auto_reply(user_settings, user_id, chat_id, ai_text)

    except Exception as e:
        print(f"{get_timestamp()} [PYROGRAM] ERROR _generate_reply_for_chat for user {user_id}: {e}")
        if not draft_replaced:
            _bot_drafts.pop(key, None)
            _bot_draft_echoes.pop(key, None)
            await pyrogram_client.set_draft(user_id, chat_id, "")
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

        # Если пользователь сейчас печатает — не трогаем чат
        if await _is_user_typing(user_id, chat_id):
            return

        # Показываем пробу (и обновляем _bot_drafts, чтобы _verify_draft_delivery
        # от предыдущего ответа не сделала ложный retry)
        style = get_effective_style(user_settings, chat_id)
        style_emoji = STYLE_TO_EMOJI.get(style, "🦉")
        probe_text = (await get_system_message(lang, "draft_typing")).format(emoji=style_emoji)
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
        style = get_effective_style(user_settings, chat_id)
        model = get_effective_model(user_settings, style)
        if model:
            kwargs["model"] = model
        custom_prompt = get_effective_prompt(user_settings, chat_id)
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
        _track_replied_chat(user_id, chat_id)

        print(f"{get_timestamp()} [PYROGRAM] Reply re-generated for user {user_id} in chat {chat_id}")
        asyncio.create_task(_verify_draft_delivery(user_id, chat_id, ai_text))

        _maybe_schedule_auto_reply(user_settings, user_id, chat_id, ai_text)

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

# Чаты, в которых бот реально ответил (set_draft / send_message): {user_id: {chat_id, ...}}
_replied_chats: dict[int, set[int]] = defaultdict(set)


def _track_replied_chat(user_id: int, chat_id: int) -> None:
    """Запоминает чат, в котором бот поставил черновик или отправил сообщение."""
    _replied_chats[user_id].add(chat_id)


def get_replied_chats(user_id: int) -> set[int]:
    """Возвращает set chat_id, в которых бот реально ответил (in-memory)."""
    return set(_replied_chats.get(user_id, set()))


def _maybe_schedule_auto_reply(
    user_settings: dict, user_id: int, chat_id: int, text: str,
) -> None:
    """Запускает таймер автоответа, если per-chat или глобальный auto_reply включён."""
    # Global ignore: Saved Messages + системные чаты — без обращения к БД
    if chat_id == user_id or chat_id in IGNORED_CHAT_IDS:
        return
    # Per-user ignore: sentinel -1 не пройдёт > 0 проверку
    auto_reply = get_effective_auto_reply(user_settings, chat_id)
    if auto_reply and auto_reply > 0:
        _schedule_auto_reply(user_id, chat_id, text, auto_reply)


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
            _track_replied_chat(user_id, chat_id)
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

    # Global ignore: Saved Messages + системные чаты — без обращения к БД
    if chat_id == user_id or chat_id in IGNORED_CHAT_IDS:
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
    if not get_effective_drafts(user_settings):
        if DEBUG_PRINT:
            print(f"{get_timestamp()} [PYROGRAM] Drafts disabled for user {user_id}, skipping draft")
        return

    # Per-user ignore: пользователь пометил чат как 🔇 в /chats (из БД)
    if is_chat_ignored(user_settings, chat_id):
        return

    # Emoji-шорткат: пользователь ставит emoji стиля (опционально + инструкцию)
    emoji_style = None
    instruction = draft_text
    stripped = draft_text.strip()
    for emoji, style_key in EMOJI_TO_STYLE.items():
        if stripped.startswith(emoji):
            emoji_style = style_key
            instruction = stripped[len(emoji):].strip()
            break

    if emoji_style is not None or (stripped in EMOJI_TO_STYLE):
        # Сохраняем per-chat стиль
        if emoji_style is None:
            emoji_style = EMOJI_TO_STYLE[stripped]
        
        # Сбрасываем override (передаем None), если выбранный стиль совпадает с глобальным
        global_style = user_settings.get("style") or DEFAULT_STYLE
        override_value = None if emoji_style == global_style else emoji_style
        await update_chat_style(user_id, chat_id, override_value)
        if DEBUG_PRINT:
            print(
                f"{get_timestamp()} [DRAFT] Emoji style shortcut for user {user_id} "
                f"in chat {chat_id}: {emoji_style!r}"
            )
        # Перечитываем настройки после обновления стиля
        user = await get_user(user_id)
        user_settings = (user or {}).get("settings") or {}

        if not instruction:
            # Только emoji без инструкции — генерируем ответ как on_pyrogram_message
            _cancel_auto_reply(key)
            _bot_drafts.pop(key, None)
            probe_text = (await get_system_message(lang, "draft_typing")).format(emoji=STYLE_TO_EMOJI.get(emoji_style, "🦉"))
            _bot_draft_echoes[key] = probe_text
            await pyrogram_client.set_draft(user_id, chat_id, probe_text)
            await _generate_reply_for_chat(user_id, chat_id, user, user_settings, lang)
            return
        # Если есть инструкция — продолжаем обычную обработку ниже

    # Пользователь набрал текст — запоминаем как инструкцию
    _cancel_auto_reply(key)
    _bot_drafts.pop(key, None)

    if DEBUG_PRINT:
        print(
            f"{get_timestamp()} [DRAFT] User updated draft for {user_id} "
            f"in chat {chat_id}: {len(instruction)} chars"
        )

    # Сохраняем инструкцию и ставим пробу (статус-сообщение)
    style = get_effective_style(user_settings, chat_id)
    style_emoji = STYLE_TO_EMOJI.get(style, "🦉")
    probe_text = (await get_system_message(lang, "draft_typing")).format(emoji=style_emoji)
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
        style = get_effective_style(user_settings, chat_id)
        gen_kwargs: dict = {
            "user_message": user_message,
            "system_prompt": build_draft_prompt(
                has_history=bool(history),
                custom_prompt=get_effective_prompt(user_settings, chat_id),
                style=style,
            ),
        }
        model = get_effective_model(user_settings, style)
        if model:
            gen_kwargs["model"] = model
        response = await generate_response(**gen_kwargs)
        if not response or not response.strip():
            return

        # Устанавливаем черновик с AI-ответом и запоминаем
        ai_text = response.strip()
        _bot_drafts[key] = ai_text
        _bot_draft_echoes[key] = ai_text
        await pyrogram_client.set_draft(user_id, chat_id, ai_text)
        _track_replied_chat(user_id, chat_id)

        print(f"{get_timestamp()} [DRAFT] Response set as draft for user {user_id} in chat {chat_id}")
        asyncio.create_task(_verify_draft_delivery(user_id, chat_id, ai_text))

        # Запускаем таймер автоответа
        _maybe_schedule_auto_reply(user_settings, user_id, chat_id, ai_text)

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

    # Читаем настройки для per-user ignore
    user = await get_user(user_id)
    user_settings = (user or {}).get("settings") or {}

    for chat_id in chat_ids:
        # Global ignore: Saved Messages + системные чаты — без обращения к БД
        if chat_id == user_id or chat_id in IGNORED_CHAT_IDS:
            continue

        # Per-user ignore: пользователь пометил чат как 🔇 в /chats (из БД)
        if is_chat_ignored(user_settings, chat_id):
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

