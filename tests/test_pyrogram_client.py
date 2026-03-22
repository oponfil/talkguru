# tests/test_pyrogram_client.py — Тесты для clients/pyrogram_client.py

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from clients import pyrogram_client
from config import STICKER_FALLBACK_EMOJI


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


class TestLoopExceptionHandler:
    """Тесты для установки и восстановления loop exception handler."""

    def teardown_method(self):
        pyrogram_client._loop_handler_state["previous_handler"] = None
        pyrogram_client._loop_handler_state["loop"] = None

    def test_installs_and_restores_previous_handler(self):
        previous_handler = MagicMock()
        loop = MagicMock()
        loop.get_exception_handler.return_value = previous_handler

        pyrogram_client._install_pyrogram_exception_handler(loop)

        loop.set_exception_handler.assert_called_once_with(pyrogram_client._pyrogram_task_exception_handler)
        assert pyrogram_client._loop_handler_state["previous_handler"] is previous_handler
        assert pyrogram_client._loop_handler_state["loop"] is loop

        loop.get_exception_handler.return_value = pyrogram_client._pyrogram_task_exception_handler
        pyrogram_client._restore_pyrogram_exception_handler(loop)

        assert loop.set_exception_handler.call_args_list[-1].args[0] is previous_handler
        assert pyrogram_client._loop_handler_state["previous_handler"] is None
        assert pyrogram_client._loop_handler_state["loop"] is None

    def test_delegates_non_pyrogram_errors_to_previous_handler(self):
        previous_handler = MagicMock()
        pyrogram_client._loop_handler_state["previous_handler"] = previous_handler
        loop = MagicMock()
        context = {"exception": RuntimeError("boom")}

        pyrogram_client._pyrogram_task_exception_handler(loop, context)

        previous_handler.assert_called_once_with(loop, context)

    def test_suppresses_known_peer_id_invalid_error(self):
        previous_handler = MagicMock()
        pyrogram_client._loop_handler_state["previous_handler"] = previous_handler
        loop = MagicMock()
        context = {"exception": ValueError("Peer id invalid: -100123")}

        with patch("builtins.print") as mock_print:
            pyrogram_client._pyrogram_task_exception_handler(loop, context)

        previous_handler.assert_not_called()
        mock_print.assert_called_once()


class TestStopListening:
    """Тесты для stop_listening()."""

    @pytest.mark.asyncio
    async def test_stops_and_removes_client(self):
        mock_client = AsyncMock()
        pyrogram_client._active_clients[100] = mock_client

        result = await pyrogram_client.stop_listening(100)

        assert result is True
        mock_client.stop.assert_called_once()
        assert 100 not in pyrogram_client._active_clients

    @pytest.mark.asyncio
    async def test_no_error_for_missing_user(self):
        """Не падает если пользователь не найден."""
        result = await pyrogram_client.stop_listening(99999)
        assert result is True

    @pytest.mark.asyncio
    async def test_handles_stop_exception(self):
        """При ошибке stop() клиент остаётся под контролем."""
        mock_client = AsyncMock()
        mock_client.stop.side_effect = Exception("disconnect error")
        pyrogram_client._active_clients[200] = mock_client

        result = await pyrogram_client.stop_listening(200)

        assert result is False
        assert pyrogram_client._active_clients[200] is mock_client

        del pyrogram_client._active_clients[200]


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
        msg1.voice = None
        msg1.from_user = MagicMock()
        msg1.from_user.id = 300  # Это пользователь

        msg2 = MagicMock()
        msg2.text = "Ответ"
        msg2.voice = None
        msg2.from_user = MagicMock()
        msg2.from_user.id = 400  # Это собеседник

        msg3 = MagicMock()
        msg3.text = None  # Без текста — пропускается
        msg3.sticker = None  # И не стикер
        msg3.voice = None   # И не голосовое

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

    @pytest.mark.asyncio
    async def test_stickers_included_as_emoji(self):
        """Стикеры попадают в историю как эмодзи."""
        mock_client = AsyncMock()

        msg_text = MagicMock()
        msg_text.text = "Привет"
        msg_text.sticker = None
        msg_text.from_user = MagicMock()
        msg_text.from_user.id = 300

        msg_sticker = MagicMock()
        msg_sticker.text = None
        msg_sticker.sticker = MagicMock()
        msg_sticker.sticker.emoji = "😂"
        msg_sticker.from_user = MagicMock()
        msg_sticker.from_user.id = 400

        msg_sticker_no_emoji = MagicMock()
        msg_sticker_no_emoji.text = None
        msg_sticker_no_emoji.sticker = MagicMock()
        msg_sticker_no_emoji.sticker.emoji = None  # Без эмодзи → STICKER_FALLBACK_EMOJI
        msg_sticker_no_emoji.from_user = MagicMock()
        msg_sticker_no_emoji.from_user.id = 400

        async def mock_get_history(*args, **kwargs):
            for m in [msg_sticker_no_emoji, msg_sticker, msg_text]:
                yield m

        mock_client.get_chat_history = mock_get_history
        pyrogram_client._active_clients[300] = mock_client

        result = await pyrogram_client.read_chat_history(300, 400, limit=10)

        assert len(result) == 3
        # Reversed: msg_text → msg_sticker → msg_sticker_no_emoji
        assert result[0]["text"] == "Привет"
        assert result[1]["text"] == "😂"
        assert result[2]["text"] == STICKER_FALLBACK_EMOJI

        del pyrogram_client._active_clients[300]

    @pytest.mark.asyncio
    async def test_voice_messages_transcribed_in_history(self):
        """Голосовые сообщения с обеих сторон транскрибируются."""
        mock_client = AsyncMock()

        msg_text = MagicMock()
        msg_text.text = "Привет"
        msg_text.sticker = None
        msg_text.voice = None
        msg_text.from_user = MagicMock()
        msg_text.from_user.id = 400  # оппонент

        msg_voice_other = MagicMock()
        msg_voice_other.text = None
        msg_voice_other.sticker = None
        msg_voice_other.voice = MagicMock()
        msg_voice_other.id = 10
        msg_voice_other.from_user = MagicMock()
        msg_voice_other.from_user.id = 400  # оппонент

        msg_voice_user = MagicMock()
        msg_voice_user.text = None
        msg_voice_user.sticker = None
        msg_voice_user.voice = MagicMock()
        msg_voice_user.id = 11
        msg_voice_user.from_user = MagicMock()
        msg_voice_user.from_user.id = 300  # пользователь

        # get_chat_history returns newest first
        async def mock_get_history(*args, **kwargs):
            for m in [msg_voice_user, msg_voice_other, msg_text]:
                yield m

        mock_client.get_chat_history = mock_get_history
        pyrogram_client._active_clients[300] = mock_client

        with patch.object(pyrogram_client, "transcribe_voice", new_callable=AsyncMock) as mock_transcribe:
            mock_transcribe.side_effect = lambda uid, cid, mid: {
                10: "Привет из голосового",
                11: "Мой ответ голосом",
            }.get(mid)

            result = await pyrogram_client.read_chat_history(300, 400, limit=10)

        assert len(result) == 3
        # Reversed: msg_text → msg_voice_other → msg_voice_user
        assert result[0]["text"] == "Привет"
        assert result[1]["text"] == "Привет из голосового"
        assert result[1]["role"] == "other"
        assert result[2]["text"] == "Мой ответ голосом"
        assert result[2]["role"] == "user"
        assert mock_transcribe.call_count == 2

        del pyrogram_client._active_clients[300]

    @pytest.mark.asyncio
    async def test_voice_transcription_failure_uses_fallback(self):
        """Если транскрипция не удалась — подставляется '[voice message]'."""
        mock_client = AsyncMock()

        msg_voice = MagicMock()
        msg_voice.text = None
        msg_voice.sticker = None
        msg_voice.voice = MagicMock()
        msg_voice.id = 10
        msg_voice.from_user = MagicMock()
        msg_voice.from_user.id = 400

        async def mock_get_history(*args, **kwargs):
            yield msg_voice

        mock_client.get_chat_history = mock_get_history
        pyrogram_client._active_clients[300] = mock_client

        with patch.object(pyrogram_client, "transcribe_voice", new_callable=AsyncMock, return_value=None):
            result = await pyrogram_client.read_chat_history(300, 400, limit=10)

        assert len(result) == 1
        assert result[0]["text"] == "[voice message]"

        del pyrogram_client._active_clients[300]

    @pytest.mark.asyncio
    async def test_respects_message_count_limit(self):
        """limit ограничивает количество сообщений, переданных в get_chat_history."""
        mock_client = AsyncMock()
        calls = []

        async def mock_get_history(*args, **kwargs):
            calls.append(kwargs)
            # Возвращаем 3 сообщения, как будто API вернул ровно limit
            for i in range(3):
                msg = MagicMock()
                msg.text = f"msg{i}"
                msg.sticker = None
                msg.voice = None
                msg.from_user = MagicMock()
                msg.from_user.id = 400
                yield msg

        mock_client.get_chat_history = mock_get_history
        pyrogram_client._active_clients[300] = mock_client

        result = await pyrogram_client.read_chat_history(300, 400, limit=3)

        assert len(result) == 3
        assert calls[0]["limit"] == 3

        del pyrogram_client._active_clients[300]

    @pytest.mark.asyncio
    async def test_truncates_by_total_char_length(self):
        """Если суммарная длина текста превышает MAX_CONTEXT_CHARS — старые сообщения обрезаются."""
        mock_client = AsyncMock()

        # 3 сообщения по 6000 символов = 18000 > MAX_CONTEXT_CHARS (16000)
        async def mock_get_history(*args, **kwargs):
            for i in range(3):
                msg = MagicMock()
                msg.text = "A" * 6000
                msg.sticker = None
                msg.voice = None
                msg.from_user = MagicMock()
                msg.from_user.id = 400
                yield msg

        mock_client.get_chat_history = mock_get_history
        pyrogram_client._active_clients[300] = mock_client

        result = await pyrogram_client.read_chat_history(300, 400, limit=10)

        # 18000 > 16000 → первое (самое старое) сообщение убрано, осталось 2
        assert len(result) == 2
        total = sum(len(m["text"]) for m in result)
        assert total <= 16000

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

        update = MagicMock(spec=[])
        update.peer = MagicMock(spec=[])  # Нет атрибутов
        update.draft = MagicMock()
        update.draft.message = "text"

        await pyrogram_client._handle_draft_update(123, update)

        callback.assert_not_called()

        pyrogram_client._on_draft_callback = None

    @pytest.mark.asyncio
    async def test_converts_group_chat_id(self):
        """PeerChat.chat_id = 456 → callback получает -456."""
        callback = AsyncMock()
        pyrogram_client._on_draft_callback = callback

        update = MagicMock()
        update.peer = MagicMock(spec=["chat_id"])
        update.peer.chat_id = 456
        update.draft = MagicMock()
        update.draft.message = "group draft"

        await pyrogram_client._handle_draft_update(123, update)

        callback.assert_called_once_with(123, -456, "group draft")

        pyrogram_client._on_draft_callback = None

    @pytest.mark.asyncio
    async def test_converts_channel_id(self):
        """PeerChannel.channel_id = 789 → callback получает -100789."""
        callback = AsyncMock()
        pyrogram_client._on_draft_callback = callback

        update = MagicMock()
        update.peer = MagicMock(spec=["channel_id"])
        update.peer.channel_id = 789
        update.draft = MagicMock()
        update.draft.message = "channel draft"

        await pyrogram_client._handle_draft_update(123, update)

        callback.assert_called_once_with(123, -100789, "channel draft")

        pyrogram_client._on_draft_callback = None


class TestHandleRawNewMessage:
    """Тесты для _handle_raw_new_message()."""

    def teardown_method(self):
        pyrogram_client._on_new_message_callback = None
        pyrogram_client._processed_msg_ids.clear()

    @pytest.mark.asyncio
    async def test_same_message_id_in_another_chat_is_not_treated_as_duplicate(self):
        """Одинаковый message.id в другом личном чате не должен дедуплицироваться."""
        callback = AsyncMock()
        pyrogram_client._on_new_message_callback = callback
        pyrogram_client._processed_msg_ids[123].add((456, 1))

        client = AsyncMock()
        update = MagicMock()
        update.message = MagicMock()
        update.message.id = 1
        update.message.out = False
        update.message.peer_id = MagicMock()
        update.message.peer_id.user_id = 789

        parsed_message = MagicMock()
        with patch(
            "clients.pyrogram_client.pyrogram.types.Message._parse",
            new=AsyncMock(return_value=parsed_message),
        ) as mock_parse:
            await pyrogram_client._handle_raw_new_message(123, client, update, users={})

        mock_parse.assert_awaited_once_with(client, update.message, {}, {})
        callback.assert_awaited_once_with(123, client, parsed_message)
        assert (789, 1) in pyrogram_client._processed_msg_ids[123]


class TestTranscribeVoice:
    """Тесты для transcribe_voice()."""

    def teardown_method(self):
        pyrogram_client._transcription_cache.clear()

    @pytest.mark.asyncio
    async def test_returns_none_when_no_client(self):
        """Без активного клиента → None."""
        pyrogram_client._active_clients.pop(123, None)

        result = await pyrogram_client.transcribe_voice(123, 456, 1)

        assert result is None

    @pytest.mark.asyncio
    async def test_successful_transcription(self):
        """Успешная мгновенная транскрипция."""
        mock_client = AsyncMock()
        mock_client.resolve_peer = AsyncMock(return_value=MagicMock())

        transcription_result = MagicMock()
        transcription_result.pending = False
        transcription_result.text = "Привет, как дела?"
        mock_client.invoke = AsyncMock(return_value=transcription_result)

        pyrogram_client._active_clients[123] = mock_client

        result = await pyrogram_client.transcribe_voice(123, 456, 1)

        assert result == "Привет, как дела?"
        mock_client.invoke.assert_called_once()

        pyrogram_client._active_clients.pop(123, None)

    @pytest.mark.asyncio
    async def test_returns_none_on_error(self):
        """При ошибке → None."""
        mock_client = AsyncMock()
        mock_client.resolve_peer = AsyncMock(side_effect=Exception("API error"))

        pyrogram_client._active_clients[123] = mock_client

        result = await pyrogram_client.transcribe_voice(123, 456, 1)

        assert result is None

        pyrogram_client._active_clients.pop(123, None)


class TestSendMessage:
    """Тесты для send_message()."""

    @pytest.mark.asyncio
    async def test_returns_false_when_no_client(self):
        """Без активного клиента → False."""
        pyrogram_client._active_clients.pop(123, None)

        result = await pyrogram_client.send_message(123, 456, "Hello")

        assert result is False

    @pytest.mark.asyncio
    async def test_successful_send(self):
        """Успешная отправка."""
        mock_client = AsyncMock()

        pyrogram_client._active_clients[123] = mock_client

        result = await pyrogram_client.send_message(123, 456, "Hello")

        assert result is True
        mock_client.send_message.assert_called_once_with(456, "Hello")

        pyrogram_client._active_clients.pop(123, None)

    @pytest.mark.asyncio
    async def test_returns_false_on_error(self):
        """При ошибке → False."""
        mock_client = AsyncMock()
        mock_client.send_message = AsyncMock(side_effect=Exception("Send failed"))

        pyrogram_client._active_clients[123] = mock_client

        result = await pyrogram_client.send_message(123, 456, "Hello")

        assert result is False

        pyrogram_client._active_clients.pop(123, None)


class TestGetChatBio:
    """Тесты для get_chat_bio()."""

    @pytest.mark.asyncio
    async def test_returns_bio_string(self):
        """Возвращает bio при успешном get_chat."""
        mock_client = AsyncMock()
        mock_chat = MagicMock()
        mock_chat.bio = "Дизайнер из Москвы"
        mock_client.get_chat = AsyncMock(return_value=mock_chat)

        pyrogram_client._active_clients[123] = mock_client

        result = await pyrogram_client.get_chat_bio(123, 456)

        assert result == "Дизайнер из Москвы"
        mock_client.get_chat.assert_called_once_with(456)

        pyrogram_client._active_clients.pop(123, None)

    @pytest.mark.asyncio
    async def test_returns_none_when_no_client(self):
        """Без активного клиента → None."""
        pyrogram_client._active_clients.pop(123, None)

        result = await pyrogram_client.get_chat_bio(123, 456)

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_exception(self):
        """При ошибке get_chat → None (graceful)."""
        mock_client = AsyncMock()
        mock_client.get_chat = AsyncMock(side_effect=Exception("API error"))

        pyrogram_client._active_clients[123] = mock_client

        result = await pyrogram_client.get_chat_bio(123, 456)

        assert result is None

        pyrogram_client._active_clients.pop(123, None)

    @pytest.mark.asyncio
    async def test_returns_none_when_bio_is_empty(self):
        """Пустая строка bio → None."""
        mock_client = AsyncMock()
        mock_chat = MagicMock()
        mock_chat.bio = ""
        mock_client.get_chat = AsyncMock(return_value=mock_chat)

        pyrogram_client._active_clients[123] = mock_client

        result = await pyrogram_client.get_chat_bio(123, 456)

        assert result is None

        pyrogram_client._active_clients.pop(123, None)
