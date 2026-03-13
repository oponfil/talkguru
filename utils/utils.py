# utils/utils.py — Вспомогательные функции

from datetime import datetime, timezone


def get_timestamp() -> str:
    """Возвращает текущее время в формате для логов."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
