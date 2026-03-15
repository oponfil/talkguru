# tests/test_handlers.py — Тесты для handlers/pyrogram_handlers.py

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.ext import ApplicationHandlerStop

from handlers.pyrogram_handlers import (
    _auto_reply_tasks,
    _bot_drafts,
    _bot_draft_echoes,
    _pending_2fa,
    _pending_drafts,
    _poll_qr_login,
    handle_2fa_password,
    on_disconnect,
    on_connect,
    on_pyrogram_draft,
    on_pyrogram_message,
    on_status,
)
from system_messages import SYSTEM_MESSAGES
from utils.bot_utils import update_user_menu

TYPING_TEXT = SYSTEM_MESSAGES["draft_typing"]


@pytest.fixture(autouse=True)
def cleanup_handler_state():
    """Очищает глобальное состояние обработчиков между тестами."""
    _auto_reply_tasks.clear()
    _bot_drafts.clear()
    _bot_draft_echoes.clear()
    _pending_drafts.clear()
    _pending_2fa.clear()
    yield
    _auto_reply_tasks.clear()
    _bot_drafts.clear()
    _bot_draft_echoes.clear()
    _pending_drafts.clear()
    _pending_2fa.clear()


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

    @pytest.mark.asyncio
    async def test_disconnect_cancels_pending_2fa(self, mock_update, mock_context):
        temp_client = AsyncMock()
        _pending_2fa[mock_update.effective_user.id] = {
            "client": temp_client,
            "language_code": "en",
            "chat_id": mock_update.effective_chat.id,
        }

        with patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc, \
             patch("handlers.pyrogram_handlers.clear_session", new_callable=AsyncMock, return_value=True), \
             patch("handlers.pyrogram_handlers.get_system_message", new_callable=AsyncMock, return_value="Disconnected"):
            mock_pc.is_active.return_value = False

            await on_disconnect(mock_update, mock_context)

        temp_client.disconnect.assert_called_once()
        assert mock_update.effective_user.id not in _pending_2fa
        mock_update.message.reply_text.assert_called_once_with("Disconnected")


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
    async def test_connect_rejects_when_waiting_for_2fa_password(self, mock_update, mock_context):
        _pending_2fa[mock_update.effective_user.id] = {
            "client": AsyncMock(),
            "language_code": "en",
            "chat_id": mock_update.effective_chat.id,
        }

        with patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc, \
             patch("handlers.pyrogram_handlers._get_qr_login_task", return_value=None), \
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
        """Сообщение без текста и без голоса → ранний return."""
        message = MagicMock()
        message.text = None
        message.voice = None

        await on_pyrogram_message(123, MagicMock(), message)

    @pytest.mark.asyncio
    async def test_voice_message_transcribes_and_generates_draft(self):
        """Голосовое сообщение → транскрипция → черновик."""
        message = MagicMock()
        message.text = None
        message.voice = MagicMock()  # есть голосовое
        message.id = 42
        message.date = "2026-03-15T10:00:00Z"
        message.outgoing = False
        message.from_user = MagicMock()
        message.from_user.is_bot = False
        message.from_user.first_name = "Test"
        message.from_user.last_name = "User"
        message.from_user.username = "testuser"
        message.from_user.language_code = "ru"
        message.from_user.is_premium = True
        message.chat = MagicMock()
        message.chat.id = 456
        message.chat.type = MagicMock(value="private")

        with patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc, \
             patch("handlers.pyrogram_handlers.generate_reply", new_callable=AsyncMock) as mock_gen, \
             patch("handlers.pyrogram_handlers.get_user_settings", new_callable=AsyncMock, return_value={}), \
             patch("handlers.pyrogram_handlers.get_user", new_callable=AsyncMock, return_value={"language_code": "en"}), \
             patch("handlers.pyrogram_handlers.get_system_message", new_callable=AsyncMock, return_value=TYPING_TEXT):
            mock_pc.transcribe_voice = AsyncMock(return_value="Привет, как дела?")
            mock_pc.read_chat_history = AsyncMock(return_value=[])
            mock_pc.set_draft = AsyncMock(return_value=True)
            mock_gen.return_value = "Всё отлично!"

            await on_pyrogram_message(123, MagicMock(), message)

        mock_pc.transcribe_voice.assert_called_once_with(123, 456, 42)
        mock_gen.assert_called_once()
        history_arg = mock_gen.await_args.args[0]
        assert history_arg == [{
            "role": "other",
            "text": "Привет, как дела?",
            "date": "2026-03-15T10:00:00Z",
            "name": "Test",
            "last_name": "User",
            "username": "testuser",
        }]

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

    @pytest.mark.asyncio
    async def test_invalid_auto_reply_is_treated_as_off(self):
        """Невалидный auto_reply не должен запускать таймер автоответа."""
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
             patch("handlers.pyrogram_handlers.get_user_settings", new_callable=AsyncMock, return_value={"auto_reply": 86400}), \
             patch("handlers.pyrogram_handlers.get_user", new_callable=AsyncMock, return_value={"language_code": "en"}), \
             patch("handlers.pyrogram_handlers.get_system_message", new_callable=AsyncMock, return_value=TYPING_TEXT), \
             patch("handlers.pyrogram_handlers._schedule_auto_reply") as mock_schedule:
            mock_pc.read_chat_history = AsyncMock(return_value=[
                {"role": "other", "text": "Hello"}
            ])
            mock_pc.set_draft = AsyncMock(return_value=True)
            mock_gen.return_value = "Hi there!"

            await on_pyrogram_message(123, MagicMock(), message)

        mock_schedule.assert_not_called()


class TestHandle2FAPassword:
    """Тесты для handle_2fa_password()."""

    @pytest.mark.asyncio
    async def test_ignores_users_without_pending_2fa(self, mock_update, mock_context):
        await handle_2fa_password(mock_update, mock_context)

        mock_update.message.delete.assert_not_called()
        mock_context.bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_wrong_password_keeps_pending_2fa_session(self, mock_update, mock_context):
        temp_client = AsyncMock()

        class PasswordHashInvalid(Exception):
            pass

        temp_client.invoke = AsyncMock(side_effect=[MagicMock(), PasswordHashInvalid()])

        _pending_2fa[mock_update.effective_user.id] = {
            "client": temp_client,
            "language_code": "en",
            "chat_id": mock_update.effective_chat.id,
        }

        with patch("handlers.pyrogram_handlers.GetPassword", return_value="get-password"), \
             patch("handlers.pyrogram_handlers.CheckPassword", side_effect=lambda password: ("check-password", password)), \
             patch("handlers.pyrogram_handlers.compute_password_check", return_value="srp-check"), \
             patch("handlers.pyrogram_handlers.get_system_message", new_callable=AsyncMock, return_value="Wrong password"):
            with pytest.raises(ApplicationHandlerStop):
                await handle_2fa_password(mock_update, mock_context)

        assert _pending_2fa[mock_update.effective_user.id]["client"] is temp_client
        mock_update.message.delete.assert_called_once()
        mock_context.bot.send_message.assert_called_once_with(
            chat_id=mock_update.effective_chat.id,
            text="Wrong password",
        )
        temp_client.disconnect.assert_not_called()

    @pytest.mark.asyncio
    async def test_successful_password_flow_saves_session_and_starts_listener(self, mock_update, mock_context):
        temp_client = AsyncMock()
        temp_client.storage.user_id = AsyncMock()
        temp_client.storage.is_bot = AsyncMock()
        temp_client.export_session_string = AsyncMock(return_value="session-123")

        user_obj = MagicMock()
        user_obj.id = 777
        user_obj.bot = False
        auth_result = MagicMock()
        auth_result.user = user_obj
        temp_client.invoke = AsyncMock(side_effect=[MagicMock(), auth_result])

        _pending_2fa[mock_update.effective_user.id] = {
            "client": temp_client,
            "language_code": "en",
            "chat_id": mock_update.effective_chat.id,
        }

        with patch("handlers.pyrogram_handlers.GetPassword", return_value="get-password"), \
             patch("handlers.pyrogram_handlers.CheckPassword", side_effect=lambda password: ("check-password", password)), \
             patch("handlers.pyrogram_handlers.compute_password_check", return_value="srp-check"), \
             patch("handlers.pyrogram_handlers.save_session", new_callable=AsyncMock, return_value=True) as mock_save, \
             patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc, \
             patch("handlers.pyrogram_handlers.get_system_message", new_callable=AsyncMock, return_value="Connected"):
            mock_pc.start_listening = AsyncMock(return_value=True)

            with pytest.raises(ApplicationHandlerStop):
                await handle_2fa_password(mock_update, mock_context)

        mock_update.message.delete.assert_called_once()
        mock_save.assert_called_once_with(mock_update.effective_user.id, "session-123")
        mock_pc.start_listening.assert_called_once_with(mock_update.effective_user.id, "session-123")
        mock_context.bot.send_message.assert_called_once_with(
            chat_id=mock_update.effective_chat.id,
            text="Connected",
        )
        temp_client.disconnect.assert_called_once()
        assert mock_update.effective_user.id not in _pending_2fa


class TestOnPyrogramDraft:
    """Тесты для on_pyrogram_draft() — probe-based detection."""

    @pytest.mark.asyncio
    async def test_ignores_bot_draft(self):
        """Черновик, установленный ботом → игнорируется."""
        _bot_draft_echoes[(123, 456)] = TYPING_TEXT

        with patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc:
            mock_pc.set_draft = AsyncMock()
            await on_pyrogram_draft(123, 456, TYPING_TEXT)

        mock_pc.set_draft.assert_not_called()
        # Cleanup
        _bot_drafts.pop((123, 456), None)

    @pytest.mark.asyncio
    async def test_empty_draft_clears_pending(self):
        """Пустой черновик → очищает pending."""
        task = MagicMock()
        task.done.return_value = False
        _pending_drafts[(123, 456)] = "some text"
        _bot_drafts[(123, 456)] = "AI ответ"
        _auto_reply_tasks[(123, 456)] = task

        with patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc:
            mock_pc.set_draft = AsyncMock()
            await on_pyrogram_draft(123, 456, "")

        assert (123, 456) not in _pending_drafts
        assert (123, 456) not in _bot_drafts
        assert (123, 456) not in _auto_reply_tasks
        task.cancel.assert_called_once()

    @pytest.mark.asyncio
    async def test_ignores_bot_echo_without_clearing_auto_reply_draft(self):
        """Echo от set_draft не должен удалять AI-черновик, ожидающий автоответа."""
        _bot_drafts[(123, 456)] = "AI ответ"
        _bot_draft_echoes[(123, 456)] = "AI ответ"

        with patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc:
            mock_pc.set_draft = AsyncMock()
            await on_pyrogram_draft(123, 456, "AI ответ")

        assert _bot_drafts[(123, 456)] == "AI ответ"
        assert (123, 456) not in _bot_draft_echoes
        mock_pc.set_draft.assert_not_called()

    @pytest.mark.asyncio
    async def test_processes_user_draft(self):
        """Текст пользователя → устанавливает пробу, ждёт, генерирует ответ."""
        _bot_drafts.pop((123, 456), None)
        _pending_drafts.pop((123, 456), None)

        with patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc, \
             patch("handlers.pyrogram_handlers.generate_response", new_callable=AsyncMock) as mock_gen, \
             patch("handlers.pyrogram_handlers.asyncio.sleep", new_callable=AsyncMock), \
             patch("handlers.pyrogram_handlers.get_user_settings", new_callable=AsyncMock, return_value={}), \
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


class TestUpdateUserMenu:
    """Тесты для update_user_menu()."""

    @pytest.mark.asyncio
    async def test_sets_disconnect_when_connected(self, mock_bot):
        """Подключён → показывает disconnect, скрывает connect."""
        with patch(
            "utils.bot_utils.get_system_messages",
            new_callable=AsyncMock,
            return_value={
                "menu_connect": "Connect",
                "menu_disconnect": "Disconnect",
                "menu_status": "Status",
                "menu_settings": "Settings",
            },
        ):
            await update_user_menu(mock_bot, 123, "en", is_connected=True)

        call_args = mock_bot.set_my_commands.call_args
        commands = call_args.args[0]
        command_names = [c.command for c in commands]
        assert "disconnect" in command_names
        assert "connect" not in command_names

    @pytest.mark.asyncio
    async def test_sets_connect_when_disconnected(self, mock_bot):
        """Отключён → показывает connect, скрывает disconnect."""
        with patch(
            "utils.bot_utils.get_system_messages",
            new_callable=AsyncMock,
            return_value={
                "menu_connect": "Connect",
                "menu_disconnect": "Disconnect",
                "menu_status": "Status",
                "menu_settings": "Settings",
            },
        ):
            await update_user_menu(mock_bot, 123, "en", is_connected=False)

        call_args = mock_bot.set_my_commands.call_args
        commands = call_args.args[0]
        command_names = [c.command for c in commands]
        assert "connect" in command_names
        assert "disconnect" not in command_names

    @pytest.mark.asyncio
    async def test_none_language_defaults_to_english(self, mock_bot):
        with patch(
            "utils.bot_utils.get_system_messages",
            new_callable=AsyncMock,
            return_value={
                "menu_connect": "Connect",
                "menu_disconnect": "Disconnect",
                "menu_status": "Status",
                "menu_settings": "Settings",
            },
        ):
            await update_user_menu(mock_bot, 123, None, is_connected=False)

        mock_bot.set_my_commands.assert_called_once()
