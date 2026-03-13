# database/users.py — CRUD для таблицы users

from typing import Optional

from database import supabase
from config import DEBUG_PRINT
from utils.utils import get_timestamp


async def upsert_user(
    user_id: int,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    is_bot: bool = False,
    is_premium: bool = False,
    language_code: Optional[str] = None,
) -> None:
    """Создаёт или обновляет пользователя в БД.

    При первом контакте создаёт запись с first_seen.
    При повторном — обновляет остальные поля.
    """
    data = {"user_id": user_id, "is_bot": is_bot, "is_premium": is_premium}
    if username is not None:
        data["username"] = username
    if first_name is not None:
        data["first_name"] = first_name
    if last_name is not None:
        data["last_name"] = last_name
    if language_code is not None:
        data["language_code"] = language_code

    try:
        supabase.table("users").upsert(
            data,
            on_conflict="user_id",
        ).execute()

        if DEBUG_PRINT:
            print(f"{get_timestamp()} [DB] Upsert user {user_id} (@{username})")
    except Exception as e:
        print(f"{get_timestamp()} [DB] ERROR upsert_user {user_id}: {e}")


async def update_last_msg_at(user_id: int) -> None:
    """Обновляет время последнего сообщения пользователя."""
    try:
        supabase.table("users").update(
            {"last_msg_at": "now()"}
        ).eq("user_id", user_id).execute()
    except Exception as e:
        print(f"{get_timestamp()} [DB] ERROR update_last_msg_at {user_id}: {e}")


async def update_tg_rating(user_id: int, rating: Optional[int]) -> None:
    """Обновляет рейтинг Telegram Stars пользователя."""
    try:
        supabase.table("users").update(
            {"tg_rating": rating}
        ).eq("user_id", user_id).execute()
    except Exception as e:
        print(f"{get_timestamp()} [DB] ERROR update_tg_rating {user_id}: {e}")
