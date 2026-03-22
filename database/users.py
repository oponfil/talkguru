# database/users.py — CRUD для таблицы users

import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from config import DEBUG_PRINT, USER_CACHE_TTL
from database import run_supabase, supabase
from utils.session_crypto import decrypt_session_string, encrypt_session_string
from utils.utils import get_timestamp

# In-memory кэш get_user(): {user_id: (expires_at, data)}
_user_cache: dict[int, tuple[float, dict]] = {}


def invalidate_user_cache(user_id: int) -> None:
    """Удаляет пользователя из in-memory кэша."""
    _user_cache.pop(user_id, None)


class UserStorageError(RuntimeError):
    """Ошибка чтения/создания пользователя в БД."""


async def upsert_user(
    user_id: int,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    is_bot: bool = False,
    is_premium: bool = False,
    language_code: Optional[str] = None,
    phone_number: Optional[str] = None,
    bio: Optional[str] = None,
) -> bool:
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
    if phone_number is not None:
        data["phone_number"] = phone_number
    if bio is not None:
        data["bio"] = bio

    try:
        await run_supabase(
            lambda: supabase.table("users").upsert(
                data,
                on_conflict="user_id",
            ).execute()
        )

        if DEBUG_PRINT:
            print(f"{get_timestamp()} [DB] Upsert user {user_id} (@{username})")
        invalidate_user_cache(user_id)
        return True
    except Exception as e:
        print(f"{get_timestamp()} [DB] ERROR upsert_user {user_id}: {e}")
        return False


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

        invalidate_user_cache(user_id)
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
                "user_id, session_string, language_code"
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

        invalidate_user_cache(user_id)
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
    """Получает все поля пользователя из БД (с in-memory кэшем)."""
    cached = _user_cache.get(user_id)
    if cached is not None:
        expires_at, data = cached
        if time.monotonic() < expires_at:
            return data
        _user_cache.pop(user_id, None)

    try:
        result = await run_supabase(
            lambda: supabase.table("users").select(
                "*"
            ).eq("user_id", user_id).execute()
        )

        if result.data and result.data[0]:
            user = result.data[0]
            _user_cache[user_id] = (time.monotonic() + USER_CACHE_TTL, user)
            return user
        return None
    except Exception as e:
        print(f"{get_timestamp()} [DB] ERROR get_user {user_id}: {e}")
        raise UserStorageError(f"Failed to read user {user_id}") from e


async def ensure_user_exists(
    user_id: int,
    username: Optional[str] = None,
    first_name: Optional[str] = None,
    last_name: Optional[str] = None,
    is_bot: bool = False,
    is_premium: bool = False,
    language_code: Optional[str] = None,
    phone_number: Optional[str] = None,
    bio: Optional[str] = None,
) -> dict:
    """Возвращает пользователя, создавая запись при отсутствии.

    Raises:
        UserStorageError: если чтение или создание пользователя не удалось.
    """
    user = await get_user(user_id)
    if user is not None:
        return user

    created = await upsert_user(
        user_id=user_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
        is_bot=is_bot,
        is_premium=is_premium,
        language_code=language_code,
        phone_number=phone_number,
        bio=bio,
    )
    if not created:
        raise UserStorageError(f"Failed to create user {user_id}")

    return {
        "user_id": user_id,
        "username": username,
        "first_name": first_name,
        "last_name": last_name,
        "is_bot": is_bot,
        "is_premium": is_premium,
        "language_code": language_code,
        "phone_number": phone_number,
        "bio": bio,
        "settings": {},
    }



async def update_user_settings(user_id: int, settings: dict, *, current_settings: dict | None = None) -> dict | None:
    """Обновляет настройки пользователя (merge с существующими).

    Args:
        user_id: ID пользователя
        settings: Словарь с обновляемыми ключами (не перезаписывает остальные)
        current_settings: Текущие настройки (если уже прочитаны). Пропускает лишний read.

    Returns:
        Merged-настройки при успехе, None при ошибке.
    """
    try:
        if current_settings is not None:
            current = current_settings
            user_exists = True
        else:
            user = await get_user(user_id)
            current = (user or {}).get("settings") or {}
            user_exists = user is not None
        merged = {**current, **settings}

        if not user_exists:
            await run_supabase(
                lambda: supabase.table("users").upsert(
                    {"user_id": user_id, "settings": merged},
                    on_conflict="user_id",
                ).execute()
            )
        else:
            await run_supabase(
                lambda: supabase.table("users").update(
                    {"settings": merged}
                ).eq("user_id", user_id).execute()
            )

        invalidate_user_cache(user_id)
        if DEBUG_PRINT:
            log_settings = {**merged}
            cp = log_settings.get("custom_prompt")
            if cp and len(cp) > 30:
                log_settings["custom_prompt"] = cp[:30] + "…"
            print(f"{get_timestamp()} [DB] Settings updated for user {user_id}: {log_settings}")
        return merged
    except Exception as e:
        print(f"{get_timestamp()} [DB] ERROR update_user_settings {user_id}: {e}")
        return None


async def update_chat_style(user_id: int, chat_id: int, style: str | None) -> dict | None:
    """Устанавливает стиль для конкретного чата (None = сброс на глобальный).

    Args:
        user_id: ID пользователя
        chat_id: ID чата
        style: Ключ стиля или None для сброса

    Returns:
        Merged-настройки при успехе, None при ошибке.
    """
    user = await get_user(user_id)
    settings = (user or {}).get("settings") or {}
    chat_styles = dict(settings.get("chat_styles") or {})

    if style is None:
        chat_styles.pop(str(chat_id), None)
    else:
        chat_styles[str(chat_id)] = style

    return await update_user_settings(
        user_id, {"chat_styles": chat_styles}, current_settings=settings,
    )


async def update_chat_auto_reply(user_id: int, chat_id: int, value: int | None) -> dict | None:
    """Устанавливает auto_reply для конкретного чата (None = сброс на глобальный).

    Args:
        user_id: ID пользователя
        chat_id: ID чата
        value: Секунды автоответа или None для сброса

    Returns:
        Merged-настройки при успехе, None при ошибке.
    """
    user = await get_user(user_id)
    settings = (user or {}).get("settings") or {}
    chat_auto_replies = dict(settings.get("chat_auto_replies") or {})

    if value is None:
        chat_auto_replies.pop(str(chat_id), None)
    else:
        chat_auto_replies[str(chat_id)] = value

    return await update_user_settings(
        user_id, {"chat_auto_replies": chat_auto_replies}, current_settings=settings,
    )


async def update_chat_prompt(user_id: int, chat_id: int, prompt: str | None) -> dict | None:
    """Устанавливает per-chat системный промпт (None = сброс).

    Args:
        user_id: ID пользователя
        chat_id: ID чата
        prompt: Текст промпта или None для сброса

    Returns:
        Merged-настройки при успехе, None при ошибке.
    """
    user = await get_user(user_id)
    settings = (user or {}).get("settings") or {}
    chat_prompts = dict(settings.get("chat_prompts") or {})

    if prompt is None or prompt == "":
        chat_prompts.pop(str(chat_id), None)
    else:
        chat_prompts[str(chat_id)] = prompt

    return await update_user_settings(
        user_id, {"chat_prompts": chat_prompts}, current_settings=settings,
    )


async def get_dashboard_user_stats() -> dict[str, int]:
    """Возвращает статистику пользователей для дашборда.

    Returns:
        dict с ключами total_users, connected_users, active_users_24h
    """
    try:
        total_result = await run_supabase(
            lambda: supabase.table("users").select(
                "user_id", count="exact"
            ).execute()
        )
        total_users = total_result.count or 0

        connected_result = await run_supabase(
            lambda: supabase.table("users").select(
                "user_id", count="exact"
            ).not_.is_("session_string", "null").execute()
        )
        connected_users = connected_result.count or 0

        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=24)
        ).isoformat()
        active_result = await run_supabase(
            lambda: supabase.table("users").select(
                "user_id", count="exact"
            ).gte("last_msg_at", cutoff).execute()
        )
        active_users_24h = active_result.count or 0

        return {
            "total_users": total_users,
            "connected_users": connected_users,
            "active_users_24h": active_users_24h,
        }
    except Exception as e:
        print(f"{get_timestamp()} [DB] ERROR get_dashboard_user_stats: {e}")
        return {
            "total_users": 0,
            "connected_users": 0,
            "active_users_24h": 0,
        }
