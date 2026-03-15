# tests/test_handlers.py — Тесты для handlers/pyrogram_handlers.py

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from handlers.pyrogram_handlers import (
    _bot_drafts,
    _pending_drafts,
    _poll_qr_login,
    on_disconnect,
    on_connect,
    on_pyrogram_draft,
    on_pyrogram_message,
    on_status,
)
from system_messages import SYSTEM_MESSAGES
from utils.bot_utils import update_menu_language

TYPING_TEXT = SYSTEM_MESSAGES["draft_typing"]


class TestOnDisconnect:
    """Тесты для on_disconnect()."""

    @pytest.mark.asyncio
    async def test_not_connected(self, mock_update, mock_context):
        """Не подключён → сообщение 'not connected'."""
        with patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc, \
             patch("handlers.pyrogram_handlers.clear_session", new_callable=AsyncMock, return_value=True) as mock_clear:
            mock_pc.is_active.return_value = False

            await on_disconnect(mock_update, mock_context)

        mock_update.message.reply_text.assert_called_once()
        mock_pc.stop_listening.assert_not_called()
        mock_clear.assert_called_once_with(mock_update.effective_user.id)

    @pytest.mark.asyncio
    async def test_disconnects(self, mock_update, mock_context):
        """Подключён → отключает и очищает сессию."""
        with patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc, \
             patch("handlers.pyrogram_handlers.clear_session", new_callable=AsyncMock, return_value=True) as mock_clear:
            mock_pc.is_active.return_value = True
            mock_pc.stop_listening = AsyncMock(return_value=True)

            await on_disconnect(mock_update, mock_context)

        mock_pc.stop_listening.assert_called_once_with(mock_update.effective_user.id)
        mock_clear.assert_called_once_with(mock_update.effective_user.id)

    @pytest.mark.asyncio
    async def test_disconnect_clears_saved_session_even_without_active_listener(self, mock_update, mock_context):
        with patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc, \
             patch("handlers.pyrogram_handlers.clear_session", new_callable=AsyncMock, return_value=True) as mock_clear:
            mock_pc.is_active.return_value = False

            await on_disconnect(mock_update, mock_context)

        mock_pc.stop_listening.assert_not_called()
        mock_clear.assert_called_once_with(mock_update.effective_user.id)

    @pytest.mark.asyncio
    async def test_disconnect_returns_error_when_stop_fails(self, mock_update, mock_context):
        with patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc, \
             patch("handlers.pyrogram_handlers.clear_session", new_callable=AsyncMock, return_value=True) as mock_clear, \
             patch("handlers.pyrogram_handlers.get_system_message", new_callable=AsyncMock, return_value="Disconnect failed"):
            mock_pc.is_active.return_value = True
            mock_pc.stop_listening = AsyncMock(return_value=False)

            await on_disconnect(mock_update, mock_context)

        mock_clear.assert_not_called()
        mock_update.message.reply_text.assert_called_once_with("Disconnect failed")

    @pytest.mark.asyncio
    async def test_disconnect_returns_error_when_clear_session_fails(self, mock_update, mock_context):
        with patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc, \
             patch("handlers.pyrogram_handlers.clear_session", new_callable=AsyncMock, return_value=False), \
             patch("handlers.pyrogram_handlers.get_system_message", new_callable=AsyncMock, return_value="Disconnect failed"):
            mock_pc.is_active.return_value = False

            await on_disconnect(mock_update, mock_context)

        mock_update.message.reply_text.assert_called_once_with("Disconnect failed")


class TestOnStatus:
    """Тесты для on_status()."""

    @pytest.mark.asyncio
    async def test_connected_status(self, mock_update, mock_context):
        with patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc:
            mock_pc.is_active.return_value = True

            await on_status(mock_update, mock_context)

        mock_update.message.reply_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnected_status(self, mock_update, mock_context):
        with patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc:
            mock_pc.is_active.return_value = False

            await on_status(mock_update, mock_context)

        mock_update.message.reply_text.assert_called_once()


class TestOnConnect:
    """Тесты для on_connect() и QR login flow."""

    @pytest.mark.asyncio
    async def test_connect_upserts_user_and_starts_background_polling(self, mock_update, mock_context):
        """`/connect` должен создавать пользователя и запускать polling."""
        mock_client = AsyncMock()
        mock_client.invoke = AsyncMock(return_value=MagicMock(token=b"login-token"))
        mock_client.connect = AsyncMock()

        mock_qr = MagicMock()
        mock_qr.save = MagicMock()
        task = MagicMock()

        def create_task_stub(coro):
            coro.close()
            return task

        with patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc, \
             patch("handlers.pyrogram_handlers.Client", return_value=mock_client), \
             patch("handlers.pyrogram_handlers.qrcode.make", return_value=mock_qr), \
             patch("handlers.pyrogram_handlers.upsert_user", new_callable=AsyncMock) as mock_upsert, \
             patch("handlers.pyrogram_handlers.get_system_message", new_callable=AsyncMock, return_value="Scan QR"), \
             patch("handlers.pyrogram_handlers._get_qr_login_task", return_value=None), \
             patch("handlers.pyrogram_handlers.asyncio.create_task", side_effect=create_task_stub) as mock_create_task, \
             patch("handlers.pyrogram_handlers._register_qr_login_task") as mock_register_task:
            mock_pc.is_active.return_value = False

            await on_connect(mock_update, mock_context)

        mock_upsert.assert_called_once_with(
            user_id=mock_update.effective_user.id,
            username=mock_update.effective_user.username,
            first_name=mock_update.effective_user.first_name,
            last_name=mock_update.effective_user.last_name,
            is_bot=mock_update.effective_user.is_bot,
            is_premium=bool(mock_update.effective_user.is_premium),
            language_code=mock_update.effective_user.language_code,
        )
        mock_client.connect.assert_called_once()
        mock_update.message.reply_photo.assert_called_once()
        mock_create_task.assert_called_once()
        mock_register_task.assert_called_once_with(mock_update.effective_user.id, task)

    @pytest.mark.asyncio
    async def test_connect_rejects_when_qr_login_already_running(self, mock_update, mock_context):
        with patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc, \
             patch("handlers.pyrogram_handlers._get_qr_login_task", return_value=MagicMock()), \
             patch("handlers.pyrogram_handlers.get_system_message", new_callable=AsyncMock, return_value="In progress"):
            mock_pc.is_active.return_value = False

            await on_connect(mock_update, mock_context)

        mock_update.message.reply_text.assert_called_once_with("In progress")

    @pytest.mark.asyncio
    async def test_poll_qr_login_success_saves_session_and_starts_listening(self, mock_bot):
        """Успешный QR login должен сохранить сессию и запустить listener."""
        login_success = type("LoginTokenSuccess", (), {})()

        mock_client = AsyncMock()
        mock_client.invoke = AsyncMock(return_value=login_success)
        mock_client.export_session_string = AsyncMock(return_value="session-123")
        mock_client.disconnect = AsyncMock()

        with patch("handlers.pyrogram_handlers.asyncio.sleep", new_callable=AsyncMock), \
             patch("handlers.pyrogram_handlers.save_session", new_callable=AsyncMock, return_value=True) as mock_save_session, \
             patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc, \
             patch("handlers.pyrogram_handlers.get_system_message", new_callable=AsyncMock, return_value="Connected"):
            mock_pc.start_listening = AsyncMock(return_value=True)

            await _poll_qr_login(mock_client, 123, "en", mock_bot, 456)

        mock_client.export_session_string.assert_called_once()
        mock_client.disconnect.assert_called_once()
        mock_save_session.assert_called_once_with(123, "session-123")
        mock_pc.start_listening.assert_called_once_with(123, "session-123")
        mock_bot.send_message.assert_called_once_with(chat_id=456, text="Connected")

    @pytest.mark.asyncio
    async def test_poll_qr_login_stops_when_save_session_fails(self, mock_bot):
        login_success = type("LoginTokenSuccess", (), {})()

        mock_client = AsyncMock()
        mock_client.invoke = AsyncMock(return_value=login_success)
        mock_client.export_session_string = AsyncMock(return_value="session-123")
        mock_client.disconnect = AsyncMock()

        with patch("handlers.pyrogram_handlers.asyncio.sleep", new_callable=AsyncMock), \
             patch("handlers.pyrogram_handlers.save_session", new_callable=AsyncMock, return_value=False), \
             patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc, \
             patch("handlers.pyrogram_handlers.get_system_message", new_callable=AsyncMock, return_value="Connect failed"):
            mock_pc.start_listening = AsyncMock(return_value=True)

            await _poll_qr_login(mock_client, 123, "en", mock_bot, 456)

        mock_pc.start_listening.assert_not_called()
        mock_bot.send_message.assert_called_once_with(chat_id=456, text="Connect failed")

    @pytest.mark.asyncio
    async def test_poll_qr_login_clears_session_when_listener_start_fails(self, mock_bot):
        login_success = type("LoginTokenSuccess", (), {})()

        mock_client = AsyncMock()
        mock_client.invoke = AsyncMock(return_value=login_success)
        mock_client.export_session_string = AsyncMock(return_value="session-123")
        mock_client.disconnect = AsyncMock()

        with patch("handlers.pyrogram_handlers.asyncio.sleep", new_callable=AsyncMock), \
             patch("handlers.pyrogram_handlers.save_session", new_callable=AsyncMock, return_value=True), \
             patch("handlers.pyrogram_handlers.clear_session", new_callable=AsyncMock, return_value=True) as mock_clear, \
             patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc, \
             patch("handlers.pyrogram_handlers.get_system_message", new_callable=AsyncMock, return_value="Connect failed"):
            mock_pc.start_listening = AsyncMock(return_value=False)

            await _poll_qr_login(mock_client, 123, "en", mock_bot, 456)

        mock_clear.assert_called_once_with(123)
        mock_bot.send_message.assert_called_once_with(chat_id=456, text="Connect failed")


class TestOnPyrogramMessage:
    """Тесты для on_pyrogram_message()."""

    @pytest.mark.asyncio
    async def test_no_text_returns_early(self):
        """Сообщение без текста → ранний return."""
        message = MagicMock()
        message.text = None

        await on_pyrogram_message(123, MagicMock(), message)

    @pytest.mark.asyncio
    async def test_outgoing_returns_early(self):
        """Исходящее сообщение → ранний return."""
        message = MagicMock()
        message.text = "Hello"
        message.outgoing = True

        with patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc:
            mock_pc.read_chat_history = AsyncMock()

            await on_pyrogram_message(123, MagicMock(), message)

        mock_pc.read_chat_history.assert_not_called()

    @pytest.mark.asyncio
    async def test_bot_message_returns_early(self):
        """Сообщение от бота → ранний return."""
        message = MagicMock()
        message.text = "Hello"
        message.outgoing = False
        message.from_user = MagicMock()
        message.from_user.is_bot = True

        with patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc:
            mock_pc.read_chat_history = AsyncMock()

            await on_pyrogram_message(123, MagicMock(), message)

        mock_pc.read_chat_history.assert_not_called()

    @pytest.mark.asyncio
    async def test_generates_and_sets_draft(self):
        """Генерирует ответ и устанавливает черновик."""
        message = MagicMock()
        message.text = "Hello"
        message.outgoing = False
        message.from_user = MagicMock()
        message.from_user.is_bot = False
        message.from_user.first_name = "Test"
        message.chat = MagicMock()
        message.chat.id = 456
        message.chat.type = MagicMock(value="private")

        with patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc, \
             patch("handlers.pyrogram_handlers.generate_reply", new_callable=AsyncMock) as mock_gen, \
             patch("handlers.pyrogram_handlers.get_user", new_callable=AsyncMock, return_value={"language_code": "en"}), \
             patch("handlers.pyrogram_handlers.get_system_message", new_callable=AsyncMock, return_value=TYPING_TEXT):
            mock_pc.read_chat_history = AsyncMock(return_value=[
                {"role": "other", "text": "Hello"}
            ])
            mock_pc.set_draft = AsyncMock(return_value=True)
            mock_gen.return_value = "Hi there!"

            await on_pyrogram_message(123, MagicMock(), message)

        mock_gen.assert_called_once()
        # Первый вызов — проба (статус), второй — AI-ответ
        assert mock_pc.set_draft.call_count == 2
        mock_pc.set_draft.assert_any_call(123, 456, TYPING_TEXT)
        mock_pc.set_draft.assert_any_call(123, 456, "Hi there!")


class TestOnPyrogramDraft:
    """Тесты для on_pyrogram_draft() — probe-based detection."""

    @pytest.mark.asyncio
    async def test_ignores_bot_draft(self):
        """Черновик, установленный ботом → игнорируется."""
        _bot_drafts[(123, 456)] = TYPING_TEXT

        with patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc:
            mock_pc.set_draft = AsyncMock()
            await on_pyrogram_draft(123, 456, TYPING_TEXT)

        mock_pc.set_draft.assert_not_called()
        # Cleanup
        _bot_drafts.pop((123, 456), None)

    @pytest.mark.asyncio
    async def test_empty_draft_clears_pending(self):
        """Пустой черновик → очищает pending."""
        _pending_drafts[(123, 456)] = "some text"

        with patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc:
            mock_pc.set_draft = AsyncMock()
            await on_pyrogram_draft(123, 456, "")

        assert (123, 456) not in _pending_drafts

    @pytest.mark.asyncio
    async def test_processes_user_draft(self):
        """Текст пользователя → устанавливает пробу, ждёт, генерирует ответ."""
        _bot_drafts.pop((123, 456), None)
        _pending_drafts.pop((123, 456), None)

        with patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc, \
             patch("handlers.pyrogram_handlers.generate_response", new_callable=AsyncMock) as mock_gen, \
             patch("handlers.pyrogram_handlers.asyncio.sleep", new_callable=AsyncMock), \
             patch("handlers.pyrogram_handlers.get_user", new_callable=AsyncMock, return_value={"language_code": "en"}), \
             patch("handlers.pyrogram_handlers.get_system_message", new_callable=AsyncMock, return_value=TYPING_TEXT):
            mock_pc.read_chat_history = AsyncMock(return_value=[
                {"role": "user", "text": "Привет"},
            ])
            mock_pc.set_draft = AsyncMock(return_value=True)
            mock_gen.return_value = "AI ответ"

            await on_pyrogram_draft(123, 456, "напиши стихи")

        # Первый вызов — проба (статус), второй — AI-ответ
        assert mock_pc.set_draft.call_count == 2
        mock_pc.set_draft.assert_any_call(123, 456, TYPING_TEXT)
        mock_pc.set_draft.assert_any_call(123, 456, "AI ответ")
        mock_gen.assert_called_once()


class TestUpdateMenuLanguage:
    """Тесты для update_menu_language()."""

    @pytest.mark.asyncio
    async def test_english_returns_early(self, mock_bot):
        """Английский → ранний return (уже по умолчанию)."""
        await update_menu_language(mock_bot, "en")
        mock_bot.set_my_commands.assert_not_called()

    @pytest.mark.asyncio
    async def test_sets_commands_for_other_language(self, mock_bot):
        """Другой язык → устанавливает команды."""
        with patch(
            "utils.bot_utils.get_system_messages",
            new_callable=AsyncMock,
            return_value={
                "menu_start": "Начать",
                "menu_connect": "QR",
                "menu_disconnect": "Отключить",
                "menu_status": "Статус",
            },
        ):
            await update_menu_language(mock_bot, "ru")

        mock_bot.set_my_commands.assert_called_once()

    @pytest.mark.asyncio
    async def test_none_language_treated_as_english(self, mock_bot):
        await update_menu_language(mock_bot, None)
        mock_bot.set_my_commands.assert_not_called()
