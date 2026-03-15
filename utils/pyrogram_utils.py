# utils/pyrogram_utils.py — Утилиты для Pyrogram Client API

from telegram.ext import Application

from clients import pyrogram_client
from database.users import get_users_with_sessions
from utils.utils import get_timestamp


async def restore_sessions(app: Application) -> None:
    """Восстанавливает активные Pyrogram-сессии при старте бота."""
    try:
        rows = await get_users_with_sessions()

        if not rows:
            return

        count = 0
        failed_user_ids = []
        for row in rows:
            user_id = row["user_id"]
            session_string = row["session_string"]
            if session_string:
                ok = await pyrogram_client.start_listening(user_id, session_string)
                if ok:
                    count += 1
                else:
                    failed_user_ids.append(user_id)

        if count > 0:
            print(f"{get_timestamp()} [BOT] Restored {count} Pyrogram session(s)")
        if failed_user_ids:
            print(
                f"{get_timestamp()} [BOT] WARNING: Failed to restore sessions for users: "
                f"{failed_user_ids}"
            )

    except Exception as e:
        print(f"{get_timestamp()} [BOT] ERROR restoring sessions: {e}")
