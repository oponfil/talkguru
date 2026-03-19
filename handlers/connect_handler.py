# handlers/connect_handler.py — Обработчики /connect (phone, QR, 2FA)

import asyncio
import base64
import io
import re
import time
import traceback

import qrcode
from pyrogram import Client
from pyrogram.raw.functions.account import GetPassword
from pyrogram.raw.functions.auth import CheckPassword, ExportLoginToken, ImportLoginToken
from pyrogram.session import Session as PyroSession
from pyrogram.session.auth import Auth
from pyrogram.utils import compute_password_check
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

from config import (
    PYROGRAM_API_ID, PYROGRAM_API_HASH, DEBUG_PRINT,
    PHONE_CODE_TIMEOUT_SECONDS,
    QR_LOGIN_TIMEOUT_SECONDS, QR_LOGIN_POLL_INTERVAL,
)
from utils.utils import (
    get_timestamp,
    keep_typing,
    serialize_user_updates,
    typing_action,
)
from utils.bot_utils import update_user_menu
from clients import pyrogram_client
from database.users import clear_session, save_session, update_last_msg_at
from system_messages import get_system_message
from utils.telegram_user import upsert_effective_user


# --- Состояние connect-flow ---
# Три отдельных dict'а: QR-flow (фоновый polling + cleanup) и phone-flow (многошаговая
# state-machine с таймаутами) имеют разную логику и жизненный цикл.
# _pending_2fa — промежуточная фаза QR-flow: после сканирования QR потребовался 2FA-пароль,
# polling-задача уже завершилась, но клиент остаётся живым для check_password.

# QR-flow: фоновая задача polling + ID сообщений для cleanup
# {user_id: {"task": Task, "sensitive_msg_ids": list[int], "chat_id": int}}
_qr_login_tasks: dict[int, dict] = {}

# QR-flow, фаза 2FA: клиент ожидает ввода пароля после успешного сканирования QR
# {user_id: {"client": Client, "language_code": str, "bot": Bot, "chat_id": int}}
_pending_2fa: dict[int, dict] = {}

# Phone-flow: многошаговая state-machine (ввод номера → подтверждение → код → 2FA)
# {user_id: {"state": str, "client": Client, ..., "sensitive_msg_ids": list[int], "expires_at": float}}
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


async def _delete_sensitive_messages(bot: Bot, chat_id: int, msg_ids: list[int]) -> None:
    """Удаляет собранные чувствительные сообщения (номер, код, пароль)."""
    for mid in msg_ids:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=mid)
        except Exception as e:
            print(f"{get_timestamp()} [CONNECT_PHONE] Failed to delete message {mid}: {e}")


async def cancel_pending_phone(
    user_id: int, bot: Bot | None = None, client: Client | None = None,
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
    entry = _qr_login_tasks.get(user_id)
    if entry is None:
        return None
    task = entry["task"]
    if task.done():
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


async def clear_pending_input(context: ContextTypes.DEFAULT_TYPE, user_id: int, bot: Bot | None) -> None:
    """Сбрасывает все состояния ожидания текстового ввода (prompt + connect flow)."""
    context.user_data.pop("awaiting_prompt", None)
    context.user_data.pop("awaiting_chat_prompt", None)
    await cancel_pending_2fa(user_id)
    await cancel_pending_phone(user_id, bot=bot)


def _register_qr_login_task(
    user_id: int, task: asyncio.Task,
    sensitive_msg_ids: list[int] | None = None, chat_id: int | None = None,
) -> None:
    """Регистрирует фоновую QR-задачу до её завершения."""
    _qr_login_tasks[user_id] = {
        "task": task, "sensitive_msg_ids": sensitive_msg_ids or [], "chat_id": chat_id,
    }

    def _cleanup(done_task: asyncio.Task) -> None:
        entry = _qr_login_tasks.get(user_id)
        if entry is not None and entry["task"] is done_task:
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
    user_id: int, language_code: str, bot: Bot, chat_id: int,
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
        qr_sent = await bot.send_photo(chat_id=chat_id, photo=buf, caption=msg, reply_markup=keyboard)
        sensitive_msg_ids = []
        qr_mid = getattr(qr_sent, "message_id", None)
        if qr_mid is not None:
            sensitive_msg_ids.append(qr_mid)

        if DEBUG_PRINT:
            print(f"{get_timestamp()} [CONNECT_QR] QR sent to user {user_id}, waiting for scan...")

        # Запускаем polling в фоне
        task = asyncio.create_task(
            _poll_qr_login(client, user_id, language_code, bot, chat_id, sensitive_msg_ids=sensitive_msg_ids)
        )
        _register_qr_login_task(user_id, task, sensitive_msg_ids=sensitive_msg_ids, chat_id=chat_id)
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

    async with keep_typing(context.bot, update.effective_chat.id):
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
                msg = await get_system_message(language_code, "connect_phone_invalid")
                sent_err = await context.bot.send_message(chat_id=chat_id, text=msg)
                sensitive_msg_ids.append(sent_err.message_id)
                _put_pending_phone(u.id, {
                    "state": "awaiting_phone",
                    "language_code": language_code,
                    "chat_id": chat_id,
                    "sensitive_msg_ids": sensitive_msg_ids,
                })
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

    # Отменяем QR-flow; cleanup выполнит сама фоновая задача в finally
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
    user_id: int, client: Client, language_code: str, bot: Bot, chat_id: int,
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


async def _poll_qr_login(
    client: Client, user_id: int, language_code: str, bot: Bot, chat_id: int,
    sensitive_msg_ids: list[int] | None = None,
) -> None:
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
        # Удаляем чувствительные сообщения QR-flow (QR-фото и др.)
        if sensitive_msg_ids:
            await _delete_sensitive_messages(bot, chat_id, sensitive_msg_ids)


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
