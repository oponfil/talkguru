# tests/test_handlers.py — Тесты для handlers/pyrogram_handlers.py

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.ext import ApplicationHandlerStop

from handlers.pyrogram_handlers import (
    _auto_reply_tasks,
    _bot_drafts,
    _bot_draft_echoes,
    _maybe_schedule_auto_reply,
    _pending_drafts,
    _processed_incoming_ids,
    _regenerate_reply,
    _reply_locks,
    _reply_pending,
    on_disconnect, on_disconnect_confirm_callback, on_disconnect_cancel_callback,
    on_pyrogram_draft,
    on_pyrogram_message,
    on_status,
    _verify_draft_delivery,
    poll_missed_messages,
)
from handlers.connect_handler import (
    _pending_2fa,
    _pending_phone,
    _poll_qr_login,
    handle_2fa_password,
    handle_connect_text,
    on_connect_qr_callback,
    on_connect,
)
from system_messages import SYSTEM_MESSAGES
from utils.bot_utils import update_user_menu

from config import CHAT_IGNORED_SENTINEL, DEFAULT_STYLE, STYLE_TO_EMOJI
TYPING_TEXT = SYSTEM_MESSAGES["draft_typing"].format(emoji=STYLE_TO_EMOJI[DEFAULT_STYLE])
REAL_ASYNCIO_SLEEP = asyncio.sleep


def _close_coroutine_task(coro):
    """Имитирует create_task в тестах и закрывает coroutine без запуска."""
    coro.close()
    task = MagicMock()
    task.done.return_value = False
    return task


@pytest.fixture(autouse=True)
def cleanup_handler_state():
    """Очищает глобальное состояние обработчиков между тестами."""
    _auto_reply_tasks.clear()
    _bot_drafts.clear()
    _bot_draft_echoes.clear()
    _pending_drafts.clear()
    _pending_2fa.clear()
    _pending_phone.clear()
    _reply_locks.clear()
    _reply_pending.clear()
    _processed_incoming_ids.clear()
    yield
    _auto_reply_tasks.clear()
    _bot_drafts.clear()
    _bot_draft_echoes.clear()
    _pending_drafts.clear()
    _pending_2fa.clear()
    _pending_phone.clear()
    _reply_locks.clear()
    _reply_pending.clear()
    _processed_incoming_ids.clear()


class TestOnDisconnect:
    """Тесты для on_disconnect() и disconnect callbacks."""

    @pytest.mark.asyncio
    async def test_not_connected_shows_status(self, mock_update, mock_context):
        """Не подключён → сообщение 'not connected', без подтверждения."""
        with patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc, \
             patch("handlers.pyrogram_handlers.ensure_effective_user", new_callable=AsyncMock, return_value={}), \
             patch("handlers.pyrogram_handlers.clear_session", new_callable=AsyncMock, return_value=True) as mock_clear, \
             patch("handlers.pyrogram_handlers.get_system_message", new_callable=AsyncMock, return_value="Not connected"):
            mock_pc.is_active.return_value = False

            await on_disconnect(mock_update, mock_context)

        mock_update.message.reply_text.assert_called_once_with("Not connected")
        mock_clear.assert_called_once_with(mock_update.effective_user.id)

    @pytest.mark.asyncio
    async def test_not_connected_returns_error_when_clear_session_fails(self, mock_update, mock_context):
        """Не подключён + clear_session fails → сообщение об ошибке."""
        with patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc, \
             patch("handlers.pyrogram_handlers.ensure_effective_user", new_callable=AsyncMock, return_value={}), \
             patch("handlers.pyrogram_handlers.clear_session", new_callable=AsyncMock, return_value=False) as mock_clear, \
             patch("handlers.pyrogram_handlers.get_system_message", new_callable=AsyncMock, return_value="Disconnect failed"):
            mock_pc.is_active.return_value = False

            await on_disconnect(mock_update, mock_context)

        mock_update.message.reply_text.assert_called_once_with("Disconnect failed")
        mock_clear.assert_called_once_with(mock_update.effective_user.id)

    @pytest.mark.asyncio
    async def test_connected_shows_confirmation(self, mock_update, mock_context):
        """Подключён → показывает предупреждение с кнопками."""
        with patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc, \
             patch("handlers.pyrogram_handlers.ensure_effective_user", new_callable=AsyncMock, return_value={}), \
             patch("handlers.pyrogram_handlers.get_system_message", new_callable=AsyncMock, return_value="Are you sure?"):
            mock_pc.is_active.return_value = True

            await on_disconnect(mock_update, mock_context)

        # Показано предупреждение с reply_markup (кнопки)
        mock_update.message.reply_text.assert_called_once()
        call_kwargs = mock_update.message.reply_text.call_args
        assert call_kwargs[1].get("reply_markup") is not None or \
               (len(call_kwargs[0]) > 0 and hasattr(call_kwargs, "kwargs") and "reply_markup" in call_kwargs.kwargs)

    @pytest.mark.asyncio
    async def test_pending_2fa_shows_confirmation(self, mock_update, mock_context):
        """Pending 2FA → показывает подтверждение вместо мгновенного отключения."""
        _pending_2fa[mock_update.effective_user.id] = {
            "client": AsyncMock(),
            "language_code": "en",
            "chat_id": mock_update.effective_chat.id,
        }

        with patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc, \
             patch("handlers.pyrogram_handlers.ensure_effective_user", new_callable=AsyncMock, return_value={}), \
             patch("handlers.pyrogram_handlers.get_system_message", new_callable=AsyncMock, return_value="Are you sure?"):
            mock_pc.is_active.return_value = False

            await on_disconnect(mock_update, mock_context)

        # Показано предупреждение, pending_2fa НЕ отменён (ждём подтверждения)
        mock_update.message.reply_text.assert_called_once()
        assert mock_update.effective_user.id in _pending_2fa


class TestOnDisconnectCallbacks:
    """Тесты для on_disconnect_confirm_callback и on_disconnect_cancel_callback."""

    def _make_callback_update(self, user_id=12345, chat_id=12345):
        """Создаёт mock update для callback query."""
        update = AsyncMock()
        update.effective_user.id = user_id
        update.effective_user.language_code = "en"
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_reply_markup = AsyncMock()
        update.callback_query.message.chat_id = chat_id
        return update

    @pytest.mark.asyncio
    async def test_confirm_disconnects_and_clears_session(self, mock_context):
        """Подтверждение → отключает и очищает сессию."""
        update = self._make_callback_update()
        with patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc, \
             patch("handlers.pyrogram_handlers.clear_session", new_callable=AsyncMock, return_value=True) as mock_clear, \
             patch("handlers.pyrogram_handlers.get_system_message", new_callable=AsyncMock, return_value="Disconnected"), \
             patch("handlers.pyrogram_handlers.update_user_menu", new_callable=AsyncMock):
            mock_pc.is_active.return_value = True
            mock_pc.stop_listening = AsyncMock(return_value=True)

            await on_disconnect_confirm_callback(update, mock_context)

        mock_pc.stop_listening.assert_called_once_with(update.effective_user.id)
        mock_clear.assert_called_once_with(update.effective_user.id)
        mock_context.bot.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_confirm_error_when_stop_fails(self, mock_context):
        """Подтверждение + stop_listening fails → ошибка."""
        update = self._make_callback_update()
        with patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc, \
             patch("handlers.pyrogram_handlers.clear_session", new_callable=AsyncMock, return_value=True) as mock_clear, \
             patch("handlers.pyrogram_handlers.get_system_message", new_callable=AsyncMock, return_value="Error"):
            mock_pc.is_active.return_value = True
            mock_pc.stop_listening = AsyncMock(return_value=False)

            await on_disconnect_confirm_callback(update, mock_context)

        mock_clear.assert_not_called()

    @pytest.mark.asyncio
    async def test_confirm_error_when_clear_session_fails(self, mock_context):
        """Подтверждение + clear_session fails → ошибка."""
        update = self._make_callback_update()
        with patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc, \
             patch("handlers.pyrogram_handlers.clear_session", new_callable=AsyncMock, return_value=False), \
             patch("handlers.pyrogram_handlers.get_system_message", new_callable=AsyncMock, return_value="Error"):
            mock_pc.is_active.return_value = False

            await on_disconnect_confirm_callback(update, mock_context)

        mock_context.bot.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_confirm_cancels_pending_2fa(self, mock_context):
        """Подтверждение отменяет pending 2FA."""
        update = self._make_callback_update()
        user_id = update.effective_user.id
        temp_client = AsyncMock()
        _pending_2fa[user_id] = {
            "client": temp_client,
            "language_code": "en",
            "chat_id": update.callback_query.message.chat_id,
        }

        with patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc, \
             patch("handlers.pyrogram_handlers.clear_session", new_callable=AsyncMock, return_value=True), \
             patch("handlers.pyrogram_handlers.get_system_message", new_callable=AsyncMock, return_value="Disconnected"), \
             patch("handlers.pyrogram_handlers.update_user_menu", new_callable=AsyncMock):
            mock_pc.is_active.return_value = False

            await on_disconnect_confirm_callback(update, mock_context)

        temp_client.disconnect.assert_called_once()
        assert user_id not in _pending_2fa

    @pytest.mark.asyncio
    async def test_cancel_removes_buttons(self, mock_context):
        """Отмена → убирает кнопки и показывает статус."""
        update = self._make_callback_update()
        with patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc, \
             patch("handlers.pyrogram_handlers.get_system_message", new_callable=AsyncMock, return_value="Connected"):
            mock_pc.is_active.return_value = True
            await on_disconnect_cancel_callback(update, mock_context)

        update.callback_query.answer.assert_called_once()
        update.callback_query.edit_message_reply_markup.assert_called_once_with(reply_markup=None)
        mock_context.bot.send_message.assert_called_once()


class TestOnStatus:
    """Тесты для on_status()."""

    @pytest.mark.asyncio
    async def test_connected_status(self, mock_update, mock_context):
        with patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc, \
             patch("handlers.pyrogram_handlers.ensure_effective_user", new_callable=AsyncMock, return_value={}):
            mock_pc.is_active.return_value = True

            await on_status(mock_update, mock_context)

        mock_update.message.reply_text.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnected_status(self, mock_update, mock_context):
        with patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc, \
             patch("handlers.pyrogram_handlers.ensure_effective_user", new_callable=AsyncMock, return_value={}):
            mock_pc.is_active.return_value = False

            await on_status(mock_update, mock_context)

        mock_update.message.reply_text.assert_called_once()


class TestOnConnect:
    """Тесты для on_connect() и QR login flow."""

    @pytest.mark.asyncio
    async def test_connect_upserts_user_and_shows_phone_prompt(self, mock_update, mock_context):
        """`/connect` должен создавать пользователя и показывать phone prompt с кнопкой QR."""
        with patch("handlers.connect_handler.pyrogram_client") as mock_pc, \
             patch("handlers.connect_handler.upsert_effective_user", new_callable=AsyncMock, return_value=True) as mock_upsert, \
             patch("handlers.connect_handler.get_system_message", new_callable=AsyncMock, return_value="Enter phone"), \
             patch("handlers.connect_handler._get_qr_login_task", return_value=None):
            mock_pc.is_active.return_value = False

            await on_connect(mock_update, mock_context)

        mock_upsert.assert_called_once_with(mock_update)
        # reply_text вызван с InlineKeyboard (кнопка QR)
        mock_update.message.reply_text.assert_called_once()
        assert mock_update.effective_user.id in _pending_phone
        assert _pending_phone[mock_update.effective_user.id]["state"] == "awaiting_phone"

    @pytest.mark.asyncio
    async def test_connect_rejects_when_qr_login_already_running(self, mock_update, mock_context):
        with patch("handlers.connect_handler.pyrogram_client") as mock_pc, \
             patch("handlers.connect_handler._get_qr_login_task", return_value=MagicMock()), \
             patch("handlers.connect_handler.get_system_message", new_callable=AsyncMock, return_value="In progress"):
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

        with patch("handlers.connect_handler.pyrogram_client") as mock_pc, \
             patch("handlers.connect_handler._get_qr_login_task", return_value=None), \
             patch("handlers.connect_handler.get_system_message", new_callable=AsyncMock, return_value="In progress"):
            mock_pc.is_active.return_value = False

            await on_connect(mock_update, mock_context)

        mock_update.message.reply_text.assert_called_once_with("In progress")

    @pytest.mark.asyncio
    async def test_connect_clears_pending_phone_when_prompt_setup_fails(self, mock_update, mock_context):
        """Сбой при показе phone prompt не должен оставлять пользователя в зависшем flow."""
        user_id = mock_update.effective_user.id

        with patch("handlers.connect_handler.pyrogram_client") as mock_pc, \
             patch("handlers.connect_handler.upsert_effective_user", new_callable=AsyncMock, return_value=True), \
             patch("handlers.connect_handler._get_qr_login_task", return_value=None), \
             patch("handlers.connect_handler.get_system_message", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
            mock_pc.is_active.return_value = False

            await on_connect(mock_update, mock_context)

        assert user_id not in _pending_phone

    @pytest.mark.asyncio
    async def test_poll_qr_login_success_saves_session_and_starts_listening(self, mock_bot):
        """Успешный QR login должен сохранить сессию и запустить listener."""
        mock_user = MagicMock(id=999, bot=False)
        mock_auth = MagicMock(user=mock_user)
        login_success = type("LoginTokenSuccess", (), {"authorization": mock_auth})()

        mock_client = AsyncMock()
        mock_client.invoke = AsyncMock(return_value=login_success)
        mock_client.export_session_string = AsyncMock(return_value="session-123")
        mock_client.disconnect = AsyncMock()

        with patch("handlers.connect_handler.asyncio.sleep", new_callable=AsyncMock), \
             patch("handlers.connect_handler.save_session", new_callable=AsyncMock, return_value=True) as mock_save_session, \
             patch("handlers.connect_handler.pyrogram_client") as mock_pc, \
             patch("handlers.connect_handler.get_system_message", new_callable=AsyncMock, return_value="Connected"):
            mock_pc.start_listening = AsyncMock(return_value=True)

            await _poll_qr_login(mock_client, 123, "en", mock_bot, 456, sensitive_msg_ids=[500])

        mock_client.export_session_string.assert_called_once()
        mock_client.disconnect.assert_called_once()
        mock_save_session.assert_called_once_with(123, "session-123")
        mock_pc.start_listening.assert_called_once_with(123, "session-123")
        mock_bot.send_message.assert_called_once_with(chat_id=456, text="Connected")
        # QR-сообщение удалено
        mock_bot.delete_message.assert_called_once_with(chat_id=456, message_id=500)

    @pytest.mark.asyncio
    async def test_poll_qr_login_stops_when_save_session_fails(self, mock_bot):
        mock_user = MagicMock(id=999, bot=False)
        mock_auth = MagicMock(user=mock_user)
        login_success = type("LoginTokenSuccess", (), {"authorization": mock_auth})()

        mock_client = AsyncMock()
        mock_client.invoke = AsyncMock(return_value=login_success)
        mock_client.export_session_string = AsyncMock(return_value="session-123")
        mock_client.disconnect = AsyncMock()

        with patch("handlers.connect_handler.asyncio.sleep", new_callable=AsyncMock), \
             patch("handlers.connect_handler.save_session", new_callable=AsyncMock, return_value=False), \
             patch("handlers.connect_handler.pyrogram_client") as mock_pc, \
             patch("handlers.connect_handler.get_system_message", new_callable=AsyncMock, return_value="Connect failed"):
            mock_pc.start_listening = AsyncMock(return_value=True)

            await _poll_qr_login(mock_client, 123, "en", mock_bot, 456, sensitive_msg_ids=[500])

        mock_pc.start_listening.assert_not_called()
        mock_bot.send_message.assert_called_once_with(chat_id=456, text="Connect failed")

    @pytest.mark.asyncio
    async def test_poll_qr_login_clears_session_when_listener_start_fails(self, mock_bot):
        mock_user = MagicMock(id=999, bot=False)
        mock_auth = MagicMock(user=mock_user)
        login_success = type("LoginTokenSuccess", (), {"authorization": mock_auth})()

        mock_client = AsyncMock()
        mock_client.invoke = AsyncMock(return_value=login_success)
        mock_client.export_session_string = AsyncMock(return_value="session-123")
        mock_client.disconnect = AsyncMock()

        with patch("handlers.connect_handler.asyncio.sleep", new_callable=AsyncMock), \
             patch("handlers.connect_handler.save_session", new_callable=AsyncMock, return_value=True), \
             patch("handlers.connect_handler.clear_session", new_callable=AsyncMock, return_value=True) as mock_clear, \
             patch("handlers.connect_handler.pyrogram_client") as mock_pc, \
             patch("handlers.connect_handler.get_system_message", new_callable=AsyncMock, return_value="Connect failed"):
            mock_pc.start_listening = AsyncMock(return_value=False)

            await _poll_qr_login(mock_client, 123, "en", mock_bot, 456, sensitive_msg_ids=[500])

        mock_clear.assert_called_once_with(123)
        mock_bot.send_message.assert_called_once_with(chat_id=456, text="Connect failed")

    @pytest.mark.asyncio
    async def test_poll_qr_login_stores_user_from_success_result(self, mock_bot):
        """При LoginTokenSuccess сохраняет user_id/is_bot в storage."""
        mock_user = MagicMock(id=777, bot=False)
        mock_auth = MagicMock(user=mock_user)
        login_success = type("LoginTokenSuccess", (), {"authorization": mock_auth})()

        mock_client = AsyncMock()
        mock_client.invoke = AsyncMock(return_value=login_success)
        mock_client.export_session_string = AsyncMock(return_value="session-777")
        mock_client.disconnect = AsyncMock()
        mock_client.storage = AsyncMock()

        with patch("handlers.connect_handler.asyncio.sleep", new_callable=AsyncMock), \
             patch("handlers.connect_handler.save_session", new_callable=AsyncMock, return_value=True), \
             patch("handlers.connect_handler.pyrogram_client") as mock_pc, \
             patch("handlers.connect_handler.get_system_message", new_callable=AsyncMock, return_value="OK"):
            mock_pc.start_listening = AsyncMock(return_value=True)

            await _poll_qr_login(mock_client, 123, "en", mock_bot, 456, sensitive_msg_ids=[500])

        mock_client.storage.user_id.assert_called_with(777)
        mock_client.storage.is_bot.assert_called_with(False)

    @pytest.mark.asyncio
    async def test_poll_qr_migration_2fa_enters_pending_2fa(self, mock_bot):
        """При SESSION_PASSWORD_NEEDED после миграции DC — переход в 2FA flow."""
        # Первый вызов ExportLoginToken → MigrateTo, второй → ImportLoginToken → 2FA error
        migrate_result = type("LoginTokenMigrateTo", (), {"dc_id": 5, "token": b"tok"})()
        session_pwd_error = type("SessionPasswordNeeded", (Exception,), {})()

        mock_client = AsyncMock()
        mock_client.invoke = AsyncMock(side_effect=[migrate_result, session_pwd_error])
        mock_client.disconnect = AsyncMock()
        mock_client.session = AsyncMock()
        mock_client.storage = AsyncMock()
        mock_client.storage.test_mode = AsyncMock(return_value=False)
        mock_client.storage.dc_id = AsyncMock()
        mock_client.storage.auth_key = AsyncMock(return_value=b"key")

        with patch("handlers.connect_handler.asyncio.sleep", new_callable=AsyncMock), \
             patch("handlers.connect_handler.Auth") as mock_auth_cls, \
             patch("handlers.connect_handler.PyroSession") as mock_pyro_session, \
             patch("handlers.connect_handler.get_system_message", new_callable=AsyncMock, return_value="Enter 2FA password"):
            mock_auth_cls.return_value.create = AsyncMock(return_value=b"new_key")
            mock_pyro_session.return_value = AsyncMock()

            await _poll_qr_login(mock_client, 42, "en", mock_bot, 456, sensitive_msg_ids=[500])

        # Клиент сохранён в _pending_2fa (не отключен)
        assert 42 in _pending_2fa
        assert _pending_2fa[42]["client"] is mock_client
        mock_bot.send_message.assert_called_once_with(chat_id=456, text="Enter 2FA password")
        mock_client.disconnect.assert_not_called()
        # QR-сообщение удалено даже при переходе в 2FA
        mock_bot.delete_message.assert_called_once_with(chat_id=456, message_id=500)

        # Cleanup
        _pending_2fa.pop(42, None)

    @pytest.mark.asyncio
    async def test_poll_qr_login_deletes_qr_message_on_timeout(self, mock_bot):
        """QR-сообщение удаляется при таймауте."""
        # Все poll-ы возвращают LoginToken (не авторизован)
        login_token = type("LoginToken", (), {"token": b"tok"})()

        mock_client = AsyncMock()
        mock_client.invoke = AsyncMock(return_value=login_token)
        mock_client.disconnect = AsyncMock()

        with patch("handlers.connect_handler.asyncio.sleep", new_callable=AsyncMock), \
             patch("handlers.connect_handler.get_system_message", new_callable=AsyncMock, return_value="QR expired"):

            await _poll_qr_login(mock_client, 123, "en", mock_bot, 456, sensitive_msg_ids=[500])

        # QR-сообщение удалено при таймауте
        mock_bot.delete_message.assert_called_once_with(chat_id=456, message_id=500)

    @pytest.mark.asyncio
    async def test_poll_qr_login_deletes_qr_message_on_task_cancel(self, mock_bot):
        """При отмене фоновой QR-задачи cleanup удаляет QR-сообщение ровно один раз."""
        login_token = type("LoginToken", (), {"token": b"tok"})()

        mock_client = AsyncMock()
        mock_client.invoke = AsyncMock(return_value=login_token)
        mock_client.disconnect = AsyncMock()

        sleep_started = asyncio.Event()
        release_sleep = asyncio.Event()

        async def blocked_sleep(_: int) -> None:
            sleep_started.set()
            await release_sleep.wait()

        with patch("handlers.connect_handler.asyncio.sleep", side_effect=blocked_sleep):
            task = asyncio.create_task(
                _poll_qr_login(mock_client, 123, "en", mock_bot, 456, sensitive_msg_ids=[500])
            )
            await sleep_started.wait()
            await REAL_ASYNCIO_SLEEP(0)
            task.cancel()

            with pytest.raises(asyncio.CancelledError):
                await task

        mock_client.disconnect.assert_called_once()
        mock_bot.delete_message.assert_called_once_with(chat_id=456, message_id=500)


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

        # read_chat_history теперь сам транскрибирует голосовые
        voice_history = [{
            "role": "other",
            "text": "Привет, как дела?",
            "date": "2026-03-15T10:00:00Z",
            "name": "Test",
            "last_name": "User",
            "username": "testuser",
        }]

        with patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc, \
             patch("handlers.pyrogram_handlers.generate_reply", new_callable=AsyncMock) as mock_gen, \
             patch("handlers.pyrogram_handlers.get_user", new_callable=AsyncMock, return_value={"language_code": "en", "settings": {}}), \
             patch("handlers.pyrogram_handlers.get_system_message", new_callable=AsyncMock, return_value=TYPING_TEXT):
            mock_pc.transcribe_voice = AsyncMock(return_value="Привет, как дела?")
            mock_pc.read_chat_history = AsyncMock(return_value=voice_history)
            mock_pc.set_draft = AsyncMock(return_value=True)
            mock_pc.get_draft = AsyncMock(return_value=None)
            mock_pc.get_chat_bio = AsyncMock(return_value=None)
            mock_gen.return_value = "Всё отлично!"

            await on_pyrogram_message(123, MagicMock(), message)

        mock_pc.transcribe_voice.assert_called_once_with(123, 456, 42)
        mock_gen.assert_called_once()
        history_arg = mock_gen.await_args.args[0]
        assert history_arg == voice_history

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
             patch("handlers.pyrogram_handlers.get_user", new_callable=AsyncMock, return_value={"language_code": "en", "settings": {}}), \
             patch("handlers.pyrogram_handlers.get_system_message", new_callable=AsyncMock, return_value=TYPING_TEXT):
            mock_pc.read_chat_history = AsyncMock(return_value=[
                {"role": "other", "text": "Hello"}
            ])
            mock_pc.set_draft = AsyncMock(return_value=True)
            mock_pc.get_draft = AsyncMock(return_value=None)
            mock_pc.get_chat_bio = AsyncMock(return_value=None)
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
             patch("handlers.pyrogram_handlers.get_user", new_callable=AsyncMock, return_value={"language_code": "en", "settings": {"auto_reply": 86400}}), \
             patch("handlers.pyrogram_handlers.get_system_message", new_callable=AsyncMock, return_value=TYPING_TEXT), \
             patch("handlers.pyrogram_handlers._schedule_auto_reply") as mock_schedule:
            mock_pc.read_chat_history = AsyncMock(return_value=[
                {"role": "other", "text": "Hello"}
            ])
            mock_pc.set_draft = AsyncMock(return_value=True)
            mock_pc.get_draft = AsyncMock(return_value=None)
            mock_pc.get_chat_bio = AsyncMock(return_value=None)
            mock_gen.return_value = "Hi there!"

            await on_pyrogram_message(123, MagicMock(), message)

        mock_schedule.assert_not_called()

    @pytest.mark.asyncio
    async def test_second_message_during_lock_is_queued(self):
        """Второе сообщение во время генерации ставит pending-флаг и не вызывает AI."""
        # Имитируем активный лок
        _reply_locks[(123, 456)] = True

        message = MagicMock()
        message.text = "Second message"
        message.voice = None
        message.outgoing = False
        message.from_user = MagicMock()
        message.from_user.is_bot = False
        message.from_user.first_name = "Test"
        message.chat = MagicMock()
        message.chat.id = 456
        message.chat.type = MagicMock(value="private")

        with patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc, \
             patch("handlers.pyrogram_handlers.generate_reply", new_callable=AsyncMock) as mock_gen, \
             patch("handlers.pyrogram_handlers.get_user", new_callable=AsyncMock, return_value={"language_code": "en", "settings": {}}):
            mock_pc.set_draft = AsyncMock()
            mock_pc.get_draft = AsyncMock(return_value=None)
            mock_pc.get_chat_bio = AsyncMock(return_value=None)

            await on_pyrogram_message(123, MagicMock(), message)

        # AI не вызван — сообщение поставлено в очередь
        mock_gen.assert_not_called()
        assert _reply_pending[(123, 456)] is True
        # Лок всё ещё активен (не снимался)
        assert _reply_locks[(123, 456)] is True

    @pytest.mark.asyncio
    async def test_pending_message_triggers_regeneration_after_lock_release(self):
        """После генерации с pending-флагом запускается _regenerate_reply."""
        message = MagicMock()
        message.text = "Hello"
        message.outgoing = False
        message.from_user = MagicMock()
        message.from_user.is_bot = False
        message.from_user.first_name = "Test"
        message.chat = MagicMock()
        message.chat.id = 456
        message.chat.type = MagicMock(value="private")

        # Ставим pending-флаг до вызова (имитация: второе сообщение пришло)
        _reply_pending[(123, 456)] = True

        with patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc, \
             patch("handlers.pyrogram_handlers.generate_reply", new_callable=AsyncMock) as mock_gen, \
             patch("handlers.pyrogram_handlers.get_user", new_callable=AsyncMock, return_value={"language_code": "en", "settings": {}}), \
             patch("handlers.pyrogram_handlers.get_system_message", new_callable=AsyncMock, return_value=TYPING_TEXT), \
             patch("handlers.pyrogram_handlers._regenerate_reply", new_callable=AsyncMock), \
             patch("handlers.pyrogram_handlers.asyncio.create_task", side_effect=_close_coroutine_task) as mock_create_task:
            mock_pc.read_chat_history = AsyncMock(return_value=[
                {"role": "other", "text": "Hello"}
            ])
            mock_pc.set_draft = AsyncMock(return_value=True)
            mock_pc.get_draft = AsyncMock(return_value=None)
            mock_pc.get_chat_bio = AsyncMock(return_value=None)
            mock_gen.return_value = "Hi there!"

            await on_pyrogram_message(123, MagicMock(), message)

        # generate_reply был вызван (первое сообщение обработано)
        mock_gen.assert_called_once()
        # create_task вызван 2 раза: _verify_draft_delivery + _regenerate_reply
        assert mock_create_task.call_count == 2
        # Лок снят после завершения
        assert (123, 456) not in _reply_locks
        assert (123, 456) not in _reply_pending

    @pytest.mark.asyncio
    async def test_no_regeneration_without_pending(self):
        """Без pending-флага _regenerate_reply не вызывается."""
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
             patch("handlers.pyrogram_handlers.get_user", new_callable=AsyncMock, return_value={"language_code": "en", "settings": {}}), \
             patch("handlers.pyrogram_handlers.get_system_message", new_callable=AsyncMock, return_value=TYPING_TEXT), \
             patch("handlers.pyrogram_handlers.asyncio.create_task", side_effect=_close_coroutine_task) as mock_create_task:
            mock_pc.read_chat_history = AsyncMock(return_value=[
                {"role": "other", "text": "Hello"}
            ])
            mock_pc.set_draft = AsyncMock(return_value=True)
            mock_pc.get_draft = AsyncMock(return_value=None)
            mock_pc.get_chat_bio = AsyncMock(return_value=None)
            mock_gen.return_value = "Hi there!"

            await on_pyrogram_message(123, MagicMock(), message)

        mock_gen.assert_called_once()
        # create_task вызван только для _verify_draft_delivery (без _regenerate_reply)
        assert mock_create_task.call_count == 1
        assert (123, 456) not in _reply_locks
        assert (123, 456) not in _reply_pending

    @pytest.mark.asyncio
    async def test_duplicate_message_is_skipped(self):
        """Повторное сообщение с тем же msg_id пропускается (дедупликация)."""
        message = MagicMock()
        message.text = "Hello"
        message.voice = None
        message.sticker = None
        message.id = 999
        message.outgoing = False
        message.from_user = MagicMock()
        message.from_user.is_bot = False
        message.from_user.first_name = "Test"
        message.chat = MagicMock()
        message.chat.id = 456
        message.chat.type = MagicMock(value="private")

        with patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc, \
             patch("handlers.pyrogram_handlers.generate_reply", new_callable=AsyncMock) as mock_gen, \
             patch("handlers.pyrogram_handlers.get_user", new_callable=AsyncMock, return_value={"language_code": "en", "settings": {}}), \
             patch("handlers.pyrogram_handlers.get_system_message", new_callable=AsyncMock, return_value=TYPING_TEXT):
            mock_pc.read_chat_history = AsyncMock(return_value=[
                {"role": "other", "text": "Hello"}
            ])
            mock_pc.set_draft = AsyncMock(return_value=True)
            mock_pc.get_draft = AsyncMock(return_value=None)
            mock_pc.get_chat_bio = AsyncMock(return_value=None)
            mock_gen.return_value = "Hi there!"
            await on_pyrogram_message(123, MagicMock(), message)
            assert mock_gen.call_count == 1

            # Второй вызов с тем же msg_id — пропускается
            await on_pyrogram_message(123, MagicMock(), message)
            assert mock_gen.call_count == 1  # не увеличился


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

        with patch("handlers.connect_handler.GetPassword", return_value="get-password"), \
             patch("handlers.connect_handler.CheckPassword", side_effect=lambda password: ("check-password", password)), \
             patch("handlers.connect_handler.compute_password_check", return_value="srp-check"), \
             patch("handlers.connect_handler.get_system_message", new_callable=AsyncMock, return_value="Wrong password"):
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

        with patch("handlers.connect_handler.GetPassword", return_value="get-password"), \
             patch("handlers.connect_handler.CheckPassword", side_effect=lambda password: ("check-password", password)), \
             patch("handlers.connect_handler.compute_password_check", return_value="srp-check"), \
             patch("handlers.connect_handler.save_session", new_callable=AsyncMock, return_value=True) as mock_save, \
             patch("handlers.connect_handler.pyrogram_client") as mock_pc, \
             patch("handlers.connect_handler.get_system_message", new_callable=AsyncMock, return_value="Connected"):
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
             patch("handlers.pyrogram_handlers.get_user", new_callable=AsyncMock, return_value={"language_code": "en", "settings": {}}), \
             patch("handlers.pyrogram_handlers.get_system_message", new_callable=AsyncMock, return_value=TYPING_TEXT), \
             patch("utils.utils.DEFAULT_PRO_MODEL", True):
            mock_pc.read_chat_history = AsyncMock(return_value=[
                {"role": "user", "text": "Привет"},
            ])
            mock_pc.set_draft = AsyncMock(return_value=True)
            mock_gen.return_value = "AI ответ"

            await on_pyrogram_draft(123, 456, "напиши стихи")

        # Первый вызов — проба (статус), второй — AI-ответ
        assert mock_pc.set_draft.call_count == 2
        mock_pc.set_draft.assert_any_call(123, 456, "AI ответ")
        mock_gen.assert_called_once()
        assert "model" in mock_gen.call_args.kwargs

    @pytest.mark.asyncio
    async def test_emoji_shortcut_resets_style(self):
        """Эмодзи стиля, совпадающего с глобальным, сбрасывает per-chat настройку (передает None)."""
        _bot_drafts.pop((123, 456), None)
        _pending_drafts.pop((123, 456), None)
        
        global_style = "romance"
        emoji = STYLE_TO_EMOJI[global_style]
        
        with patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc, \
             patch("handlers.pyrogram_handlers.generate_response", new_callable=AsyncMock) as mock_gen, \
             patch("handlers.pyrogram_handlers.asyncio.sleep", new_callable=AsyncMock), \
             patch("handlers.pyrogram_handlers.get_user", new_callable=AsyncMock, return_value={"language_code": "en", "settings": {"style": global_style}}), \
             patch("handlers.pyrogram_handlers.get_system_message", new_callable=AsyncMock, return_value=TYPING_TEXT), \
             patch("handlers.pyrogram_handlers.update_chat_style", new_callable=AsyncMock) as mock_update_style:
            
            mock_pc.read_chat_history = AsyncMock(return_value=[
                {"role": "user", "text": "Привет"},
            ])
            mock_pc.set_draft = AsyncMock(return_value=True)
            mock_gen.return_value = "AI ответ"

            await on_pyrogram_draft(123, 456, f"{emoji} напиши стихи")

        mock_update_style.assert_called_once_with(123, 456, None)

    @pytest.mark.asyncio
    async def test_emoji_shortcut_bypasses_global_ignore(self):
        """Глобальный Ignore не должен блокировать ручной emoji shortcut."""
        _bot_drafts.pop((123, 456), None)
        _pending_drafts.pop((123, 456), None)

        with patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc, \
             patch("handlers.pyrogram_handlers.generate_response", new_callable=AsyncMock) as mock_gen, \
             patch("handlers.pyrogram_handlers.asyncio.sleep", new_callable=AsyncMock), \
             patch("handlers.pyrogram_handlers.get_user", new_callable=AsyncMock, side_effect=[
                 {"language_code": "en", "settings": {"auto_reply": CHAT_IGNORED_SENTINEL}},
                 {"language_code": "en", "settings": {"auto_reply": CHAT_IGNORED_SENTINEL, "chat_styles": {"456": "romance"}}},
             ]), \
             patch("handlers.pyrogram_handlers.get_system_message", new_callable=AsyncMock, return_value=TYPING_TEXT), \
             patch("handlers.pyrogram_handlers.update_chat_style", new_callable=AsyncMock) as mock_update_style:
            mock_pc.read_chat_history = AsyncMock(return_value=[
                {"role": "user", "text": "Привет"},
            ])
            mock_pc.set_draft = AsyncMock(return_value=True)
            mock_gen.return_value = "AI ответ"

            await on_pyrogram_draft(123, 456, "💕 напиши стихи")

        mock_update_style.assert_called_once_with(123, 456, "romance")
        mock_gen.assert_called_once()
        mock_pc.set_draft.assert_any_call(123, 456, "AI ответ")

    @pytest.mark.asyncio
    async def test_emoji_shortcut_does_not_bypass_specific_ignore(self):
        """Per-chat Ignore должен блокировать даже ручной emoji shortcut."""
        with patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc, \
             patch("handlers.pyrogram_handlers.generate_response", new_callable=AsyncMock) as mock_gen, \
             patch("handlers.pyrogram_handlers.get_user", new_callable=AsyncMock, return_value={
                 "language_code": "en",
                 "settings": {"chat_auto_replies": {"456": CHAT_IGNORED_SENTINEL}},
             }), \
             patch("handlers.pyrogram_handlers.update_chat_style", new_callable=AsyncMock) as mock_update_style:
            mock_pc.set_draft = AsyncMock(return_value=True)

            await on_pyrogram_draft(123, 456, "💕 напиши стихи")

        mock_update_style.assert_not_called()
        mock_gen.assert_not_called()
        mock_pc.set_draft.assert_not_called()

    @pytest.mark.asyncio
    async def test_emoji_only_clears_probe_when_history_is_empty(self):
        """Только emoji без истории не должно оставлять вечный typing-пробник в draft."""
        _bot_drafts.pop((123, 456), None)
        _pending_drafts.pop((123, 456), None)

        with patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc, \
             patch("handlers.pyrogram_handlers.get_user", new_callable=AsyncMock, side_effect=[
                 {"language_code": "en", "settings": {}},
                 {"language_code": "en", "settings": {"chat_styles": {"456": "romance"}}},
             ]), \
             patch("handlers.pyrogram_handlers.get_system_message", new_callable=AsyncMock, return_value=SYSTEM_MESSAGES["draft_typing"]), \
             patch("handlers.pyrogram_handlers.update_chat_style", new_callable=AsyncMock) as mock_update_style, \
             patch("handlers.pyrogram_handlers.generate_reply", new_callable=AsyncMock) as mock_generate_reply:
            mock_pc.read_chat_history = AsyncMock(return_value=[])
            mock_pc.set_draft = AsyncMock(return_value=True)

            await on_pyrogram_draft(123, 456, "💕")

        mock_update_style.assert_called_once_with(123, 456, "romance")
        mock_generate_reply.assert_not_called()
        assert mock_pc.set_draft.call_count == 2
        mock_pc.set_draft.assert_any_call(123, 456, "💕 is typing...")
        mock_pc.set_draft.assert_any_call(123, 456, "")
        assert (123, 456) not in _bot_draft_echoes
        assert (123, 456) not in _bot_drafts


class TestVerifyDraftDelivery:
    """Тесты для _verify_draft_delivery()."""

    @pytest.mark.asyncio
    async def test_skips_when_user_already_cleared_draft(self):
        """Если пользователь уже удалил черновик — re-push пропускается."""
        # _bot_drafts пуст — пользователь уже очистил
        with patch("handlers.pyrogram_handlers.asyncio.sleep", new_callable=AsyncMock), \
             patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc:
            mock_pc.set_draft = AsyncMock()

            await _verify_draft_delivery(123, 456, "AI ответ")

        # set_draft не вызван — re-push пропущен
        mock_pc.set_draft.assert_not_called()

    @pytest.mark.asyncio
    async def test_re_pushes_draft_when_server_matches(self):
        """Server draft совпадает — повторно отправляем (extra push)."""
        _bot_drafts[(123, 456)] = "AI ответ"

        with patch("handlers.pyrogram_handlers.asyncio.sleep", new_callable=AsyncMock), \
             patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc:
            mock_pc.get_draft = AsyncMock(return_value="AI ответ")
            mock_pc.set_draft = AsyncMock(return_value=True)

            await _verify_draft_delivery(123, 456, "AI ответ")

        mock_pc.get_draft.assert_called_once_with(123, 456)
        mock_pc.set_draft.assert_called_once_with(123, 456, "AI ответ")

    @pytest.mark.asyncio
    async def test_skips_re_push_when_user_edited_draft(self):
        """Пользователь отредактировал draft — re-push пропускается."""
        _bot_drafts[(123, 456)] = "AI ответ"

        with patch("handlers.pyrogram_handlers.asyncio.sleep", new_callable=AsyncMock), \
             patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc:
            mock_pc.get_draft = AsyncMock(return_value="AI ответ + мои правки")
            mock_pc.set_draft = AsyncMock()

            await _verify_draft_delivery(123, 456, "AI ответ")

        mock_pc.get_draft.assert_called_once_with(123, 456)
        mock_pc.set_draft.assert_not_called()


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
                "menu_chats": "Chats",
                "menu_disconnect": "Disconnect",
                "menu_poke": "Poke",
                "menu_status": "Status",
                "menu_settings": "Settings",
            },
        ):
            await update_user_menu(mock_bot, 123, "en", is_connected=True)

        call_args = mock_bot.set_my_commands.call_args
        commands = call_args.args[0]
        command_names = [c.command for c in commands]
        assert "chats" in command_names
        assert "poke" in command_names
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


class TestConnectPhoneFlow:
    """Тесты для phone-first connect flow."""

    @pytest.mark.asyncio
    async def test_connect_sends_phone_prompt_with_qr_button(self, mock_update, mock_context):
        """`/connect` отправляет сообщение с кнопкой QR."""
        with patch("handlers.connect_handler.pyrogram_client") as mock_pc, \
             patch("handlers.connect_handler.upsert_effective_user", new_callable=AsyncMock, return_value=True), \
             patch("handlers.connect_handler._get_qr_login_task", return_value=None), \
             patch("handlers.connect_handler.get_system_message", new_callable=AsyncMock, return_value="Enter phone"):
            mock_pc.is_active.return_value = False

            await on_connect(mock_update, mock_context)

        # Проверяем: reply_text вызван с reply_markup (InlineKeyboard)
        mock_update.message.reply_text.assert_called_once()
        call_kwargs = mock_update.message.reply_text.call_args
        assert call_kwargs.kwargs.get("reply_markup") is not None or \
            (len(call_kwargs.args) > 1 and call_kwargs.args[1] is not None) or \
            "reply_markup" in (call_kwargs.kwargs or {})
        # Пользователь должен быть зарегистрирован в phone-flow
        assert mock_update.effective_user.id in _pending_phone
        assert _pending_phone[mock_update.effective_user.id]["state"] == "awaiting_phone"

    @pytest.mark.asyncio
    async def test_connect_ignores_expired_phone_flow(self, mock_update, mock_context):
        """Протухший phone-flow не должен блокировать новый /connect."""
        user_id = mock_update.effective_user.id
        temp_client = AsyncMock()
        _pending_phone[user_id] = {
            "state": "awaiting_code",
            "client": temp_client,
            "phone_number": "+1234567890",
            "phone_code_hash": "hash123",
            "language_code": "en",
            "chat_id": mock_update.effective_chat.id,
            "expires_at": 1,
        }

        with patch("handlers.connect_handler.time.monotonic", return_value=2), \
             patch("handlers.connect_handler.pyrogram_client") as mock_pc, \
             patch("handlers.connect_handler.upsert_effective_user", new_callable=AsyncMock, return_value=True), \
             patch("handlers.connect_handler._get_qr_login_task", return_value=None), \
             patch("handlers.connect_handler.get_system_message", new_callable=AsyncMock, return_value="Enter phone"):
            mock_pc.is_active.return_value = False

            await on_connect(mock_update, mock_context)

        temp_client.disconnect.assert_called_once()
        assert user_id in _pending_phone
        assert _pending_phone[user_id]["state"] == "awaiting_phone"

    @pytest.mark.asyncio
    async def test_phone_number_triggers_confirm(self, mock_update, mock_context):
        """Ввод номера → подтверждение, переход в awaiting_confirm."""
        user_id = mock_update.effective_user.id
        _pending_phone[user_id] = {
            "state": "awaiting_phone",
            "language_code": "en",
            "chat_id": mock_update.effective_chat.id,
        }

        mock_update.message.text = "+1234567890"
        mock_update.message.message_id = 42

        with patch("handlers.connect_handler.get_system_message", new_callable=AsyncMock, return_value="Confirm {phone_number}"):
            with pytest.raises(ApplicationHandlerStop):
                await handle_connect_text(mock_update, mock_context)

        assert _pending_phone[user_id]["state"] == "awaiting_confirm"
        assert _pending_phone[user_id]["phone_number"] == "+1234567890"
        assert 42 in _pending_phone[user_id]["sensitive_msg_ids"]

    @pytest.mark.asyncio
    async def test_invalid_phone_number_rejected_immediately(self, mock_update, mock_context):
        """Невалидный номер → ошибка сразу, остаёмся в awaiting_phone."""
        user_id = mock_update.effective_user.id
        _pending_phone[user_id] = {
            "state": "awaiting_phone",
            "language_code": "en",
            "chat_id": mock_update.effective_chat.id,
        }

        mock_update.message.text = "invalid"
        mock_update.message.message_id = 55

        with patch("handlers.connect_handler.get_system_message", new_callable=AsyncMock, return_value="Invalid phone"):
            with pytest.raises(ApplicationHandlerStop):
                await handle_connect_text(mock_update, mock_context)

        # Остаёмся в awaiting_phone — не перешли в awaiting_confirm
        assert _pending_phone[user_id]["state"] == "awaiting_phone"
        # Сообщение с невалидным номером сохранено для удаления (privacy)
        assert 55 in _pending_phone[user_id]["sensitive_msg_ids"]
        mock_context.bot.send_message.assert_called_once_with(
            chat_id=mock_update.effective_chat.id, text="Invalid phone",
        )

    @pytest.mark.asyncio
    async def test_phone_code_success_saves_session(self, mock_update, mock_context):
        """Правильный код → сессия сохранена, подключение."""
        user_id = mock_update.effective_user.id
        mock_client = AsyncMock()
        mock_client.export_session_string = AsyncMock(return_value="session-phone")

        user_obj = MagicMock()
        user_obj.id = 777
        user_obj.bot = False
        auth_result = MagicMock()
        auth_result.user = user_obj
        mock_client.sign_in = AsyncMock(return_value=auth_result)
        mock_client.storage.user_id = AsyncMock()
        mock_client.storage.is_bot = AsyncMock()

        _pending_phone[user_id] = {
            "state": "awaiting_code",
            "client": mock_client,
            "phone_number": "+1234567890",
            "phone_code_hash": "hash123",
            "language_code": "en",
            "chat_id": mock_update.effective_chat.id,
        }

        mock_update.message.text = "12345"

        with patch("handlers.connect_handler.save_session", new_callable=AsyncMock, return_value=True) as mock_save, \
             patch("handlers.connect_handler.pyrogram_client") as mock_pc, \
             patch("handlers.connect_handler.get_system_message", new_callable=AsyncMock, return_value="Connected"):
            mock_pc.start_listening = AsyncMock(return_value=True)

            with pytest.raises(ApplicationHandlerStop):
                await handle_connect_text(mock_update, mock_context)

        mock_save.assert_called_once_with(user_id, "session-phone")
        mock_pc.start_listening.assert_called_once_with(user_id, "session-phone")
        assert user_id not in _pending_phone

    @pytest.mark.asyncio
    @pytest.mark.parametrize("masked_code", ["1-2-3-4-5", "12x345", "1 2 3 4 5", "1.2.3.4.5", "1a2b3c4d5"])
    async def test_phone_code_strips_separators(self, mock_update, mock_context, masked_code):
        """Код с разделителями (1-2-3-4-5, 12x345 и т.д.) → стрипится до цифр."""
        user_id = mock_update.effective_user.id
        mock_client = AsyncMock()
        mock_client.export_session_string = AsyncMock(return_value="session-phone")

        user_obj = MagicMock()
        user_obj.id = 777
        user_obj.bot = False
        auth_result = MagicMock()
        auth_result.user = user_obj
        mock_client.sign_in = AsyncMock(return_value=auth_result)
        mock_client.storage.user_id = AsyncMock()
        mock_client.storage.is_bot = AsyncMock()

        _pending_phone[user_id] = {
            "state": "awaiting_code",
            "client": mock_client,
            "phone_number": "+1234567890",
            "phone_code_hash": "hash123",
            "language_code": "en",
            "chat_id": mock_update.effective_chat.id,
        }

        mock_update.message.text = masked_code

        with patch("handlers.connect_handler.save_session", new_callable=AsyncMock, return_value=True), \
             patch("handlers.connect_handler.pyrogram_client") as mock_pc, \
             patch("handlers.connect_handler.get_system_message", new_callable=AsyncMock, return_value="Connected"):
            mock_pc.start_listening = AsyncMock(return_value=True)

            with pytest.raises(ApplicationHandlerStop):
                await handle_connect_text(mock_update, mock_context)

        # sign_in получает чистые цифры, без разделителей
        mock_client.sign_in.assert_called_once_with(
            phone_number="+1234567890",
            phone_code_hash="hash123",
            phone_code="12345",
        )

    @pytest.mark.asyncio
    async def test_phone_code_invalid_shows_error(self, mock_update, mock_context):
        """Неправильный код → ошибка, остаёмся в awaiting_code."""
        user_id = mock_update.effective_user.id
        mock_client = AsyncMock()

        class PhoneCodeInvalid(Exception):
            pass

        mock_client.sign_in = AsyncMock(side_effect=PhoneCodeInvalid())

        _pending_phone[user_id] = {
            "state": "awaiting_code",
            "client": mock_client,
            "phone_number": "+1234567890",
            "phone_code_hash": "hash123",
            "language_code": "en",
            "chat_id": mock_update.effective_chat.id,
        }

        mock_update.message.text = "9x9x9x9x9"

        with patch("handlers.connect_handler.get_system_message", new_callable=AsyncMock, return_value="Invalid code"):
            with pytest.raises(ApplicationHandlerStop):
                await handle_connect_text(mock_update, mock_context)

        mock_context.bot.send_message.assert_called_once_with(
            chat_id=mock_update.effective_chat.id, text="Invalid code",
        )
        # Остаёмся в awaiting_code
        assert _pending_phone[user_id]["state"] == "awaiting_code"

    @pytest.mark.asyncio
    async def test_phone_code_invalid_pure_digits_shows_both_messages(self, mock_update, mock_context):
        """Чистые цифры без разделителя + PhoneCodeInvalid → ошибка + hint про разделитель."""
        user_id = mock_update.effective_user.id
        mock_client = AsyncMock()

        class PhoneCodeInvalid(Exception):
            pass

        mock_client.sign_in = AsyncMock(side_effect=PhoneCodeInvalid())

        _pending_phone[user_id] = {
            "state": "awaiting_code",
            "client": mock_client,
            "phone_number": "+1234567890",
            "phone_code_hash": "hash123",
            "language_code": "en",
            "chat_id": mock_update.effective_chat.id,
        }

        mock_update.message.text = "99999"

        with patch("handlers.connect_handler.get_system_message", new_callable=AsyncMock) as mock_msg:
            mock_msg.side_effect = lambda lang, key: f"[{key}]"
            with pytest.raises(ApplicationHandlerStop):
                await handle_connect_text(mock_update, mock_context)

        # Остаёмся в awaiting_code
        assert _pending_phone[user_id]["state"] == "awaiting_code"
        # Два сообщения: invalid + hint про разделитель
        assert mock_context.bot.send_message.call_count == 2
        mock_context.bot.send_message.assert_any_call(
            chat_id=mock_update.effective_chat.id, text="[connect_code_invalid]",
        )
        mock_context.bot.send_message.assert_any_call(
            chat_id=mock_update.effective_chat.id, text="[connect_code_no_separator]",
        )

    @pytest.mark.asyncio
    async def test_phone_code_expired_shows_error(self, mock_update, mock_context):
        """Истёкший код → ошибка, flow отменяется."""
        user_id = mock_update.effective_user.id
        mock_client = AsyncMock()

        class PhoneCodeExpired(Exception):
            pass

        mock_client.sign_in = AsyncMock(side_effect=PhoneCodeExpired())

        _pending_phone[user_id] = {
            "state": "awaiting_code",
            "client": mock_client,
            "phone_number": "+1234567890",
            "phone_code_hash": "hash123",
            "language_code": "en",
            "chat_id": mock_update.effective_chat.id,
        }

        mock_update.message.text = "1x2345"

        with patch("handlers.connect_handler.get_system_message", new_callable=AsyncMock, return_value="Code expired"):
            with pytest.raises(ApplicationHandlerStop):
                await handle_connect_text(mock_update, mock_context)

        mock_context.bot.send_message.assert_called_once_with(
            chat_id=mock_update.effective_chat.id, text="Code expired",
        )
        assert user_id not in _pending_phone

    @pytest.mark.asyncio
    async def test_phone_2fa_prompt_on_password_needed(self, mock_update, mock_context):
        """SessionPasswordNeeded → prompt 2FA."""
        user_id = mock_update.effective_user.id
        mock_client = AsyncMock()

        class SessionPasswordNeeded(Exception):
            pass

        mock_client.sign_in = AsyncMock(side_effect=SessionPasswordNeeded())

        _pending_phone[user_id] = {
            "state": "awaiting_code",
            "client": mock_client,
            "phone_number": "+1234567890",
            "phone_code_hash": "hash123",
            "language_code": "en",
            "chat_id": mock_update.effective_chat.id,
        }

        mock_update.message.text = "12345"

        with patch("handlers.connect_handler.get_system_message", new_callable=AsyncMock, return_value="Enter 2FA"):
            with pytest.raises(ApplicationHandlerStop):
                await handle_connect_text(mock_update, mock_context)

        mock_context.bot.send_message.assert_called_once_with(
            chat_id=mock_update.effective_chat.id, text="Enter 2FA",
        )
        assert _pending_phone[user_id]["state"] == "awaiting_2fa"

    @pytest.mark.asyncio
    async def test_phone_2fa_success(self, mock_update, mock_context):
        """Правильный 2FA → подключение."""
        user_id = mock_update.effective_user.id
        mock_client = AsyncMock()
        mock_client.export_session_string = AsyncMock(return_value="session-2fa")
        mock_client.storage.user_id = AsyncMock()
        mock_client.storage.is_bot = AsyncMock()

        user_obj = MagicMock()
        user_obj.id = 777
        user_obj.bot = False
        auth_result = MagicMock()
        auth_result.user = user_obj
        mock_client.invoke = AsyncMock(side_effect=[MagicMock(), auth_result])

        _pending_phone[user_id] = {
            "state": "awaiting_2fa",
            "client": mock_client,
            "phone_number": "+1234567890",
            "phone_code_hash": "hash123",
            "language_code": "en",
            "chat_id": mock_update.effective_chat.id,
        }

        mock_update.message.text = "password123"

        with patch("handlers.connect_handler.GetPassword", return_value="get-password"), \
             patch("handlers.connect_handler.CheckPassword", side_effect=lambda password: ("check-password", password)), \
             patch("handlers.connect_handler.compute_password_check", return_value="srp-check"), \
             patch("handlers.connect_handler.save_session", new_callable=AsyncMock, return_value=True) as mock_save, \
             patch("handlers.connect_handler.pyrogram_client") as mock_pc, \
             patch("handlers.connect_handler.get_system_message", new_callable=AsyncMock, return_value="Connected"):
            mock_pc.start_listening = AsyncMock(return_value=True)

            with pytest.raises(ApplicationHandlerStop):
                await handle_connect_text(mock_update, mock_context)

        mock_save.assert_called_once_with(user_id, "session-2fa")
        assert user_id not in _pending_phone

    @pytest.mark.asyncio
    async def test_phone_2fa_wrong_password(self, mock_update, mock_context):
        """Неправильный 2FA → ошибка, остаёмся в awaiting_2fa."""
        user_id = mock_update.effective_user.id
        mock_client = AsyncMock()

        class PasswordHashInvalid(Exception):
            pass

        mock_client.invoke = AsyncMock(side_effect=[MagicMock(), PasswordHashInvalid()])

        _pending_phone[user_id] = {
            "state": "awaiting_2fa",
            "client": mock_client,
            "phone_number": "+1234567890",
            "phone_code_hash": "hash123",
            "language_code": "en",
            "chat_id": mock_update.effective_chat.id,
        }

        mock_update.message.text = "wrongpassword"

        with patch("handlers.connect_handler.GetPassword", return_value="get-password"), \
             patch("handlers.connect_handler.CheckPassword", side_effect=lambda password: ("check-password", password)), \
             patch("handlers.connect_handler.compute_password_check", return_value="srp-check"), \
             patch("handlers.connect_handler.get_system_message", new_callable=AsyncMock, return_value="Wrong password"):
            with pytest.raises(ApplicationHandlerStop):
                await handle_connect_text(mock_update, mock_context)

        mock_context.bot.send_message.assert_called_once_with(
            chat_id=mock_update.effective_chat.id, text="Wrong password",
        )
        assert _pending_phone[user_id]["state"] == "awaiting_2fa"

    @pytest.mark.asyncio
    async def test_qr_button_cancels_phone_and_starts_qr(self, mock_update, mock_context):
        """Нажатие QR-кнопки → отменяет phone-flow, запускает QR."""
        user_id = mock_update.effective_user.id
        _pending_phone[user_id] = {
            "state": "awaiting_phone",
            "language_code": "en",
            "chat_id": mock_update.effective_chat.id,
        }

        query = AsyncMock()
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        mock_update.callback_query = query

        with patch("handlers.connect_handler.pyrogram_client") as mock_pc, \
             patch("handlers.connect_handler._get_qr_login_task", return_value=None), \
             patch("handlers.connect_handler._start_qr_flow", new_callable=AsyncMock) as mock_qr:
            mock_pc.is_active.return_value = False

            await on_connect_qr_callback(mock_update, mock_context)

        query.answer.assert_called_once()
        assert user_id not in _pending_phone
        mock_qr.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect_shows_confirmation_with_pending_phone(self, mock_update, mock_context):
        """`/disconnect` при pending phone-flow → показывает подтверждение."""
        user_id = mock_update.effective_user.id
        temp_client = AsyncMock()
        _pending_phone[user_id] = {
            "state": "awaiting_code",
            "client": temp_client,
            "language_code": "en",
            "chat_id": mock_update.effective_chat.id,
        }

        with patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc, \
             patch("handlers.pyrogram_handlers.ensure_effective_user", new_callable=AsyncMock, return_value={}), \
             patch("handlers.pyrogram_handlers.get_system_message", new_callable=AsyncMock, return_value="Are you sure?"):
            mock_pc.is_active.return_value = False

            await on_disconnect(mock_update, mock_context)

        # Показано подтверждение, pending_phone НЕ отменён (ждём кнопки)
        mock_update.message.reply_text.assert_called_once()
        assert user_id in _pending_phone

    @pytest.mark.asyncio
    async def test_connect_rejects_when_phone_flow_running(self, mock_update, mock_context):
        """Повторный /connect при активном phone-flow → отказ."""
        user_id = mock_update.effective_user.id
        _pending_phone[user_id] = {
            "state": "awaiting_phone",
            "language_code": "en",
            "chat_id": mock_update.effective_chat.id,
        }

        with patch("handlers.connect_handler.pyrogram_client") as mock_pc, \
             patch("handlers.connect_handler._get_qr_login_task", return_value=None), \
             patch("handlers.connect_handler.get_system_message", new_callable=AsyncMock, return_value="In progress"):
            mock_pc.is_active.return_value = False

            await on_connect(mock_update, mock_context)

        mock_update.message.reply_text.assert_called_once_with("In progress")

    @pytest.mark.asyncio
    async def test_flood_wait_tested_via_confirm_callback(self, mock_update, mock_context):
        """FloodWait тестируется через on_confirm_phone_callback в test_connect_flow.py.
        Здесь проверяем, что ввод номера сначала идёт в awaiting_confirm."""
        user_id = mock_update.effective_user.id
        _pending_phone[user_id] = {
            "state": "awaiting_phone",
            "language_code": "en",
            "chat_id": mock_update.effective_chat.id,
        }

        mock_update.message.text = "+1234567890"
        mock_update.message.message_id = 77

        with patch("handlers.connect_handler.get_system_message", new_callable=AsyncMock, return_value="Confirm {phone_number}"):
            with pytest.raises(ApplicationHandlerStop):
                await handle_connect_text(mock_update, mock_context)

        assert _pending_phone[user_id]["state"] == "awaiting_confirm"

    @pytest.mark.asyncio
    async def test_phone_message_stored_for_deferred_deletion(self, mock_update, mock_context):
        """Номер телефона сохраняется для отложенного удаления (не удаляется сразу)."""
        user_id = mock_update.effective_user.id
        _pending_phone[user_id] = {
            "state": "awaiting_phone",
            "language_code": "en",
            "chat_id": mock_update.effective_chat.id,
        }

        mock_update.message.text = "+1234567890"
        mock_update.message.message_id = 42

        with patch("handlers.connect_handler.get_system_message", new_callable=AsyncMock, return_value="Confirm {phone_number}"):
            with pytest.raises(ApplicationHandlerStop):
                await handle_connect_text(mock_update, mock_context)

        # Сообщение НЕ удалено сразу
        mock_update.message.delete.assert_not_called()
        # ID сохранён для отложенного удаления
        assert 42 in _pending_phone[user_id]["sensitive_msg_ids"]

    @pytest.mark.asyncio
    async def test_expired_phone_flow_sends_timeout_and_stops(self, mock_update, mock_context):
        """Протухший phone-flow должен очиститься и не дойти до on_text."""
        user_id = mock_update.effective_user.id
        temp_client = AsyncMock()
        _pending_phone[user_id] = {
            "state": "awaiting_code",
            "client": temp_client,
            "phone_number": "+1234567890",
            "phone_code_hash": "hash123",
            "language_code": "en",
            "chat_id": mock_update.effective_chat.id,
            "expires_at": 1,
        }
        mock_update.message.text = "12345"

        with patch("handlers.connect_handler.time.monotonic", return_value=2), \
             patch("handlers.connect_handler.get_system_message", new_callable=AsyncMock, return_value="Timed out"):
            with pytest.raises(ApplicationHandlerStop):
                await handle_connect_text(mock_update, mock_context)

        temp_client.disconnect.assert_called_once()
        assert user_id not in _pending_phone
        mock_context.bot.send_message.assert_called_once_with(
            chat_id=mock_update.effective_chat.id, text="Timed out",
        )


class TestIgnoredChatIDs:
    """IGNORED_CHAT_IDS (777000 и т.д.) полностью игнорируются."""

    @pytest.mark.asyncio
    async def test_on_pyrogram_message_skips_ignored_chat(self):
        """on_pyrogram_message → return early для IGNORED_CHAT_IDS."""
        message = MagicMock()
        message.text = "Service notification"
        message.voice = None
        message.outgoing = False
        message.from_user = MagicMock()
        message.from_user.is_bot = False
        message.chat = MagicMock()
        message.chat.id = 777000
        message.chat.type = MagicMock(value="private")

        with patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc, \
             patch("handlers.pyrogram_handlers.get_user", new_callable=AsyncMock) as mock_get:
            mock_pc.read_chat_history = AsyncMock()
            await on_pyrogram_message(123, MagicMock(), message)

        mock_get.assert_not_called()
        mock_pc.read_chat_history.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_pyrogram_draft_skips_ignored_chat(self):
        """on_pyrogram_draft → return early для IGNORED_CHAT_IDS."""
        with patch("handlers.pyrogram_handlers.get_user", new_callable=AsyncMock) as mock_get:
            await on_pyrogram_draft(user_id=123, chat_id=777000, draft_text="hello")

        mock_get.assert_not_called()

    def test_maybe_schedule_auto_reply_skips_ignored_chat(self):
        """_maybe_schedule_auto_reply → не планирует для IGNORED_CHAT_IDS."""
        with patch("handlers.pyrogram_handlers._schedule_auto_reply") as mock_sched:
            _maybe_schedule_auto_reply(
                {"auto_reply": 60}, user_id=123, chat_id=777000, text="hello",
            )

        mock_sched.assert_not_called()

    def test_maybe_schedule_auto_reply_skips_saved_messages(self):
        """_maybe_schedule_auto_reply → не планирует для Saved Messages."""
        with patch("handlers.pyrogram_handlers._schedule_auto_reply") as mock_sched:
            _maybe_schedule_auto_reply(
                {"auto_reply": 60}, user_id=123, chat_id=123, text="hello",
            )

        mock_sched.assert_not_called()

    @pytest.mark.asyncio
    async def test_poll_missed_skips_ignored_chat(self):
        """poll_missed_messages → пропускает IGNORED_CHAT_IDS."""
        with patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc:
            mock_pc.is_active.return_value = True
            mock_pc._active_clients = {123: MagicMock()}
            mock_pc.get_private_dialogs = AsyncMock(return_value=[777000])
            mock_pc.get_last_message = AsyncMock()

            found = await poll_missed_messages(123)

        assert found == 0
        mock_pc.get_last_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_pyrogram_message_skips_per_user_ignored_chat(self):
        """on_pyrogram_message → return early для per-user ignored chat (sentinel -1)."""
        message = MagicMock()
        message.text = "Hello"
        message.voice = None
        message.sticker = None
        message.outgoing = False
        message.from_user = MagicMock()
        message.from_user.is_bot = False
        message.chat = MagicMock()
        message.chat.id = 999
        message.chat.type = MagicMock(value="private")
        message.id = 1

        with patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc, \
             patch("handlers.pyrogram_handlers.get_user", new_callable=AsyncMock, return_value={
                 "settings": {"chat_auto_replies": {"999": -1}},
             }):
            mock_pc.read_chat_history = AsyncMock()
            await on_pyrogram_message(123, MagicMock(), message)

        mock_pc.read_chat_history.assert_not_called()

    def test_maybe_schedule_auto_reply_skips_per_user_ignored_chat(self):
        """_maybe_schedule_auto_reply → не планирует для per-user ignored chat."""
        with patch("handlers.pyrogram_handlers._schedule_auto_reply") as mock_sched:
            _maybe_schedule_auto_reply(
                {"chat_auto_replies": {"999": -1}},
                user_id=123, chat_id=999, text="hello",
            )

        mock_sched.assert_not_called()


class TestDefaultProModelRuntime:
    """Новый пользователь (пустые settings) получает PRO-модель в рантайме."""

    @pytest.mark.asyncio
    async def test_on_pyrogram_message_uses_pro_model_by_default(self):
        """on_pyrogram_message → settings={} → generate_reply с PRO-моделью."""
        message = MagicMock()
        message.text = "Hello"
        message.voice = None
        message.outgoing = False
        message.from_user = MagicMock()
        message.from_user.is_bot = False
        message.from_user.first_name = "Test"
        message.from_user.last_name = None
        message.from_user.username = None
        message.from_user.language_code = "en"
        message.from_user.is_premium = False
        message.chat = MagicMock()
        message.chat.id = 456
        message.chat.type = MagicMock(value="private")

        with patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc, \
             patch("handlers.pyrogram_handlers.generate_reply", new_callable=AsyncMock) as mock_gen, \
             patch("handlers.pyrogram_handlers.get_user", new_callable=AsyncMock, return_value={"language_code": "en", "settings": {}}), \
             patch("handlers.pyrogram_handlers.get_system_message", new_callable=AsyncMock, return_value=TYPING_TEXT), \
             patch("utils.utils.DEFAULT_PRO_MODEL", True):
            mock_pc.read_chat_history = AsyncMock(return_value=[
                {"role": "other", "text": "Hello"}
            ])
            mock_pc.set_draft = AsyncMock(return_value=True)
            mock_pc.get_draft = AsyncMock(return_value=None)
            mock_pc.get_chat_bio = AsyncMock(return_value=None)
            mock_gen.return_value = "Hi there!"

            await on_pyrogram_message(123, MagicMock(), message)

        call_kwargs = mock_gen.call_args.kwargs
        assert "model" in call_kwargs, "Empty settings should use PRO model by default"

    @pytest.mark.asyncio
    async def test_regenerate_reply_uses_pro_model_by_default(self):
        """_regenerate_reply → settings={} → generate_reply с PRO-моделью."""
        with patch("handlers.pyrogram_handlers.pyrogram_client") as mock_pc, \
             patch("handlers.pyrogram_handlers.generate_reply", new_callable=AsyncMock) as mock_gen, \
             patch("handlers.pyrogram_handlers.get_user", new_callable=AsyncMock, return_value={"language_code": "en", "settings": {}}), \
             patch("handlers.pyrogram_handlers.get_system_message", new_callable=AsyncMock, return_value=TYPING_TEXT), \
             patch("handlers.pyrogram_handlers.asyncio.create_task", side_effect=_close_coroutine_task), \
             patch("utils.utils.DEFAULT_PRO_MODEL", True):
            mock_pc.read_chat_history = AsyncMock(return_value=[
                {"role": "other", "text": "Hello", "name": "Test"},
            ])
            mock_pc.set_draft = AsyncMock(return_value=True)
            mock_pc.get_draft = AsyncMock(return_value=None)
            mock_pc.get_chat_bio = AsyncMock(return_value=None)
            mock_gen.return_value = "Hi there!"

            await _regenerate_reply(123, 456)

        call_kwargs = mock_gen.call_args.kwargs
        assert "model" in call_kwargs, "Regeneration should use PRO model by default"
