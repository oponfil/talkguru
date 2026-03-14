# tests/conftest.py — Общие фикстуры для тестов TalkGuru

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Мокаем переменные окружения ДО импорта config
os.environ.setdefault("BOT_TOKEN", "test-bot-token")
os.environ.setdefault("PYROGRAM_API_ID", "12345")
os.environ.setdefault("PYROGRAM_API_HASH", "test-api-hash")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-supabase-key")
os.environ.setdefault("EVM_PRIVATE_KEY", "0x0000000000000000000000000000000000000000000000000000000000000001")
os.environ.setdefault("DEBUG_PRINT", "false")


# ====== Мок Supabase до импорта database ======

_mock_supabase_client = MagicMock()


def _mock_create_client(*args, **kwargs):
    return _mock_supabase_client


# Патчим создание Supabase клиента до импорта database
patch("supabase.create_client", _mock_create_client).start()


@pytest.fixture
def mock_supabase():
    """Фикстура: свежий мок Supabase клиента для каждого теста."""
    _mock_supabase_client.reset_mock()
    return _mock_supabase_client


# ====== Telegram Bot фикстуры ======

@pytest.fixture
def mock_bot():
    """Мок Telegram Bot."""
    bot = AsyncMock()
    bot.send_chat_action = AsyncMock()
    bot.send_message = AsyncMock()
    bot.get_chat = AsyncMock()
    bot.set_my_commands = AsyncMock()
    return bot


@pytest.fixture
def mock_user():
    """Мок Telegram User."""
    user = MagicMock()
    user.id = 123456
    user.username = "testuser"
    user.first_name = "Test"
    user.last_name = "User"
    user.is_bot = False
    user.is_premium = False
    user.language_code = "en"
    return user


@pytest.fixture
def mock_update(mock_user):
    """Мок Telegram Update."""
    update = MagicMock()
    update.effective_user = mock_user
    update.effective_chat = MagicMock()
    update.effective_chat.id = 123456
    update.message = AsyncMock()
    update.message.text = "Hello"
    update.message.reply_text = AsyncMock()
    update.message.reply_photo = AsyncMock()
    return update


@pytest.fixture
def mock_context(mock_bot):
    """Мок Telegram Context."""
    context = MagicMock()
    context.bot = mock_bot
    context.user_data = {}
    return context
