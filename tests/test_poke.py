# tests/test_poke.py — Тесты для handlers/poke_handler.py

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from handlers.poke_handler import on_poke
from handlers.pyrogram_handlers import _bot_drafts, _reply_locks


def _make_update(user_id: int = 123) -> MagicMock:
    """Создаёт мок Update для /poke."""
    update = MagicMock()
    update.effective_user = MagicMock(id=user_id, language_code="en", username="test")
    update.effective_chat = MagicMock(id=user_id)
    update.message = AsyncMock()
    update.message.reply_text = AsyncMock()
    return update


def _make_context() -> MagicMock:
    """Создаёт мок ContextTypes."""
    context = MagicMock()
    context.bot = AsyncMock()
    return context


def _make_message(outgoing: bool = False, age_seconds: int = 0, from_bot: bool = False) -> MagicMock:
    """Создаёт мок pyrogram Message."""
    msg = MagicMock()
    msg.outgoing = outgoing
    msg.date = datetime.now(tz=timezone.utc) - timedelta(seconds=age_seconds)
    msg.from_user = MagicMock()
    msg.from_user.is_bot = from_bot
    return msg


def _sys_msg_side_effect(*args, **kwargs):
    """Side-effect для get_system_message: возвращает разные строки по ключу."""
    key = args[1] if len(args) > 1 else kwargs.get("key", "")
    mapping = {
        "status_disconnected": "Connect first",
        "poke_result": "Checked {checked} chats — generating {drafts} drafts.",
        "poke_result_none": "Checked {checked} chats — no drafts needed.",
        "draft_typing": "{emoji} is typing...",
    }
    return mapping.get(key, key)


class TestOnPoke:
    """Тесты для on_poke()."""

    @pytest.mark.asyncio
    async def test_not_connected_shows_message(self):
        """Без подключения → сообщение «подключитесь»."""
        update = _make_update()
        context = _make_context()

        with patch("handlers.poke_handler.pyrogram_client") as mock_pc, \
             patch("handlers.poke_handler.ensure_effective_user", new_callable=AsyncMock), \
             patch("handlers.poke_handler.update_last_msg_at", new_callable=AsyncMock), \
             patch("handlers.poke_handler.get_system_message", new_callable=AsyncMock, side_effect=_sys_msg_side_effect):
            mock_pc.is_active = MagicMock(return_value=False)

            await on_poke(update, context)

        update.message.reply_text.assert_called_once_with("Connect first")

    @pytest.mark.asyncio
    async def test_unanswered_incoming_generates_draft(self):
        """Входящее сообщение без черновика → генерация + результат с drafts=1."""
        user_id = 123
        chat_id = 456
        update = _make_update(user_id=user_id)
        context = _make_context()

        incoming_msg = _make_message(outgoing=False)

        with patch("handlers.poke_handler.pyrogram_client") as mock_pc, \
             patch("handlers.poke_handler.ensure_effective_user", new_callable=AsyncMock), \
             patch("handlers.poke_handler.update_last_msg_at", new_callable=AsyncMock), \
             patch("handlers.poke_handler.get_system_message", new_callable=AsyncMock, side_effect=_sys_msg_side_effect), \
             patch("handlers.poke_handler.get_user", new_callable=AsyncMock, return_value={"settings": {}}), \
             patch("handlers.poke_handler._is_user_typing", new_callable=AsyncMock, return_value=False), \
             patch("handlers.poke_handler._generate_reply_for_chat", new_callable=AsyncMock) as mock_gen:
            mock_pc.is_active = MagicMock(return_value=True)
            mock_pc.get_private_dialogs = AsyncMock(return_value=[chat_id])
            mock_pc.get_last_message = AsyncMock(return_value=incoming_msg)
            mock_pc.set_draft = AsyncMock(return_value=True)

            _bot_drafts.pop((user_id, chat_id), None)
            _reply_locks.pop((user_id, chat_id), None)

            await on_poke(update, context)

        mock_gen.assert_called_once_with(user_id, chat_id, {"settings": {}}, {}, None)
        # result only
        update.message.reply_text.assert_called_once()
        result_call = update.message.reply_text.call_args_list[-1]
        assert "1 chats" in result_call.args[0]
        assert "1 drafts" in result_call.args[0]

    @pytest.mark.asyncio
    async def test_incoming_with_existing_draft_skipped(self):
        """Входящее с существующим черновиком → пропуск, drafts=0."""
        user_id = 123
        chat_id = 456
        update = _make_update(user_id=user_id)
        context = _make_context()

        with patch("handlers.poke_handler.pyrogram_client") as mock_pc, \
             patch("handlers.poke_handler.ensure_effective_user", new_callable=AsyncMock), \
             patch("handlers.poke_handler.update_last_msg_at", new_callable=AsyncMock), \
             patch("handlers.poke_handler.get_system_message", new_callable=AsyncMock, side_effect=_sys_msg_side_effect), \
             patch("handlers.poke_handler.get_user", new_callable=AsyncMock, return_value={"settings": {}}), \
             patch("handlers.poke_handler._is_user_typing", new_callable=AsyncMock, return_value=False), \
             patch("handlers.poke_handler._generate_reply_for_chat", new_callable=AsyncMock) as mock_gen:
            mock_pc.is_active = MagicMock(return_value=True)
            mock_pc.get_private_dialogs = AsyncMock(return_value=[chat_id])

            _bot_drafts[(user_id, chat_id)] = "existing draft"

            await on_poke(update, context)

        mock_gen.assert_not_called()
        # result_none only
        update.message.reply_text.assert_called_once()
        result_call = update.message.reply_text.call_args_list[-1]
        assert "no drafts needed" in result_call.args[0]
        _bot_drafts.pop((user_id, chat_id), None)

    @pytest.mark.asyncio
    async def test_outgoing_fresh_skipped(self):
        """Исходящее свежее (< 12ч) → пропуск, drafts=0."""
        user_id = 123
        chat_id = 456
        update = _make_update(user_id=user_id)
        context = _make_context()

        fresh_msg = _make_message(outgoing=True, age_seconds=3600)  # 1 час

        with patch("handlers.poke_handler.pyrogram_client") as mock_pc, \
             patch("handlers.poke_handler.ensure_effective_user", new_callable=AsyncMock), \
             patch("handlers.poke_handler.update_last_msg_at", new_callable=AsyncMock), \
             patch("handlers.poke_handler.get_system_message", new_callable=AsyncMock, side_effect=_sys_msg_side_effect), \
             patch("handlers.poke_handler.get_user", new_callable=AsyncMock, return_value={"settings": {}}), \
             patch("handlers.poke_handler._is_user_typing", new_callable=AsyncMock, return_value=False), \
             patch("handlers.poke_handler._generate_reply_for_chat", new_callable=AsyncMock) as mock_gen:
            mock_pc.is_active = MagicMock(return_value=True)
            mock_pc.get_private_dialogs = AsyncMock(return_value=[chat_id])
            mock_pc.get_last_message = AsyncMock(return_value=fresh_msg)

            _bot_drafts.pop((user_id, chat_id), None)
            _reply_locks.pop((user_id, chat_id), None)

            await on_poke(update, context)

        mock_gen.assert_not_called()
        # result_none only (checked=1, drafts=0)
        update.message.reply_text.assert_called_once()
        result_call = update.message.reply_text.call_args_list[-1]
        assert "1 chats" in result_call.args[0]
        assert "no drafts needed" in result_call.args[0]

    @pytest.mark.asyncio
    async def test_outgoing_old_generates_followup(self):
        """Исходящее старое (> 12ч) → follow-up генерация, drafts=1."""
        user_id = 123
        chat_id = 456
        update = _make_update(user_id=user_id)
        context = _make_context()

        old_msg = _make_message(outgoing=True, age_seconds=50000)  # ~14 часов

        with patch("handlers.poke_handler.pyrogram_client") as mock_pc, \
             patch("handlers.poke_handler.ensure_effective_user", new_callable=AsyncMock), \
             patch("handlers.poke_handler.update_last_msg_at", new_callable=AsyncMock), \
             patch("handlers.poke_handler.get_system_message", new_callable=AsyncMock, side_effect=_sys_msg_side_effect), \
             patch("handlers.poke_handler.get_user", new_callable=AsyncMock, return_value={"settings": {}}), \
             patch("handlers.poke_handler._is_user_typing", new_callable=AsyncMock, return_value=False), \
             patch("handlers.poke_handler._generate_reply_for_chat", new_callable=AsyncMock) as mock_gen:
            mock_pc.is_active = MagicMock(return_value=True)
            mock_pc.get_private_dialogs = AsyncMock(return_value=[chat_id])
            mock_pc.get_last_message = AsyncMock(return_value=old_msg)
            mock_pc.set_draft = AsyncMock(return_value=True)

            _bot_drafts.pop((user_id, chat_id), None)
            _reply_locks.pop((user_id, chat_id), None)

            await on_poke(update, context)

        mock_gen.assert_called_once()
        # result only
        update.message.reply_text.assert_called_once()
        result_call = update.message.reply_text.call_args_list[-1]
        assert "1 drafts" in result_call.args[0]

    @pytest.mark.asyncio
    async def test_ignored_chat_skipped(self):
        """Ignored чат → пропуск."""
        user_id = 123
        chat_id = 456
        update = _make_update(user_id=user_id)
        context = _make_context()

        settings = {"chat_auto_replies": {str(chat_id): -1}}

        with patch("handlers.poke_handler.pyrogram_client") as mock_pc, \
             patch("handlers.poke_handler.ensure_effective_user", new_callable=AsyncMock), \
             patch("handlers.poke_handler.update_last_msg_at", new_callable=AsyncMock), \
             patch("handlers.poke_handler.get_system_message", new_callable=AsyncMock, side_effect=_sys_msg_side_effect), \
             patch("handlers.poke_handler.get_user", new_callable=AsyncMock, return_value={"settings": settings}), \
             patch("handlers.poke_handler._is_user_typing", new_callable=AsyncMock, return_value=False), \
             patch("handlers.poke_handler._generate_reply_for_chat", new_callable=AsyncMock) as mock_gen:
            mock_pc.is_active = MagicMock(return_value=True)
            mock_pc.get_private_dialogs = AsyncMock(return_value=[chat_id])

            await on_poke(update, context)

        mock_gen.assert_not_called()

    @pytest.mark.asyncio
    async def test_bot_message_skipped(self):
        """Входящее от бота → пропуск."""
        user_id = 123
        chat_id = 456
        update = _make_update(user_id=user_id)
        context = _make_context()

        bot_msg = _make_message(outgoing=False, from_bot=True)

        with patch("handlers.poke_handler.pyrogram_client") as mock_pc, \
             patch("handlers.poke_handler.ensure_effective_user", new_callable=AsyncMock), \
             patch("handlers.poke_handler.update_last_msg_at", new_callable=AsyncMock), \
             patch("handlers.poke_handler.get_system_message", new_callable=AsyncMock, side_effect=_sys_msg_side_effect), \
             patch("handlers.poke_handler.get_user", new_callable=AsyncMock, return_value={"settings": {}}), \
             patch("handlers.poke_handler._is_user_typing", new_callable=AsyncMock, return_value=False), \
             patch("handlers.poke_handler._generate_reply_for_chat", new_callable=AsyncMock) as mock_gen:
            mock_pc.is_active = MagicMock(return_value=True)
            mock_pc.get_private_dialogs = AsyncMock(return_value=[chat_id])
            mock_pc.get_last_message = AsyncMock(return_value=bot_msg)

            _bot_drafts.pop((user_id, chat_id), None)
            _reply_locks.pop((user_id, chat_id), None)

            await on_poke(update, context)

        mock_gen.assert_not_called()

    @pytest.mark.asyncio
    async def test_user_typing_skipped(self):
        """Пользователь уже печатает свой черновик → /poke не перезаписывает его."""
        user_id = 123
        chat_id = 456
        update = _make_update(user_id=user_id)
        context = _make_context()

        incoming_msg = _make_message(outgoing=False)

        with patch("handlers.poke_handler.pyrogram_client") as mock_pc, \
             patch("handlers.poke_handler.ensure_effective_user", new_callable=AsyncMock), \
             patch("handlers.poke_handler.update_last_msg_at", new_callable=AsyncMock), \
             patch("handlers.poke_handler.get_system_message", new_callable=AsyncMock, side_effect=_sys_msg_side_effect), \
             patch("handlers.poke_handler.get_user", new_callable=AsyncMock, return_value={"settings": {}}), \
             patch("handlers.poke_handler._is_user_typing", new_callable=AsyncMock, return_value=True) as mock_typing, \
             patch("handlers.poke_handler._generate_reply_for_chat", new_callable=AsyncMock) as mock_gen:
            mock_pc.is_active = MagicMock(return_value=True)
            mock_pc.get_private_dialogs = AsyncMock(return_value=[chat_id])
            mock_pc.get_last_message = AsyncMock(return_value=incoming_msg)
            mock_pc.set_draft = AsyncMock(return_value=True)

            _bot_drafts.pop((user_id, chat_id), None)
            _reply_locks.pop((user_id, chat_id), None)

            await on_poke(update, context)

        mock_typing.assert_called_once_with(user_id, chat_id)
        mock_pc.set_draft.assert_not_called()
        mock_gen.assert_not_called()
