# logic/reply.py — Бизнес-логика генерации ответов

from clients.x402gate.openrouter import generate_response
from prompts import build_reply_prompt
from utils.utils import format_chat_history


async def generate_reply(
    chat_history: list[dict],
    user_info: dict | None = None,
    opponent_info: dict | None = None,
    model: str | None = None,
    custom_prompt: str = "",
    style: str | None = None,
) -> str:
    """Генерирует ответ на основе контекста переписки.

    Args:
        chat_history: Список сообщений [{role, text, date?, name?}]
        user_info: Полная информация о пользователе (из БД)
        opponent_info: Информация об оппоненте (из Pyrogram)
        model: Модель OpenRouter (None — используется LLM_MODEL по умолчанию)
        custom_prompt: Пользовательский промпт из настроек
        style: Стиль общения (None = под пользователя)

    Returns:
        Текст ответа от лица пользователя
    """
    history_text = format_chat_history(chat_history, user_info, opponent_info)

    kwargs: dict = {
        "user_message": history_text,
        "system_prompt": build_reply_prompt(custom_prompt=custom_prompt, style=style),
    }
    if model:
        kwargs["model"] = model
    return await generate_response(**kwargs)
