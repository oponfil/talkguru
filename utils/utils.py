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
