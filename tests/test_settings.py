# tests/test_settings.py — Тесты для handlers/settings_handler.py

from unittest.mock import AsyncMock, patch

import pytest

from handlers.settings_handler import _build_settings_text, on_settings, on_settings_callback
from system_messages import SYSTEM_MESSAGES

MESSAGES = SYSTEM_MESSAGES
TITLE = MESSAGES["settings_title"]


class TestBuildSettingsText:
    """Тесты для _build_settings_text()."""

    def test_no_prompt_returns_title(self):
        """Без промпта → только заголовок."""
        assert _build_settings_text(TITLE, {}) == TITLE
        assert _build_settings_text(TITLE, {"custom_prompt": ""}) == TITLE

    def test_short_prompt_shown_fully(self):
        """Короткий промпт → показывается полностью."""
        result = _build_settings_text(TITLE, {"custom_prompt": "Be friendly"})
        assert "«Be friendly»" in result
        assert "…" not in result

    def test_long_prompt_shown_fully(self):
        """Длинный промпт → показывается полностью."""
        long_prompt = "A" * 900
        result = _build_settings_text(TITLE, {"custom_prompt": long_prompt})
        assert f"«{long_prompt}»" in result


class TestOnSettings:
    """Тесты для on_settings()."""

    @pytest.mark.asyncio
    async def test_shows_default_settings(self, mock_update, mock_context):
        """Показывает настройки по умолчанию (drafts ON, FREE model)."""
        with patch("handlers.settings_handler.get_user_settings", new_callable=AsyncMock, return_value={}), \
             patch("handlers.settings_handler.get_system_message", new_callable=AsyncMock, return_value=TITLE), \
             patch("handlers.settings_handler.get_system_messages", new_callable=AsyncMock, return_value=MESSAGES):
            await on_settings(mock_update, mock_context)

        keyboard = mock_update.message.reply_text.call_args.kwargs["reply_markup"]
        buttons = keyboard.inline_keyboard
        assert len(buttons) == 5
        assert buttons[0][0].text == "✏️ Drafts: ✅ ON"
        assert buttons[1][0].text == "🤖 Model: FREE"
        assert buttons[2][0].text == "📝 Prompt: not set"
        assert buttons[3][0].text == "👤 Style: Userlike"
        assert buttons[4][0].text == "⏰ Auto-reply: OFF"

    @pytest.mark.asyncio
    async def test_shows_custom_settings(self, mock_update, mock_context):
        """Показывает сохранённые настройки (drafts OFF, PRO model)."""
        settings = {"drafts_enabled": False, "pro_model": True}
        with patch("handlers.settings_handler.get_user_settings", new_callable=AsyncMock, return_value=settings), \
             patch("handlers.settings_handler.get_system_message", new_callable=AsyncMock, return_value=TITLE), \
             patch("handlers.settings_handler.get_system_messages", new_callable=AsyncMock, return_value=MESSAGES):
            await on_settings(mock_update, mock_context)

        keyboard = mock_update.message.reply_text.call_args.kwargs["reply_markup"]
        buttons = keyboard.inline_keyboard
        assert buttons[0][0].text == "✏️ Drafts: ❌ OFF"
        assert buttons[1][0].text == "🤖 Model: ⭐ PRO"

    @pytest.mark.asyncio
    async def test_invalid_auto_reply_is_shown_as_off(self, mock_update, mock_context):
        """Невалидный auto_reply отображается как OFF."""
        settings = {"auto_reply": 86400}
        with patch("handlers.settings_handler.get_user_settings", new_callable=AsyncMock, return_value=settings), \
             patch("handlers.settings_handler.get_system_message", new_callable=AsyncMock, return_value=TITLE), \
             patch("handlers.settings_handler.get_system_messages", new_callable=AsyncMock, return_value=MESSAGES):
            await on_settings(mock_update, mock_context)

        keyboard = mock_update.message.reply_text.call_args.kwargs["reply_markup"]
        assert keyboard.inline_keyboard[4][0].text == "⏰ Auto-reply: OFF"

    @pytest.mark.asyncio
    async def test_shows_prompt_preview(self, mock_update, mock_context):
        """При установленном промпте — превью в тексте сообщения."""
        settings = {"custom_prompt": "Be concise and friendly"}
        with patch("handlers.settings_handler.get_user_settings", new_callable=AsyncMock, return_value=settings), \
             patch("handlers.settings_handler.get_system_message", new_callable=AsyncMock, return_value=TITLE), \
             patch("handlers.settings_handler.get_system_messages", new_callable=AsyncMock, return_value=MESSAGES):
            await on_settings(mock_update, mock_context)

        sent_text = mock_update.message.reply_text.call_args.args[0]
        assert "«Be concise and friendly»" in sent_text


class TestOnSettingsCallback:
    """Тесты для on_settings_callback()."""

    @pytest.fixture
    def mock_callback_update(self, mock_update):
        """Создаёт update с callback_query."""
        mock_update.callback_query = AsyncMock()
        mock_update.callback_query.answer = AsyncMock()
        mock_update.callback_query.edit_message_text = AsyncMock()
        mock_update.callback_query.message = mock_update.message
        return mock_update

    @pytest.mark.asyncio
    async def test_toggles_drafts_on_to_off(self, mock_callback_update, mock_context):
        """Переключает drafts_enabled из ON в OFF."""
        mock_callback_update.callback_query.data = "settings:drafts"

        with patch("handlers.settings_handler.get_user_settings", new_callable=AsyncMock,
                    side_effect=[{"drafts_enabled": True}, {"drafts_enabled": False}]), \
             patch("handlers.settings_handler.update_user_settings", new_callable=AsyncMock, return_value=True) as mock_update, \
             patch("handlers.settings_handler.get_system_messages", new_callable=AsyncMock, return_value=MESSAGES):
            await on_settings_callback(mock_callback_update, mock_context)

        mock_update.assert_called_once_with(mock_callback_update.effective_user.id, {"drafts_enabled": False})

    @pytest.mark.asyncio
    async def test_toggles_model_free_to_pro(self, mock_callback_update, mock_context):
        """Переключает модель из FREE в PRO."""
        mock_callback_update.callback_query.data = "settings:model"

        with patch("handlers.settings_handler.get_user_settings", new_callable=AsyncMock,
                    side_effect=[{}, {"pro_model": True}]), \
             patch("handlers.settings_handler.update_user_settings", new_callable=AsyncMock, return_value=True) as mock_update, \
             patch("handlers.settings_handler.get_system_messages", new_callable=AsyncMock, return_value=MESSAGES):
            await on_settings_callback(mock_callback_update, mock_context)

        mock_update.assert_called_once_with(mock_callback_update.effective_user.id, {"pro_model": True})
        keyboard = mock_callback_update.callback_query.edit_message_text.call_args.kwargs["reply_markup"]
        assert keyboard.inline_keyboard[1][0].text == "🤖 Model: ⭐ PRO"

    @pytest.mark.asyncio
    async def test_ignores_unknown_callback(self, mock_callback_update, mock_context):
        """Игнорирует неизвестный callback data."""
        mock_callback_update.callback_query.data = "settings:unknown"

        with patch("handlers.settings_handler.get_user_settings", new_callable=AsyncMock, return_value={}), \
             patch("handlers.settings_handler.update_user_settings", new_callable=AsyncMock) as mock_update:
            await on_settings_callback(mock_callback_update, mock_context)

        mock_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_sends_error_when_toggle_save_fails(self, mock_callback_update, mock_context):
        """При сбое сохранения отправляет ошибку вместо ложного успеха."""
        mock_callback_update.callback_query.data = "settings:drafts"

        with patch("handlers.settings_handler.get_user_settings", new_callable=AsyncMock, return_value={}), \
             patch("handlers.settings_handler.update_user_settings", new_callable=AsyncMock, return_value=False), \
             patch("handlers.settings_handler.get_system_message", new_callable=AsyncMock, return_value="Ошибка"):
            await on_settings_callback(mock_callback_update, mock_context)

        mock_callback_update.callback_query.edit_message_text.assert_called_once_with(text="Ошибка")

    @pytest.mark.asyncio
    async def test_cycles_auto_reply(self, mock_callback_update, mock_context):
        """Переключает auto_reply по кругу: None → 60 → 300 → ..."""
        mock_callback_update.callback_query.data = "settings:auto_reply"

        with patch("handlers.settings_handler.get_user_settings", new_callable=AsyncMock,
                    side_effect=[{}, {"auto_reply": 60}]), \
             patch("handlers.settings_handler.update_user_settings", new_callable=AsyncMock, return_value=True) as mock_update, \
             patch("handlers.settings_handler.get_system_messages", new_callable=AsyncMock, return_value=MESSAGES):
            await on_settings_callback(mock_callback_update, mock_context)

        mock_update.assert_called_once_with(mock_callback_update.effective_user.id, {"auto_reply": 60})
        keyboard = mock_callback_update.callback_query.edit_message_text.call_args.kwargs["reply_markup"]
        assert keyboard.inline_keyboard[4][0].text == "⏰ Auto-reply: 1 min"

    @pytest.mark.asyncio
    async def test_cycles_style(self, mock_callback_update, mock_context):
        """Переключает style по кругу: None → flirt → business → ..."""
        mock_callback_update.callback_query.data = "settings:style"

        with patch("handlers.settings_handler.get_user_settings", new_callable=AsyncMock,
                    side_effect=[{}, {"style": "flirt"}]), \
             patch("handlers.settings_handler.update_user_settings", new_callable=AsyncMock, return_value=True) as mock_update, \
             patch("handlers.settings_handler.get_system_messages", new_callable=AsyncMock, return_value=MESSAGES):
            await on_settings_callback(mock_callback_update, mock_context)

        mock_update.assert_called_once_with(mock_callback_update.effective_user.id, {"style": "flirt"})
        keyboard = mock_callback_update.callback_query.edit_message_text.call_args.kwargs["reply_markup"]
        assert keyboard.inline_keyboard[3][0].text == "💋 Style: Flirt Guru"
