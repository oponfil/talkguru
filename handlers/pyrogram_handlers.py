# handlers/pyrogram_handlers.py — Обработчики /disconnect, /status, Pyrogram callback

import asyncio
import random
from collections import defaultdict
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config import (
    DEBUG_PRINT,
    DRAFT_PROBE_DELAY, DRAFT_VERIFY_DELAY, DEFAULT_STYLE, STYLE_TO_EMOJI, STICKER_FALLBACK_EMOJI,
    EMOJI_TO_STYLE, IGNORED_CHAT_IDS, MAX_REGENERATIONS,
)
from utils.utils import (
    format_chat_history,
    get_effective_auto_reply,
    get_effective_model,
    get_effective_prompt,
    get_effective_style,
    get_timestamp,
    is_chat_ignored,
    is_chat_specifically_ignored,
    serialize_user_updates,
    typing_action,
)
from utils.bot_utils import update_user_menu
from clients.x402gate.openrouter import generate_response
from dashboard import stats as dash_stats
from logic.reply import generate_reply
from clients import pyrogram_client
from database.users import clear_session, get_user, update_chat_style, update_last_msg_at
from system_messages import get_system_message
from prompts import build_draft_prompt
from utils.telegram_user import ensure_effective_user
from clients.vision_client import analyze_photo_bytes
from handlers.connect_handler import (
    _pending_2fa, _pending_phone,
    cancel_pending_2fa, cancel_pending_phone,
)


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
    dash_stats.record_command("/disconnect")

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

    # Очищаем in-memory состояние follow-up для этого пользователя
    _follow_up_done.difference_update({k for k in _follow_up_done if k[0] == u.id})

    msg = await get_system_message(u.language_code, "disconnect_success")
    await context.bot.send_message(chat_id=query.message.chat_id, text=msg)
    await update_user_menu(context.bot, u.id, u.language_code, is_connected=False)
    print(f"{get_timestamp()} [BOT] User {u.id} disconnected")


@serialize_user_updates
async def on_disconnect_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Callback кнопки 'Отмена' — убираем кнопки и показываем статус."""
    query = update.callback_query
    await query.answer()
    u = update.effective_user

    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    # Показываем текущий статус подключения
    if pyrogram_client.is_active(u.id):
        msg = await get_system_message(u.language_code, "status_connected")
    else:
        msg = await get_system_message(u.language_code, "status_disconnected")
    await context.bot.send_message(chat_id=query.message.chat_id, text=msg)


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
    dash_stats.record_command("/status")

    if pyrogram_client.is_active(u.id):
        msg = await get_system_message(u.language_code, "status_connected")
    else:
        msg = await get_system_message(u.language_code, "status_disconnected")

    await update.message.reply_text(msg)






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
    if existing and existing.strip():
        bot_draft = _bot_drafts.get(key)
        bot_echo = _bot_draft_echoes.get(key)
        if existing != bot_draft and existing != bot_echo:
            if DEBUG_PRINT:
                print(
                    f"{get_timestamp()} [PYROGRAM] User is typing in chat {chat_id}, "
                    f"skipping generation for user {user_id}"
                )
            return True
    return False

async def on_pyrogram_message(user_id: int, pyrogram_client_instance, message) -> None:
    """Вызывается при новом входящем сообщении в любом чате пользователя."""
    text = message.text or message.caption

    # Голосовое сообщение → транскрибируем
    if not text and message.voice:
        text = await pyrogram_client.transcribe_voice(
            user_id, message.chat.id, message.id
        )
        if text:
            print(f"{get_timestamp()} [PYROGRAM] Voice transcribed for user {user_id} in chat {message.chat.id}: {len(text)} chars")
            dash_stats.record_voice_transcription()
        else:
            text = "[voice message]"

    # Стикер → используем эмодзи как текстовое представление
    if not text and message.sticker:
        emoji = message.sticker.emoji or STICKER_FALLBACK_EMOJI
        text = emoji

    # Фото без подписи — ставим заглушку, чтобы пробить if not text
    if not text and message.photo:
        text = "[photo]"

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

    # === Распознавание фото (Vision) ===
    if message.photo:
        try:
            if DEBUG_PRINT:
                print(f"{get_timestamp()} [VISION] Downloading photo for user {user_id} in {chat_id}...")
            file_bytes = await pyrogram_client_instance.download_media(message, in_memory=True)
            if file_bytes:
                photo_desc = await analyze_photo_bytes(file_bytes.getvalue())
                if photo_desc:
                    dash_stats.record_photo_recognition()
                    pyrogram_client.cache_photo_description(message.photo.file_unique_id, photo_desc)
        except Exception as e:
            print(f"{get_timestamp()} [VISION] ERROR processing photo for user {user_id}: {e}")

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

    # Входящее сообщение от собеседника — сбрасываем флаг follow-up (anti-loop)
    _follow_up_done.discard((user_id, chat_id))

    if DEBUG_PRINT:
        sender = message.from_user.first_name if message.from_user else "Unknown"
        print(
            f"{get_timestamp()} [PYROGRAM] New message for user {user_id} "
            f"from {sender} in chat {chat_id}: {len(text)} chars"
        )

    # Лок: если уже генерируем ответ для этого чата — ставим флаг и уходим.
    # Когда текущая генерация закончится, она увидит флаг и перегенерирует.
    _cancel_auto_reply(key)

    if _reply_locks.get(key):
        _reply_pending[key] = True
        if DEBUG_PRINT:
            print(f"{get_timestamp()} [PYROGRAM] Reply locked for user {user_id} in chat {chat_id}, queued")
        return

    # Если пользователь сейчас печатает — не трогаем чат.
    # Генерация запустится позже через on_pyrogram_draft, когда пользователь уйдёт.
    if await _is_user_typing(user_id, chat_id):
        return

    _bot_drafts.pop(key, None)

    _reply_locks[key] = True
    draft_replaced = False
    try:
        async def _clear_probe_draft() -> None:
            if draft_replaced:
                return
            _bot_drafts.pop(key, None)
            _bot_draft_echoes.pop(key, None)
            try:
                await pyrogram_client.set_draft(user_id, chat_id, "")
            except Exception:
                pass

        # Показываем пользователю что бот работает
        style = get_effective_style(user_settings, chat_id)
        style_emoji = STYLE_TO_EMOJI.get(style, "🦉")
        probe_text = (await get_system_message(lang, "draft_typing")).format(emoji=style_emoji)
        _bot_drafts[key] = probe_text
        _bot_draft_echoes[key] = probe_text
        await pyrogram_client.set_draft(user_id, chat_id, probe_text)

        opponent = message.from_user
        opponent_info = {
            "first_name": opponent.first_name,
            "last_name": opponent.last_name,
            "username": opponent.username,
            "language_code": opponent.language_code,
            "is_premium": opponent.is_premium,
            "bio": await pyrogram_client.get_chat_bio(user_id, chat_id),
            "phone_number": opponent.phone_number,
        } if opponent else None

        draft_replaced = await _run_generation_loop(
            user_id, chat_id, user, user_settings, style, opponent_info, _clear_probe_draft,
        )

    except Exception as e:
        print(f"{get_timestamp()} [PYROGRAM] ERROR processing message for user {user_id}: {e}")
        await _clear_probe_draft()
    finally:
        _reply_locks.pop(key, None)
        _reply_pending.pop(key, None)

async def _extract_opponent_from_history(
    history: list[dict], user_id: int, chat_id: int,
) -> dict | None:
    """Извлекает информацию об оппоненте из истории чата."""
    for msg in reversed(history):
        if msg["role"] == "other" and msg.get("name"):
            return {
                "first_name": msg.get("name"),
                "last_name": msg.get("last_name"),
                "username": msg.get("username"),
                "bio": await pyrogram_client.get_chat_bio(user_id, chat_id),
                "phone_number": msg.get("phone_number"),
            }
    return None


async def _run_generation_loop(
    user_id: int, chat_id: int,
    user: dict | None, user_settings: dict, style: str,
    opponent_info: dict | None,
    clear_probe: "asyncio.coroutines" = None,
) -> bool:
    """Цикл генерации с перегенерацией при поступлении новых сообщений.

    Возвращает True, если черновик с AI-ответом был установлен.
    Используется как в on_pyrogram_message, так и в _generate_reply_for_chat.
    """
    key = (user_id, chat_id)

    for iteration in range(MAX_REGENERATIONS):
        _reply_pending.pop(key, None)

        history = await pyrogram_client.read_chat_history(user_id, chat_id)
        if not history:
            if clear_probe:
                await clear_probe()
            return False

        # Для emoji-шорткатов opponent_info берётся из истории
        effective_opponent = opponent_info
        if effective_opponent is None:
            effective_opponent = await _extract_opponent_from_history(history, user_id, chat_id)

        kwargs: dict = {}
        model = get_effective_model(user_settings, style)
        if model:
            kwargs["model"] = model
        custom_prompt = get_effective_prompt(user_settings, chat_id)
        tz_offset = user_settings.get("tz_offset", 0) or 0
        reply_text = await generate_reply(
            history, user, effective_opponent,
            custom_prompt=custom_prompt, style=style, tz_offset=tz_offset,
            **kwargs,
        )

        # Проверяем, не пришло ли новое сообщение за время генерации
        if _reply_pending.get(key) and iteration < MAX_REGENERATIONS - 1:
            print(
                f"{get_timestamp()} [PYROGRAM] New message arrived! Regenerating draft "
                f"(iteration {iteration + 2}/{MAX_REGENERATIONS}) for user {user_id} in chat {chat_id}..."
            )
            continue

        if not reply_text or not reply_text.strip():
            if clear_probe:
                await clear_probe()
            return False

        ai_text = reply_text.strip()
        _bot_drafts[key] = ai_text
        _bot_draft_echoes[key] = ai_text
        await pyrogram_client.set_draft(user_id, chat_id, ai_text)
        _track_replied_chat(user_id, chat_id)

        print(f"{get_timestamp()} [DRAFT] Reply set as draft for user {user_id} in chat {chat_id}")
        dash_stats.record_draft(style)
        asyncio.create_task(_verify_draft_delivery(user_id, chat_id, ai_text))

        _maybe_schedule_auto_reply(user_settings, user_id, chat_id, ai_text)
        return True

    return False


async def _generate_reply_for_chat(
    user_id: int, chat_id: int,
    user: dict | None, user_settings: dict, lang: str | None,
) -> None:
    """Генерирует ответ для чата (используется emoji-шорткатом и другими).

    Предполагает, что draft_typing проба уже установлена.
    """
    key = (user_id, chat_id)

    if _reply_locks.get(key):
        _reply_pending[key] = True
        return

    _reply_locks[key] = True
    draft_replaced = False
    try:
        async def _clear_probe_draft() -> None:
            """Убирает probe-черновик, если финальный ответ так и не был установлен."""
            if draft_replaced:
                return
            _bot_drafts.pop(key, None)
            _bot_draft_echoes.pop(key, None)
            await pyrogram_client.set_draft(user_id, chat_id, "")

        style = get_effective_style(user_settings, chat_id)
        draft_replaced = await _run_generation_loop(
            user_id, chat_id, user, user_settings, style,
            opponent_info=None, clear_probe=_clear_probe_draft,
        )

    except Exception as e:
        print(f"{get_timestamp()} [PYROGRAM] ERROR _generate_reply_for_chat for user {user_id}: {e}")
        if not draft_replaced:
            _bot_drafts.pop(key, None)
            _bot_draft_echoes.pop(key, None)
            await pyrogram_client.set_draft(user_id, chat_id, "")
    finally:
        _reply_locks.pop(key, None)
        _reply_pending.pop(key, None)


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

# Follow-up: чаты, в которых follow-up уже отправлен (anti-loop).
# Сбрасывается при входящем сообщении от собеседника.
_follow_up_done: set[tuple[int, int]] = set()


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
            try:
                await pyrogram_client.set_draft(user_id, chat_id, "")
            except Exception:
                pass
            print(f"{get_timestamp()} [AUTO-REPLY] Sent for user {user_id} in chat {chat_id} after {delay:.0f}s")
            dash_stats.record_auto_reply()

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
    if (bot_echo_text is not None and draft_text == bot_echo_text) or draft_text == _bot_drafts.get(key):
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

    # Emoji-шорткат: пользователь ставит emoji стиля (опционально + инструкцию)
    emoji_style = None
    instruction = draft_text
    stripped = draft_text.strip()
    for emoji, style_key in EMOJI_TO_STYLE.items():
        if stripped.startswith(emoji):
            emoji_style = style_key
            instruction = stripped[len(emoji):].strip()
            break

    is_emoji_shortcut = emoji_style is not None or (stripped in EMOJI_TO_STYLE)

    # Жесткий ручной игнор на конкретный чат — блокирует всё, даже шорткаты
    if is_chat_specifically_ignored(user_settings, chat_id):
        return

    # Глобальный игнор: блокируем обычный набор текста, 
    # НО если это ручной вызов через эмодзи-шорткат — пробиваем глобальный блок.
    if is_chat_ignored(user_settings, chat_id) and not is_emoji_shortcut:
        return

    if is_emoji_shortcut:
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
            # Только emoji без инструкции — ждём выхода из чата, потом генерируем
            _cancel_auto_reply(key)
            _bot_drafts.pop(key, None)
            _pending_drafts[key] = draft_text

            await asyncio.sleep(DRAFT_PROBE_DELAY)

            # Проверяем: пользователь не изменил черновик за время ожидания
            if _pending_drafts.get(key) != draft_text:
                return
            _pending_drafts.pop(key, None)

            if DEBUG_PRINT:
                print(
                    f"{get_timestamp()} [DRAFT] Processing emoji shortcut for {user_id} "
                    f"in chat {chat_id}: {emoji_style!r}"
                )

            # Пользователь вышел — показываем пробу и генерируем
            probe_text = (await get_system_message(lang, "draft_typing")).format(emoji=STYLE_TO_EMOJI.get(emoji_style, "🦉"))
            _bot_drafts[key] = probe_text
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
    _bot_drafts[key] = probe_text
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
                    "bio": await pyrogram_client.get_chat_bio(user_id, chat_id),
                    "phone_number": msg.get("phone_number"),
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
        dash_stats.record_draft(style)
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


async def poll_follow_ups(user_id: int) -> int:
    """Проверяет чаты пользователя на необходимость отправки follow-up сообщения.

    Алгоритм: если последнее сообщение в чате — наше (исходящее), и прошло больше
    follow_up секунд, и follow-up ещё не отправлен — генерируем и отправляем.

    Returns:
        Количество отправленных follow-up сообщений.
    """
    if not pyrogram_client.is_active(user_id):
        return 0

    user = await get_user(user_id)
    user_settings = (user or {}).get("settings") or {}
    chat_follow_ups = user_settings.get("chat_follow_ups") or {}
    if not chat_follow_ups:
        return 0

    sent = 0
    for chat_id_str, timer_value in chat_follow_ups.items():
        try:
            chat_id = int(chat_id_str)
        except ValueError:
            continue

        # 0 = явно OFF
        if not timer_value or timer_value <= 0:
            continue

        key = (user_id, chat_id)

        # Anti-loop: уже отправляли follow-up для этого чата
        if key in _follow_up_done:
            continue

        # Не мешаем, если идёт генерация или стоит бот-черновик
        if _reply_locks.get(key) or _bot_drafts.get(key):
            continue

        # Получаем последнее сообщение в чате
        msg = await pyrogram_client.get_last_message(user_id, chat_id)
        if not msg:
            continue

        # Если последнее сообщение от собеседника — follow-up не нужен
        if not msg.outgoing:
            continue

        # Проверяем таймер
        if not msg.date:
            continue
        msg_date = msg.date.astimezone(timezone.utc) if msg.date.tzinfo else msg.date.replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - msg_date).total_seconds()
        if elapsed < timer_value:
            continue

        # Генерируем follow-up
        try:
            history = await pyrogram_client.read_chat_history(user_id, chat_id)
            if not history:
                continue

            opponent_info = await _extract_opponent_from_history(history, user_id, chat_id)

            style = get_effective_style(user_settings, chat_id)
            model = get_effective_model(user_settings, style)
            custom_prompt = get_effective_prompt(user_settings, chat_id)
            tz_offset = user_settings.get("tz_offset", 0) or 0

            # AI prompt suffix (не UI-строка) — инструкция для модели по генерации follow-up
            hours = int(elapsed // 3600)
            follow_up_instruction = (
                f"\n\nFOLLOW-UP CONTEXT: The other person has not replied to your last message "
                f"for {hours} hour(s). Write a short, natural follow-up message to re-engage "
                f"the conversation. Do NOT repeat or paraphrase your previous message. "
                f"Be casual and non-desperate."
            )
            effective_prompt = (custom_prompt or "") + follow_up_instruction

            kwargs: dict = {}
            if model:
                kwargs["model"] = model
            reply_text = await generate_reply(
                history, user, opponent_info,
                custom_prompt=effective_prompt, style=style, tz_offset=tz_offset,
                **kwargs,
            )
            if not reply_text or not reply_text.strip():
                continue

            # Отправляем сообщение
            success = await pyrogram_client.send_message(user_id, chat_id, reply_text.strip())
            if success:
                _follow_up_done.add(key)
                _track_replied_chat(user_id, chat_id)
                sent += 1
                print(
                    f"{get_timestamp()} [FOLLOW-UP] Sent follow-up for user {user_id} "
                    f"in chat {chat_id} (elapsed {hours}h)"
                )
                dash_stats.record_draft(style)

        except Exception as e:
            print(f"{get_timestamp()} [FOLLOW-UP] ERROR for user {user_id} chat {chat_id}: {e}")

    return sent

