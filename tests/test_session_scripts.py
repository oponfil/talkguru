from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("telethon", reason="telethon not installed")

from scripts import generate_session, generate_session_qr  # noqa: E402


class TestGenerateSessionScript:
    @pytest.mark.asyncio
    async def test_hides_session_string_without_explicit_confirmation(self):
        mock_app = AsyncMock()
        mock_app.export_session_string = AsyncMock(return_value="secret-session")

        mock_client_context = AsyncMock()
        mock_client_context.__aenter__.return_value = mock_app
        mock_client_context.__aexit__.return_value = False

        with patch("pyrogram.Client", return_value=mock_client_context), \
             patch("builtins.input", return_value="no"), \
             patch("builtins.print") as mock_print:
            await generate_session.main()

        printed_text = "\n".join(
            " ".join(str(arg) for arg in call.args)
            for call in mock_print.call_args_list
        )
        assert "secret-session" not in printed_text


class TestGenerateSessionQrScript:
    @pytest.mark.asyncio
    async def test_cleans_up_session_file_on_error(self):
        mock_client = AsyncMock()
        mock_client.connect = AsyncMock()
        mock_client.qr_login = AsyncMock(side_effect=RuntimeError("boom"))
        mock_client.disconnect = AsyncMock()

        with patch("scripts.generate_session_qr.TelegramClient", return_value=mock_client), \
             patch("scripts.generate_session_qr.os.path.exists", return_value=True), \
             patch("scripts.generate_session_qr.os.remove") as mock_remove, \
             patch("builtins.print"):
            await generate_session_qr.main()

        mock_client.disconnect.assert_called_once()
        mock_remove.assert_called_once_with("anon_telethon.session")
