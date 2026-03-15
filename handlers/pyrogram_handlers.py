# handlers/pyrogram_handlers.py — Обработчики /connect, /disconnect, Pyrogram callback

import asyncio
import base64
import io
import traceback

import qrcode
from pyrogram import Client
from pyrogram.raw.functions.account import GetPassword
from pyrogram.raw.functions.auth import CheckPassword, ExportLoginToken, ImportLoginToken
from pyrogram.session import Session as PyroSession
from pyrogram.session.auth import Auth
from pyrogram.utils import compute_password_check
from telegram import Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

from config import (
    PYROGRAM_API_ID, PYROGRAM_API_HASH, DEBUG_PRINT,
    QR_LOGIN_TIMEOUT_SECONDS, QR_LOGIN_POLL_INTERVAL,
    DRAFT_PROBE_DELAY, LLM_MODEL_PRO,
)
from utils.utils import get_timestamp, typing_action, format_chat_history
from utils.bot_utils import update_user_menu
from clients.x402gate.openrouter import generate_reply, generate_response
from clients import pyrogram_client
from database.users import clear_session, get_user, get_user_settings, save_session, upsert_user
from system_messages import get_system_message
from prompts import build_draft_prompt


# ====== /disconnect ======

@typing_action
async def on_disconnect(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /disconnect — отключает аккаунт."""
    u = update.effective_user
    is_active = pyrogram_client.is_active(u.id)
    had_pending_2fa = await _cancel_pending_2fa(u.id)

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

    if not is_active and not had_pending_2fa:
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


def _get_chat_type(update: Update) -> str | None:
    """Возвращает тип чата Telegram как строку."""
    chat = update.effective_chat
    chat_type = getattr(chat, "type", None)
    return getattr(chat_type, "value", chat_type)


def _has_pending_2fa(user_id: int) -> bool:
    """Проверяет, ожидается ли у пользователя ввод 2FA-пароля."""
    return user_id in _pending_2fa


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
    """Обработчик команды /connect — подключение аккаунта через QR-код."""
    u = update.effective_user
    client: Client | None = None

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

    try:
        # /connect должен работать даже без предварительного /start
        await upsert_user(
            user_id=u.id,
            username=u.username,
            first_name=u.first_name,
            last_name=u.last_name,
            is_bot=u.is_bot,
            is_premium=bool(u.is_premium),
            language_code=u.language_code,
        )

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
        task = asyncio.create_task(
            _poll_qr_login(
                client,
                u.id,
                u.language_code,
                context.bot,
                update.effective_chat.id,
            )
        )
        _register_qr_login_task(u.id, task)
        client = None

    except Exception as e:
        print(f"{get_timestamp()} [BOT] ERROR connect for user {u.id}: {e}")
        traceback.print_exc()
        msg = await get_system_message(u.language_code, "connect_error")
        await update.message.reply_text(msg)
        if client is not None:
            await _safe_disconnect_temp_client(client, u.id)


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

    # Читаем настройки пользователя
    user_settings = await get_user_settings(user_id)

    if DEBUG_PRINT:
        sender = message.from_user.first_name if message.from_user else "Unknown"
        print(
            f"{get_timestamp()} [PYROGRAM] New message for user {user_id} "
            f"from {sender} in chat {chat_id}: {len(message.text)} chars"
        )

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
        if user_settings.get("pro_model"):
            kwargs["model"] = LLM_MODEL_PRO
        custom_prompt = user_settings.get("custom_prompt", "")
        reply_text = await generate_reply(history, user, opponent_info, custom_prompt=custom_prompt, **kwargs)
        if not reply_text or not reply_text.strip():
            return

        # Устанавливаем черновик с AI-ответом
        ai_text = reply_text.strip()
        _bot_drafts[(user_id, chat_id)] = ai_text
        await pyrogram_client.set_draft(user_id, chat_id, ai_text)

        print(f"{get_timestamp()} [PYROGRAM] Reply set as draft for user {user_id} in chat {chat_id}")

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

    # Проверяем настройку drafts_enabled
    user_settings = await get_user_settings(user_id)
    if not user_settings.get("drafts_enabled", True):
        if DEBUG_PRINT:
            print(f"{get_timestamp()} [PYROGRAM] Drafts disabled for user {user_id}, skipping draft")
        return

    # Пользователь набрал текст — запоминаем как инструкцию
    instruction = draft_text

    if DEBUG_PRINT:
        print(
            f"{get_timestamp()} [DRAFT] User updated draft for {user_id} "
            f"in chat {chat_id}: {len(instruction)} chars"
        )

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
            user_message = format_chat_history(history, user, opponent_info)
            user_message += "\n\n"
        user_message += f"INSTRUCTION: {instruction}"

        # Генерируем ответ
        gen_kwargs: dict = {
            "user_message": user_message,
            "system_prompt": build_draft_prompt(
                has_history=bool(history),
                custom_prompt=user_settings.get("custom_prompt", ""),
            ),
        }
        if user_settings.get("pro_model"):
            gen_kwargs["model"] = LLM_MODEL_PRO
        response = await generate_response(**gen_kwargs)
        if not response or not response.strip():
            return

        # Устанавливаем черновик с AI-ответом и запоминаем
        ai_text = response.strip()
        _bot_drafts[key] = ai_text
        await pyrogram_client.set_draft(user_id, chat_id, ai_text)

        print(f"{get_timestamp()} [DRAFT] Response set as draft for user {user_id} in chat {chat_id}")

    except Exception as e:
        print(f"{get_timestamp()} [DRAFT] ERROR processing draft for user {user_id}: {e}")
