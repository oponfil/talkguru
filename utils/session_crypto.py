"""Шифрование и расшифровка Pyrogram session string."""

from cryptography.fernet import Fernet, InvalidToken

from config import SESSION_ENCRYPTION_KEY

_session_cipher: Fernet | None = None

if SESSION_ENCRYPTION_KEY:
    try:
        _session_cipher = Fernet(SESSION_ENCRYPTION_KEY.encode("utf-8"))
    except Exception as e:
        print(f"⚠️  WARNING: SESSION_ENCRYPTION_KEY некорректен: {e}")


def _get_session_cipher() -> Fernet:
    """Возвращает инициализированный cipher для шифрования сессий."""
    if _session_cipher is None:
        raise ValueError("SESSION_ENCRYPTION_KEY is not configured or invalid.")
    return _session_cipher


def encrypt_session_string(session_string: str) -> str:
    """Шифрует Pyrogram session string для безопасного хранения в БД."""
    cipher = _get_session_cipher()
    return cipher.encrypt(session_string.encode("utf-8")).decode("utf-8")


def decrypt_session_string(encrypted_session_string: str) -> str:
    """Расшифровывает Pyrogram session string, считанный из БД."""
    cipher = _get_session_cipher()
    try:
        return cipher.decrypt(encrypted_session_string.encode("utf-8")).decode("utf-8")
    except InvalidToken as e:
        raise ValueError("Encrypted session string is invalid or corrupted.") from e
