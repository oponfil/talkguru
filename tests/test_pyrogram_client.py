# tests/test_pyrogram_client.py — Тесты для clients/pyrogram_client.py

from unittest.mock import AsyncMock, MagicMock

import pytest

from clients import pyrogram_client


class TestSetCallbacks:
    """Тесты для set_message_callback и set_draft_callback."""

    def test_set_message_callback(self):
        callback = MagicMock()
        pyrogram_client.set_message_callback(callback)
        assert pyrogram_client._on_new_message_callback is callback

    def test_set_draft_callback(self):
        callback = MagicMock()
        pyrogram_client.set_draft_callback(callback)
        assert pyrogram_client._on_draft_callback is callback


class TestIsActive:
    """Тесты для is_active()."""

    def test_active_when_client_exists(self):
        pyrogram_client._active_clients[999] = MagicMock()
        assert pyrogram_client.is_active(999) is True
        # Cleanup
        del pyrogram_client._active_clients[999]

    def test_not_active_when_no_client(self):
        assert pyrogram_client.is_active(88888) is False


class TestStopListening:
    """Тесты для stop_listening()."""

    @pytest.mark.asyncio
    async def test_stops_and_removes_client(self):
        mock_client = AsyncMock()
        pyrogram_client._active_clients[100] = mock_client

        await pyrogram_client.stop_listening(100)

        mock_client.stop.assert_called_once()
        assert 100 not in pyrogram_client._active_clients

    @pytest.mark.asyncio
    async def test_no_error_for_missing_user(self):
        """Не падает если пользователь не найден."""
        await pyrogram_client.stop_listening(99999)

    @pytest.mark.asyncio
    async def test_handles_stop_exception(self):
        """Не падает если client.stop() бросает исключение."""
        mock_client = AsyncMock()
        mock_client.stop.side_effect = Exception("disconnect error")
        pyrogram_client._active_clients[200] = mock_client

        await pyrogram_client.stop_listening(200)
        assert 200 not in pyrogram_client._active_clients


class TestReadChatHistory:
    """Тесты для read_chat_history()."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_client(self):
        result = await pyrogram_client.read_chat_history(77777, 1234)
        assert result == []

    @pytest.mark.asyncio
    async def test_reads_messages(self):
        """Читает сообщения и форматирует в [{role, text}]."""
        mock_client = AsyncMock()

        msg1 = MagicMock()
        msg1.text = "Привет"
        msg1.from_user = MagicMock()
        msg1.from_user.id = 300  # Это пользователь

        msg2 = MagicMock()
        msg2.text = "Ответ"
        msg2.from_user = MagicMock()
        msg2.from_user.id = 400  # Это собеседник

        msg3 = MagicMock()
        msg3.text = None  # Без текста — пропускается

        async def mock_get_history(*args, **kwargs):
            for m in [msg1, msg2, msg3]:
                yield m

        mock_client.get_chat_history = mock_get_history
        pyrogram_client._active_clients[300] = mock_client

        result = await pyrogram_client.read_chat_history(300, 400, limit=10)

        assert len(result) == 2
        # Должен быть reversed (от старых к новым)
        assert result[0]["role"] == "other"  # msg2 reversed first
        assert result[1]["role"] == "user"   # msg1 reversed second

        # Cleanup
        del pyrogram_client._active_clients[300]


class TestHandleDraftUpdate:
    """Тесты для _handle_draft_update()."""

    @pytest.mark.asyncio
    async def test_no_callback_returns_early(self):
        """Без callback — ранний return."""
        original = pyrogram_client._on_draft_callback
        pyrogram_client._on_draft_callback = None

        update = MagicMock()
        await pyrogram_client._handle_draft_update(123, update)

        pyrogram_client._on_draft_callback = original

    @pytest.mark.asyncio
    async def test_calls_callback_with_data(self):
        """Извлекает chat_id из peer и текст из draft."""
        callback = AsyncMock()
        pyrogram_client._on_draft_callback = callback

        update = MagicMock()
        update.peer = MagicMock()
        update.peer.user_id = 456
        update.draft = MagicMock()
        update.draft.message = "  Hello world  "

        await pyrogram_client._handle_draft_update(123, update)

        callback.assert_called_once_with(123, 456, "Hello world")

        pyrogram_client._on_draft_callback = None

    @pytest.mark.asyncio
    async def test_passes_empty_draft(self):
        """Пустой текст черновика → передаёт пустую строку в callback."""
        callback = AsyncMock()
        pyrogram_client._on_draft_callback = callback

        update = MagicMock()
        update.peer = MagicMock()
        update.peer.user_id = 456
        update.draft = MagicMock()
        update.draft.message = "   "

        await pyrogram_client._handle_draft_update(123, update)

        callback.assert_called_once_with(123, 456, "")

        pyrogram_client._on_draft_callback = None

    @pytest.mark.asyncio
    async def test_skips_no_peer_id(self):
        """Если нет user_id/chat_id/channel_id → пропускает."""
        callback = AsyncMock()
        pyrogram_client._on_draft_callback = callback

        update = MagicMock()
        update.peer = MagicMock(spec=[])  # Нет атрибутов
        update.draft = MagicMock()
        update.draft.message = "text"

        await pyrogram_client._handle_draft_update(123, update)

        callback.assert_not_called()

        pyrogram_client._on_draft_callback = None
