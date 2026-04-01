# tests/test_styles.py — Тесты для per-chat стилей и автоответа

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from config import EMOJI_TO_STYLE, STYLE_TO_EMOJI
from utils.utils import (
    get_effective_auto_reply,
    get_effective_prompt,
    get_effective_style,
    is_chat_ignored,
    is_chat_specifically_ignored,
)
from handlers.styles_handler import (
    _auto_reply_label,
    _chat_display_name,
    _build_styles_keyboard,
    _build_chat_settings_keyboard,
    _style_emoji,
)

CHAT_MESSAGES = {
    "chats_title": "Chat Styles",
    "chats_chat_title": "⚙️ {chat_name}",
    "chats_show_more": "⬇️ Show more ⬇️",
    "settings_prompt_set": "📝 Prompt: ✅ ON",
    "settings_prompt_empty": "📝 Prompt: ❌ OFF",
    "auto_reply_off": "✅ OFF",
    "auto_reply_1m": "⚠️ 1 min",
    "auto_reply_5m": "⚠️ 5 min",
    "auto_reply_15m": "⚠️ 15 min",
    "auto_reply_1h": "⚠️ 1 hour",
    "auto_reply_16h": "⚠️ 16 hours",
    "auto_reply_ignore": "🔇 Ignore",
    "auto_reply_prefix": "⏰ Auto-reply:",
    "prompt_cancel": "❌ Cancel",
    "prompt_clear": "🗑 Clear",
    # Стили из settings
    **{
        f"settings_style_{style or 'userlike'}": f"{emoji} Style: {style or 'userlike'}"
        for emoji, style in EMOJI_TO_STYLE.items()
    },
}


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


# ====== get_effective_auto_reply ======

class TestGetEffectiveAutoReply:
    """Тесты для get_effective_auto_reply()."""

    def test_global_default(self):
        """Без chat_id — глобальный auto_reply."""
        assert get_effective_auto_reply({"auto_reply": 60}) == 60

    def test_global_none(self):
        """Без auto_reply — None (OFF)."""
        assert get_effective_auto_reply({}) is None

    def test_per_chat_override(self):
        """Per-chat auto_reply переопределяет глобальный."""
        settings = {"auto_reply": 60, "chat_auto_replies": {"100": 300}}
        assert get_effective_auto_reply(settings, chat_id=100) == 300

    def test_per_chat_fallback(self):
        """Если для чата нет override — используем глобальный."""
        settings = {"auto_reply": 60, "chat_auto_replies": {"100": 300}}
        assert get_effective_auto_reply(settings, chat_id=999) == 60

    def test_per_chat_invalid_falls_back(self):
        """Невалидный per-chat auto_reply → None."""
        settings = {"auto_reply": 60, "chat_auto_replies": {"100": 99999}}
        assert get_effective_auto_reply(settings, chat_id=100) is None

    def test_empty_chat_auto_replies(self):
        """Пустой chat_auto_replies → глобальный."""
        settings = {"auto_reply": 300, "chat_auto_replies": {}}
        assert get_effective_auto_reply(settings, chat_id=100) == 300

    def test_chat_id_none_returns_global(self):
        """chat_id=None → глобальный auto_reply."""
        settings = {"auto_reply": 60, "chat_auto_replies": {"100": 300}}
        assert get_effective_auto_reply(settings, chat_id=None) == 60

    def test_per_chat_ignored(self):
        """Сентинел -1 → возвращается как есть."""
        settings = {"auto_reply": 60, "chat_auto_replies": {"100": -1}}
        assert get_effective_auto_reply(settings, chat_id=100) == -1


class TestIsChatIgnored:
    """Тесты для is_chat_ignored()."""

    def test_ignored_chat(self):
        """Чат с sentinel -1 → ignored."""
        settings = {"chat_auto_replies": {"100": -1}}
        assert is_chat_ignored(settings, 100) is True

    def test_not_ignored_chat(self):
        """Обычный чат → not ignored."""
        settings = {"chat_auto_replies": {"100": 60}}
        assert is_chat_ignored(settings, 100) is False

    def test_no_override(self):
        """Чат без override → not ignored."""
        assert is_chat_ignored({}, 100) is False

    def test_global_ignore(self):
        """Глобальный auto_reply -1 → все чаты ignored."""
        settings = {"auto_reply": -1}
        assert is_chat_ignored(settings, 100) is True

    def test_per_chat_overrides_global_ignore(self):
        """Per-chat override имеет приоритет над глобальным ignore."""
        settings = {"auto_reply": -1, "chat_auto_replies": {"100": 60}}
        assert is_chat_ignored(settings, 100) is False
        # Другой чат без override → глобальный ignore
        assert is_chat_ignored(settings, 200) is True


class TestIsChatSpecificallyIgnored:
    """Тесты для is_chat_specifically_ignored()."""

    def test_per_chat_ignore_sentinel(self):
        """Только явный 🔇 Ignore на чат → True."""
        settings = {"chat_auto_replies": {"100": -1}}
        assert is_chat_specifically_ignored(settings, 100) is True

    def test_per_chat_non_ignore(self):
        """Per-chat с обычным автоответом → False."""
        settings = {"chat_auto_replies": {"100": 60}}
        assert is_chat_specifically_ignored(settings, 100) is False

    def test_no_entry_for_chat(self):
        """Нет записи для chat_id → False (даже при глобальном ignore)."""
        settings = {"auto_reply": -1, "chat_auto_replies": {"200": 60}}
        assert is_chat_specifically_ignored(settings, 100) is False

    def test_empty_settings(self):
        assert is_chat_specifically_ignored({}, 100) is False


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

    def test_auto_reply_label_off(self):
        assert _auto_reply_label(None, CHAT_MESSAGES) == "⏰ Auto-reply: ✅ OFF"

    def test_auto_reply_label_minutes(self):
        assert _auto_reply_label(60, CHAT_MESSAGES) == "⏰ Auto-reply: ⚠️ 1 min"
        assert _auto_reply_label(300, CHAT_MESSAGES) == "⏰ Auto-reply: ⚠️ 5 min"

    def test_auto_reply_label_hours(self):
        assert _auto_reply_label(3600, CHAT_MESSAGES) == "⏰ Auto-reply: ⚠️ 1 hour"
        assert _auto_reply_label(57600, CHAT_MESSAGES) == "⏰ Auto-reply: ⚠️ 16 hours"

    def test_auto_reply_label_ignore(self):
        assert _auto_reply_label(-1, CHAT_MESSAGES) == "🔇 Ignore"

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

    def test_build_styles_keyboard_one_button_per_row(self):
        """Одна кнопка на строку с emoji-индикаторами настроек."""
        dialogs = [
            {"chat_id": 100, "first_name": "Алиса", "last_name": "", "username": ""},
            {"chat_id": 200, "first_name": "Боб", "last_name": "", "username": ""},
        ]
        user_settings = {
            "style": "userlike",
            "chat_styles": {"100": "romance"},
            "chat_prompts": {"100": "Be formal"},
            "chat_auto_replies": {"100": 60},
        }
        keyboard = _build_styles_keyboard(dialogs, user_settings, CHAT_MESSAGES)
        buttons = keyboard.inline_keyboard
        assert len(buttons) == 2
        assert len(buttons[0]) == 1
        # Алиса: romance + prompt + auto-reply
        assert buttons[0][0].text == "💕📝⏰ | Алиса"
        assert buttons[0][0].callback_data == "chatmenu:100"
        # Боб: дефолтный стиль (без per-chat override), без промпта, без auto-reply
        assert buttons[1][0].text == "Боб"
        assert buttons[1][0].callback_data == "chatmenu:200"

    def test_build_styles_keyboard_ignore_indicator(self):
        """🔇 показывается для ignored чатов."""
        dialogs = [{"chat_id": 100, "first_name": "Алиса", "last_name": "", "username": ""}]
        user_settings = {"chat_auto_replies": {"100": -1}}
        keyboard = _build_styles_keyboard(dialogs, user_settings, CHAT_MESSAGES)
        assert "🔇" in keyboard.inline_keyboard[0][0].text
        assert "⏰" not in keyboard.inline_keyboard[0][0].text

    def test_build_styles_keyboard_show_more_button(self):
        """Под списком появляется кнопка Show more, если есть следующая страница."""
        dialogs = [
            {"chat_id": 100, "first_name": "Алиса", "last_name": "", "username": ""},
            {"chat_id": 200, "first_name": "Боб", "last_name": "", "username": ""},
        ]
        keyboard = _build_styles_keyboard(dialogs, {}, CHAT_MESSAGES, visible_count=1)
        buttons = keyboard.inline_keyboard
        assert len(buttons) == 2
        assert buttons[0][0].callback_data == "chatmenu:100"
        assert buttons[1][0].text == CHAT_MESSAGES["chats_show_more"]
        assert buttons[1][0].callback_data == "chatsmore:2"

    def test_get_relevant_dialogs_prioritizes_overrides_then_recent(self):
        """Сначала идут чаты с override, затем остальные важные, затем просто недавние."""
        all_dialogs = [
            {"chat_id": 200, "first_name": "Боб", "last_name": "", "username": ""},
            {"chat_id": 100, "first_name": "Алиса", "last_name": "", "username": ""},
            {"chat_id": 300, "first_name": "Клара", "last_name": "", "username": ""},
            {"chat_id": 400, "first_name": "Ден", "last_name": "", "username": ""},
        ]
        user_settings = {
            "chat_styles": {"100": "friend"},
            "chat_auto_replies": {"300": 60},
            "chat_prompts": {"400": "be concise"},
        }

        with patch("handlers.styles_handler.get_replied_chats", return_value=set()):
            from handlers.styles_handler import _get_relevant_dialogs
            dialogs = _get_relevant_dialogs(all_dialogs, user_settings, user_id=123)

        assert [dialog["chat_id"] for dialog in dialogs] == [300, 100, 400, 200]

    def test_build_chat_settings_keyboard_three_buttons_column(self):
        """Level 2: три кнопки в столбец — стиль, промпт, автоответ."""
        user_settings = {
            "style": "business",
            "chat_styles": {"100": "romance"},
            "chat_prompts": {"100": "Be formal"},
            "chat_auto_replies": {"100": 60},
        }
        keyboard = _build_chat_settings_keyboard(100, user_settings, CHAT_MESSAGES, global_style="business")
        buttons = keyboard.inline_keyboard
        assert len(buttons) == 3
        # Каждая строка — 1 кнопка
        assert len(buttons[0]) == 1  # Style
        assert len(buttons[1]) == 1  # Prompt
        assert len(buttons[2]) == 1  # Auto-reply
        # Style — romance override
        assert "romance" in buttons[0][0].text.lower() or "💕" in buttons[0][0].text
        assert buttons[0][0].callback_data == "chats:100"
        # Prompt — set
        assert buttons[1][0].text == "📝 Prompt: ✅ ON"
        assert buttons[1][0].callback_data == "chatprompt:100"
        # Auto-reply — 1 min
        assert "1 min" in buttons[2][0].text
        assert buttons[2][0].callback_data == "autoreply:100"

    def test_build_chat_settings_keyboard_defaults(self):
        """Level 2 с дефолтными настройками."""
        user_settings = {"style": "userlike"}
        keyboard = _build_chat_settings_keyboard(100, user_settings, CHAT_MESSAGES, global_style="userlike")
        buttons = keyboard.inline_keyboard
        assert len(buttons) == 3
        # Prompt — empty
        assert buttons[1][0].text == "📝 Prompt: ❌ OFF"
        # Auto-reply — off
        assert "Auto-reply" in buttons[2][0].text
        assert "OFF" in buttons[2][0].text

    def test_build_chat_settings_keyboard_uses_localized_labels(self):
        """Кнопки берут готовые локализованные подписи из messages."""
        user_settings = {
            "chat_prompts": {"100": "Будь формальнее"},
            "chat_auto_replies": {"100": 60},
        }
        localized_messages = {
            **CHAT_MESSAGES,
            "settings_prompt_set": "PROMPT ON (localized)",
            "auto_reply_1m": "AUTO 1M (localized)",
        }

        keyboard = _build_chat_settings_keyboard(100, user_settings, localized_messages)
        buttons = keyboard.inline_keyboard

        assert buttons[1][0].text == "PROMPT ON (localized)"
        assert "AUTO 1M (localized)" in buttons[2][0].text


# ====== /chats command handler ======

class TestOnStyles:
    """Тесты для on_chats() и on_chats_callback()."""

    @pytest.mark.asyncio
    async def test_not_connected_shows_message(self, mock_update, mock_context):
        """Неподключённый пользователь → сообщение."""
        with patch("handlers.styles_handler.pyrogram_client") as mock_pc, \
             patch("handlers.styles_handler.get_system_message", new_callable=AsyncMock, return_value="Connect first!"):
            mock_pc.is_active.return_value = False
            from handlers.styles_handler import on_chats
            await on_chats(mock_update, mock_context)

        mock_update.message.reply_text.assert_called_with("Connect first!")

    @pytest.mark.asyncio
    async def test_no_chats_shows_message(self, mock_update, mock_context):
        """Нет чатов → сообщение."""
        with patch("handlers.styles_handler.pyrogram_client") as mock_pc, \
             patch("handlers.styles_handler.get_user", new_callable=AsyncMock, return_value={"settings": {}}), \
             patch("handlers.styles_handler.get_replied_chats", return_value=set()), \
             patch("handlers.styles_handler.get_system_message", new_callable=AsyncMock, return_value="No chats"):
            mock_pc.is_active.return_value = True
            mock_pc.get_dialog_info = AsyncMock(return_value=[])
            from handlers.styles_handler import on_chats
            await on_chats(mock_update, mock_context)

        mock_update.message.reply_text.assert_called_with("No chats")

    @pytest.mark.asyncio
    async def test_shows_chat_buttons(self, mock_update, mock_context):
        """Показывает кнопки с именами чатов (по одной на строку)."""
        dialogs = [
            {"chat_id": 100, "first_name": "Алиса", "last_name": "", "username": ""},
        ]
        with patch("handlers.styles_handler.pyrogram_client") as mock_pc, \
             patch("handlers.styles_handler.get_user", new_callable=AsyncMock, return_value={"settings": {}}), \
             patch("handlers.styles_handler.get_replied_chats", return_value={100}), \
             patch("handlers.styles_handler.get_system_messages", new_callable=AsyncMock, return_value=CHAT_MESSAGES):
            mock_pc.is_active.return_value = True
            mock_pc.get_dialog_info = AsyncMock(return_value=dialogs)
            from handlers.styles_handler import on_chats
            await on_chats(mock_update, mock_context)

        kb = mock_update.message.reply_text.call_args.kwargs["reply_markup"]
        assert len(kb.inline_keyboard) == 1
        assert len(kb.inline_keyboard[0]) == 1  # одна кнопка на строку
        assert "Алиса" in kb.inline_keyboard[0][0].text
        assert "chatmenu:100" in kb.inline_keyboard[0][0].callback_data

    @pytest.mark.asyncio
    async def test_shows_recent_chats_without_existing_overrides(self, mock_update, mock_context):
        """Если override еще нет, /chats всё равно показывает недавние диалоги."""
        dialogs = [
            {"chat_id": 100, "first_name": "Алиса", "last_name": "", "username": ""},
            {"chat_id": 200, "first_name": "Боб", "last_name": "", "username": ""},
        ]
        with patch("handlers.styles_handler.pyrogram_client") as mock_pc, \
             patch("handlers.styles_handler.get_user", new_callable=AsyncMock, return_value={"settings": {}}), \
             patch("handlers.styles_handler.get_replied_chats", return_value=set()), \
             patch("handlers.styles_handler.get_system_messages", new_callable=AsyncMock, return_value=CHAT_MESSAGES):
            mock_pc.is_active.return_value = True
            mock_pc.get_dialog_info = AsyncMock(return_value=dialogs)
            from handlers.styles_handler import on_chats
            await on_chats(mock_update, mock_context)

        kb = mock_update.message.reply_text.call_args.kwargs["reply_markup"]
        assert len(kb.inline_keyboard) == 2
        assert "Алиса" in kb.inline_keyboard[0][0].text
        assert "Боб" in kb.inline_keyboard[1][0].text

    @pytest.mark.asyncio
    async def test_shows_more_button_when_more_dialogs_available(self, mock_update, mock_context):
        """Если чатов больше лимита страницы, показывается кнопка Show more."""
        dialogs = [
            {"chat_id": 100, "first_name": "Алиса", "last_name": "", "username": ""},
            {"chat_id": 200, "first_name": "Боб", "last_name": "", "username": ""},
        ]
        with patch("handlers.styles_handler.ACTIVE_CHATS_LIMIT", 1), \
             patch("handlers.styles_handler.pyrogram_client") as mock_pc, \
             patch("handlers.styles_handler.get_user", new_callable=AsyncMock, return_value={"settings": {}}), \
             patch("handlers.styles_handler.get_replied_chats", return_value=set()), \
             patch("handlers.styles_handler.get_system_messages", new_callable=AsyncMock, return_value=CHAT_MESSAGES):
            mock_pc.is_active.return_value = True
            mock_pc.get_dialog_info = AsyncMock(return_value=dialogs)
            from handlers.styles_handler import on_chats
            await on_chats(mock_update, mock_context)

        kb = mock_update.message.reply_text.call_args.kwargs["reply_markup"]
        assert len(kb.inline_keyboard) == 2
        assert kb.inline_keyboard[0][0].callback_data == "chatmenu:100"
        assert kb.inline_keyboard[1][0].text == CHAT_MESSAGES["chats_show_more"]
        assert kb.inline_keyboard[1][0].callback_data == "chatsmore:2"

    @pytest.mark.asyncio
    async def test_chats_more_callback_expands_list(self, mock_update, mock_context):
        """Кнопка Show more раскрывает следующую страницу того же списка."""
        mock_query = AsyncMock()
        mock_query.data = "chatsmore:2"
        mock_query.answer = AsyncMock()
        mock_query.edit_message_text = AsyncMock()
        mock_update.callback_query = mock_query

        mock_context.user_data["chats_dialogs"] = [
            {"chat_id": 100, "first_name": "Алиса", "last_name": "", "username": ""},
            {"chat_id": 200, "first_name": "Боб", "last_name": "", "username": ""},
            {"chat_id": 300, "first_name": "Клара", "last_name": "", "username": ""},
        ]

        with patch("handlers.styles_handler.ACTIVE_CHATS_LIMIT", 1), \
             patch("handlers.styles_handler.get_user", new_callable=AsyncMock, return_value={"settings": {}}), \
             patch("handlers.styles_handler.get_system_messages", new_callable=AsyncMock, return_value=CHAT_MESSAGES):
            from handlers.styles_handler import on_chats_more_callback
            await on_chats_more_callback(mock_update, mock_context)

        mock_query.edit_message_text.assert_called_once()
        kb = mock_query.edit_message_text.call_args.kwargs["reply_markup"]
        assert len(kb.inline_keyboard) == 3
        assert kb.inline_keyboard[0][0].callback_data == "chatmenu:100"
        assert kb.inline_keyboard[1][0].callback_data == "chatmenu:200"
        assert kb.inline_keyboard[2][0].callback_data == "chatsmore:3"

    @pytest.mark.asyncio
    async def test_opening_chats_clears_prompt_waiting_state(self, mock_update, mock_context):
        """Открытие /chats сбрасывает состояние редактора prompt."""
        dialogs = [
            {"chat_id": 100, "first_name": "Алиса", "last_name": "", "username": ""},
        ]
        mock_context.user_data["awaiting_prompt"] = True
        mock_context.user_data["awaiting_chat_prompt"] = 100

        with patch("handlers.styles_handler.pyrogram_client") as mock_pc, \
             patch("handlers.styles_handler.get_user", new_callable=AsyncMock, return_value={"settings": {}}), \
             patch("handlers.styles_handler.get_replied_chats", return_value={100}), \
             patch("handlers.styles_handler.get_system_messages", new_callable=AsyncMock, return_value=CHAT_MESSAGES):
            mock_pc.is_active.return_value = True
            mock_pc.get_dialog_info = AsyncMock(return_value=dialogs)
            from handlers.styles_handler import on_chats
            await on_chats(mock_update, mock_context)

        assert "awaiting_prompt" not in mock_context.user_data
        assert "awaiting_chat_prompt" not in mock_context.user_data

    @pytest.mark.asyncio
    async def test_chat_menu_sends_new_message(self, mock_update, mock_context):
        """Нажатие на чат отправляет новое сообщение с настройками."""
        mock_query = AsyncMock()
        mock_query.data = "chatmenu:100"
        mock_query.answer = AsyncMock()
        mock_query.message = AsyncMock()
        mock_query.message.chat_id = 42
        mock_update.callback_query = mock_query

        mock_context.user_data["chats_dialogs"] = [
            {"chat_id": 100, "first_name": "Алиса", "last_name": "", "username": ""},
        ]

        with patch("handlers.styles_handler.get_user", new_callable=AsyncMock, return_value={"settings": {"style": "userlike"}}), \
             patch("handlers.styles_handler.get_system_messages", new_callable=AsyncMock, return_value=CHAT_MESSAGES):
            from handlers.styles_handler import on_chat_menu_callback
            await on_chat_menu_callback(mock_update, mock_context)

        # Проверяем, что отправлено НОВОЕ сообщение (send_message), а не edit
        mock_context.bot.send_message.assert_called_once()
        call_kwargs = mock_context.bot.send_message.call_args.kwargs
        assert call_kwargs["chat_id"] == 42
        assert "Алиса" in call_kwargs["text"]
        kb = call_kwargs["reply_markup"]
        assert len(kb.inline_keyboard) == 3  # 3 кнопки в столбец
        # Не вызван edit_message_text
        mock_query.edit_message_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_callback_cycles_style(self, mock_update, mock_context):
        """Нажатие на кнопку стиля циклически переключает стиль."""
        mock_query = AsyncMock()
        mock_query.data = "chats:100"
        mock_query.answer = AsyncMock()
        mock_query.edit_message_text = AsyncMock()
        mock_update.callback_query = mock_query

        initial_settings = {"style": None, "chat_styles": {}}
        updated_settings = {"style": None, "chat_styles": {"100": "friend"}}
        mock_context.user_data["awaiting_prompt"] = True
        mock_context.user_data["awaiting_chat_prompt"] = 100
        mock_context.user_data["chats_dialogs"] = [
            {"chat_id": 100, "first_name": "Алиса", "last_name": "", "username": ""},
        ]

        with patch("handlers.styles_handler.get_user", new_callable=AsyncMock, return_value={"settings": initial_settings}), \
             patch("handlers.styles_handler.update_chat_style", new_callable=AsyncMock, return_value=updated_settings), \
             patch("handlers.styles_handler.get_system_messages", new_callable=AsyncMock, return_value=CHAT_MESSAGES):
            from handlers.styles_handler import on_chats_callback
            await on_chats_callback(mock_update, mock_context)

        mock_query.edit_message_text.assert_called_once()
        call_kwargs = mock_query.edit_message_text.call_args.kwargs
        kb = call_kwargs["reply_markup"]
        # Level 2: 3 кнопки в столбец
        assert len(kb.inline_keyboard) == 3
        assert "awaiting_prompt" not in mock_context.user_data
        assert "awaiting_chat_prompt" not in mock_context.user_data

    @pytest.mark.asyncio
    async def test_callback_resets_style(self, mock_update, mock_context):
        """Выбор стиля, совпадающего с глобальным настройками, сбрасывает per-chat настройку (передает None)."""
        mock_query = AsyncMock()
        mock_query.data = "chats:100"
        mock_update.callback_query = mock_query

        from config import STYLE_OPTIONS
        options_list = list(STYLE_OPTIONS.keys())
        
        # Берём глобальный стиль и стиль, предшествующий ему в карусели
        global_style = options_list[-1]
        prev_style = options_list[-2]

        initial_settings = {"style": global_style, "chat_styles": {"100": prev_style}}
        
        mock_context.user_data["chats_dialogs"] = [
            {"chat_id": 100, "first_name": "Алиса", "last_name": "", "username": ""},
        ]

        with patch("handlers.styles_handler.get_user", new_callable=AsyncMock, return_value={"settings": initial_settings}), \
             patch("handlers.styles_handler.update_chat_style", new_callable=AsyncMock, return_value={"chat_styles": {}}) as mock_update_style, \
             patch("handlers.styles_handler.get_system_messages", new_callable=AsyncMock, return_value=CHAT_MESSAGES):
            from handlers.styles_handler import on_chats_callback
            await on_chats_callback(mock_update, mock_context)

        # Проверяем, что в update_chat_style передан None вместо DEFAULT_STYLE
        mock_update_style.assert_called_once_with(mock_update.effective_user.id, 100, None)

    @pytest.mark.asyncio
    async def test_auto_reply_callback_cycles(self, mock_update, mock_context):
        """Нажатие на кнопку автоответа циклически переключает таймер."""
        mock_query = AsyncMock()
        mock_query.data = "autoreply:100"
        mock_query.answer = AsyncMock()
        mock_query.edit_message_text = AsyncMock()
        mock_update.callback_query = mock_query

        initial_settings = {"auto_reply": None, "chat_auto_replies": {}}
        updated_settings = {"auto_reply": None, "chat_auto_replies": {"100": 60}}
        mock_context.user_data["awaiting_prompt"] = True
        mock_context.user_data["awaiting_chat_prompt"] = 100
        mock_context.user_data["chats_dialogs"] = [
            {"chat_id": 100, "first_name": "Алиса", "last_name": "", "username": ""},
        ]

        with patch("handlers.styles_handler.get_user", new_callable=AsyncMock, return_value={"settings": initial_settings}), \
             patch("handlers.styles_handler.update_chat_auto_reply", new_callable=AsyncMock, return_value=updated_settings), \
             patch("handlers.styles_handler.get_system_messages", new_callable=AsyncMock, return_value=CHAT_MESSAGES):
            from handlers.styles_handler import on_auto_reply_callback
            await on_auto_reply_callback(mock_update, mock_context)

        mock_query.edit_message_text.assert_called_once()
        call_kwargs = mock_query.edit_message_text.call_args.kwargs
        kb = call_kwargs["reply_markup"]
        # Level 2: 3 кнопки в столбец
        assert len(kb.inline_keyboard) == 3
        assert "awaiting_prompt" not in mock_context.user_data
        assert "awaiting_chat_prompt" not in mock_context.user_data

    @pytest.mark.asyncio
    async def test_auto_reply_callback_resets(self, mock_update, mock_context):
        """Автоответ, совпадающий с глобальным, сбрасывает per-chat (передаёт None)."""
        mock_query = AsyncMock()
        mock_query.data = "autoreply:100"
        mock_update.callback_query = mock_query

        from config import AUTO_REPLY_OPTIONS
        options_list = list(AUTO_REPLY_OPTIONS.keys())

        # Глобальный = последний в карусели, per-chat = предпоследний
        global_ar = options_list[-1]
        prev_ar = options_list[-2]

        initial_settings = {"auto_reply": global_ar, "chat_auto_replies": {"100": prev_ar}}
        mock_context.user_data["chats_dialogs"] = [
            {"chat_id": 100, "first_name": "Алиса", "last_name": "", "username": ""},
        ]

        with patch("handlers.styles_handler.get_user", new_callable=AsyncMock, return_value={"settings": initial_settings}), \
             patch("handlers.styles_handler.update_chat_auto_reply", new_callable=AsyncMock, return_value={"chat_auto_replies": {}}) as mock_update_ar, \
             patch("handlers.styles_handler.get_system_messages", new_callable=AsyncMock, return_value=CHAT_MESSAGES):
            from handlers.styles_handler import on_auto_reply_callback
            await on_auto_reply_callback(mock_update, mock_context)

        mock_update_ar.assert_called_once_with(mock_update.effective_user.id, 100, None)


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


# ====== update_chat_auto_reply ======

class TestUpdateChatAutoReply:
    """Тесты для update_chat_auto_reply()."""

    @pytest.mark.asyncio
    async def test_sets_per_chat_auto_reply(self):
        """Устанавливает auto_reply для конкретного чата."""
        with patch("database.users.get_user", new_callable=AsyncMock, return_value={"settings": {}}), \
             patch("database.users.update_user_settings", new_callable=AsyncMock, return_value={"chat_auto_replies": {"100": 60}}) as mock_update:
            from database.users import update_chat_auto_reply
            await update_chat_auto_reply(123, 100, 60)

        mock_update.assert_called_once()
        call_args = mock_update.call_args
        assert call_args[0][1]["chat_auto_replies"]["100"] == 60

    @pytest.mark.asyncio
    async def test_resets_per_chat_auto_reply(self):
        """None → удаляет per-chat auto_reply."""
        existing = {"settings": {"chat_auto_replies": {"100": 60, "200": 300}}}
        with patch("database.users.get_user", new_callable=AsyncMock, return_value=existing), \
             patch("database.users.update_user_settings", new_callable=AsyncMock, return_value={"chat_auto_replies": {"200": 300}}) as mock_update:
            from database.users import update_chat_auto_reply
            await update_chat_auto_reply(123, 100, None)

        call_args = mock_update.call_args
        assert "100" not in call_args[0][1]["chat_auto_replies"]
        assert call_args[0][1]["chat_auto_replies"]["200"] == 300


# ====== get_effective_prompt ======

class TestGetEffectivePrompt:
    """Тесты для get_effective_prompt()."""

    def test_global_only(self):
        """Только глобальный промпт."""
        settings = {"custom_prompt": "Be friendly"}
        assert get_effective_prompt(settings, chat_id=100) == "Be friendly"

    def test_per_chat_only(self):
        """Только per-chat промпт."""
        settings = {"chat_prompts": {"100": "Be formal"}}
        assert get_effective_prompt(settings, chat_id=100) == "Be formal"

    def test_both_combined(self):
        """Глобальный + per-chat → конкатенация через \\n."""
        settings = {"custom_prompt": "Be friendly", "chat_prompts": {"100": "Be formal"}}
        assert get_effective_prompt(settings, chat_id=100) == "Be friendly\nBe formal"

    def test_empty(self):
        """Нет промптов → пустая строка."""
        assert get_effective_prompt({}, chat_id=100) == ""

    def test_chat_id_none(self):
        """chat_id=None → только глобальный."""
        settings = {"custom_prompt": "Global", "chat_prompts": {"100": "Per-chat"}}
        assert get_effective_prompt(settings, chat_id=None) == "Global"

    def test_per_chat_fallback(self):
        """Нет промпта для конкретного чата → глобальный."""
        settings = {"custom_prompt": "Global", "chat_prompts": {"200": "Other"}}
        assert get_effective_prompt(settings, chat_id=100) == "Global"


# ====== update_chat_prompt ======

class TestUpdateChatPrompt:
    """Тесты для update_chat_prompt()."""

    @pytest.mark.asyncio
    async def test_sets_per_chat_prompt(self):
        """Устанавливает промпт для конкретного чата."""
        with patch("database.users.get_user", new_callable=AsyncMock, return_value={"settings": {}}), \
             patch("database.users.update_user_settings", new_callable=AsyncMock, return_value={"chat_prompts": {"100": "Be formal"}}) as mock_update:
            from database.users import update_chat_prompt
            await update_chat_prompt(123, 100, "Be formal")

        mock_update.assert_called_once()
        call_args = mock_update.call_args
        assert call_args[0][1]["chat_prompts"]["100"] == "Be formal"

    @pytest.mark.asyncio
    async def test_resets_per_chat_prompt(self):
        """None → удаляет per-chat промпт."""
        existing = {"settings": {"chat_prompts": {"100": "Be formal", "200": "Be casual"}}}
        with patch("database.users.get_user", new_callable=AsyncMock, return_value=existing), \
             patch("database.users.update_user_settings", new_callable=AsyncMock, return_value={"chat_prompts": {"200": "Be casual"}}) as mock_update:
            from database.users import update_chat_prompt
            await update_chat_prompt(123, 100, None)

        call_args = mock_update.call_args
        assert "100" not in call_args[0][1]["chat_prompts"]
        assert call_args[0][1]["chat_prompts"]["200"] == "Be casual"

    @pytest.mark.asyncio
    async def test_empty_string_also_resets_per_chat_prompt(self):
        """Пустая строка → удаляет per-chat промпт, а не хранит пустое значение."""
        existing = {"settings": {"chat_prompts": {"100": "Be formal", "200": "Be casual"}}}
        with patch("database.users.get_user", new_callable=AsyncMock, return_value=existing), \
             patch("database.users.update_user_settings", new_callable=AsyncMock, return_value={"chat_prompts": {"200": "Be casual"}}) as mock_update:
            from database.users import update_chat_prompt
            await update_chat_prompt(123, 100, "")

        call_args = mock_update.call_args
        assert "100" not in call_args[0][1]["chat_prompts"]
        assert call_args[0][1]["chat_prompts"]["200"] == "Be casual"


# ====== on_chat_prompt_callback ======

class TestOnChatPromptCallback:
    """Тесты для on_chat_prompt_callback()."""

    @pytest.mark.asyncio
    async def test_shows_existing_prompt_with_clear_button(self, mock_update, mock_context):
        """Клик на заполненный промпт → показывает превью + кнопки Cancel/Clear."""
        mock_query = AsyncMock()
        mock_query.data = "chatprompt:100"
        mock_query.answer = AsyncMock()
        mock_query.edit_message_text = AsyncMock()
        mock_update.callback_query = mock_query

        initial_settings = {"chat_prompts": {"100": "Be formal"}}
        mock_context.user_data["chats_dialogs"] = [
            {"chat_id": 100, "first_name": "Алиса", "last_name": "", "username": ""},
        ]

        messages = {
            "chats_prompt_current": "📝 Prompt for {chat_name}:\n«{prompt}»",
            "prompt_cancel": "❌ Cancel",
            "prompt_clear": "🗑 Clear",
        }
        with patch("handlers.styles_handler.get_user", new_callable=AsyncMock, return_value={"settings": initial_settings}), \
             patch("handlers.styles_handler.get_system_messages", new_callable=AsyncMock, return_value=messages):
            from handlers.styles_handler import on_chat_prompt_callback
            await on_chat_prompt_callback(mock_update, mock_context)

        # Показывает превью и ставит awaiting
        assert mock_context.user_data.get("awaiting_chat_prompt") == 100
        assert "awaiting_prompt" not in mock_context.user_data
        call_kwargs = mock_query.edit_message_text.call_args.kwargs
        assert "Be formal" in call_kwargs["text"]
        assert "Алиса" in call_kwargs["text"]
        # Должны быть 2 кнопки: Cancel + Clear
        kb = call_kwargs["reply_markup"].inline_keyboard
        assert len(kb[0]) == 2

    @pytest.mark.asyncio
    async def test_shows_empty_prompt_without_clear_button(self, mock_update, mock_context):
        """Клик на пустой промпт → показывает 'not set' + только Cancel."""
        mock_query = AsyncMock()
        mock_query.data = "chatprompt:100"
        mock_query.answer = AsyncMock()
        mock_query.edit_message_text = AsyncMock()
        mock_update.callback_query = mock_query

        initial_settings = {"chat_prompts": {}}
        mock_context.user_data["chats_dialogs"] = [
            {"chat_id": 100, "first_name": "Алиса", "last_name": "", "username": ""},
        ]

        messages = {
            "chats_prompt_no_prompt": "📝 Prompt for {chat_name}: not set.",
            "prompt_cancel": "❌ Cancel",
        }
        with patch("handlers.styles_handler.get_user", new_callable=AsyncMock, return_value={"settings": initial_settings}), \
             patch("handlers.styles_handler.get_system_messages", new_callable=AsyncMock, return_value=messages):
            from handlers.styles_handler import on_chat_prompt_callback
            await on_chat_prompt_callback(mock_update, mock_context)

        assert mock_context.user_data.get("awaiting_chat_prompt") == 100
        call_kwargs = mock_query.edit_message_text.call_args.kwargs
        assert "Алиса" in call_kwargs["text"]
        # Только 1 кнопка: Cancel (без Clear)
        kb = call_kwargs["reply_markup"].inline_keyboard
        assert len(kb[0]) == 1

    @pytest.mark.asyncio
    async def test_opening_chat_prompt_clears_global_prompt_waiting_state(self, mock_update, mock_context):
        """Открытие per-chat редактора сбрасывает глобальный awaiting_prompt."""
        mock_query = AsyncMock()
        mock_query.data = "chatprompt:100"
        mock_query.answer = AsyncMock()
        mock_query.edit_message_text = AsyncMock()
        mock_update.callback_query = mock_query

        mock_context.user_data["awaiting_prompt"] = True
        mock_context.user_data["chats_dialogs"] = [
            {"chat_id": 100, "first_name": "Алиса", "last_name": "", "username": ""},
        ]

        messages = {
            "chats_prompt_no_prompt": "📝 Prompt for {chat_name}: not set.",
            "prompt_cancel": "❌ Cancel",
        }
        with patch("handlers.styles_handler.get_user", new_callable=AsyncMock, return_value={"settings": {"chat_prompts": {}}}), \
             patch("handlers.styles_handler.get_system_messages", new_callable=AsyncMock, return_value=messages):
            from handlers.styles_handler import on_chat_prompt_callback
            await on_chat_prompt_callback(mock_update, mock_context)

        assert "awaiting_prompt" not in mock_context.user_data
        assert mock_context.user_data["awaiting_chat_prompt"] == 100

    @pytest.mark.asyncio
    async def test_cancel_callback_returns_to_chat_settings(self, mock_update, mock_context):
        """Отмена промпта → возврат к настройкам чата (Level 2)."""
        mock_query = AsyncMock()
        mock_query.data = "chatprompt_cancel:100"
        mock_query.answer = AsyncMock()
        mock_query.edit_message_text = AsyncMock()
        mock_update.callback_query = mock_query

        mock_context.user_data["awaiting_prompt"] = True
        mock_context.user_data["awaiting_chat_prompt"] = 100
        mock_context.user_data["chats_dialogs"] = [
            {"chat_id": 100, "first_name": "Алиса", "last_name": "", "username": ""},
        ]

        with patch("handlers.styles_handler.get_user", new_callable=AsyncMock, return_value={"settings": {}}), \
             patch("handlers.styles_handler.get_system_messages", new_callable=AsyncMock, return_value=CHAT_MESSAGES):
            from handlers.styles_handler import on_chat_prompt_cancel_callback
            await on_chat_prompt_cancel_callback(mock_update, mock_context)

        call_kwargs = mock_query.edit_message_text.call_args.kwargs
        kb = call_kwargs["reply_markup"]
        # Возвращает на Level 2 (3 кнопки в столбец)
        assert len(kb.inline_keyboard) == 3
        assert "awaiting_prompt" not in mock_context.user_data
        assert "awaiting_chat_prompt" not in mock_context.user_data

    @pytest.mark.asyncio
    async def test_clear_callback_returns_to_chat_settings(self, mock_update, mock_context):
        """После очистки промпта возвращается к настройкам чата (Level 2)."""
        mock_query = AsyncMock()
        mock_query.data = "chatprompt_clear:100"
        mock_query.answer = AsyncMock()
        mock_query.edit_message_text = AsyncMock()
        mock_update.callback_query = mock_query

        mock_context.user_data["awaiting_prompt"] = True
        mock_context.user_data["awaiting_chat_prompt"] = 100
        mock_context.user_data["chats_dialogs"] = [
            {"chat_id": 100, "first_name": "Алиса", "last_name": "", "username": ""},
        ]

        with patch("handlers.styles_handler.update_chat_prompt", new_callable=AsyncMock, return_value={"chat_prompts": {}}), \
             patch("handlers.styles_handler.get_system_messages", new_callable=AsyncMock, return_value=CHAT_MESSAGES):
            from handlers.styles_handler import on_chat_prompt_clear_callback
            await on_chat_prompt_clear_callback(mock_update, mock_context)

        call_kwargs = mock_query.edit_message_text.call_args.kwargs
        kb = call_kwargs["reply_markup"]
        # Возвращает на Level 2 (3 кнопки в столбец)
        assert len(kb.inline_keyboard) == 3
        assert "awaiting_prompt" not in mock_context.user_data
        assert "awaiting_chat_prompt" not in mock_context.user_data
