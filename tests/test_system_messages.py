# tests/test_system_messages.py — Тесты для system_messages.py

from unittest.mock import AsyncMock, patch

import pytest

from config import SYSTEM_MESSAGES_FALLBACK_TTL_SECONDS
from system_messages import (
    SYSTEM_MESSAGES,
    _fallback_cache_expiry,
    get_system_message,
    get_system_messages,
    translate_messages,
    _messages_cache,
)


class TestTranslateMessages:
    """Тесты для translate_messages()."""

    @pytest.mark.asyncio
    async def test_english_returns_original(self):
        """Английский язык → возвращает оригинал без вызова API."""
        messages = ["Hello", "World"]
        result = await translate_messages(messages, "en")
        assert result == messages

    @pytest.mark.asyncio
    async def test_empty_list_returns_original(self):
        result = await translate_messages([], "ru")
        assert result == []

    @pytest.mark.asyncio
    async def test_successful_translation(self):
        """Успешный перевод через API."""
        with patch(
            "system_messages.generate_response",
            new_callable=AsyncMock,
            return_value='["Привет", "Мир"]',
        ):
            result = await translate_messages(["Hello", "World"], "ru")

        assert result == ["Привет", "Мир"]

    @pytest.mark.asyncio
    async def test_markdown_wrapper_stripped(self):
        """Убирает ```json обёртку."""
        with patch(
            "system_messages.generate_response",
            new_callable=AsyncMock,
            return_value='```json\n["Привет"]\n```',
        ):
            result = await translate_messages(["Hello"], "ru")

        assert result == ["Привет"]

    @pytest.mark.asyncio
    async def test_invalid_response_returns_none(self):
        """Неправильный формат → None."""
        with patch(
            "system_messages.generate_response",
            new_callable=AsyncMock,
            return_value='"just a string"',
        ):
            result = await translate_messages(["Hello"], "ru")

        assert result is None

    @pytest.mark.asyncio
    async def test_wrong_count_returns_none(self):
        """Неправильное количество элементов → None."""
        with patch(
            "system_messages.generate_response",
            new_callable=AsyncMock,
            return_value='["one", "two"]',
        ):
            result = await translate_messages(["Hello"], "ru")

        assert result is None

    @pytest.mark.asyncio
    async def test_api_error_returns_none(self):
        """Ошибка API → None."""
        with patch(
            "system_messages.generate_response",
            new_callable=AsyncMock,
            side_effect=Exception("API error"),
        ):
            result = await translate_messages(["Hello"], "ru")

        assert result is None


class TestGetSystemMessages:
    """Тесты для get_system_messages()."""

    @pytest.mark.asyncio
    async def test_english_returns_cache(self):
        """Английский → возвращает кэшированные SYSTEM_MESSAGES."""
        result = await get_system_messages("en")
        assert result is SYSTEM_MESSAGES

    @pytest.mark.asyncio
    async def test_none_language_defaults_to_english(self):
        result = await get_system_messages(None)
        assert result is SYSTEM_MESSAGES

    @pytest.mark.asyncio
    async def test_caches_translated_messages(self):
        """Переведённые сообщения кэшируются."""
        test_lang = "xx"  # Уникальный язык для теста
        # Очищаем кэш для этого языка
        _messages_cache.pop(test_lang, None)

        translated_values = [f"translated_{k}" for k in SYSTEM_MESSAGES.keys()]

        with patch(
            "system_messages.translate_messages",
            new_callable=AsyncMock,
            return_value=translated_values,
        ):
            result1 = await get_system_messages(test_lang)
            result2 = await get_system_messages(test_lang)

        # Оба возвращают одно и то же (из кэша)
        assert result1 is result2
        assert "greeting" in result1

        # Cleanup
        _messages_cache.pop(test_lang, None)
        _fallback_cache_expiry.pop(test_lang, None)

    @pytest.mark.asyncio
    async def test_translation_error_returns_originals(self):
        """Ошибка перевода → возвращает оригиналы и кэширует быстрый fallback."""
        test_lang = "yy"
        _messages_cache.pop(test_lang, None)
        _fallback_cache_expiry.pop(test_lang, None)

        with patch(
            "system_messages.translate_messages",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await get_system_messages(test_lang)

        assert result["greeting"] == SYSTEM_MESSAGES["greeting"]
        assert _messages_cache[test_lang]["greeting"] == SYSTEM_MESSAGES["greeting"]
        assert _fallback_cache_expiry[test_lang] > 0

        _messages_cache.pop(test_lang, None)
        _fallback_cache_expiry.pop(test_lang, None)

    @pytest.mark.asyncio
    async def test_fallback_cache_expires_and_retries_translation(self):
        test_lang = "tt"
        _messages_cache.pop(test_lang, None)
        _fallback_cache_expiry.pop(test_lang, None)

        second_result = [f"t2_{k}" for k in SYSTEM_MESSAGES.keys()]

        with patch("system_messages.time.monotonic", side_effect=[100.0, 100.0 + SYSTEM_MESSAGES_FALLBACK_TTL_SECONDS + 1.0]), \
             patch("system_messages.translate_messages", new_callable=AsyncMock, side_effect=[None, second_result]) as mock_translate:
            fallback_messages = await get_system_messages(test_lang)
            translated_messages = await get_system_messages(test_lang)

        assert fallback_messages["greeting"] == SYSTEM_MESSAGES["greeting"]
        assert translated_messages["greeting"] == second_result[0]
        assert mock_translate.call_count == 2

        _messages_cache.pop(test_lang, None)
        _fallback_cache_expiry.pop(test_lang, None)


class TestGetSystemMessage:
    """Тесты для get_system_message()."""

    @pytest.mark.asyncio
    async def test_returns_message_by_key(self):
        result = await get_system_message("en", "greeting")
        assert result == SYSTEM_MESSAGES["greeting"]

    @pytest.mark.asyncio
    async def test_unknown_key_returns_empty(self):
        result = await get_system_message("en", "nonexistent_key_12345")
        assert result == ""

    @pytest.mark.asyncio
    async def test_fallback_to_english(self):
        """Если ключ пуст на переведённом языке → fallback на английский."""
        test_lang = "zz"
        # Помещаем в кэш пустое значение для greeting
        _messages_cache[test_lang] = {**SYSTEM_MESSAGES, "greeting": ""}

        result = await get_system_message(test_lang, "greeting")
        assert result == SYSTEM_MESSAGES["greeting"]

        # Cleanup
        _messages_cache.pop(test_lang, None)
        _fallback_cache_expiry.pop(test_lang, None)
