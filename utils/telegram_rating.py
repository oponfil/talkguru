# utils/telegram_rating.py — Извлечение рейтинга пользователя из объекта Chat (getChat)

from typing import Any, Optional


def extract_rating_from_chat(chat: Any) -> Optional[int]:
    """Извлекает числовой рейтинг (rating) из объекта Chat, возвращённого getChat.

    У личного чата Telegram может возвращать UserRating в ответе getChat;
    библиотека python-telegram-bot не всегда выставляет атрибут .rating на объекте,
    но данные есть в to_dict() или api_kwargs.
    """
    if chat is None:
        return None
    rating_dict = None
    if getattr(chat, "api_kwargs", None) and "rating" in (chat.api_kwargs or {}):
        rating_dict = chat.api_kwargs.get("rating")
    if rating_dict is None and hasattr(chat, "to_dict"):
        rating_dict = chat.to_dict().get("rating")
    if isinstance(rating_dict, dict):
        val = rating_dict.get("rating")
        if isinstance(val, int) and val >= 0:
            return val
    rating_obj = getattr(chat, "rating", None)
    if rating_obj is not None:
        val = getattr(rating_obj, "rating", None)
        if isinstance(val, int) and val >= 0:
            return val
    return None
