# tests/test_database_users.py — Тесты для database/users.py

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import database.users as users_module
from database.users import (
    clear_session,
    ensure_user_exists,
    get_session,
    get_user,
    get_users_with_sessions,
    has_saved_session,
    save_session,
    update_last_msg_at,
    update_user_settings,
    update_tg_rating,
    upsert_user,
    UserStorageError,
)
from utils.session_crypto import decrypt_session_string, encrypt_session_string


@pytest.fixture(autouse=True)
def _clear_user_cache():
    """Очищает кэш get_user() перед каждым тестом."""
    users_module._user_cache.clear()
    yield
    users_module._user_cache.clear()


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
            result = await upsert_user(user_id=789)

        assert result is False


class TestUpdateLastMsgAt:
    """Тесты для update_last_msg_at()."""

    @pytest.mark.asyncio
    async def test_updates_correctly(self):
        mock_table = _make_mock_table()
        with patch("database.users.supabase") as mock_sb:
            mock_sb.table.return_value = mock_table

            await update_last_msg_at(123)

        call_args = mock_table.update.call_args[0][0]
        assert "last_msg_at" in call_args
        assert call_args["last_msg_at"].endswith("+00:00")
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

            result = await save_session(123, "session-string-value")

        assert result is True
        call_args = mock_table.upsert.call_args
        data = call_args[0][0]
        assert data["user_id"] == 123
        assert data["session_string"] != "session-string-value"
        assert decrypt_session_string(data["session_string"]) == "session-string-value"
        assert call_args[1] == {"on_conflict": "user_id"}

    @pytest.mark.asyncio
    async def test_returns_false_on_error(self):
        with patch("database.users.supabase") as mock_sb:
            mock_sb.table.side_effect = Exception("DB error")

            result = await save_session(123, "session-string-value")

        assert result is False


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

            result = await clear_session(123)

        assert result is True
        mock_table.update.assert_called_once_with({"session_string": None})
        mock_table.eq.assert_called_once_with("user_id", 123)

    @pytest.mark.asyncio
    async def test_returns_false_on_error(self):
        with patch("database.users.supabase") as mock_sb:
            mock_sb.table.side_effect = Exception("DB error")

            result = await clear_session(123)

        assert result is False


class TestHasSavedSession:
    @pytest.mark.asyncio
    async def test_returns_true_when_session_present(self):
        mock_table = _make_mock_table()
        mock_table.execute.return_value = MagicMock(data=[{"session_string": "encrypted"}])
        with patch("database.users.supabase") as mock_sb:
            mock_sb.table.return_value = mock_table

            result = await has_saved_session(123)

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_session_absent(self):
        mock_table = _make_mock_table()
        mock_table.execute.return_value = MagicMock(data=[{"session_string": None}])
        with patch("database.users.supabase") as mock_sb:
            mock_sb.table.return_value = mock_table

            result = await has_saved_session(123)

        assert result is False


class TestGetUsersWithSessions:
    """Тесты для get_users_with_sessions()."""

    @pytest.mark.asyncio
    async def test_returns_decrypted_rows(self):
        mock_table = _make_mock_table()
        mock_table.execute.return_value = MagicMock(
            data=[{"user_id": 123, "session_string": encrypt_session_string("abc123"), "language_code": "en"}]
        )
        with patch("database.users.supabase") as mock_sb:
            mock_sb.table.return_value = mock_table

            result = await get_users_with_sessions()

        assert result == [{"user_id": 123, "session_string": "abc123", "language_code": "en"}]
        mock_table.select.assert_called_once_with("user_id, session_string, language_code")

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
    async def test_raises_on_error(self):
        with patch("database.users.supabase") as mock_sb:
            mock_sb.table.side_effect = Exception("DB error")
            with pytest.raises(UserStorageError):
                await get_user(123)


class TestEnsureUserExists:
    """Тесты для ensure_user_exists()."""

    @pytest.mark.asyncio
    async def test_returns_existing_user(self):
        existing_user = {"user_id": 123, "settings": {"pro_model": True}}

        with patch("database.users.get_user", new_callable=AsyncMock, return_value=existing_user), \
             patch("database.users.upsert_user", new_callable=AsyncMock) as mock_upsert:
            result = await ensure_user_exists(123)

        assert result == existing_user
        mock_upsert.assert_not_called()

    @pytest.mark.asyncio
    async def test_creates_missing_user(self):
        with patch("database.users.get_user", new_callable=AsyncMock, return_value=None), \
             patch("database.users.upsert_user", new_callable=AsyncMock, return_value=True) as mock_upsert:
            result = await ensure_user_exists(
                123,
                username="alice",
                first_name="Alice",
                language_code="ru",
            )

        mock_upsert.assert_called_once_with(
            user_id=123,
            username="alice",
            first_name="Alice",
            last_name=None,
            is_bot=False,
            is_premium=False,
            language_code="ru",
            phone_number=None,
            bio=None,
        )
        assert result["user_id"] == 123
        assert result["username"] == "alice"
        assert result["language_code"] == "ru"
        assert result["settings"] == {}

    @pytest.mark.asyncio
    async def test_raises_when_create_fails(self):
        with patch("database.users.get_user", new_callable=AsyncMock, return_value=None), \
             patch("database.users.upsert_user", new_callable=AsyncMock, return_value=False):
            with pytest.raises(UserStorageError):
                await ensure_user_exists(123)


class TestUpdateUserSettings:
    """Тесты для update_user_settings()."""

    @pytest.mark.asyncio
    async def test_updates_existing_user_settings_with_merge(self):
        mock_table = _make_mock_table()
        with patch("database.users.supabase") as mock_sb, \
             patch("database.users.get_user", new_callable=AsyncMock, return_value={"settings": {"drafts_enabled": True}}):
            mock_sb.table.return_value = mock_table

            result = await update_user_settings(123, {"pro_model": True})

        assert result == {"drafts_enabled": True, "pro_model": True}
        mock_table.update.assert_called_once_with(
            {"settings": {"drafts_enabled": True, "pro_model": True}}
        )
        mock_table.upsert.assert_not_called()

    @pytest.mark.asyncio
    async def test_upserts_settings_for_new_user(self):
        mock_table = _make_mock_table()
        with patch("database.users.supabase") as mock_sb, \
             patch("database.users.get_user", new_callable=AsyncMock, return_value=None):
            mock_sb.table.return_value = mock_table

            result = await update_user_settings(123, {"pro_model": True})

        assert result == {"pro_model": True}
        mock_table.upsert.assert_called_once_with(
            {"user_id": 123, "settings": {"pro_model": True}},
            on_conflict="user_id",
        )
        mock_table.update.assert_not_called()


class TestUserCache:
    """Тесты для in-memory кэша get_user()."""

    @pytest.mark.asyncio
    async def test_cache_hit_skips_db(self):
        """Повторный вызов отдаёт данные из кэша без запроса в БД."""
        mock_table = _make_mock_table()
        mock_table.execute.return_value = MagicMock(
            data=[{"user_id": 42, "settings": {}}]
        )
        with patch("database.users.supabase") as mock_sb:
            mock_sb.table.return_value = mock_table
            first = await get_user(42)
            second = await get_user(42)

        assert first == second == {"user_id": 42, "settings": {}}
        # select вызван один раз — второй раз из кэша
        assert mock_table.select.call_count == 1

    @pytest.mark.asyncio
    async def test_cache_expires_after_ttl(self):
        """После TTL данные перечитываются из БД."""
        mock_table = _make_mock_table()
        mock_table.execute.return_value = MagicMock(
            data=[{"user_id": 42, "settings": {}}]
        )
        with patch("database.users.supabase") as mock_sb, \
             patch("database.users.time") as mock_time:
            mock_sb.table.return_value = mock_table
            mock_time.monotonic.side_effect = [0, 3600, 3601, 3601, 7201]
            #                                  ^put  ^check(ok) ^check(expired) ^put ^put(new)
            await get_user(42)  # cache miss → DB query, monotonic returns 0 (put)
            await get_user(42)  # monotonic returns 3600 → < 0+3600=3600? No, == 3600, not <
            # 3600 is NOT < 3600, so cache expired → DB query again

        assert mock_table.select.call_count == 2

    @pytest.mark.asyncio
    async def test_invalidate_on_update_settings(self):
        """update_user_settings() сбрасывает кэш."""
        mock_table = _make_mock_table()
        user_data = {"user_id": 42, "settings": {"style": "friend"}}
        mock_table.execute.return_value = MagicMock(data=[user_data])
        with patch("database.users.supabase") as mock_sb:
            mock_sb.table.return_value = mock_table
            await get_user(42)  # populate cache
            assert 42 in users_module._user_cache

            await update_user_settings(42, {"style": "romance"})
            assert 42 not in users_module._user_cache

    @pytest.mark.asyncio
    async def test_invalidate_on_upsert(self):
        """upsert_user() сбрасывает кэш."""
        mock_table = _make_mock_table()
        mock_table.execute.return_value = MagicMock(
            data=[{"user_id": 42, "settings": {}}]
        )
        with patch("database.users.supabase") as mock_sb:
            mock_sb.table.return_value = mock_table
            await get_user(42)  # populate cache
            assert 42 in users_module._user_cache

            await upsert_user(42, username="new_name")
            assert 42 not in users_module._user_cache
