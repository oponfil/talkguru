from unittest.mock import patch

import pytest

from utils.session_crypto import decrypt_session_string, encrypt_session_string


class TestSessionCrypto:
    """Тесты для шифрования session string."""

    def test_encrypt_decrypt_roundtrip(self):
        plaintext = "pyrogram-session-value"

        encrypted = encrypt_session_string(plaintext)
        decrypted = decrypt_session_string(encrypted)

        assert encrypted != plaintext
        assert decrypted == plaintext

    def test_invalid_ciphertext_raises(self):
        with pytest.raises(ValueError, match="invalid or corrupted"):
            decrypt_session_string("not-a-valid-fernet-token")

    def test_missing_key_raises(self):
        with patch("utils.session_crypto._session_cipher", None):
            with pytest.raises(ValueError, match="SESSION_ENCRYPTION_KEY"):
                encrypt_session_string("plaintext")
