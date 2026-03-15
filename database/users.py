# database/users.py — CRUD для таблицы users

from datetime import datetime, timezone
from typing import Optional

from config import DEBUG_PRINT
from database import run_supabase, supabase
from utils.session_crypto import decrypt_session_string, encrypt_session_string
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
        await run_supabase(
            lambda: supabase.table("users").upsert(
                data,
                on_conflict="user_id",
            ).execute()
        )

        if DEBUG_PRINT:
            print(f"{get_timestamp()} [DB] Upsert user {user_id} (@{username})")
    except Exception as e:
        print(f"{get_timestamp()} [DB] ERROR upsert_user {user_id}: {e}")


async def update_last_msg_at(user_id: int) -> None:
    """Обновляет время последнего сообщения пользователя."""
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        await run_supabase(
            lambda: supabase.table("users").update(
                {"last_msg_at": now_iso}
            ).eq("user_id", user_id).execute()
        )
    except Exception as e:
        print(f"{get_timestamp()} [DB] ERROR update_last_msg_at {user_id}: {e}")


async def update_tg_rating(user_id: int, rating: Optional[int]) -> None:
    """Обновляет рейтинг Telegram Stars пользователя."""
    try:
        await run_supabase(
            lambda: supabase.table("users").update(
                {"tg_rating": rating}
            ).eq("user_id", user_id).execute()
        )
    except Exception as e:
        print(f"{get_timestamp()} [DB] ERROR update_tg_rating {user_id}: {e}")


async def save_session(user_id: int, session_string: str) -> bool:
    """Сохраняет Pyrogram session string пользователя."""
    try:
        encrypted_session = encrypt_session_string(session_string)
        await run_supabase(
            lambda: supabase.table("users").upsert(
                {"user_id": user_id, "session_string": encrypted_session},
                on_conflict="user_id",
            ).execute()
        )

        if DEBUG_PRINT:
            print(f"{get_timestamp()} [DB] Session saved for user {user_id}")
        return True
    except Exception as e:
        print(f"{get_timestamp()} [DB] ERROR save_session {user_id}: {e}")
        return False


async def get_session(user_id: int) -> Optional[str]:
    """Получает Pyrogram session string пользователя."""
    try:
        result = await run_supabase(
            lambda: supabase.table("users").select(
                "session_string"
            ).eq("user_id", user_id).execute()
        )

        if result.data and result.data[0].get("session_string"):
            return decrypt_session_string(result.data[0]["session_string"])
        return None
    except Exception as e:
        print(f"{get_timestamp()} [DB] ERROR get_session {user_id}: {e}")
        return None


async def get_users_with_sessions() -> list[dict]:
    """Возвращает пользователей с сохранёнными Pyrogram-сессиями."""
    try:
        result = await run_supabase(
            lambda: supabase.table("users").select(
                "user_id, session_string"
            ).not_.is_("session_string", "null").execute()
        )
        rows = result.data or []
        decrypted_rows = []
        for row in rows:
            encrypted_session = row.get("session_string")
            if not encrypted_session:
                continue

            try:
                decrypted_row = dict(row)
                decrypted_row["session_string"] = decrypt_session_string(encrypted_session)
                decrypted_rows.append(decrypted_row)
            except ValueError:
                print(f"{get_timestamp()} [DB] WARNING: corrupted session for user {row.get('user_id')}, skipping")
        return decrypted_rows
    except Exception as e:
        print(f"{get_timestamp()} [DB] ERROR get_users_with_sessions: {e}")
        return []


async def clear_session(user_id: int) -> bool:
    """Очищает Pyrogram session string пользователя."""
    try:
        await run_supabase(
            lambda: supabase.table("users").update(
                {"session_string": None}
            ).eq("user_id", user_id).execute()
        )

        if DEBUG_PRINT:
            print(f"{get_timestamp()} [DB] Session cleared for user {user_id}")
        return True
    except Exception as e:
        print(f"{get_timestamp()} [DB] ERROR clear_session {user_id}: {e}")
        return False


async def has_saved_session(user_id: int) -> bool:
    """Проверяет, есть ли у пользователя сохраненная сессия в БД."""
    try:
        result = await run_supabase(
            lambda: supabase.table("users").select(
                "session_string"
            ).eq("user_id", user_id).execute()
        )
        if not result.data:
            return False
        return bool(result.data[0].get("session_string"))
    except Exception as e:
        print(f"{get_timestamp()} [DB] ERROR has_saved_session {user_id}: {e}")
        return False


async def get_user(user_id: int) -> Optional[dict]:
    """Получает все поля пользователя из БД."""
    try:
        result = await run_supabase(
            lambda: supabase.table("users").select(
                "*"
            ).eq("user_id", user_id).execute()
        )

        if result.data and result.data[0]:
            return result.data[0]
        return None
    except Exception as e:
        print(f"{get_timestamp()} [DB] ERROR get_user {user_id}: {e}")
        return None

