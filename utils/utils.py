import asyncio
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Callable

from config import AUTO_REPLY_OPTIONS, DEFAULT_STYLE


def get_timestamp() -> str:
    """Возвращает текущее время в формате для логов."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


_TYPING_INTERVAL = 4  # Telegram сбрасывает индикатор ~5 сек; обновляем каждые 4


def typing_action(func: Callable) -> Callable:
    """Декоратор: удерживает индикатор 'печатает...' на всё время выполнения обработчика."""
    @wraps(func)
    async def wrapper(update, context, *args, **kwargs):
        chat_id = update.effective_chat.id

        # Первый вызов сразу, чтобы индикатор появился мгновенно
        try:
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        except Exception:
            pass  # Игнорируем ошибки (напр. бот заблокирован)

        async def _keep_typing() -> None:
            try:
                while True:
                    await asyncio.sleep(_TYPING_INTERVAL)
                    try:
                        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
                    except Exception:
                        break  # Прерываем цикл при сетевых или API ошибках
            except asyncio.CancelledError:
                pass

        task = asyncio.create_task(_keep_typing())
        try:
            return await func(update, context, *args, **kwargs)
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    return wrapper


def format_profile(info: dict | None, label: str) -> str:
    """Форматирует профиль участника в строку для промпта."""
    if not info:
        return label

    name = info.get("first_name", "")
    last = info.get("last_name")
    if last:
        name += f" {last}"

    return name if name else label


def format_chat_history(
    chat_history: list[dict],
    user_info: dict | None = None,
    opponent_info: dict | None = None,
    tz_offset: float = 0,
) -> str:
    """Форматирует историю чата с профилями участников для AI-промпта.

    Args:
        chat_history: Список сообщений [{role, text, date?, name?}]
        user_info: Информация о пользователе
        opponent_info: Информация об оппоненте
        tz_offset: Смещение часового пояса пользователя (часы, напр. 5.5)

    Возвращает строку вида:
        PARTICIPANTS:
        You: Name, @username
        Them: Name, @username

        CHAT HISTORY:
        [2026-03-14 14:30] Name: text
    """
    you = format_profile(user_info, "You")
    them = format_profile(opponent_info, "Them")

    # Заголовок с профилями участников
    user_profile = format_profile(user_info, "You")
    lines = [f"You: {user_profile}"]

    # Собираем всех собеседников (Them)
    them_names = []
    seen_names = set()
    if opponent_info:
        opp_profile = format_profile(opponent_info, "Them")
        seen_names.add(opponent_info.get("first_name", ""))
        them_names.append(opp_profile)

    for msg in chat_history:
        if msg["role"] != "user" and msg.get("name") and msg["name"] not in seen_names:
            seen_names.add(msg["name"])
            full_name = msg["name"]
            if msg.get("last_name"):
                full_name += f" {msg['last_name']}"
            them_names.append(full_name)

    if them_names:
        lines.append("Them: " + ", ".join(them_names))
    else:
        lines.append("Them: Them")

    parts = ["PARTICIPANTS:\n" + "\n".join(lines)]

    # Сдвиг для часового пояса
    tz_delta = timedelta(hours=tz_offset) if tz_offset else timedelta()

    # Форматируем историю в текст для AI
    formatted = []
    for msg in chat_history:
        # В группах у каждого сообщения своё имя отправителя
        if msg["role"] == "user":
            name = you
        else:
            name = msg.get("name") or them
            if msg.get("last_name"):
                name += f" {msg['last_name']}"
        date = msg.get("date")
        if date:
            local_date = date + tz_delta
            ts = local_date.strftime("%Y-%m-%d %H:%M")
            formatted.append(f"[{ts}] {name}: {msg['text']}")
        else:
            formatted.append(f"{name}: {msg['text']}")

    if formatted:
        parts.append("CHAT HISTORY:\n" + "\n".join(formatted))

    return "\n\n".join(parts)


def normalize_auto_reply(value: object) -> int | None:
    """Возвращает валидный auto_reply или None (OFF)."""
    return value if value in AUTO_REPLY_OPTIONS else None


def get_effective_style(settings: dict, chat_id: int | None = None) -> str:
    """Возвращает стиль для конкретного чата (per-chat override → глобальный дефолт).

    Args:
        settings: Настройки пользователя
        chat_id: ID чата (None → глобальный стиль)

    Returns:
        Ключ стиля (по умолчанию 'userlike')
    """
    if chat_id is not None:
        chat_styles = settings.get("chat_styles") or {}
        per_chat = chat_styles.get(str(chat_id))
        if per_chat:
            return per_chat
    return settings.get("style") or DEFAULT_STYLE


