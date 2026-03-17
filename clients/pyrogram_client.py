# clients/pyrogram_client.py — Управление Pyrogram-сессиями пользователей

import asyncio
from collections import defaultdict
from datetime import datetime, timezone

from pyrogram import Client, filters, raw
import pyrogram
from pyrogram.handlers import MessageHandler, RawUpdateHandler

from config import PYROGRAM_API_ID, PYROGRAM_API_HASH, MAX_CONTEXT_MESSAGES, DEBUG_PRINT, VOICE_TRANSCRIPTION_TIMEOUT, POLL_MISSED_DIALOGS_LIMIT, STICKER_FALLBACK_EMOJI
from utils.utils import get_timestamp


# Активные Pyrogram-клиенты: {user_id: Client}
_active_clients: dict[int, Client] = {}

# Состояние глобального exception handler event loop-а, который мы временно
# подменяем ради Pyrogram.
_loop_handler_state = {
    "loop": None,
    "previous_handler": None,
}

# Callback для обработки входящих сообщений (устанавливается из bot.py)
_on_new_message_callback = None

# Callback для обработки черновиков (устанавливается из bot.py)
_on_draft_callback = None

# ID сообщений, уже обработанных через MessageHandler (для дедупликации с RawUpdateHandler).
# Ключ — (chat_id, message_id), чтобы одинаковые message.id из разных диалогов
# не считались дубликатами. Последние 200 ключей на каждого пользователя.
_processed_msg_ids: dict[int, set[tuple[int, int]]] = defaultdict(set)
_PROCESSED_IDS_MAX = 200


def _make_processed_message_key(chat_id: int | None, message_id: int | None) -> tuple[int, int] | None:
    """Собирает ключ дедупликации сообщения."""
    if chat_id is None or message_id is None:
        return None
    return (chat_id, message_id)


def set_message_callback(callback) -> None:
    """Устанавливает callback для обработки входящих сообщений."""
    global _on_new_message_callback
    _on_new_message_callback = callback


def set_draft_callback(callback) -> None:
    """Устанавливает callback для обработки черновиков."""
    global _on_draft_callback
    _on_draft_callback = callback


async def create_client(user_id: int, session_string: str) -> Client:
    """Создаёт Pyrogram Client из session string."""
    client = Client(
        name=f"draftguru_{user_id}",
        api_id=PYROGRAM_API_ID,
        api_hash=PYROGRAM_API_HASH,
        session_string=session_string,
        in_memory=True,
    )
    return client


def _pyrogram_task_exception_handler(loop, context):
    """Обработчик исключений в asyncio-задачах Pyrogram.

    Pyrogram создаёт внутренние Task-и (handle_updates) которые могут
    бросать ValueError при получении update-ов из незнакомых supergroup/channel.
    Логируем как WARNING вместо полного traceback.
    """
    exception = context.get("exception")
    if isinstance(exception, ValueError) and "Peer id invalid" in str(exception):
        print(f"{get_timestamp()} [PYROGRAM] WARNING: {exception} (ignored)")
        return

    # Для всех остальных исключений делегируем предыдущему handler-у,
    # если он был настроен, иначе используем стандартный.
    previous_handler = _loop_handler_state["previous_handler"]
    if previous_handler:
        previous_handler(loop, context)
    else:
        loop.default_exception_handler(context)


def _install_pyrogram_exception_handler(loop) -> None:
    """Устанавливает обёртку над loop exception handler один раз."""
    if (
        _loop_handler_state["loop"] is loop
        and loop.get_exception_handler() is _pyrogram_task_exception_handler
    ):
        return

    _loop_handler_state["previous_handler"] = loop.get_exception_handler()
    loop.set_exception_handler(_pyrogram_task_exception_handler)
    _loop_handler_state["loop"] = loop


def _restore_pyrogram_exception_handler(loop) -> None:
    """Восстанавливает предыдущий loop exception handler."""
    if _loop_handler_state["loop"] is not loop:
        return

    if loop.get_exception_handler() is _pyrogram_task_exception_handler:
        loop.set_exception_handler(_loop_handler_state["previous_handler"])

    _loop_handler_state["previous_handler"] = None
    _loop_handler_state["loop"] = None


async def start_listening(user_id: int, session_string: str) -> bool:
    """Запускает Pyrogram-клиент и слушатель входящих сообщений.

    Args:
        user_id: Telegram user ID
        session_string: Pyrogram session string из БД

    Returns:
        True если запуск успешен
    """
    # Останавливаем предыдущий клиент, если есть
    await stop_listening(user_id)

    try:
        client = await create_client(user_id, session_string)
        loop = asyncio.get_running_loop()

        # Подавляем ValueError: Peer id invalid из внутренних задач Pyrogram
        _install_pyrogram_exception_handler(loop)

        await client.start()

        # Хендлер входящих сообщений (только личные чаты, не от себя)
        async def on_incoming(pyrogram_client_inst: Client, message):
            # Отмечаем как обработанное, чтобы raw handler не дублировал
            processed_key = _make_processed_message_key(
                getattr(message.chat, "id", None),
                getattr(message, "id", None),
            )
            if processed_key:
                _processed_msg_ids[user_id].add(processed_key)
            if _on_new_message_callback:
                await _on_new_message_callback(user_id, pyrogram_client_inst, message)

        client.add_handler(
            MessageHandler(on_incoming, filters.private & filters.incoming)
        )

        # Хендлер raw updates: черновики + fallback для пропущенных сообщений.
        # Pyrogram может уронить Message._parse() (например ValueError: Peer id invalid
        # в другом update того же батча) — тогда MessageHandler не вызовется.
        # RawUpdateHandler не проходит через _parse, поэтому более устойчив.
        async def on_raw(client_inst: Client, update, users, chats):
            if isinstance(update, raw.types.UpdateDraftMessage):
                await _handle_draft_update(user_id, update)
            elif isinstance(update, (raw.types.UpdateNewMessage,)):
                await _handle_raw_new_message(user_id, client_inst, update, users)

        client.add_handler(RawUpdateHandler(on_raw))

        _active_clients[user_id] = client
        print(f"{get_timestamp()} [PYROGRAM] Started listening for user {user_id}")
        return True

    except Exception as e:
        try:
            loop = asyncio.get_running_loop()
            if not _active_clients:
                _restore_pyrogram_exception_handler(loop)
        except RuntimeError:
            pass
        print(f"{get_timestamp()} [PYROGRAM] ERROR starting client for user {user_id}: {e}")
        return False


async def stop_listening(user_id: int) -> bool:
    """Останавливает Pyrogram-клиент пользователя."""
    client = _active_clients.get(user_id)
    if not client:
        return True

    try:
        loop = asyncio.get_running_loop()
        await client.stop()
        _active_clients.pop(user_id, None)
        if not _active_clients:
            _restore_pyrogram_exception_handler(loop)
        print(f"{get_timestamp()} [PYROGRAM] Stopped listening for user {user_id}")
        return True
    except Exception as e:
        print(f"{get_timestamp()} [PYROGRAM] ERROR stopping client for user {user_id}: {e}")
        return False


def is_active(user_id: int) -> bool:
    """Проверяет, активен ли Pyrogram-клиент пользователя."""
    return user_id in _active_clients


async def read_chat_history(user_id: int, chat_id: int, limit: int = MAX_CONTEXT_MESSAGES) -> list[dict]:
    """Читает последние сообщения из чата пользователя.

    Args:
        user_id: Telegram user ID
        chat_id: ID чата для чтения
        limit: Максимальное количество сообщений

    Returns:
        Список сообщений [{role: "user"/"other", text: "..."}]
    """
    client = _active_clients.get(user_id)
    if not client:
        return []

    messages = []
    try:
        async for msg in client.get_chat_history(chat_id, limit=limit):
            text = msg.text
            # Стикер → эмодзи как текстовое представление
            if not text and msg.sticker:
                text = msg.sticker.emoji or STICKER_FALLBACK_EMOJI
            if not text:
                continue

            role = "user" if msg.from_user and msg.from_user.id == user_id else "other"
            sender = msg.from_user
            # Pyrogram uses datetime.fromtimestamp() without tz — returns naive
            # local time.  Normalize to UTC so that downstream tz_offset math
            # (in format_chat_history) doesn't double-apply the server offset.
            date = msg.date
            if isinstance(date, datetime):
                date = date.astimezone(timezone.utc)
            messages.append({
                "role": role,
                "text": text,
                "date": date,
                "name": sender.first_name if sender else None,
                "last_name": sender.last_name if sender else None,
                "username": sender.username if sender else None,
            })

        # Переворачиваем — от старых к новым
        messages.reverse()

        if DEBUG_PRINT:
            print(f"{get_timestamp()} [PYROGRAM] Read {len(messages)} messages from chat {chat_id}")

    except Exception as e:
        print(f"{get_timestamp()} [PYROGRAM] ERROR reading chat {chat_id} for user {user_id}: {e}")

    return messages


async def get_private_dialogs(user_id: int, limit: int = POLL_MISSED_DIALOGS_LIMIT) -> list[int]:
    """Возвращает список chat_id приватных диалогов пользователя.

    Args:
        user_id: Telegram user ID
        limit: Максимальное количество диалогов

    Returns:
        Список chat_id приватных чатов
    """
    client = _active_clients.get(user_id)
    if not client:
        return []

    chat_ids: list[int] = []
    try:
        async for dialog in client.get_dialogs(limit):
            if dialog.chat and dialog.chat.type.value == "private":
                chat_ids.append(dialog.chat.id)
    except Exception as e:
        print(f"{get_timestamp()} [PYROGRAM] ERROR get_private_dialogs for user {user_id}: {e}")

    return chat_ids


async def get_dialog_info(user_id: int, limit: int) -> list[dict]:
    """Возвращает инфо о последних диалогах, кроме Saved Messages.

    Args:
        user_id: Telegram user ID
        limit: Максимальное количество диалогов

    Returns:
        [{chat_id, first_name, last_name, username, title}]
    """
    client = _active_clients.get(user_id)
    if not client:
        return []

    dialogs: list[dict] = []
    try:
        async for dialog in client.get_dialogs():
            chat = dialog.chat
            if not chat:
                continue
            # Пропускаем Saved Messages
            if chat.id == user_id:
                continue
            dialogs.append({
                "chat_id": chat.id,
                "first_name": chat.first_name or "",
                "last_name": chat.last_name or "",
                "username": chat.username or "",
                "title": chat.title or "",
            })
            if len(dialogs) >= limit:
                break
    except Exception as e:
        print(f"{get_timestamp()} [PYROGRAM] ERROR get_dialog_info for user {user_id}: {e}")

    return dialogs


def get_active_user_ids() -> list[int]:
    """Возвращает ID пользователей с активными Pyrogram-клиентами."""
    return list(_active_clients.keys())


async def get_last_incoming(user_id: int, chat_id: int) -> "pyrogram.types.Message | None":
    """Возвращает последнее входящее сообщение в чате (или None).

    Returns:
        pyrogram.types.Message | None
    """
    client = _active_clients.get(user_id)
    if not client:
        return None

    try:
        async for msg in client.get_chat_history(chat_id, limit=1):
            if not msg.outgoing:
                return msg
            return None  # последнее сообщение — исходящее
    except Exception as e:
        print(f"{get_timestamp()} [PYROGRAM] ERROR get_last_incoming for user {user_id} chat {chat_id}: {e}")

    return None


async def _handle_draft_update(user_id: int, update: raw.types.UpdateDraftMessage) -> None:
    """Обрабатывает raw UpdateDraftMessage — извлекает chat_id и текст, вызывает callback."""
    if not _on_draft_callback:
        return

    try:
        # Извлекаем chat_id из peer и конвертируем в стандартный Telegram формат:
        # PeerUser.user_id → положительный (без изменений)
        # PeerChat.chat_id → отрицательный (-chat_id)
        # PeerChannel.channel_id → отрицательный с префиксом -100 (-100channel_id)
        peer = update.peer
        if hasattr(peer, "user_id"):
            chat_id = peer.user_id
        elif hasattr(peer, "chat_id"):
            chat_id = -peer.chat_id
        elif hasattr(peer, "channel_id"):
            chat_id = int(f"-100{peer.channel_id}")
        else:
            return

        # Извлекаем текст черновика (может быть пустым при очистке)
        draft = update.draft
        draft_text = getattr(draft, "message", "") or ""

        if DEBUG_PRINT:
            print(
                f"{get_timestamp()} [PYROGRAM] Draft update for user {user_id} "
                f"in chat {chat_id}: {len(draft_text)} chars"
            )

        await _on_draft_callback(user_id, chat_id, draft_text.strip())

    except Exception as e:
        print(f"{get_timestamp()} [PYROGRAM] ERROR handling draft update for user {user_id}: {e}")


async def _handle_raw_new_message(
    user_id: int, client: Client, update: raw.types.UpdateNewMessage, users: dict,
) -> None:
    """Fallback-обработчик для UpdateNewMessage через RawUpdateHandler.

    Вызывается если Pyrogram не смог распарсить сообщение через MessageHandler
    (например, ValueError: Peer id invalid в батче). Проверяет дедупликацию
    по _processed_msg_ids.
    """
    try:
        msg = update.message

        # Только PeerUser (личные чаты)
        if not hasattr(msg, "peer_id") or not hasattr(msg.peer_id, "user_id"):
            return

        chat_id = msg.peer_id.user_id
        msg_id = getattr(msg, "id", None)
        processed_key = _make_processed_message_key(chat_id, msg_id)
        if processed_key and processed_key in _processed_msg_ids.get(user_id, set()):
            return

        # Исходящее сообщение — пропускаем
        if getattr(msg, "out", False):
            return

        # Пытаемся распарсить через Pyrogram (может упасть)
        try:
            parsed_message = await pyrogram.types.Message._parse(
                client, msg, users, {}
            )
        except Exception as parse_err:
            print(
                f"{get_timestamp()} [PYROGRAM] WARNING: failed to parse message "
                f"{msg_id} for user {user_id}: {parse_err}"
            )
            return

        # Отмечаем как обработанное
        ids_set = _processed_msg_ids[user_id]
        if processed_key:
            ids_set.add(processed_key)
            # Чистим старые ID, чтобы set не рос бесконечно
            if len(ids_set) > _PROCESSED_IDS_MAX:
                to_remove = sorted(ids_set)[:len(ids_set) - _PROCESSED_IDS_MAX]
                ids_set.difference_update(to_remove)

        if _on_new_message_callback:
            print(
                f"{get_timestamp()} [PYROGRAM] Recovered message {msg_id} "
                f"for user {user_id} via raw handler"
            )
            await _on_new_message_callback(user_id, client, parsed_message)

    except Exception as e:
        print(f"{get_timestamp()} [PYROGRAM] ERROR in raw message handler for user {user_id}: {e}")


async def set_draft(user_id: int, chat_id: int, text: str) -> bool:
    """Устанавливает черновик (draft) в чате пользователя.

    Args:
        user_id: Telegram user ID
        chat_id: ID чата
        text: Текст черновика

    Returns:
        True если установка успешна
    """
    client = _active_clients.get(user_id)
    if not client:
        return False

    try:
        peer = await client.resolve_peer(chat_id)
        await client.invoke(
            raw.functions.messages.SaveDraft(
                peer=peer,
                message=text,
            )
        )

        if DEBUG_PRINT:
            print(f"{get_timestamp()} [PYROGRAM] Draft set in chat {chat_id} for user {user_id}")
        return True

    except Exception as e:
        print(f"{get_timestamp()} [PYROGRAM] ERROR setting draft in chat {chat_id}: {e}")
        return False


async def get_draft(user_id: int, chat_id: int) -> str | None:
    """Читает текущий черновик из чата через GetPeerDialogs.

    Returns:
        Текст черновика или None если черновика нет / ошибка.
    """
    client = _active_clients.get(user_id)
    if not client:
        return None

    try:
        peer = await client.resolve_peer(chat_id)
        result = await client.invoke(
            raw.functions.messages.GetPeerDialogs(
                peers=[raw.types.InputDialogPeer(peer=peer)]
            )
        )
        for dialog in result.dialogs:
            draft = getattr(dialog, "draft", None)
            if draft and hasattr(draft, "message"):
                return draft.message
        return None

    except Exception as e:
        if DEBUG_PRINT:
            print(f"{get_timestamp()} [PYROGRAM] ERROR reading draft from chat {chat_id}: {e}")
        return None


async def send_message(user_id: int, chat_id: int, text: str) -> bool:
    """Отправляет сообщение от имени пользователя через Pyrogram.

    Args:
        user_id: Telegram user ID
        chat_id: ID чата
        text: Текст сообщения

    Returns:
        True если отправка успешна
    """
    client = _active_clients.get(user_id)
    if not client:
        return False

    try:
        await client.send_message(chat_id, text)

        if DEBUG_PRINT:
            print(f"{get_timestamp()} [PYROGRAM] Message sent in chat {chat_id} for user {user_id}")
        return True

    except Exception as e:
        print(f"{get_timestamp()} [PYROGRAM] ERROR sending message in chat {chat_id}: {e}")
        return False


async def transcribe_voice(user_id: int, chat_id: int, msg_id: int) -> str | None:
    """Транскрибирует голосовое сообщение через Telegram Premium TranscribeAudio.

    Args:
        user_id: Telegram user ID
        chat_id: ID чата
        msg_id: ID сообщения с голосовым

    Returns:
        Текст транскрипции или None при ошибке
    """
    client = _active_clients.get(user_id)
    if not client:
        return None

    try:
        peer = await client.resolve_peer(chat_id)
        result = await client.invoke(
            raw.functions.messages.TranscribeAudio(
                peer=peer,
                msg_id=msg_id,
            )
        )

        # Если транскрипция готова сразу
        if not result.pending:
            if DEBUG_PRINT:
                print(f"{get_timestamp()} [PYROGRAM] Transcribed voice in chat {chat_id}: {len(result.text)} chars")
            return result.text or None

        # Ждём UpdateTranscribedAudio через polling
        final_text = result.text or ""

        deadline = asyncio.get_event_loop().time() + VOICE_TRANSCRIPTION_TIMEOUT
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(1)
            # Повторяем запрос — Telegram вернёт обновлённый результат
            try:
                result = await client.invoke(
                    raw.functions.messages.TranscribeAudio(
                        peer=peer,
                        msg_id=msg_id,
                    )
                )
                if not result.pending:
                    final_text = result.text or ""
                    break
                final_text = result.text or final_text
            except Exception:
                break

        if final_text:
            if DEBUG_PRINT:
                print(f"{get_timestamp()} [PYROGRAM] Transcribed voice in chat {chat_id}: {len(final_text)} chars")
            return final_text

        return None

    except Exception as e:
        error_str = str(e)
        if "PREMIUM_ACCOUNT_REQUIRED" in error_str:
            print(f"{get_timestamp()} [PYROGRAM] WARNING: voice transcription requires Premium in chat {chat_id}")
        else:
            print(f"{get_timestamp()} [PYROGRAM] ERROR transcribing voice in chat {chat_id}: {e}")
        return None
