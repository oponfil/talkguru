# tests/test_database_users.py — Тесты для database/users.py

from unittest.mock import MagicMock, patch

import pytest

from database.users import (
    clear_session,
    get_session,
    get_user,
    get_users_with_sessions,
    save_session,
    update_last_msg_at,
    update_tg_rating,
    upsert_user,
)
from utils.session_crypto import decrypt_session_string, encrypt_session_string


def _make_mock_table():
    """Создаёт мок таблицы Supabase с чейнингом."""
    mock_table = MagicMock()
    mock_table.upsert.return_value = mock_table
    mock_table.update.return_value = mock_table
    mock_table.select.return_value = mock_table
    mock_table.eq.return_value = mock_table
    mock_table.not_.is_.return_value = mock_table
    mock_table.is_.return_value = mock_table
    mock_table.execute.return_value = MagicMock(data=[])
    return mock_table


class TestUpsertUser:
    """Тесты для upsert_user()."""

    @pytest.mark.asyncio
    async def test_upsert_with_all_fields(self):
        mock_table = _make_mock_table()
        with patch("database.users.supabase") as mock_sb:
            mock_sb.table.return_value = mock_table

            await upsert_user(
                user_id=123,
                username="alice",
                first_name="Alice",
                last_name="Smith",
                is_bot=False,
                is_premium=True,
                language_code="ru",
            )

        mock_sb.table.assert_called_with("users")
        call_args = mock_table.upsert.call_args
        data = call_args[0][0]
        assert data["user_id"] == 123
        assert data["username"] == "alice"
        assert data["first_name"] == "Alice"
        assert data["last_name"] == "Smith"
        assert data["is_premium"] is True
        assert data["language_code"] == "ru"
        assert call_args[1]["on_conflict"] == "user_id"

    @pytest.mark.asyncio
    async def test_upsert_minimal_fields(self):
        """Без опциональных полей."""
        mock_table = _make_mock_table()
        with patch("database.users.supabase") as mock_sb:
            mock_sb.table.return_value = mock_table

            await upsert_user(user_id=456)

        data = mock_table.upsert.call_args[0][0]
        assert data["user_id"] == 456
        assert "username" not in data
        assert "first_name" not in data

    @pytest.mark.asyncio
    async def test_upsert_handles_exception(self):
        """Не падает при ошибке Supabase."""
        with patch("database.users.supabase") as mock_sb:
            mock_sb.table.side_effect = Exception("DB error")
            # Не должно бросить исключение
            await upsert_user(user_id=789)


class TestUpdateLastMsgAt:
    """Тесты для update_last_msg_at()."""

    @pytest.mark.asyncio
    async def test_updates_correctly(self):
        mock_table = _make_mock_table()
        with patch("database.users.supabase") as mock_sb:
            mock_sb.table.return_value = mock_table

            await update_last_msg_at(123)

        mock_table.update.assert_called_once_with({"last_msg_at": "now()"})
        mock_table.eq.assert_called_once_with("user_id", 123)

    @pytest.mark.asyncio
    async def test_handles_exception(self):
        with patch("database.users.supabase") as mock_sb:
            mock_sb.table.side_effect = Exception("DB error")
            await update_last_msg_at(123)


class TestUpdateTgRating:
    """Тесты для update_tg_rating()."""

    @pytest.mark.asyncio
    async def test_updates_rating(self):
        mock_table = _make_mock_table()
        with patch("database.users.supabase") as mock_sb:
            mock_sb.table.return_value = mock_table

            await update_tg_rating(123, 5)

        mock_table.update.assert_called_once_with({"tg_rating": 5})

    @pytest.mark.asyncio
    async def test_updates_none_rating(self):
        mock_table = _make_mock_table()
        with patch("database.users.supabase") as mock_sb:
            mock_sb.table.return_value = mock_table

            await update_tg_rating(123, None)

        mock_table.update.assert_called_once_with({"tg_rating": None})


class TestSaveSession:
    """Тесты для save_session()."""

    @pytest.mark.asyncio
    async def test_saves_session(self):
        mock_table = _make_mock_table()
        with patch("database.users.supabase") as mock_sb:
            mock_sb.table.return_value = mock_table

            await save_session(123, "session-string-value")

        call_args = mock_table.upsert.call_args
        data = call_args[0][0]
        assert data["user_id"] == 123
        assert data["session_string"] != "session-string-value"
        assert decrypt_session_string(data["session_string"]) == "session-string-value"
        assert call_args[1] == {"on_conflict": "user_id"}


class TestGetSession:
    """Тесты для get_session()."""

    @pytest.mark.asyncio
    async def test_returns_decrypted_session(self):
        mock_table = _make_mock_table()
        mock_table.execute.return_value = MagicMock(
            data=[{"session_string": encrypt_session_string("abc123")}]
        )
        with patch("database.users.supabase") as mock_sb:
            mock_sb.table.return_value = mock_table

            result = await get_session(123)

        assert result == "abc123"

    @pytest.mark.asyncio
    async def test_returns_none_when_empty(self):
        mock_table = _make_mock_table()
        mock_table.execute.return_value = MagicMock(data=[])
        with patch("database.users.supabase") as mock_sb:
            mock_sb.table.return_value = mock_table

            result = await get_session(123)

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_error(self):
        with patch("database.users.supabase") as mock_sb:
            mock_sb.table.side_effect = Exception("DB error")
            result = await get_session(123)
        assert result is None


class TestClearSession:
    """Тесты для clear_session()."""

    @pytest.mark.asyncio
    async def test_clears_session(self):
        mock_table = _make_mock_table()
        with patch("database.users.supabase") as mock_sb:
            mock_sb.table.return_value = mock_table

            await clear_session(123)

        mock_table.update.assert_called_once_with({"session_string": None})
        mock_table.eq.assert_called_once_with("user_id", 123)


class TestGetUsersWithSessions:
    """Тесты для get_users_with_sessions()."""

    @pytest.mark.asyncio
    async def test_returns_decrypted_rows(self):
        mock_table = _make_mock_table()
        mock_table.execute.return_value = MagicMock(
            data=[{"user_id": 123, "session_string": encrypt_session_string("abc123")}]
        )
        with patch("database.users.supabase") as mock_sb:
            mock_sb.table.return_value = mock_table

            result = await get_users_with_sessions()

        assert result == [{"user_id": 123, "session_string": "abc123"}]
        mock_table.select.assert_called_once_with("user_id, session_string")

    @pytest.mark.asyncio
    async def test_returns_empty_list_on_error(self):
        with patch("database.users.supabase") as mock_sb:
            mock_sb.table.side_effect = Exception("DB error")

            result = await get_users_with_sessions()

        assert result == []


class TestGetUser:
    """Тесты для get_user()."""

    @pytest.mark.asyncio
    async def test_returns_user(self):
        mock_table = _make_mock_table()
        mock_table.execute.return_value = MagicMock(
            data=[{"user_id": 123, "language_code": "ru", "username": "alice"}]
        )
        with patch("database.users.supabase") as mock_sb:
            mock_sb.table.return_value = mock_table

            result = await get_user(123)

        assert result["user_id"] == 123
        assert result["language_code"] == "ru"
        assert result["username"] == "alice"

    @pytest.mark.asyncio
    async def test_returns_none_when_empty(self):
        mock_table = _make_mock_table()
        mock_table.execute.return_value = MagicMock(data=[])
        with patch("database.users.supabase") as mock_sb:
            mock_sb.table.return_value = mock_table

            result = await get_user(123)

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_error(self):
        with patch("database.users.supabase") as mock_sb:
            mock_sb.table.side_effect = Exception("DB error")
            result = await get_user(123)
        assert result is None

