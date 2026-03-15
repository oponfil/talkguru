# clients/x402gate/openrouter.py — Генерация текста через OpenRouter (via x402gate.io)
#
# Эндпоинт: POST /v1/openrouter/chat/completions
# Формат: стандартный OpenAI Chat Completions API.

import asyncio
import json
import os
import time
from datetime import datetime

from clients.x402gate import NonRetriableRequestError, TopupError, x402gate_client
from config import (
    LLM_MODEL,
    DEBUG_PRINT,
    LOG_TO_FILE,
    RETRY_ATTEMPTS,
    RETRY_DELAY,
    RETRY_EXPONENTIAL_BASE,
)
from prompts import BOT_PROMPT, REPLY_SYSTEM_PROMPT
from utils.utils import get_timestamp, format_chat_history

LOG_DIR = "logs"


def _log_to_file(
    payload: dict, response_text: str, model: str, duration: float, usage: dict, reasoning_text: str = "",
) -> None:
    """Записывает полный запрос и ответ в отдельный лог-файл."""
    if not LOG_TO_FILE:
        return

    os.makedirs(LOG_DIR, exist_ok=True)

    entry = {
        "timestamp": get_timestamp(),
        "model": model,
        "duration_s": round(duration, 2),
        "usage": usage,
        "request": payload["messages"],
        "response": response_text,
    }
    if reasoning_text:
        entry["reasoning"] = reasoning_text

    filename = datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + "_openrouter.log"
    filepath = os.path.join(LOG_DIR, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, indent=2) + "\n")


async def generate_response(
    user_message: str,
    model: str = LLM_MODEL,
    system_prompt: str | None = BOT_PROMPT,
    reasoning_effort: str = "medium",
) -> str:
    """Генерирует ответ на сообщение пользователя через OpenRouter.

    Args:
        user_message: Текст сообщения пользователя
        model: Модель OpenRouter (по умолчанию LLM_MODEL из config)
        system_prompt: Системный промпт (None — без системного промпта)
        reasoning_effort: Уровень reasoning (minimal/low/medium/high)

    Returns:
        Текстовый ответ модели

    Raises:
        ValueError: Если клиент x402gate не инициализирован
        RuntimeError: При ошибках API
    """
    if not x402gate_client.available:
        raise ValueError(
            "EVM_PRIVATE_KEY is not set. "
            "Please set it in .env to use x402gate.io for OpenRouter."
        )

    # Формируем messages в формате Chat Completions
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_message})

    payload = {
        "model": model,
        "messages": messages,
        "reasoning": {"effort": reasoning_effort},
    }

    api_path = "/v1/openrouter/chat/completions"
    start_time = time.time()
    last_error = None

    # Retry с экспоненциальной задержкой
    for attempt in range(RETRY_ATTEMPTS + 1):
        try:
            result = await x402gate_client.request(api_path, payload)

            # x402gate оборачивает ответ в {"data": {...}}
            if "data" in result and isinstance(result["data"], dict):
                result = result["data"]

            # Парсим Chat Completions ответ
            if "choices" not in result or len(result["choices"]) == 0:
                raise RuntimeError("OpenRouter API returned empty response (no choices)")

            choice = result["choices"][0]
            message_data = choice["message"]
            text = message_data.get("content", "")

            if not text or not text.strip():
                raise RuntimeError("OpenRouter API returned empty response content")

            # Reasoning content (если модель поддерживает)
            reasoning_text = message_data.get("reasoning_content") or message_data.get("reasoning") or ""

            # Логируем информацию о токенах
            usage = result.get("usage", {}) or {}
            input_tokens = usage.get("prompt_tokens", 0) or 0
            output_tokens = usage.get("completion_tokens", 0) or 0
            reasoning_tokens = usage.get("reasoning_tokens", 0) or 0
            duration = time.time() - start_time

            token_info = f"tokens: {input_tokens} → {output_tokens}"
            if reasoning_tokens:
                token_info += f" (reasoning: {reasoning_tokens})"

            print(
                f"{get_timestamp()} [OPENROUTER] {model} | "
                f"{duration:.2f}s | {token_info}"
            )

            _log_to_file(payload, text.strip(), model, duration, usage, reasoning_text)

            return text.strip()

        except Exception as e:
            last_error = e

            # TopupError — ретрай бесполезен
            if isinstance(e, (TopupError, NonRetriableRequestError, ValueError)):
                print(f"{get_timestamp()} [OPENROUTER] Non-retriable error: {e}")
                break

            if isinstance(e, RuntimeError) and "empty response" in str(e).lower():
                print(f"{get_timestamp()} [OPENROUTER] Invalid model response — not retrying: {e}")
                break

            if attempt < RETRY_ATTEMPTS:
                delay = RETRY_DELAY * (RETRY_EXPONENTIAL_BASE ** attempt)
                if DEBUG_PRINT:
                    print(f"{get_timestamp()} [OPENROUTER] Error: {e}")
                    print(f"{get_timestamp()} [OPENROUTER] Retry {attempt + 1}/{RETRY_ATTEMPTS} after {delay:.1f}s...")
                await asyncio.sleep(delay)
                continue
            else:
                print(f"{get_timestamp()} [OPENROUTER] Failed after {RETRY_ATTEMPTS} retries: {e}")
                break

    if last_error:
        raise last_error
    raise RuntimeError("Unexpected error in generate_response")

async def generate_reply(
    chat_history: list[dict],
    user_info: dict | None = None,
    opponent_info: dict | None = None,
) -> str:
    """Генерирует ответ на основе контекста переписки.

    Args:
        chat_history: Список сообщений [{role, text, date?, name?}]
        user_info: Полная информация о пользователе (из БД)
        opponent_info: Информация об оппоненте (из Pyrogram)

    Returns:
        Текст ответа от лица пользователя
    """
    history_text = format_chat_history(chat_history, user_info, opponent_info)

    return await generate_response(
        user_message=history_text,
        system_prompt=REPLY_SYSTEM_PROMPT,
    )
