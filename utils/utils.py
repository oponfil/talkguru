# utils/utils.py — Вспомогательные функции

from datetime import datetime, timezone
from functools import wraps
from typing import Callable


def get_timestamp() -> str:
    """Возвращает текущее время в формате для логов."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def typing_action(func: Callable) -> Callable:
    """Декоратор: отправляет индикатор 'печатает...' перед выполнением обработчика."""
    @wraps(func)
    async def wrapper(update, context, *args, **kwargs):
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id, action="typing"
        )
        return await func(update, context, *args, **kwargs)
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
) -> str:
    """Форматирует историю чата с профилями участников для AI-промпта.

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
            ts = date.strftime("%Y-%m-%d %H:%M")
            formatted.append(f"[{ts}] {name}: {msg['text']}")
        else:
            formatted.append(f"{name}: {msg['text']}")

    if formatted:
        parts.append("CHAT HISTORY:\n" + "\n".join(formatted))

    return "\n\n".join(parts)

