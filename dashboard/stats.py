# dashboard/stats.py — In-memory сбор метрик для дашборда DraftGuru
#
# Хранит счётчики LLM-запросов, черновиков, команд и rolling log buffer.
# Все данные в RAM, сбрасываются при перезапуске.

from __future__ import annotations

import collections
import time
from dataclasses import dataclass, field
from typing import Any

MAX_LOG_ENTRIES = 1000  # Размер rolling log buffer


@dataclass
class _GlobalStats:
    """Синглтон с метриками бота."""

    started_at: float = field(default_factory=time.time)

    # LLM-запросы
    llm_requests: int = 0
    llm_errors: int = 0
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    total_reasoning_tokens: int = 0
    total_latency_s: float = 0.0
    models: dict[str, int] = field(default_factory=dict)
    model_details: dict[str, dict[str, float]] = field(default_factory=dict)

    # Баланс x402gate
    last_balance: float | None = None
    initial_balance: float | None = None
    topup_count: int = 0
    topup_total: float = 0.0
    wallet_balance: float | None = None

    # Черновики и автоответы
    drafts_generated: int = 0
    draft_styles: dict[str, int] = field(default_factory=dict)
    auto_replies_sent: int = 0
    bot_replies: int = 0

    # Голосовые
    voice_transcriptions: int = 0

    # Команды
    commands: dict[str, int] = field(default_factory=dict)

    # Ошибки и предупреждения
    errors: int = 0
    warnings: int = 0

    # Пользователи (заполняются из Supabase по запросу)
    total_users: int = 0
    connected_users: int = 0
    active_users_24h: int = 0

    # Логи
    logs: collections.deque = field(
        default_factory=lambda: collections.deque(maxlen=MAX_LOG_ENTRIES)
    )


# Синглтон
_stats = _GlobalStats()


# ---------------------------------------------------------------------------
# Перехват логов — вызывается из обёртки print() в bot.py
# ---------------------------------------------------------------------------


def capture_log(message: str) -> None:
    """Добавляет строку лога в rolling buffer.

    Определяет уровень по содержимому (ERROR/WARNING/INFO).
    """
    level = "INFO"
    if "ERROR" in message:
        level = "ERROR"
        _stats.errors += 1
    elif "WARNING" in message:
        level = "WARNING"
        _stats.warnings += 1

    _stats.logs.append({
        "ts": time.time(),
        "level": level,
        "message": message.rstrip(),
    })


# Стоимость последнего запроса (вычисляется в update_balance,
# привязывается к модели в record_llm_request).
_last_request_cost: float = 0.0


# ---------------------------------------------------------------------------
# API записи метрик
# ---------------------------------------------------------------------------


def record_llm_request(
    model: str,
    latency_s: float,
    tokens_in: int,
    tokens_out: int,
    reasoning_tokens: int = 0,
) -> None:
    """Записывает успешный LLM-запрос."""
    _stats.llm_requests += 1
    _stats.total_latency_s += latency_s
    _stats.total_tokens_in += tokens_in
    _stats.total_tokens_out += tokens_out
    _stats.total_reasoning_tokens += reasoning_tokens
    _stats.models[model] = _stats.models.get(model, 0) + 1

    # Per-model детали
    md = _stats.model_details.get(model)
    if md is None:
        md = {"count": 0, "tokens_in": 0, "tokens_out": 0, "reasoning": 0, "latency": 0.0, "cost": 0.0}
        _stats.model_details[model] = md
    md["count"] += 1
    md["tokens_in"] += tokens_in
    md["tokens_out"] += tokens_out
    md["reasoning"] += reasoning_tokens
    md["latency"] += latency_s

    # Привязываем стоимость запроса (вычисленную в update_balance)
    global _last_request_cost
    md["cost"] += _last_request_cost
    _last_request_cost = 0.0


def record_llm_error() -> None:
    """Записывает ошибку LLM-запроса."""
    _stats.llm_errors += 1


def record_draft(style: str = "") -> None:
    """Записывает генерацию черновика."""
    _stats.drafts_generated += 1
    if style:
        _stats.draft_styles[style] = _stats.draft_styles.get(style, 0) + 1


def record_auto_reply() -> None:
    """Записывает отправку автоответа."""
    _stats.auto_replies_sent += 1


def record_bot_reply() -> None:
    """Записывает ответ бота в чате с пользователем."""
    _stats.bot_replies += 1


def record_voice_transcription() -> None:
    """Записывает транскрипцию голосового сообщения."""
    _stats.voice_transcriptions += 1


def record_command(command: str) -> None:
    """Записывает использование команды бота."""
    _stats.commands[command] = _stats.commands.get(command, 0) + 1


def update_balance(balance: float) -> None:
    """Обновляет кэшированный prepaid-баланс x402gate.

    Если баланс вырос — фиксирует пополнение.
    Если упал — сохраняет стоимость запроса для привязки к модели.
    """
    global _last_request_cost
    _last_request_cost = 0.0
    if _stats.initial_balance is None:
        _stats.initial_balance = balance
    elif _stats.last_balance is not None:
        if balance > _stats.last_balance:
            topup_amount = balance - _stats.last_balance
            _stats.topup_count += 1
            _stats.topup_total += topup_amount
        elif balance < _stats.last_balance:
            _last_request_cost = _stats.last_balance - balance
    _stats.last_balance = balance


def update_wallet_balance(balance: float) -> None:
    """Обновляет кэшированный баланс USDC-кошелька."""
    _stats.wallet_balance = balance


def update_user_counts(
    total: int,
    connected: int,
    active_24h: int,
) -> None:
    """Обновляет счётчики пользователей (из периодического запроса к Supabase)."""
    _stats.total_users = total
    _stats.connected_users = connected
    _stats.active_users_24h = active_24h


# ---------------------------------------------------------------------------
# API снапшота
# ---------------------------------------------------------------------------


def get_stats() -> dict[str, Any]:
    """Возвращает снапшот всех метрик для дашборда."""
    now = time.time()
    uptime_s = now - _stats.started_at

    avg_latency_s = (
        round(_stats.total_latency_s / _stats.llm_requests, 2)
        if _stats.llm_requests > 0
        else 0
    )

    balance_spent = None
    if _stats.initial_balance is not None and _stats.last_balance is not None:
        balance_spent = round(
            _stats.initial_balance + _stats.topup_total - _stats.last_balance, 4,
        )

    return {
        "uptime_s": round(uptime_s, 0),
        # LLM
        "llm_requests": _stats.llm_requests,
        "llm_errors": _stats.llm_errors,
        "total_tokens_in": _stats.total_tokens_in,
        "total_tokens_out": _stats.total_tokens_out,
        "total_reasoning_tokens": _stats.total_reasoning_tokens,
        "avg_latency_s": avg_latency_s,
        "models": dict(_stats.models),
        "model_details": {k: dict(v) for k, v in _stats.model_details.items()},
        # Баланс
        "last_balance": _stats.last_balance,
        "initial_balance": _stats.initial_balance,
        "balance_spent": balance_spent,
        "topup_count": _stats.topup_count,
        "topup_total": round(_stats.topup_total, 4),
        "wallet_balance": _stats.wallet_balance,
        # Черновики
        "drafts_generated": _stats.drafts_generated,
        "draft_styles": dict(_stats.draft_styles),
        "auto_replies_sent": _stats.auto_replies_sent,
        "bot_replies": _stats.bot_replies,
        # Голосовые
        "voice_transcriptions": _stats.voice_transcriptions,
        # Команды
        "commands": dict(_stats.commands),
        # Здоровье
        "errors": _stats.errors,
        "warnings": _stats.warnings,
        # Пользователи
        "total_users": _stats.total_users,
        "connected_users": _stats.connected_users,
        "active_users_24h": _stats.active_users_24h,
    }


def get_logs(limit: int = MAX_LOG_ENTRIES) -> list[dict[str, Any]]:
    """Возвращает последние записи логов (сначала новые)."""
    all_entries = list(_stats.logs)
    if limit:
        all_entries = all_entries[-limit:]
    return list(reversed(all_entries))
