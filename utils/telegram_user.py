# utils/telegram_user.py — Хелперы для работы с Telegram user через Update

from telegram import Update

from database.users import get_user, upsert_user


async def _fetch_bio(update: Update) -> str | None:
    """Получает bio пользователя через Bot API get_chat()."""
    try:
        chat = await update.get_bot().get_chat(update.effective_user.id)
        return getattr(chat, "bio", None) or None
    except Exception:
        return None


async def ensure_effective_user(update: Update) -> dict:
    """Возвращает пользователя из БД, предварительно полностью обновляя его профиль."""
    u = update.effective_user

    # Пользователь попросил всегда делать полное обновление (био, имя и т.д.) при любой команде.
    await upsert_effective_user(update)
    
    user = await get_user(u.id)
    if user is None:
        raise RuntimeError("Failed to ensure user exists after upsert")
    return user


async def upsert_effective_user(update: Update) -> bool:
    """Создаёт или обновляет пользователя по данным из Telegram Update."""
    u = update.effective_user
    bio = await _fetch_bio(update)
    return await upsert_user(
        user_id=u.id,
        username=u.username,
        first_name=u.first_name,
        last_name=u.last_name,
        is_bot=u.is_bot,
        is_premium=bool(u.is_premium),
        language_code=u.language_code,
        phone_number=None,
        bio=bio,
    )
