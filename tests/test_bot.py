# tests/test_bot.py — Тесты для bot.py и handlers/bot_handlers.py

from unittest.mock import AsyncMock, MagicMock, patch, ANY

import pytest

import bot as bot_module
from handlers.bot_handlers import on_start, on_text
from bot import on_error


class TestOnStart:
    """Тесты для on_start()."""

    @pytest.mark.asyncio
    async def test_upserts_user(self, mock_update, mock_context):
        """Сохраняет пользователя в БД."""
        with patch("handlers.bot_handlers.upsert_effective_user", new_callable=AsyncMock, return_value=True) as mock_upsert, \
             patch("handlers.bot_handlers.update_tg_rating", new_callable=AsyncMock), \
             patch("handlers.bot_handlers.extract_rating_from_chat", return_value=None), \
             patch("handlers.bot_handlers.get_system_message", new_callable=AsyncMock, return_value="Hi!"), \
             patch("handlers.bot_handlers.update_user_menu", new_callable=AsyncMock):

            await on_start(mock_update, mock_context)

        mock_upsert.assert_called_once()

    @pytest.mark.asyncio
    async def test_sends_greeting(self, mock_update, mock_context):
        """Отправляет приветствие на языке пользователя."""
        with patch("handlers.bot_handlers.upsert_effective_user", new_callable=AsyncMock, return_value=True), \
             patch("handlers.bot_handlers.update_tg_rating", new_callable=AsyncMock), \
             patch("handlers.bot_handlers.extract_rating_from_chat", return_value=None), \
             patch("handlers.bot_handlers.get_system_message", new_callable=AsyncMock, return_value="Привет!"), \
             patch("handlers.bot_handlers.update_user_menu", new_callable=AsyncMock):

            await on_start(mock_update, mock_context)

        mock_update.message.reply_text.assert_called_with("Привет!")

    @pytest.mark.asyncio
    async def test_updates_tg_rating(self, mock_update, mock_context):
        """Обновляет tg_rating через getChat."""
        with patch("handlers.bot_handlers.upsert_effective_user", new_callable=AsyncMock, return_value=True), \
             patch("handlers.bot_handlers.update_tg_rating", new_callable=AsyncMock) as mock_rating, \
             patch("handlers.bot_handlers.extract_rating_from_chat", return_value=5), \
             patch("handlers.bot_handlers.get_system_message", new_callable=AsyncMock, return_value="Hi!"), \
             patch("handlers.bot_handlers.update_user_menu", new_callable=AsyncMock):

            await on_start(mock_update, mock_context)

        mock_rating.assert_called_once_with(mock_update.effective_user.id, 5)

    @pytest.mark.asyncio
    async def test_updates_user_menu(self, mock_update, mock_context):
        """Устанавливает меню команд с учётом статуса подключения."""
        with patch("handlers.bot_handlers.upsert_effective_user", new_callable=AsyncMock, return_value=True), \
             patch("handlers.bot_handlers.update_tg_rating", new_callable=AsyncMock), \
             patch("handlers.bot_handlers.extract_rating_from_chat", return_value=None), \
             patch("handlers.bot_handlers.get_system_message", new_callable=AsyncMock, return_value="Hi!"), \
             patch("handlers.bot_handlers.update_user_menu", new_callable=AsyncMock) as mock_menu, \
             patch("handlers.bot_handlers.pyrogram_client") as mock_pc:
            mock_pc.is_active.return_value = False

            await on_start(mock_update, mock_context)

        mock_menu.assert_called_once_with(
            mock_context.bot, mock_update.effective_user.id,
            mock_update.effective_user.language_code, False
        )


class TestOnText:
    """Тесты для on_text()."""

    @pytest.mark.asyncio
    async def test_empty_text_returns_early(self, mock_update, mock_context):
        """Пустой текст → ранний return."""
        mock_update.message.text = "   "

        with patch("handlers.bot_handlers.update_last_msg_at", new_callable=AsyncMock) as mock_update_msg:
            await on_text(mock_update, mock_context)

        mock_update_msg.assert_not_called()

    @pytest.mark.asyncio
    async def test_generates_and_sends_response(self, mock_update, mock_context):
        """Генерирует ответ и отправляет."""
        mock_update.message.text = "Привет!"

        with patch("handlers.bot_handlers.ensure_effective_user", new_callable=AsyncMock, return_value={"settings": {}}), \
             patch("handlers.bot_handlers.update_last_msg_at", new_callable=AsyncMock), \
             patch("handlers.bot_handlers.generate_response", new_callable=AsyncMock, return_value="Ответ"):

            await on_text(mock_update, mock_context)

        mock_update.message.reply_text.assert_called_with("Ответ")

    @pytest.mark.asyncio
    async def test_passes_recent_history_to_model(self, mock_update, mock_context):
        """Передаёт в модель только последние сообщения из chat_data."""
        mock_update.message.text = "Новый вопрос"
        mock_context.chat_data["history"] = [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
        ]

        with patch("handlers.bot_handlers.MAX_CONTEXT_MESSAGES", 2), \
             patch("handlers.bot_handlers.ensure_effective_user", new_callable=AsyncMock, return_value={"settings": {}}), \
             patch("handlers.bot_handlers.update_last_msg_at", new_callable=AsyncMock), \
             patch("handlers.bot_handlers.generate_response", new_callable=AsyncMock, return_value="Ответ") as mock_generate:
            await on_text(mock_update, mock_context)

        mock_generate.assert_called_once_with(
            "Новый вопрос",
            chat_history=[
                {"role": "assistant", "content": "a1"},
                {"role": "user", "content": "u2"},
            ],
            system_prompt=ANY,
            reasoning_effort=ANY,
        )
        assert mock_context.chat_data["history"] == [
            {"role": "user", "content": "Новый вопрос"},
            {"role": "assistant", "content": "Ответ"},
        ]

    @pytest.mark.asyncio
    async def test_uses_style_pro_model(self, mock_update, mock_context):
        """pro_model + style='seducer' → модель из STYLE_PRO_MODELS['seducer']."""
        mock_update.message.text = "Hello"

        with patch("handlers.bot_handlers.ensure_effective_user", new_callable=AsyncMock, return_value={"settings": {"pro_model": True, "style": "seducer"}}), \
             patch("handlers.bot_handlers.update_last_msg_at", new_callable=AsyncMock), \
             patch("handlers.bot_handlers.generate_response", new_callable=AsyncMock, return_value="Reply") as mock_generate:
            await on_text(mock_update, mock_context)

        call_kwargs = mock_generate.call_args.kwargs
        assert call_kwargs["model"] == "google/gemini-3.1-pro-preview"
        assert call_kwargs["reasoning_effort"] == "low"

    @pytest.mark.asyncio
    async def test_style_included_in_system_prompt(self, mock_update, mock_context):
        """Стиль общения включается в system_prompt."""
        mock_update.message.text = "Hello"

        with patch("handlers.bot_handlers.ensure_effective_user", new_callable=AsyncMock, return_value={"settings": {"style": "paranoid"}}), \
             patch("handlers.bot_handlers.update_last_msg_at", new_callable=AsyncMock), \
             patch("handlers.bot_handlers.generate_response", new_callable=AsyncMock, return_value="Reply") as mock_generate:
            await on_text(mock_update, mock_context)

        call_kwargs = mock_generate.call_args.kwargs
        assert "Paranoid Guru" in call_kwargs["system_prompt"]
        assert "DraftGuru" in call_kwargs["system_prompt"]

    @pytest.mark.asyncio
    async def test_no_style_uses_base_prompt(self, mock_update, mock_context):
        """Без стиля — базовый BOT_PROMPT без дополнений."""
        mock_update.message.text = "Hello"

        with patch("handlers.bot_handlers.ensure_effective_user", new_callable=AsyncMock, return_value={"settings": {}}), \
             patch("handlers.bot_handlers.update_last_msg_at", new_callable=AsyncMock), \
             patch("handlers.bot_handlers.generate_response", new_callable=AsyncMock, return_value="Reply") as mock_generate:
            await on_text(mock_update, mock_context)

        call_kwargs = mock_generate.call_args.kwargs
        assert "DraftGuru" in call_kwargs["system_prompt"]
        assert "COMMUNICATION STYLE" not in call_kwargs["system_prompt"]

    @pytest.mark.asyncio
    async def test_error_sends_error_message(self, mock_update, mock_context):
        """Ошибка генерации → отправляет error message."""
        mock_update.message.text = "Test"

        with patch("handlers.bot_handlers.ensure_effective_user", new_callable=AsyncMock, return_value={"settings": {}}), \
             patch("handlers.bot_handlers.update_last_msg_at", new_callable=AsyncMock), \
             patch("handlers.bot_handlers.generate_response", new_callable=AsyncMock, side_effect=Exception("API fail")), \
             patch("handlers.bot_handlers.get_system_message", new_callable=AsyncMock, return_value="Ошибка"):

            await on_text(mock_update, mock_context)

        mock_update.message.reply_text.assert_called()

    @pytest.mark.asyncio
    async def test_updates_last_msg_at(self, mock_update, mock_context):
        """Обновляет last_msg_at."""
        mock_update.message.text = "Hello"

        with patch("handlers.bot_handlers.ensure_effective_user", new_callable=AsyncMock, return_value={"settings": {}}), \
             patch("handlers.bot_handlers.update_last_msg_at", new_callable=AsyncMock) as mock_update_msg, \
             patch("handlers.bot_handlers.generate_response", new_callable=AsyncMock, return_value="Reply"):

            await on_text(mock_update, mock_context)

        mock_update_msg.assert_called_once_with(mock_update.effective_user.id)

    @pytest.mark.asyncio
    async def test_prompt_save_failure_sends_error_and_keeps_waiting_state(self, mock_update, mock_context):
        """При сбое сохранения промпта не показывает ложный успех."""
        mock_update.message.text = "Будь короче"
        mock_context.user_data["awaiting_prompt"] = True

        with patch("handlers.bot_handlers.update_user_settings", new_callable=AsyncMock, return_value=False), \
             patch("handlers.bot_handlers.get_system_message", new_callable=AsyncMock, return_value="Ошибка"):
            await on_text(mock_update, mock_context)

        mock_update.message.reply_text.assert_called_once_with("Ошибка")
        assert mock_context.user_data["awaiting_prompt"] is True

    @pytest.mark.asyncio
    async def test_prompt_too_long_is_truncated_saved_and_reported(self, mock_update, mock_context):
        """Слишком длинный промпт обрезается, сохраняется и сообщает об этом."""
        mock_update.message.text = "A" * 601
        mock_context.user_data["awaiting_prompt"] = True

        with patch("handlers.bot_handlers.CUSTOM_PROMPT_MAX_LENGTH", 600), \
             patch("handlers.bot_handlers.update_user_settings", new_callable=AsyncMock, return_value=True) as mock_update_settings, \
             patch("handlers.bot_handlers.get_system_message", new_callable=AsyncMock, return_value="Промпт обрезан и сохранён"):
            await on_text(mock_update, mock_context)

        mock_update_settings.assert_called_once_with(
            mock_update.effective_user.id,
            {"custom_prompt": "A" * 600},
        )
        mock_update.message.reply_text.assert_called_once_with("Промпт обрезан и сохранён")
        assert "awaiting_prompt" not in mock_context.user_data


class TestOnError:
    """Тесты для on_error()."""

    @pytest.mark.asyncio
    async def test_does_not_crash(self, mock_context):
        """Обработчик ошибок не падает."""
        mock_context.error = Exception("Test error")

        # Не должно бросить исключение
        await on_error(MagicMock(), mock_context)


class TestMain:
    """Тесты для main()."""

    def test_registers_handlers_for_private_chats_only(self):
        fake_builder = MagicMock()
        fake_app = MagicMock()

        fake_builder.token.return_value = fake_builder
        fake_builder.read_timeout.return_value = fake_builder
        fake_builder.post_init.return_value = fake_builder
        fake_builder.build.return_value = fake_app

        command_handlers = []
        message_handlers = []

        def command_handler_stub(*args, **kwargs):
            command_handlers.append((args, kwargs))
            return MagicMock()

        def message_handler_stub(*args, **kwargs):
            message_handlers.append((args, kwargs))
            return MagicMock()

        with patch("bot.Application.builder", return_value=fake_builder), \
             patch("bot.CommandHandler", side_effect=command_handler_stub), \
             patch("bot.MessageHandler", side_effect=message_handler_stub), \
             patch("bot.CallbackQueryHandler", return_value=MagicMock()), \
             patch("bot.pyrogram_client") as mock_pc:
            bot_module.main()

        assert len(command_handlers) == 6
        assert len(message_handlers) == 2
        assert all(kwargs["filters"] is bot_module.PRIVATE_ONLY_FILTER for _, kwargs in command_handlers)
        assert "ChatType.PRIVATE" in repr(message_handlers[0][0][0])
        assert "ChatType.PRIVATE" in repr(message_handlers[1][0][0])
        mock_pc.set_message_callback.assert_called_once()
        mock_pc.set_draft_callback.assert_called_once()
        fake_app.run_polling.assert_called_once_with(drop_pending_updates=True)
