# tests/test_user_helpers.py — Тесты для utils/telegram_user.py

from unittest.mock import AsyncMock, patch

import pytest

from utils.telegram_user import ensure_effective_user, upsert_effective_user


class TestEnsureEffectiveUser:
    """Тесты для ensure_effective_user()."""

    @pytest.mark.asyncio
    async def test_always_upserts_and_returns_user(self, mock_update):
        expected_user = {"user_id": mock_update.effective_user.id, "settings": {}}

        with patch("utils.telegram_user.get_user", new_callable=AsyncMock, return_value=expected_user), \
             patch("utils.telegram_user.upsert_effective_user", new_callable=AsyncMock) as mock_upsert:
            result = await ensure_effective_user(mock_update)

        mock_upsert.assert_called_once_with(mock_update)
        assert result == expected_user


class TestUpsertEffectiveUser:
    """Тесты для upsert_effective_user()."""

    @pytest.mark.asyncio
    async def test_passes_effective_user_fields(self, mock_update):
        with patch("utils.telegram_user.upsert_user", new_callable=AsyncMock, return_value=True) as mock_upsert, \
             patch("utils.telegram_user._fetch_bio", new_callable=AsyncMock, return_value=None):
            result = await upsert_effective_user(mock_update)

        mock_upsert.assert_called_once_with(
            user_id=mock_update.effective_user.id,
            username=mock_update.effective_user.username,
            first_name=mock_update.effective_user.first_name,
            last_name=mock_update.effective_user.last_name,
            is_bot=mock_update.effective_user.is_bot,
            is_premium=bool(mock_update.effective_user.is_premium),
            language_code=mock_update.effective_user.language_code,
            phone_number=None,
            bio=None,
        )
        assert result is True
