# tests/test_connect_flow.py — Тесты для phone confirm, cancel и deferred deletion

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.ext import ApplicationHandlerStop

from handlers.connect_handler import (
    _delete_sensitive_messages,
    _get_pending_phone,
    _pending_phone,
    _pending_2fa,
    _qr_login_tasks,
    on_cancel_phone_callback,
    on_confirm_phone_callback,
    on_connect_cancel_callback,
    handle_connect_text,
)


@pytest.fixture(autouse=True)
def cleanup_handler_state():
    """Очищает глобальное состояние обработчиков между тестами."""
    _pending_phone.clear()
    _pending_2fa.clear()
    _qr_login_tasks.clear()
    yield
    _pending_phone.clear()
    _pending_2fa.clear()
    _qr_login_tasks.clear()


def _make_callback_update(user_id: int = 123456, chat_id: int = 123456, lang: str = "en") -> MagicMock:
    """Создаёт мок Update с callback_query."""
    update = MagicMock()
    update.effective_user = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.language_code = lang
    update.effective_chat = MagicMock()
    update.effective_chat.id = chat_id
    update.callback_query = AsyncMock()
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    update.callback_query.edit_message_reply_markup = AsyncMock()
    return update


def _make_context(bot: AsyncMock | None = None) -> MagicMock:
    """Создаёт мок Context."""
    context = MagicMock()
    context.bot = bot or AsyncMock()
    context.bot.send_message = AsyncMock()
    context.bot.delete_message = AsyncMock()
    return context


# ====== _delete_sensitive_messages ======


class TestDeleteSensitiveMessages:
    """Тесты для _delete_sensitive_messages()."""

    @pytest.mark.asyncio
    async def test_deletes_all_messages(self):
        """Удаляет все сообщения по списку ID."""
        bot = AsyncMock()
        bot.delete_message = AsyncMock()

        await _delete_sensitive_messages(bot, chat_id=100, msg_ids=[1, 2, 3])

        assert bot.delete_message.call_count == 3
        bot.delete_message.assert_any_call(chat_id=100, message_id=1)
        bot.delete_message.assert_any_call(chat_id=100, message_id=2)
        bot.delete_message.assert_any_call(chat_id=100, message_id=3)

    @pytest.mark.asyncio
    async def test_continues_on_failure(self):
        """Ошибка удаления одного сообщения не блокирует остальные."""
        bot = AsyncMock()
        bot.delete_message = AsyncMock(side_effect=[None, Exception("forbidden"), None])

        await _delete_sensitive_messages(bot, chat_id=100, msg_ids=[1, 2, 3])

        assert bot.delete_message.call_count == 3

    @pytest.mark.asyncio
    async def test_empty_list_is_noop(self):
        """Пустой список — ничего не делает."""
        bot = AsyncMock()
        bot.delete_message = AsyncMock()

        await _delete_sensitive_messages(bot, chat_id=100, msg_ids=[])

        bot.delete_message.assert_not_called()


class TestGetPendingPhone:
    """Тесты для _get_pending_phone()."""

    @pytest.mark.asyncio
    async def test_expired_pending_deletes_sensitive_messages_when_bot_provided(self):
        """Протухший flow удаляет чувствительные сообщения через bot cleanup."""
        user_id = 123456
        _pending_phone[user_id] = {
            "state": "awaiting_code",
            "chat_id": user_id,
            "expires_at": 1,
            "sensitive_msg_ids": [10, 20],
        }
        bot = AsyncMock()
        bot.delete_message = AsyncMock()

        with patch("handlers.connect_handler.time.monotonic", return_value=2):
            pending = await _get_pending_phone(user_id, bot=bot)

        assert pending is None
        assert user_id not in _pending_phone
        assert bot.delete_message.call_count == 2


# ====== _handle_phone_number (confirmation step) ======


class TestHandlePhoneNumber:
    """Тесты для _handle_phone_number() — промежуточное подтверждение номера."""

    @pytest.mark.asyncio
    async def test_shows_confirmation_with_normalized_number(self, mock_update, mock_context):
        """Ввод номера без '+' → показывает подтверждение с нормализованным номером."""
        user_id = mock_update.effective_user.id
        mock_update.message.text = "79991234567"
        mock_update.message.message_id = 42

        _pending_phone[user_id] = {
            "state": "awaiting_phone",
            "language_code": "en",
            "chat_id": mock_update.effective_chat.id,
        }

        with patch("handlers.connect_handler.get_system_message", new_callable=AsyncMock, return_value="Confirm {phone_number}"):
            with pytest.raises(ApplicationHandlerStop):
                await handle_connect_text(mock_update, mock_context)

        # Перешли в awaiting_confirm
        assert _pending_phone[user_id]["state"] == "awaiting_confirm"
        assert _pending_phone[user_id]["phone_number"] == "+79991234567"
        # ID сообщения сохранён для отложенного удаления
        assert 42 in _pending_phone[user_id]["sensitive_msg_ids"]

    @pytest.mark.asyncio
    async def test_preserves_existing_sensitive_msg_ids(self, mock_update, mock_context):
        """Повторный ввод номера сохраняет ID предыдущих сообщений."""
        user_id = mock_update.effective_user.id
        mock_update.message.text = "+79991234567"
        mock_update.message.message_id = 99

        _pending_phone[user_id] = {
            "state": "awaiting_phone",
            "language_code": "en",
            "chat_id": mock_update.effective_chat.id,
            "sensitive_msg_ids": [42],  # предыдущий номер
        }

        with patch("handlers.connect_handler.get_system_message", new_callable=AsyncMock, return_value="Confirm {phone_number}"):
            with pytest.raises(ApplicationHandlerStop):
                await handle_connect_text(mock_update, mock_context)

        # Предыдущий (42), текущий (99) и бот-сообщение подтверждения — все сохранены
        ids = _pending_phone[user_id]["sensitive_msg_ids"]
        assert 42 in ids
        assert 99 in ids
        assert len(ids) == 3  # 42 + 99 + bot confirmation message

# ====== on_confirm_phone_callback ======


class TestOnConfirmPhoneCallback:
    """Тесты для on_confirm_phone_callback() — кнопка 'Да, номер верный'."""

    @pytest.mark.asyncio
    async def test_expired_state_shows_timeout(self):
        """Нажатие при отсутствии pending → таймаут."""
        update = _make_callback_update()
        context = _make_context()

        with patch("handlers.connect_handler.get_system_message", new_callable=AsyncMock, return_value="Timed out"):
            await on_confirm_phone_callback(update, context)

        update.callback_query.edit_message_text.assert_called_once_with("Timed out")

    @pytest.mark.asyncio
    async def test_wrong_state_shows_timeout(self):
        """Нажатие в неверном state → таймаут."""
        user_id = 123456
        _pending_phone[user_id] = {
            "state": "awaiting_code",
            "language_code": "en",
            "chat_id": user_id,
        }

        update = _make_callback_update(user_id=user_id)
        context = _make_context()

        with patch("handlers.connect_handler.get_system_message", new_callable=AsyncMock, return_value="Timed out"):
            await on_confirm_phone_callback(update, context)

        update.callback_query.edit_message_text.assert_called_once_with("Timed out")

    @pytest.mark.asyncio
    async def test_sends_code_on_confirm(self):
        """Подтверждение → отправка кода, переход в awaiting_code."""
        user_id = 123456
        _pending_phone[user_id] = {
            "state": "awaiting_confirm",
            "phone_number": "+79991234567",
            "language_code": "en",
            "chat_id": user_id,
            "sensitive_msg_ids": [42],
        }

        update = _make_callback_update(user_id=user_id)
        context = _make_context()

        mock_client = AsyncMock()
        mock_sent_code = MagicMock()
        mock_sent_code.phone_code_hash = "hash123"
        mock_client.send_code = AsyncMock(return_value=mock_sent_code)

        with patch("handlers.connect_handler.Client", return_value=mock_client), \
             patch("handlers.connect_handler.get_system_message", new_callable=AsyncMock, return_value="Enter code"), \
             patch("handlers.connect_handler.keep_typing"):
            await on_confirm_phone_callback(update, context)

        assert _pending_phone[user_id]["state"] == "awaiting_code"
        assert _pending_phone[user_id]["phone_code_hash"] == "hash123"
        # sensitive_msg_ids: оригинальный (42) + code prompt msg
        ids = _pending_phone[user_id]["sensitive_msg_ids"]
        assert 42 in ids
        assert len(ids) == 2  # 42 + code prompt msg

    @pytest.mark.asyncio
    async def test_phone_invalid_returns_to_awaiting_phone(self):
        """PhoneNumberInvalid → возврат в awaiting_phone с сохранённым sensitive_msg_ids."""
        user_id = 123456
        _pending_phone[user_id] = {
            "state": "awaiting_confirm",
            "phone_number": "+invalid",
            "language_code": "en",
            "chat_id": user_id,
            "sensitive_msg_ids": [42],
        }

        update = _make_callback_update(user_id=user_id)
        context = _make_context()

        mock_client = AsyncMock()

        class PhoneNumberInvalid(Exception):
            pass

        mock_client.send_code = AsyncMock(side_effect=PhoneNumberInvalid())

        with patch("handlers.connect_handler.Client", return_value=mock_client), \
             patch("handlers.connect_handler.get_system_message", new_callable=AsyncMock, return_value="Invalid phone"), \
             patch("handlers.connect_handler.keep_typing"):
            await on_confirm_phone_callback(update, context)

        assert _pending_phone[user_id]["state"] == "awaiting_phone"
        # sensitive_msg_ids: оригинальный (42) + ошибка PhoneNumberInvalid
        ids = _pending_phone[user_id]["sensitive_msg_ids"]
        assert 42 in ids
        assert len(ids) == 2

    @pytest.mark.asyncio
    async def test_floodwait_deletes_sensitive_messages(self):
        """FloodWait завершает flow и удаляет накопленные sensitive messages."""
        user_id = 123456
        _pending_phone[user_id] = {
            "state": "awaiting_confirm",
            "phone_number": "+79991234567",
            "language_code": "en",
            "chat_id": user_id,
            "sensitive_msg_ids": [42, 99],
        }

        update = _make_callback_update(user_id=user_id)
        context = _make_context()

        mock_client = AsyncMock()

        class FloodWait(Exception):
            def __init__(self, value):
                self.value = value

        mock_client.send_code = AsyncMock(side_effect=FloodWait(15))

        with patch("handlers.connect_handler.Client", return_value=mock_client), \
             patch("handlers.connect_handler.get_system_message", new_callable=AsyncMock, return_value="Flood"), \
             patch("handlers.connect_handler.keep_typing"):
            await on_confirm_phone_callback(update, context)

        assert user_id not in _pending_phone
        assert context.bot.delete_message.call_count == 2


# ====== on_cancel_phone_callback ======


class TestOnCancelPhoneCallback:
    """Тесты для on_cancel_phone_callback() — кнопка 'Нет, ввести заново'."""

    @pytest.mark.asyncio
    async def test_returns_to_awaiting_phone(self):
        """Отмена → возврат в awaiting_phone, показ phone prompt."""
        user_id = 123456
        _pending_phone[user_id] = {
            "state": "awaiting_confirm",
            "phone_number": "+79991234567",
            "language_code": "en",
            "chat_id": user_id,
            "sensitive_msg_ids": [42],
        }

        update = _make_callback_update(user_id=user_id)
        context = _make_context()

        with patch("handlers.connect_handler.get_system_message", new_callable=AsyncMock, return_value="Enter phone"):
            await on_cancel_phone_callback(update, context)

        assert _pending_phone[user_id]["state"] == "awaiting_phone"

    @pytest.mark.asyncio
    async def test_preserves_sensitive_msg_ids(self):
        """Отмена сохраняет accumulated sensitive_msg_ids."""
        user_id = 123456
        _pending_phone[user_id] = {
            "state": "awaiting_confirm",
            "phone_number": "+79991234567",
            "language_code": "en",
            "chat_id": user_id,
            "sensitive_msg_ids": [42, 99],
        }

        update = _make_callback_update(user_id=user_id)
        context = _make_context()

        with patch("handlers.connect_handler.get_system_message", new_callable=AsyncMock, return_value="Enter phone"):
            await on_cancel_phone_callback(update, context)

        assert _pending_phone[user_id]["sensitive_msg_ids"] == [42, 99]

    @pytest.mark.asyncio
    async def test_expired_state_shows_timeout(self):
        """Нажатие без pending → таймаут."""
        update = _make_callback_update()
        context = _make_context()

        with patch("handlers.connect_handler.get_system_message", new_callable=AsyncMock, return_value="Timed out"):
            await on_cancel_phone_callback(update, context)

        update.callback_query.edit_message_text.assert_called_once_with("Timed out")


# ====== on_connect_cancel_callback ======


class TestOnConnectCancelCallback:
    """Тесты для on_connect_cancel_callback() — универсальная кнопка 'Отмена'."""

    @pytest.mark.asyncio
    async def test_cancels_phone_flow(self):
        """Отмена phone-flow → очищает pending и удаляет чувствительные сообщения."""
        user_id = 123456
        _pending_phone[user_id] = {
            "state": "awaiting_code",
            "language_code": "en",
            "chat_id": user_id,
            "sensitive_msg_ids": [10, 20],
        }

        update = _make_callback_update(user_id=user_id)
        context = _make_context()

        with patch("handlers.connect_handler.get_system_message", new_callable=AsyncMock, return_value="Timed out"):
            await on_connect_cancel_callback(update, context)

        assert user_id not in _pending_phone
        # Чувствительные сообщения удалены
        assert context.bot.delete_message.call_count == 2

    @pytest.mark.asyncio
    async def test_cancels_qr_task(self):
        """Отмена QR-flow → отменяет задачу и убирает её из реестра."""
        user_id = 123456
        mock_task = MagicMock()
        mock_task.done.return_value = False
        _qr_login_tasks[user_id] = {
            "task": mock_task, "sensitive_msg_ids": [500], "chat_id": user_id,
        }

        update = _make_callback_update(user_id=user_id)
        context = _make_context()

        with patch("handlers.connect_handler.get_system_message", new_callable=AsyncMock, return_value="Timed out"):
            await on_connect_cancel_callback(update, context)

        mock_task.cancel.assert_called_once()
        assert user_id not in _qr_login_tasks
        context.bot.delete_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_cancels_2fa_flow(self):
        """Отмена 2FA-flow → очищает pending_2fa и отключает клиент."""
        user_id = 123456
        temp_client = AsyncMock()
        _pending_2fa[user_id] = {
            "client": temp_client,
            "language_code": "en",
            "chat_id": user_id,
        }

        update = _make_callback_update(user_id=user_id)
        context = _make_context()

        with patch("handlers.connect_handler.get_system_message", new_callable=AsyncMock, return_value="Timed out"):
            await on_connect_cancel_callback(update, context)

        assert user_id not in _pending_2fa
        temp_client.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_removes_reply_markup(self):
        """Убирает inline клавиатуру после отмены."""
        update = _make_callback_update()
        context = _make_context()

        with patch("handlers.connect_handler.get_system_message", new_callable=AsyncMock, return_value="Timed out"):
            await on_connect_cancel_callback(update, context)

        update.callback_query.edit_message_reply_markup.assert_called_once_with(reply_markup=None)


class TestHandlePhoneCodeCleanup:
    """Тесты cleanup для terminal-веток ввода кода."""

    @pytest.mark.asyncio
    async def test_phone_code_expired_deletes_sensitive_messages(self, mock_update, mock_context):
        """PhoneCodeExpired завершает flow и удаляет номер и введённый код."""
        user_id = mock_update.effective_user.id
        mock_update.message.text = "1-2-3-4-5"
        mock_update.message.message_id = 77

        mock_client = AsyncMock()

        class PhoneCodeExpired(Exception):
            pass

        mock_client.sign_in = AsyncMock(side_effect=PhoneCodeExpired())

        _pending_phone[user_id] = {
            "state": "awaiting_code",
            "client": mock_client,
            "phone_number": "+79991234567",
            "phone_code_hash": "hash123",
            "language_code": "en",
            "chat_id": mock_update.effective_chat.id,
            "sensitive_msg_ids": [42],
        }

        with patch("handlers.connect_handler.get_system_message", new_callable=AsyncMock, return_value="Expired"):
            with pytest.raises(ApplicationHandlerStop):
                await handle_connect_text(mock_update, mock_context)

        assert user_id not in _pending_phone
        assert mock_context.bot.delete_message.call_count == 2
