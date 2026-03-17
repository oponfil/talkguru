# tests/test_styles.py — Тесты для per-chat стилей

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from config import EMOJI_TO_STYLE, STYLE_TO_EMOJI
from utils.utils import get_effective_style
from handlers.styles_handler import (
    _chat_display_name,
    _build_styles_keyboard,
    _style_emoji,
)


# ====== get_effective_style ======

class TestGetEffectiveStyle:
    """Тесты для get_effective_style()."""

    def test_global_default(self):
        """Без chat_id — глобальный стиль."""
        assert get_effective_style({"style": "romance"}) == "romance"

    def test_global_none(self):
        """Без стиля — userlike."""
        assert get_effective_style({}) == "userlike"

    def test_per_chat_override(self):
        """Per-chat стиль переопределяет глобальный."""
        settings = {"style": "romance", "chat_styles": {"100": "business"}}
        assert get_effective_style(settings, chat_id=100) == "business"

    def test_per_chat_fallback(self):
        """Если для чата нет стиля — используем глобальный."""
        settings = {"style": "romance", "chat_styles": {"100": "business"}}
        assert get_effective_style(settings, chat_id=999) == "romance"

    def test_empty_chat_styles(self):
        """Пустой chat_styles → глобальный."""
        settings = {"style": "friend", "chat_styles": {}}
        assert get_effective_style(settings, chat_id=100) == "friend"

    def test_missing_chat_styles_key(self):
        """Нет ключа chat_styles → глобальный."""
        settings = {"style": "paranoid"}
        assert get_effective_style(settings, chat_id=100) == "paranoid"

    def test_per_chat_none_value(self):
        """Per-chat None → считается как 'не задано', fallback на глобальный."""
        settings = {"style": "romance", "chat_styles": {"100": None}}
        assert get_effective_style(settings, chat_id=100) == "romance"

    def test_chat_id_none_returns_global(self):
        """chat_id=None → глобальный стиль."""
        settings = {"style": "sales", "chat_styles": {"100": "friend"}}
        assert get_effective_style(settings, chat_id=None) == "sales"


# ====== Config mappings ======

class TestConfigMappings:
    """Тесты для EMOJI_TO_STYLE / STYLE_TO_EMOJI."""

    def test_all_styles_have_emoji(self):
        """Каждый стиль имеет emoji в обратном маппинге."""
        for style in ["userlike", "romance", "business", "sales", "friend", "seducer", "paranoid"]:
            assert style in STYLE_TO_EMOJI

    def test_roundtrip(self):
        """emoji → style → emoji — круговой маппинг."""
        for emoji, style in EMOJI_TO_STYLE.items():
            assert STYLE_TO_EMOJI[style] == emoji


class TestGetDialogInfo:
    """Тесты для clients.pyrogram_client.get_dialog_info()."""

    @pytest.mark.asyncio
    async def test_skips_saved_messages_and_keeps_full_limit(self):
        dialogs = [
            SimpleNamespace(chat=SimpleNamespace(id=123, first_name="Saved", last_name="", username="", title="")),
            SimpleNamespace(chat=SimpleNamespace(id=1, first_name="Алиса", last_name="", username="alice", title="")),
            SimpleNamespace(chat=SimpleNamespace(id=2, first_name="Боб", last_name="", username="bob", title="")),
            SimpleNamespace(chat=SimpleNamespace(id=3, first_name="Вика", last_name="", username="vika", title="")),
        ]

        class FakeClient:
            def get_dialogs(self):
                async def _iter():
                    for dialog in dialogs:
                        yield dialog

                return _iter()

        with patch.dict("clients.pyrogram_client._active_clients", {123: FakeClient()}, clear=True):
            from clients.pyrogram_client import get_dialog_info

            result = await get_dialog_info(123, limit=3)

        assert [dialog["chat_id"] for dialog in result] == [1, 2, 3]


# ====== Styles handler helpers ======

class TestStylesHelpers:
    """Тесты вспомогательных функций styles_handler."""

    def test_style_emoji_known(self):
        assert _style_emoji("romance") == "💕"
        assert _style_emoji(None) == "🦉"

    def test_style_emoji_unknown(self):
        assert _style_emoji("unknown_style") == "🦉"

    def test_chat_display_name_full(self):
        assert _chat_display_name({"first_name": "Алиса", "last_name": "Б.", "title": ""}) == "Алиса Б."

    def test_chat_display_name_first_only(self):
        assert _chat_display_name({"first_name": "Алиса", "last_name": None, "title": ""}) == "Алиса"

    def test_chat_display_name_group_title(self):
        assert _chat_display_name({"first_name": "", "last_name": "", "title": "Рабочий чат"}) == "Рабочий чат"

    def test_chat_display_name_username_fallback(self):
        assert _chat_display_name({"first_name": "", "username": "alice", "title": ""}) == "alice"

    def test_chat_display_name_empty(self):
        assert _chat_display_name({}) == "???"

    def test_build_styles_keyboard(self):
        dialogs = [
            {"chat_id": 100, "first_name": "Алиса", "last_name": "", "username": ""},
            {"chat_id": 200, "first_name": "Боб", "last_name": "", "username": ""},
        ]
        chat_styles = {"100": "romance"}
        keyboard = _build_styles_keyboard(dialogs, chat_styles, global_style="business")
        buttons = keyboard.inline_keyboard
        assert len(buttons) == 2
        assert "💕" in buttons[0][0].text  # per-chat override
        assert "Алиса" in buttons[0][0].text
        assert buttons[0][0].callback_data == "styles:100"
        assert "💼" in buttons[1][0].text  # fallback to global "business"
        assert "Боб" in buttons[1][0].text


# ====== /styles command handler ======

class TestOnStyles:
    """Тесты для on_styles() и on_styles_callback()."""

    @pytest.mark.asyncio
    async def test_not_connected_shows_message(self, mock_update, mock_context):
        """Неподключённый пользователь → сообщение."""
        with patch("handlers.styles_handler.pyrogram_client") as mock_pc, \
             patch("handlers.styles_handler.get_system_message", new_callable=AsyncMock, return_value="Connect first!"):
            mock_pc.is_active.return_value = False
            from handlers.styles_handler import on_styles
            await on_styles(mock_update, mock_context)

        mock_update.message.reply_text.assert_called_with("Connect first!")

    @pytest.mark.asyncio
    async def test_no_chats_shows_message(self, mock_update, mock_context):
        """Нет чатов → сообщение."""
        with patch("handlers.styles_handler.pyrogram_client") as mock_pc, \
             patch("handlers.styles_handler.get_user", new_callable=AsyncMock, return_value={"settings": {}}), \
             patch("handlers.styles_handler.get_system_message", new_callable=AsyncMock, return_value="No chats"):
            mock_pc.is_active.return_value = True
            mock_pc.get_dialog_info = AsyncMock(return_value=[])
            from handlers.styles_handler import on_styles
            await on_styles(mock_update, mock_context)

        mock_update.message.reply_text.assert_called_with("No chats")

    @pytest.mark.asyncio
    async def test_shows_chat_buttons(self, mock_update, mock_context):
        """Показывает кнопки с чатами."""
        dialogs = [
            {"chat_id": 100, "first_name": "Алиса", "last_name": "", "username": ""},
        ]
        with patch("handlers.styles_handler.pyrogram_client") as mock_pc, \
             patch("handlers.styles_handler.get_user", new_callable=AsyncMock, return_value={"settings": {}}), \
             patch("handlers.styles_handler.get_system_message", new_callable=AsyncMock, return_value="Chat Styles"):
            mock_pc.is_active.return_value = True
            mock_pc.get_dialog_info = AsyncMock(return_value=dialogs)
            from handlers.styles_handler import on_styles
            await on_styles(mock_update, mock_context)

        kb = mock_update.message.reply_text.call_args.kwargs["reply_markup"]
        assert len(kb.inline_keyboard) == 1
        assert "Алиса" in kb.inline_keyboard[0][0].text

    @pytest.mark.asyncio
    async def test_callback_cycles_style(self, mock_update, mock_context):
        """Нажатие на кнопку циклически переключает стиль."""
        mock_query = AsyncMock()
        mock_query.data = "styles:100"
        mock_query.answer = AsyncMock()
        mock_query.edit_message_text = AsyncMock()
        mock_update.callback_query = mock_query

        initial_settings = {"style": None, "chat_styles": {}}
        updated_settings = {"style": None, "chat_styles": {"100": "friend"}}
        mock_context.user_data["styles_dialogs"] = [
            {"chat_id": 100, "first_name": "Алиса", "last_name": "", "username": ""},
        ]

        with patch("handlers.styles_handler.get_user", new_callable=AsyncMock, return_value={"settings": initial_settings}), \
             patch("handlers.styles_handler.update_chat_style", new_callable=AsyncMock, return_value=updated_settings), \
             patch("handlers.styles_handler.get_system_message", new_callable=AsyncMock, return_value="Chat Styles"):
            from handlers.styles_handler import on_styles_callback
            await on_styles_callback(mock_update, mock_context)

        mock_query.edit_message_text.assert_called_once()
        kb = mock_query.edit_message_text.call_args.kwargs["reply_markup"]
        assert "🍻" in kb.inline_keyboard[0][0].text

    @pytest.mark.asyncio
    async def test_callback_resets_style(self, mock_update, mock_context):
        """Выбор стиля, совпадающего с глобальным настройками, сбрасывает per-chat настройку (передает None)."""
        mock_query = AsyncMock()
        mock_query.data = "styles:100"
        mock_update.callback_query = mock_query

        from config import STYLE_OPTIONS
        options_list = list(STYLE_OPTIONS.keys())
        
        # Берём глобальный стиль и стиль, предшествующий ему в карусели
        global_style = options_list[-1]
        prev_style = options_list[-2]

        initial_settings = {"style": global_style, "chat_styles": {"100": prev_style}}
        
        mock_context.user_data["styles_dialogs"] = [
            {"chat_id": 100, "first_name": "Алиса", "last_name": "", "username": ""},
        ]

        with patch("handlers.styles_handler.get_user", new_callable=AsyncMock, return_value={"settings": initial_settings}), \
             patch("handlers.styles_handler.update_chat_style", new_callable=AsyncMock, return_value={"chat_styles": {}}) as mock_update_style, \
             patch("handlers.styles_handler.get_system_message", new_callable=AsyncMock):
            from handlers.styles_handler import on_styles_callback
            await on_styles_callback(mock_update, mock_context)

        # Проверяем, что в update_chat_style передан None вместо DEFAULT_STYLE
        mock_update_style.assert_called_once_with(mock_update.effective_user.id, 100, None)


# ====== update_chat_style ======

class TestUpdateChatStyle:
    """Тесты для update_chat_style()."""

    @pytest.mark.asyncio
    async def test_sets_per_chat_style(self):
        """Устанавливает стиль для конкретного чата."""
        with patch("database.users.get_user", new_callable=AsyncMock, return_value={"settings": {}}), \
             patch("database.users.update_user_settings", new_callable=AsyncMock, return_value={"chat_styles": {"100": "romance"}}) as mock_update:
            from database.users import update_chat_style
            await update_chat_style(123, 100, "romance")

        mock_update.assert_called_once()
        call_args = mock_update.call_args
        assert call_args[0][1]["chat_styles"]["100"] == "romance"

    @pytest.mark.asyncio
    async def test_resets_per_chat_style(self):
        """None → удаляет per-chat стиль."""
        existing = {"settings": {"chat_styles": {"100": "romance", "200": "business"}}}
        with patch("database.users.get_user", new_callable=AsyncMock, return_value=existing), \
             patch("database.users.update_user_settings", new_callable=AsyncMock, return_value={"chat_styles": {"200": "business"}}) as mock_update:
            from database.users import update_chat_style
            await update_chat_style(123, 100, None)

        call_args = mock_update.call_args
        assert "100" not in call_args[0][1]["chat_styles"]
        assert call_args[0][1]["chat_styles"]["200"] == "business"
