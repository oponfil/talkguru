# utils/telegram_user.py — Хелперы для работы с Telegram user через Update

from telegram import Update

from database.users import ensure_user_exists, upsert_user


async def ensure_effective_user(update: Update) -> dict:
    """Возвращает пользователя из БД, создавая запись при отсутствии."""
    u = update.effective_user
    return await ensure_user_exists(
        user_id=u.id,
        username=u.username,
        first_name=u.first_name,
        last_name=u.last_name,
        is_bot=u.is_bot,
        is_premium=bool(u.is_premium),
        language_code=u.language_code,
    )


async def upsert_effective_user(update: Update) -> bool:
    """Создаёт или обновляет пользователя по данным из Telegram Update."""
    u = update.effective_user
    return await upsert_user(
        user_id=u.id,
        username=u.username,
        first_name=u.first_name,
        last_name=u.last_name,
        is_bot=u.is_bot,
        is_premium=bool(u.is_premium),
        language_code=u.language_code,
    )
