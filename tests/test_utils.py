# tests/test_utils.py — Тесты для utils/utils.py

import re
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from utils.utils import get_timestamp, typing_action, format_profile, format_chat_history


class TestGetTimestamp:
    """Тесты для get_timestamp()."""

    def test_returns_string(self):
        result = get_timestamp()
        assert isinstance(result, str)

    def test_format_matches_utc(self):
        result = get_timestamp()
        # Формат: "2026-03-14 12:00:00 UTC"
        pattern = r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC$"
        assert re.match(pattern, result), f"Unexpected format: {result}"

    def test_ends_with_utc(self):
        result = get_timestamp()
        assert result.endswith("UTC")


class TestTypingAction:
    """Тесты для декоратора typing_action()."""

    @pytest.mark.asyncio
    async def test_sends_typing_action(self):
        """Декоратор вызывает send_chat_action('typing') перед обработчиком."""
        mock_handler = AsyncMock(return_value=None)
        decorated = typing_action(mock_handler)

        update = MagicMock()
        update.effective_chat.id = 12345
        context = MagicMock()
        context.bot.send_chat_action = AsyncMock()

        await decorated(update, context)

        context.bot.send_chat_action.assert_called_once_with(
            chat_id=12345, action="typing"
        )
        mock_handler.assert_called_once_with(update, context)

    @pytest.mark.asyncio
    async def test_preserves_return_value(self):
        """Декоратор пробрасывает возвращаемое значение обработчика."""
        mock_handler = AsyncMock(return_value=42)
        decorated = typing_action(mock_handler)

        update = MagicMock()
        update.effective_chat.id = 1
        context = MagicMock()
        context.bot.send_chat_action = AsyncMock()

        result = await decorated(update, context)
        assert result == 42

    @pytest.mark.asyncio
    async def test_preserves_function_name(self):
        """Декоратор сохраняет имя оригинальной функции (functools.wraps)."""
        async def my_handler(update, context):
            pass

        decorated = typing_action(my_handler)
        assert decorated.__name__ == "my_handler"


class TestFormatProfile:
    """Тесты для format_profile()."""

    def test_none_info_returns_label(self):
        """Без данных возвращает label."""
        assert format_profile(None, "You") == "You"

    def test_empty_dict_returns_label(self):
        """Пустой словарь возвращает label."""
        assert format_profile({}, "Them") == "Them"

    def test_first_name_only(self):
        """Только имя."""
        assert format_profile({"first_name": "Алексей"}, "You") == "Алексей"

    def test_full_name(self):
        """Имя и фамилия."""
        info = {"first_name": "Алексей", "last_name": "Иванов"}
        assert format_profile(info, "You") == "Алексей Иванов"

    def test_with_username(self):
        """Username игнорируется — возвращается только имя."""
        info = {"first_name": "Алексей", "username": "alexey"}
        assert format_profile(info, "You") == "Алексей"

    def test_full_profile(self):
        """Имя + фамилия, username игнорируется."""
        info = {"first_name": "Алексей", "last_name": "Иванов", "username": "alexey"}
        assert format_profile(info, "You") == "Алексей Иванов"

    def test_username_only(self):
        """Только username без имени — возвращает label."""
        info = {"username": "alexey"}
        assert format_profile(info, "You") == "You"


class TestFormatChatHistory:
    """Тесты для format_chat_history()."""

    def test_empty_history(self):
        """Пустая история — только заголовок."""
        result = format_chat_history([], None, None)
        assert "PARTICIPANTS:" in result
        assert "You: You" in result
        assert "Them: Them" in result

    def test_with_names(self):
        """Имена отображаются в заголовке и сообщениях."""
        history = [{"role": "user", "text": "Привет"}, {"role": "other", "text": "Хай", "name": "Марина"}]
        user_info = {"first_name": "Алексей"}
        opponent_info = {"first_name": "Марина"}

        result = format_chat_history(history, user_info, opponent_info)
        assert "PARTICIPANTS:" in result
        assert "CHAT HISTORY:" in result
        assert "Алексей: Привет" in result
        assert "Марина: Хай" in result
        assert "You: Алексей" in result
        assert "Them: Марина" in result

    def test_with_timestamps(self):
        """Даты форматируются как [YYYY-MM-DD HH:MM]."""
        dt = datetime(2026, 3, 14, 14, 30, tzinfo=timezone.utc)
        history = [{"role": "user", "text": "Тест", "date": dt}]

        result = format_chat_history(history)
        assert "[2026-03-14 14:30]" in result

    def test_without_timestamps(self):
        """Сообщения без даты форматируются без скобок."""
        history = [{"role": "user", "text": "Тест"}]

        result = format_chat_history(history)
        assert "You: Тест" in result
        assert "[" not in result.split("\n")[-1]

    def test_mixed_timestamps(self):
        """Смешанная история — с датой и без."""
        dt = datetime(2026, 3, 14, 14, 30, tzinfo=timezone.utc)
        history = [
            {"role": "user", "text": "С датой", "date": dt},
            {"role": "other", "text": "Без даты", "name": "Them"},
        ]

        result = format_chat_history(history)
        assert "[2026-03-14 14:30]" in result
        assert "Them: Без даты" in result

