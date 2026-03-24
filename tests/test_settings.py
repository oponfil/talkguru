# tests/test_settings.py — Тесты для handlers/settings_handler.py

from unittest.mock import AsyncMock, patch

import pytest

from config import STYLE_OPTIONS
from handlers.settings_handler import _format_tz_offset, on_settings, on_settings_callback
from system_messages import SYSTEM_MESSAGES

MESSAGES = SYSTEM_MESSAGES
TITLE = MESSAGES["settings_title"]


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
        assert len(buttons) == 5
        assert buttons[0][0].text == MESSAGES["settings_model_pro"]
        assert buttons[1][0].text == MESSAGES["settings_style_userlike"]
        assert buttons[2][0].text == MESSAGES["settings_prompt_empty"]
        assert buttons[3][0].text == f"{MESSAGES['auto_reply_prefix']} {MESSAGES['auto_reply_off']}"

    @pytest.mark.asyncio
    async def test_shows_custom_settings(self, mock_update, mock_context):
        """Показывает сохранённые настройки (PRO model)."""
        settings = {"pro_model": True}
        with patch("handlers.settings_handler.ensure_effective_user", new_callable=AsyncMock, return_value={"settings": settings}), \
             patch("handlers.settings_handler.get_system_message", new_callable=AsyncMock, return_value=TITLE), \
             patch("handlers.settings_handler.get_system_messages", new_callable=AsyncMock, return_value=MESSAGES):
            await on_settings(mock_update, mock_context)

        keyboard = mock_update.message.reply_text.call_args.kwargs["reply_markup"]
        buttons = keyboard.inline_keyboard
        assert buttons[0][0].text == MESSAGES["settings_model_pro"]

    @pytest.mark.asyncio
    async def test_invalid_auto_reply_is_shown_as_off(self, mock_update, mock_context):
        """Невалидный auto_reply отображается как OFF."""
        settings = {"auto_reply": 86400}
        with patch("handlers.settings_handler.ensure_effective_user", new_callable=AsyncMock, return_value={"settings": settings}), \
             patch("handlers.settings_handler.get_system_message", new_callable=AsyncMock, return_value=TITLE), \
             patch("handlers.settings_handler.get_system_messages", new_callable=AsyncMock, return_value=MESSAGES):
            await on_settings(mock_update, mock_context)

        keyboard = mock_update.message.reply_text.call_args.kwargs["reply_markup"]
        assert keyboard.inline_keyboard[3][0].text == f"{MESSAGES['auto_reply_prefix']} {MESSAGES['auto_reply_off']}"

    @pytest.mark.asyncio
    async def test_no_prompt_preview_in_settings(self, mock_update, mock_context):
        """При установленном промпте — превью НЕ показывается в /settings."""
        settings = {"custom_prompt": "Be concise and friendly"}
        with patch("handlers.settings_handler.ensure_effective_user", new_callable=AsyncMock, return_value={"settings": settings}), \
             patch("handlers.settings_handler.get_system_message", new_callable=AsyncMock, return_value=TITLE), \
             patch("handlers.settings_handler.get_system_messages", new_callable=AsyncMock, return_value=MESSAGES):
            await on_settings(mock_update, mock_context)

        sent_text = mock_update.message.reply_text.call_args.args[0]
        assert sent_text == TITLE

    @pytest.mark.asyncio
    async def test_opening_settings_clears_prompt_waiting_state(self, mock_update, mock_context):
        """Открытие /settings сбрасывает состояние редактора prompt."""
        mock_context.user_data["awaiting_prompt"] = True
        mock_context.user_data["awaiting_chat_prompt"] = 100

        with patch("handlers.settings_handler.ensure_effective_user", new_callable=AsyncMock, return_value={"settings": {}}), \
             patch("handlers.settings_handler.get_system_message", new_callable=AsyncMock, return_value=TITLE), \
             patch("handlers.settings_handler.get_system_messages", new_callable=AsyncMock, return_value=MESSAGES):
            await on_settings(mock_update, mock_context)

        assert "awaiting_prompt" not in mock_context.user_data
        assert "awaiting_chat_prompt" not in mock_context.user_data


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
    async def test_toggles_model_pro_to_free(self, mock_callback_update, mock_context):
        """Переключает модель из PRO в FREE."""
        mock_callback_update.callback_query.data = "settings:model"

        with patch("handlers.settings_handler.ensure_effective_user", new_callable=AsyncMock,
                    return_value={"settings": {}}), \
             patch("handlers.settings_handler.update_user_settings", new_callable=AsyncMock, return_value={"pro_model": False}) as mock_update, \
             patch("handlers.settings_handler.get_system_messages", new_callable=AsyncMock, return_value=MESSAGES):
            await on_settings_callback(mock_callback_update, mock_context)

        mock_update.assert_called_once_with(
            mock_callback_update.effective_user.id,
            {"pro_model": False},
            current_settings={},
        )
        keyboard = mock_callback_update.callback_query.edit_message_text.call_args.kwargs["reply_markup"]
        assert keyboard.inline_keyboard[0][0].text == MESSAGES["settings_model_free"]

    @pytest.mark.asyncio
    async def test_toggles_model_for_new_user_after_ensure(self, mock_callback_update, mock_context):
        """Для нового пользователя после ensure работает обычное сохранение с current_settings."""
        mock_callback_update.callback_query.data = "settings:model"

        with patch("handlers.settings_handler.ensure_effective_user", new_callable=AsyncMock, return_value={"settings": {}}), \
             patch("handlers.settings_handler.update_user_settings", new_callable=AsyncMock, return_value={"pro_model": False}) as mock_update, \
             patch("handlers.settings_handler.get_system_messages", new_callable=AsyncMock, return_value=MESSAGES):
            await on_settings_callback(mock_callback_update, mock_context)

        mock_update.assert_called_once_with(
            mock_callback_update.effective_user.id,
            {"pro_model": False},
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
        mock_callback_update.callback_query.data = "settings:model"

        with patch("handlers.settings_handler.ensure_effective_user", new_callable=AsyncMock, return_value={"settings": {}}), \
             patch("handlers.settings_handler.update_user_settings", new_callable=AsyncMock, return_value=None), \
             patch("handlers.settings_handler.get_system_message", new_callable=AsyncMock, return_value="Ошибка"):
            await on_settings_callback(mock_callback_update, mock_context)

        mock_callback_update.callback_query.edit_message_text.assert_called_once_with(text="Ошибка")

    @pytest.mark.asyncio
    async def test_cycles_auto_reply(self, mock_callback_update, mock_context):
        """Переключает auto_reply по кругу: None → -1 (ignore) → 60 → ..."""
        mock_callback_update.callback_query.data = "settings:auto_reply"

        with patch("handlers.settings_handler.ensure_effective_user", new_callable=AsyncMock,
                    return_value={"settings": {}}), \
             patch("handlers.settings_handler.update_user_settings", new_callable=AsyncMock, return_value={"auto_reply": -1}) as mock_update, \
             patch("handlers.settings_handler.get_system_messages", new_callable=AsyncMock, return_value=MESSAGES):
            await on_settings_callback(mock_callback_update, mock_context)

        mock_update.assert_called_once_with(
            mock_callback_update.effective_user.id,
            {"auto_reply": -1},
            current_settings={},
        )
        keyboard = mock_callback_update.callback_query.edit_message_text.call_args.kwargs["reply_markup"]
        assert keyboard.inline_keyboard[3][0].text == MESSAGES['auto_reply_ignore']

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
    async def test_opening_global_prompt_clears_chat_prompt_waiting_state(self, mock_callback_update, mock_context):
        """Открытие глобального редактора сбрасывает awaiting_chat_prompt."""
        mock_callback_update.callback_query.data = "settings:prompt"
        mock_context.user_data["awaiting_chat_prompt"] = 100

        messages = {
            **MESSAGES,
            "settings_prompt_no_prompt": "Set prompt",
            "prompt_cancel": "Cancel",
        }
        with patch("handlers.settings_handler.ensure_effective_user", new_callable=AsyncMock, return_value={"settings": {}}), \
             patch("handlers.settings_handler.get_system_messages", new_callable=AsyncMock, return_value=messages):
            await on_settings_callback(mock_callback_update, mock_context)

        assert mock_context.user_data["awaiting_prompt"] is True
        assert "awaiting_chat_prompt" not in mock_context.user_data

    @pytest.mark.asyncio
    async def test_non_prompt_settings_action_clears_prompt_waiting_state(self, mock_callback_update, mock_context):
        """Любое не-prompt действие в /settings сбрасывает режим ввода prompt."""
        mock_callback_update.callback_query.data = "settings:auto_reply"
        mock_context.user_data["awaiting_prompt"] = True
        mock_context.user_data["awaiting_chat_prompt"] = 100

        with patch("handlers.settings_handler.ensure_effective_user", new_callable=AsyncMock, return_value={"settings": {}}), \
             patch("handlers.settings_handler.update_user_settings", new_callable=AsyncMock, return_value={"auto_reply": 60}), \
             patch("handlers.settings_handler.get_system_messages", new_callable=AsyncMock, return_value=MESSAGES):
            await on_settings_callback(mock_callback_update, mock_context)

        assert "awaiting_prompt" not in mock_context.user_data
        assert "awaiting_chat_prompt" not in mock_context.user_data

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
        assert len(keyboard.inline_keyboard) == 5

    def test_timezone_button_shows_utc(self):
        """Кнопка timezone по умолчанию содержит UTC0."""
        from handlers.settings_handler import _build_settings_keyboard
        keyboard = _build_settings_keyboard({}, MESSAGES)
        tz_row = keyboard.inline_keyboard[4]
        assert len(tz_row) == 2
        assert tz_row[0].callback_data == "settings:timezone_back"
        assert "🕐" in tz_row[0].text
        assert "UTC0" in tz_row[1].text
        assert tz_row[1].callback_data == "settings:timezone"

    def test_timezone_button_custom_offset(self):
        """Кнопка timezone с offset=5.5 содержит UTC+5:30."""
        from handlers.settings_handler import _build_settings_keyboard
        keyboard = _build_settings_keyboard({"tz_offset": 5.5}, MESSAGES)
        tz_btn = keyboard.inline_keyboard[4][1]
        assert "UTC+5:30" in tz_btn.text
