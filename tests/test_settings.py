# tests/test_settings.py — Тесты для handlers/settings_handler.py

from unittest.mock import AsyncMock, patch

import pytest

from config import STYLE_OPTIONS
from handlers.settings_handler import _build_settings_text, _format_tz_offset, on_settings, on_settings_callback
from system_messages import SYSTEM_MESSAGES

MESSAGES = SYSTEM_MESSAGES
TITLE = MESSAGES["settings_title"]


class TestBuildSettingsText:
    """Тесты для _build_settings_text()."""

    def test_no_prompt_returns_title(self):
        """Без промпта → только заголовок, без parse_mode."""
        assert _build_settings_text(TITLE, {}) == (TITLE, None)
        assert _build_settings_text(TITLE, {"custom_prompt": ""}) == (TITLE, None)

    def test_short_prompt_shown_fully(self):
        """Короткий промпт → показывается полностью, без parse_mode."""
        text, parse_mode = _build_settings_text(TITLE, {"custom_prompt": "Be friendly"})
        assert "«Be friendly»" in text
        assert parse_mode is None

    def test_long_prompt_shown_fully(self):
        """Длинный промпт → показывается полностью, без parse_mode."""
        long_prompt = "A" * 900
        text, parse_mode = _build_settings_text(TITLE, {"custom_prompt": long_prompt})
        assert f"«{long_prompt}»" in text
        assert parse_mode is None


class TestOnSettings:
    """Тесты для on_settings()."""

    @pytest.mark.asyncio
    async def test_shows_default_settings(self, mock_update, mock_context):
        """Показывает настройки по умолчанию (drafts ON, FREE model)."""
        with patch("handlers.settings_handler.ensure_effective_user", new_callable=AsyncMock, return_value={"settings": {}}), \
             patch("handlers.settings_handler.get_system_message", new_callable=AsyncMock, return_value=TITLE), \
             patch("handlers.settings_handler.get_system_messages", new_callable=AsyncMock, return_value=MESSAGES):
            await on_settings(mock_update, mock_context)

        keyboard = mock_update.message.reply_text.call_args.kwargs["reply_markup"]
        buttons = keyboard.inline_keyboard
        assert len(buttons) == 6
        assert buttons[0][0].text == MESSAGES["settings_model_free"]
        assert buttons[1][0].text == MESSAGES["settings_style_userlike"]
        assert buttons[2][0].text == MESSAGES["settings_drafts_on"]
        assert buttons[3][0].text == MESSAGES["settings_prompt_empty"]
        assert buttons[4][0].text == MESSAGES["settings_auto_reply_off"]

    @pytest.mark.asyncio
    async def test_shows_custom_settings(self, mock_update, mock_context):
        """Показывает сохранённые настройки (drafts OFF, PRO model)."""
        settings = {"drafts_enabled": False, "pro_model": True}
        with patch("handlers.settings_handler.ensure_effective_user", new_callable=AsyncMock, return_value={"settings": settings}), \
             patch("handlers.settings_handler.get_system_message", new_callable=AsyncMock, return_value=TITLE), \
             patch("handlers.settings_handler.get_system_messages", new_callable=AsyncMock, return_value=MESSAGES):
            await on_settings(mock_update, mock_context)

        keyboard = mock_update.message.reply_text.call_args.kwargs["reply_markup"]
        buttons = keyboard.inline_keyboard
        assert buttons[0][0].text == MESSAGES["settings_model_pro"]
        assert buttons[2][0].text == MESSAGES["settings_drafts_off"]

    @pytest.mark.asyncio
    async def test_invalid_auto_reply_is_shown_as_off(self, mock_update, mock_context):
        """Невалидный auto_reply отображается как OFF."""
        settings = {"auto_reply": 86400}
        with patch("handlers.settings_handler.ensure_effective_user", new_callable=AsyncMock, return_value={"settings": settings}), \
             patch("handlers.settings_handler.get_system_message", new_callable=AsyncMock, return_value=TITLE), \
             patch("handlers.settings_handler.get_system_messages", new_callable=AsyncMock, return_value=MESSAGES):
            await on_settings(mock_update, mock_context)

        keyboard = mock_update.message.reply_text.call_args.kwargs["reply_markup"]
        assert keyboard.inline_keyboard[4][0].text == MESSAGES["settings_auto_reply_off"]

    @pytest.mark.asyncio
    async def test_shows_prompt_preview(self, mock_update, mock_context):
        """При установленном промпте — превью в тексте сообщения."""
        settings = {"custom_prompt": "Be concise and friendly"}
        with patch("handlers.settings_handler.ensure_effective_user", new_callable=AsyncMock, return_value={"settings": settings}), \
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

        with patch("handlers.settings_handler.ensure_effective_user", new_callable=AsyncMock,
                    return_value={"settings": {"drafts_enabled": True}}), \
             patch("handlers.settings_handler.update_user_settings", new_callable=AsyncMock, return_value={"drafts_enabled": False}) as mock_update, \
             patch("handlers.settings_handler.get_system_messages", new_callable=AsyncMock, return_value=MESSAGES):
            await on_settings_callback(mock_callback_update, mock_context)

        mock_update.assert_called_once_with(
            mock_callback_update.effective_user.id,
            {"drafts_enabled": False},
            current_settings={"drafts_enabled": True},
        )

    @pytest.mark.asyncio
    async def test_toggles_model_free_to_pro(self, mock_callback_update, mock_context):
        """Переключает модель из FREE в PRO."""
        mock_callback_update.callback_query.data = "settings:model"

        with patch("handlers.settings_handler.ensure_effective_user", new_callable=AsyncMock,
                    return_value={"settings": {}}), \
             patch("handlers.settings_handler.update_user_settings", new_callable=AsyncMock, return_value={"pro_model": True}) as mock_update, \
             patch("handlers.settings_handler.get_system_messages", new_callable=AsyncMock, return_value=MESSAGES):
            await on_settings_callback(mock_callback_update, mock_context)

        mock_update.assert_called_once_with(
            mock_callback_update.effective_user.id,
            {"pro_model": True},
            current_settings={},
        )
        keyboard = mock_callback_update.callback_query.edit_message_text.call_args.kwargs["reply_markup"]
        assert keyboard.inline_keyboard[0][0].text == MESSAGES["settings_model_pro"]

    @pytest.mark.asyncio
    async def test_toggles_model_for_new_user_after_ensure(self, mock_callback_update, mock_context):
        """Для нового пользователя после ensure работает обычное сохранение с current_settings."""
        mock_callback_update.callback_query.data = "settings:model"

        with patch("handlers.settings_handler.ensure_effective_user", new_callable=AsyncMock, return_value={"settings": {}}), \
             patch("handlers.settings_handler.update_user_settings", new_callable=AsyncMock, return_value={"pro_model": True}) as mock_update, \
             patch("handlers.settings_handler.get_system_messages", new_callable=AsyncMock, return_value=MESSAGES):
            await on_settings_callback(mock_callback_update, mock_context)

        mock_update.assert_called_once_with(
            mock_callback_update.effective_user.id,
            {"pro_model": True},
            current_settings={},
        )

    @pytest.mark.asyncio
    async def test_ignores_unknown_callback(self, mock_callback_update, mock_context):
        """Игнорирует неизвестный callback data."""
        mock_callback_update.callback_query.data = "settings:unknown"

        with patch("handlers.settings_handler.ensure_effective_user", new_callable=AsyncMock, return_value={"settings": {}}), \
             patch("handlers.settings_handler.update_user_settings", new_callable=AsyncMock) as mock_update:
            await on_settings_callback(mock_callback_update, mock_context)

        mock_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_sends_error_when_toggle_save_fails(self, mock_callback_update, mock_context):
        """При сбое сохранения отправляет ошибку вместо ложного успеха."""
        mock_callback_update.callback_query.data = "settings:drafts"

        with patch("handlers.settings_handler.ensure_effective_user", new_callable=AsyncMock, return_value={"settings": {}}), \
             patch("handlers.settings_handler.update_user_settings", new_callable=AsyncMock, return_value=None), \
             patch("handlers.settings_handler.get_system_message", new_callable=AsyncMock, return_value="Ошибка"):
            await on_settings_callback(mock_callback_update, mock_context)

        mock_callback_update.callback_query.edit_message_text.assert_called_once_with(text="Ошибка")

    @pytest.mark.asyncio
    async def test_cycles_auto_reply(self, mock_callback_update, mock_context):
        """Переключает auto_reply по кругу: None → 60 → 300 → ..."""
        mock_callback_update.callback_query.data = "settings:auto_reply"

        with patch("handlers.settings_handler.ensure_effective_user", new_callable=AsyncMock,
                    return_value={"settings": {}}), \
             patch("handlers.settings_handler.update_user_settings", new_callable=AsyncMock, return_value={"auto_reply": 60}) as mock_update, \
             patch("handlers.settings_handler.get_system_messages", new_callable=AsyncMock, return_value=MESSAGES):
            await on_settings_callback(mock_callback_update, mock_context)

        mock_update.assert_called_once_with(
            mock_callback_update.effective_user.id,
            {"auto_reply": 60},
            current_settings={},
        )
        keyboard = mock_callback_update.callback_query.edit_message_text.call_args.kwargs["reply_markup"]
        assert keyboard.inline_keyboard[4][0].text == MESSAGES["settings_auto_reply_1m"]

    @pytest.mark.asyncio
    async def test_cycles_style(self, mock_callback_update, mock_context):
        """Переключает style по кругу: None → следующий по STYLE_OPTIONS."""
        mock_callback_update.callback_query.data = "settings:style"

        options = list(STYLE_OPTIONS)
        next_style = options[1]  # первый после None
        expected_msg_key = STYLE_OPTIONS[next_style]
        expected_label = MESSAGES[expected_msg_key]

        with patch("handlers.settings_handler.ensure_effective_user", new_callable=AsyncMock,
                    return_value={"settings": {}}), \
             patch("handlers.settings_handler.update_user_settings", new_callable=AsyncMock, return_value={"style": next_style}) as mock_update, \
             patch("handlers.settings_handler.get_system_messages", new_callable=AsyncMock, return_value=MESSAGES):
            await on_settings_callback(mock_callback_update, mock_context)

        mock_update.assert_called_once_with(
            mock_callback_update.effective_user.id,
            {"style": next_style},
            current_settings={},
        )
        keyboard = mock_callback_update.callback_query.edit_message_text.call_args.kwargs["reply_markup"]
        assert keyboard.inline_keyboard[1][0].text == expected_label

    @pytest.mark.asyncio
    async def test_cycles_timezone(self, mock_callback_update, mock_context):
        """Переключает tz_offset: 0 → 1."""
        mock_callback_update.callback_query.data = "settings:timezone"

        with patch("handlers.settings_handler.ensure_effective_user", new_callable=AsyncMock,
                    return_value={"settings": {"tz_offset": 0}}), \
             patch("handlers.settings_handler.update_user_settings", new_callable=AsyncMock, return_value={"tz_offset": 1}) as mock_update, \
             patch("handlers.settings_handler.get_system_messages", new_callable=AsyncMock, return_value=MESSAGES):
            await on_settings_callback(mock_callback_update, mock_context)

        mock_update.assert_called_once_with(
            mock_callback_update.effective_user.id,
            {"tz_offset": 1},
            current_settings={"tz_offset": 0},
        )

    @pytest.mark.asyncio
    async def test_timezone_wraps_around(self, mock_callback_update, mock_context):
        """tz_offset=13 → следующий -12 (wrap-around)."""
        mock_callback_update.callback_query.data = "settings:timezone"

        with patch("handlers.settings_handler.ensure_effective_user", new_callable=AsyncMock,
                    return_value={"settings": {"tz_offset": 13}}), \
             patch("handlers.settings_handler.update_user_settings", new_callable=AsyncMock, return_value={"tz_offset": -12}) as mock_update, \
             patch("handlers.settings_handler.get_system_messages", new_callable=AsyncMock, return_value=MESSAGES):
            await on_settings_callback(mock_callback_update, mock_context)

        mock_update.assert_called_once_with(
            mock_callback_update.effective_user.id,
            {"tz_offset": -12},
            current_settings={"tz_offset": 13},
        )


class TestTimezoneHelpers:
    """Тесты для _format_tz_offset и _build_timezone_label."""

    def test_format_tz_offset_zero(self):
        assert _format_tz_offset(0) == "0"

    def test_format_tz_offset_positive_whole(self):
        assert _format_tz_offset(7) == "+7"

    def test_format_tz_offset_negative_whole(self):
        assert _format_tz_offset(-3) == "-3"

    def test_format_tz_offset_positive_half(self):
        assert _format_tz_offset(5.5) == "+5:30"

    def test_format_tz_offset_positive_half_iran(self):
        assert _format_tz_offset(3.5) == "+3:30"

    def test_format_tz_offset_negative_half(self):
        assert _format_tz_offset(-9.5) == "-9:30"


class TestSettingsKeyboardTimezone:
    """Тесты для кнопки timezone в клавиатуре."""

    def test_keyboard_has_six_rows(self):
        """Клавиатура содержит 6 строк (включая timezone)."""
        from handlers.settings_handler import _build_settings_keyboard
        keyboard = _build_settings_keyboard({}, MESSAGES)
        assert len(keyboard.inline_keyboard) == 6

    def test_timezone_button_shows_utc(self):
        """Кнопка timezone по умолчанию содержит UTC0."""
        from handlers.settings_handler import _build_settings_keyboard
        keyboard = _build_settings_keyboard({}, MESSAGES)
        tz_row = keyboard.inline_keyboard[5]
        assert len(tz_row) == 2
        assert tz_row[0].callback_data == "settings:timezone_back"
        assert "🕐" in tz_row[0].text
        assert "UTC0" in tz_row[1].text
        assert tz_row[1].callback_data == "settings:timezone"

    def test_timezone_button_custom_offset(self):
        """Кнопка timezone с offset=5.5 содержит UTC+5:30."""
        from handlers.settings_handler import _build_settings_keyboard
        keyboard = _build_settings_keyboard({"tz_offset": 5.5}, MESSAGES)
        tz_btn = keyboard.inline_keyboard[5][1]
        assert "UTC+5:30" in tz_btn.text
