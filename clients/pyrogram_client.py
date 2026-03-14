# clients/pyrogram_client.py — Управление Pyrogram-сессиями пользователей

from pyrogram import Client, filters, raw
from pyrogram.handlers import MessageHandler, RawUpdateHandler

from config import PYROGRAM_API_ID, PYROGRAM_API_HASH, MAX_CONTEXT_MESSAGES, DEBUG_PRINT
from utils.utils import get_timestamp


# Активные Pyrogram-клиенты: {user_id: Client}
_active_clients: dict[int, Client] = {}

# Callback для обработки входящих сообщений (устанавливается из bot.py)
_on_new_message_callback = None

# Callback для обработки черновиков (устанавливается из bot.py)
_on_draft_callback = None


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
        name=f"talkguru_{user_id}",
        api_id=PYROGRAM_API_ID,
        api_hash=PYROGRAM_API_HASH,
        session_string=session_string,
        in_memory=True,
    )
    return client


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
        await client.start()

        # Хендлер входящих сообщений (только личные чаты, не от себя)
        async def on_incoming(pyrogram_client: Client, message):
            if _on_new_message_callback:
                await _on_new_message_callback(user_id, pyrogram_client, message)

        client.add_handler(
            MessageHandler(on_incoming, filters.private & filters.incoming)
        )

        # Хендлер черновиков (raw update)
        async def on_raw(client: Client, update, users, chats):
            if isinstance(update, raw.types.UpdateDraftMessage):
                await _handle_draft_update(user_id, update)

        client.add_handler(RawUpdateHandler(on_raw))

        _active_clients[user_id] = client
        print(f"{get_timestamp()} [PYROGRAM] Started listening for user {user_id}")
        return True

    except Exception as e:
        print(f"{get_timestamp()} [PYROGRAM] ERROR starting client for user {user_id}: {e}")
        return False


async def stop_listening(user_id: int) -> None:
    """Останавливает Pyrogram-клиент пользователя."""
    client = _active_clients.pop(user_id, None)
    if client:
        try:
            await client.stop()
            print(f"{get_timestamp()} [PYROGRAM] Stopped listening for user {user_id}")
        except Exception as e:
            print(f"{get_timestamp()} [PYROGRAM] ERROR stopping client for user {user_id}: {e}")


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
            if not msg.text:
                continue

            role = "user" if msg.from_user and msg.from_user.id == user_id else "other"
            messages.append({"role": role, "text": msg.text})

        # Переворачиваем — от старых к новым
        messages.reverse()

        if DEBUG_PRINT:
            print(f"{get_timestamp()} [PYROGRAM] Read {len(messages)} messages from chat {chat_id}")

    except Exception as e:
        print(f"{get_timestamp()} [PYROGRAM] ERROR reading chat {chat_id} for user {user_id}: {e}")

    return messages


async def _handle_draft_update(user_id: int, update: raw.types.UpdateDraftMessage) -> None:
    """Обрабатывает raw UpdateDraftMessage — извлекает chat_id и текст, вызывает callback."""
    if not _on_draft_callback:
        return

    try:
        # Извлекаем chat_id из peer (личные, группы, каналы)
        peer = update.peer
        chat_id = getattr(peer, "user_id", None) or getattr(peer, "chat_id", None) or getattr(peer, "channel_id", None)
        if not chat_id:
            return

        # Извлекаем текст черновика (может быть пустым при очистке)
        draft = update.draft
        draft_text = getattr(draft, "message", "") or ""

        if DEBUG_PRINT:
            print(f"{get_timestamp()} [PYROGRAM] Draft update for user {user_id} in chat {chat_id}: '{draft_text[:50]}'")

        await _on_draft_callback(user_id, chat_id, draft_text.strip())

    except Exception as e:
        print(f"{get_timestamp()} [PYROGRAM] ERROR handling draft update for user {user_id}: {e}")


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
