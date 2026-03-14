# tests/test_openrouter.py — Тесты для clients/x402gate/openrouter.py

from unittest.mock import AsyncMock, patch

import pytest

from clients.x402gate import TopupError
from clients.x402gate.openrouter import generate_response, generate_reply


class TestGenerateResponse:
    """Тесты для generate_response()."""

    @pytest.mark.asyncio
    async def test_successful_response(self):
        """Успешный ответ: парсит choices[0].message.content."""
        mock_result = {
            "data": {
                "choices": [{"message": {"content": "Hello, world!"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }
        }

        with patch("clients.x402gate.openrouter.x402gate_client") as mock_client:
            mock_client.available = True
            mock_client.request = AsyncMock(return_value=mock_result)

            result = await generate_response("Hi")

        assert result == "Hello, world!"

    @pytest.mark.asyncio
    async def test_empty_content_raises(self):
        """Пустой content → RuntimeError."""
        mock_result = {
            "data": {
                "choices": [{"message": {"content": ""}}],
            }
        }

        with patch("clients.x402gate.openrouter.x402gate_client") as mock_client, \
             patch("clients.x402gate.openrouter.RETRY_DELAY", 0):
            mock_client.available = True
            mock_client.request = AsyncMock(return_value=mock_result)

            with pytest.raises(RuntimeError, match="empty response"):
                await generate_response("Hi")

    @pytest.mark.asyncio
    async def test_no_choices_raises(self):
        """Нет choices → RuntimeError."""
        mock_result = {"data": {"choices": []}}

        with patch("clients.x402gate.openrouter.x402gate_client") as mock_client, \
             patch("clients.x402gate.openrouter.RETRY_DELAY", 0):
            mock_client.available = True
            mock_client.request = AsyncMock(return_value=mock_result)

            with pytest.raises(RuntimeError, match="empty response"):
                await generate_response("Hi")

    @pytest.mark.asyncio
    async def test_not_available_raises_value_error(self):
        """Клиент недоступен → ValueError."""
        with patch("clients.x402gate.openrouter.x402gate_client") as mock_client:
            mock_client.available = False

            with pytest.raises(ValueError, match="EVM_PRIVATE_KEY"):
                await generate_response("Hi")

    @pytest.mark.asyncio
    async def test_topup_error_not_retried(self):
        """TopupError не повторяется."""
        with patch("clients.x402gate.openrouter.x402gate_client") as mock_client:
            mock_client.available = True
            mock_client.request = AsyncMock(side_effect=TopupError("payment failed"))

            with pytest.raises(TopupError):
                await generate_response("Hi")

        # Должен быть вызван только 1 раз (без retry)
        assert mock_client.request.call_count == 1

    @pytest.mark.asyncio
    async def test_strips_whitespace(self):
        """Ответ стрипается от пробелов."""
        mock_result = {
            "data": {
                "choices": [{"message": {"content": "  trimmed  \n"}}],
                "usage": {},
            }
        }

        with patch("clients.x402gate.openrouter.x402gate_client") as mock_client:
            mock_client.available = True
            mock_client.request = AsyncMock(return_value=mock_result)

            result = await generate_response("Hi")

        assert result == "trimmed"


class TestGenerateReply:
    """Тесты для generate_reply()."""

    @pytest.mark.asyncio
    async def test_formats_history(self):
        """Правильно форматирует You:/Them: и вызывает generate_response."""
        history = [
            {"role": "other", "text": "Привет!"},
            {"role": "user", "text": "Привет, как дела?"},
            {"role": "other", "text": "Отлично!"},
        ]

        with patch("clients.x402gate.openrouter.generate_response", new_callable=AsyncMock) as mock_gen:
            mock_gen.return_value = "У меня тоже всё хорошо!"

            result = await generate_reply(history)

        assert result == "У меня тоже всё хорошо!"

        # Проверяем формат
        call_args = mock_gen.call_args
        user_message = call_args[1]["user_message"] if "user_message" in call_args[1] else call_args[0][0]
        assert "Them: Привет!" in user_message
        assert "You: Привет, как дела?" in user_message
        assert "Them: Отлично!" in user_message
