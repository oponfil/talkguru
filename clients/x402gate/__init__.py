# clients/x402gate/__init__.py — Клиент x402gate.io с оплатой через Base/EVM USDC
# Поддерживает prepaid режим: пополнение баланса и подпись запросов.

import asyncio
import base64
import time
import traceback

import httpx
from eth_account import Account
from x402 import PaymentRequired, x402Client
from x402.mechanisms.evm.exact.client import ExactEvmScheme
from x402.mechanisms.evm.signers import EthAccountSigner

from config import (
    DEBUG_PRINT,
    EVM_PRIVATE_KEY,
    X402GATE_PREPAID_MIN_BALANCE,
    X402GATE_PREPAID_TOPUP_AMOUNT,
    X402GATE_TIMEOUT,
    X402GATE_URL,
)
from utils.utils import get_timestamp


# Base mainnet chain ID (CAIP-2)
BASE_MAINNET_CHAIN_ID = "eip155:8453"


class TopupError(RuntimeError):
    """Top-up оплата не прошла. Повтор бесполезен."""
    pass


class NonRetriableRequestError(RuntimeError):
    """Ошибка запроса, которую не стоит автоматически повторять."""
    pass


class X402GateClient:
    """Клиент x402gate.io — прокси для AI-сервисов с оплатой через USDC на Base.

    Prepaid mode:
    1. Lazy top-up: при первом запросе автоматически пополняет prepaid-баланс
    2. Каждый запрос подписывается EIP-712 (без блокчейн-транзакции)
    3. При исчерпании баланса — автоматический top-up и retry
    """

    def __init__(
        self,
        base_url: str | None = None,
        private_key: str | None = None,
    ):
        self.base_url = (base_url or X402GATE_URL).rstrip("/")
        self._private_key = private_key or EVM_PRIVATE_KEY
        self._x402_client: x402Client | None = None
        self._account = None
        self._signer = None
        self._prepaid_balance: float | None = None
        self._topup_generation: int = 0
        self._topup_lock = asyncio.Lock()

        # Инициализируем x402 клиент, если есть приватный ключ
        if self._private_key:
            # Нормализуем ключ
            key = self._private_key if self._private_key.startswith("0x") else f"0x{self._private_key}"
            self._account = Account.from_key(key)
            self._signer = EthAccountSigner(self._account)
            self._x402_client = x402Client()
            self._x402_client.register(
                BASE_MAINNET_CHAIN_ID,
                ExactEvmScheme(self._signer),
            )
            if DEBUG_PRINT:
                print(f"{get_timestamp()} [X402GATE] Initialized with Base address: {self._signer.address}")

    @property
    def available(self) -> bool:
        """Клиент доступен, если есть приватный ключ и x402 клиент инициализирован."""
        return self._x402_client is not None

    async def _run_auto_topup(self, *, observed_generation: int | None = None) -> float:
        """Single-flight обёртка для auto topup."""
        async with self._topup_lock:
            if (
                observed_generation is not None
                and self._topup_generation != observed_generation
                and self._prepaid_balance is not None
            ):
                print(
                    f"{get_timestamp()} [X402GATE] Reusing balance after concurrent top-up: "
                    f"${self._prepaid_balance:.4f}"
                )
                return self._prepaid_balance

            try:
                balance = await self.topup()
                if balance <= 0:
                    raise RuntimeError("topup() returned non-positive balance.")
            except TopupError:
                raise
            except Exception as e:
                raise TopupError(f"x402gate auto top-up failed: {e}") from e

            self._prepaid_balance = balance
            return balance

    async def _ensure_prepaid_balance_ready(self) -> None:
        """Гарантирует, что перед запросом известен серверный prepaid баланс."""
        if self._prepaid_balance is None:
            await self.get_balance()
            if self._prepaid_balance < X402GATE_PREPAID_MIN_BALANCE:
                self._prepaid_balance = await self._run_auto_topup(
                    observed_generation=self._topup_generation
                )

    # ────────────────────── Prepaid: top-up & balance ──────────────────────

    async def topup(self, amount_usd: float | None = None) -> float:
        """Пополняет prepaid-баланс через x402 payment flow."""
        if not self.available:
            raise ValueError("EVM_PRIVATE_KEY is not set.")

        amount = amount_usd or X402GATE_PREPAID_TOPUP_AMOUNT
        url = f"{self.base_url}/v1/topup"
        starting_balance = (
            self._prepaid_balance
            if self._prepaid_balance is not None
            else await self.get_balance()
        )

        print(f"{get_timestamp()} [X402GATE] Prepaid top-up: ${amount:.2f}...")

        try:
            async with httpx.AsyncClient() as http:
                # 1. POST без оплаты → 402 с ценой
                response = await http.post(url, json={"amount": amount}, timeout=X402GATE_TIMEOUT)

                if response.status_code != 402:
                    raise RuntimeError(
                        f"x402gate /v1/topup expected 402, got {response.status_code}: {response.text[:300]}"
                    )

                # 2. Подписываем x402 payment
                payment_data = response.json()

                evm_accepts = [
                    a for a in payment_data.get("accepts", [])
                    if "eip155:" in a.get("network", "")
                ]
                if not evm_accepts:
                    raise RuntimeError(
                        f"x402gate /v1/topup: no EVM payment option. "
                        f"Networks: {[a.get('network') for a in payment_data.get('accepts', [])]}"
                    )

                payment_data["accepts"] = evm_accepts
                payment_required = PaymentRequired.model_validate(payment_data)

                payment_payload = await asyncio.wait_for(
                    self._x402_client.create_payment_payload(payment_required),
                    timeout=X402GATE_TIMEOUT,
                )
                signature = base64.b64encode(
                    payment_payload.model_dump_json(by_alias=True).encode()
                ).decode()

                # 3. Повторный запрос с PAYMENT-SIGNATURE
                try:
                    response = await http.post(
                        url,
                        json={"amount": amount},
                        headers={"PAYMENT-SIGNATURE": signature},
                        timeout=X402GATE_TIMEOUT,
                    )
                except Exception:
                    refreshed_balance = await self.get_balance()
                    if refreshed_balance > starting_balance:
                        print(
                            f"{get_timestamp()} [X402GATE] Prepaid top-up likely succeeded despite "
                            f"transport error, balance=${refreshed_balance:.4f}"
                        )
                        self._topup_generation += 1
                        return refreshed_balance
                    raise

                if response.status_code != 200:
                    refreshed_balance = await self.get_balance()
                    if refreshed_balance > starting_balance:
                        print(
                            f"{get_timestamp()} [X402GATE] Prepaid top-up reconciled after "
                            f"unexpected status {response.status_code}, balance=${refreshed_balance:.4f}"
                        )
                        self._topup_generation += 1
                        return refreshed_balance
                    raise RuntimeError(
                        f"x402gate /v1/topup failed after payment: "
                        f"{response.status_code} {response.text[:500]}"
                    )

                result = response.json()
                self._prepaid_balance = float(result.get("balance", 0))
                credited = result.get("credited", "?")

                print(
                    f"{get_timestamp()} [X402GATE] ✅ Prepaid top-up OK: "
                    f"credited=${credited}, balance=${self._prepaid_balance:.4f}"
                )

                self._topup_generation += 1
                return self._prepaid_balance

        except Exception as e:
            print(
                f"{get_timestamp()} [X402GATE] ❌ Top-up failed: {e}\n"
                f"{''.join(traceback.format_exception(e))}"
            )
            raise

    async def get_balance(self) -> float:
        """Получает текущий prepaid-баланс с сервера x402gate."""
        if not self._account:
            raise ValueError("EVM_PRIVATE_KEY is not set.")

        address = self._account.address
        url = f"{self.base_url}/v1/balance/{address}"

        async with httpx.AsyncClient() as http:
            response = await http.get(url, timeout=X402GATE_TIMEOUT)
            response.raise_for_status()
            data = response.json()
            self._prepaid_balance = float(data.get("balance", 0))

        if DEBUG_PRINT:
            print(f"{get_timestamp()} [X402GATE] Prepaid balance: ${self._prepaid_balance:.4f}")

        return self._prepaid_balance

    # ────────────────────── Prepaid: headers ──────────────────────

    def _prepaid_headers(self, path: str) -> dict:
        """Формирует заголовки X-PREPAID-* для запроса."""
        sign_path = path.lstrip("/")
        if sign_path.startswith("v1/"):
            sign_path = sign_path[3:]

        ts = int(time.time())
        msg = f"x402gate:{sign_path}:{ts}".encode("utf-8")

        # EVM подписывает hash сообщения
        from eth_account.messages import encode_defunct
        signed = self._account.sign_message(encode_defunct(msg))

        return {
            "X-PREPAID-PUBKEY": self._account.address,
            "X-PREPAID-SIGNATURE": signed.signature.hex(),
            "X-PREPAID-TIMESTAMP": str(ts),
        }

    # ────────────────────── Main request ──────────────────────

    async def request(self, path: str, body: dict, timeout: float | None = None) -> dict:
        """Выполняет запрос к x402gate.io с prepaid оплатой."""
        if not self.available:
            raise ValueError("EVM_PRIVATE_KEY is not set. Please set it in .env to use x402gate.io.")

        await self._ensure_prepaid_balance_ready()

        url = f"{self.base_url}{path}"
        req_timeout = timeout if timeout is not None else X402GATE_TIMEOUT

        async with httpx.AsyncClient() as http:
            print(
                f"{get_timestamp()} [X402GATE] POST {path} "
                f"(prepaid, balance=${self._prepaid_balance:.4f})"
            )

            headers = self._prepaid_headers(path)
            response = await http.post(url, json=body, headers=headers, timeout=req_timeout)

            # Баланс исчерпан — auto top-up и retry
            if response.status_code == 402 or (
                response.status_code == 400
                and "insufficient" in response.text.lower()
            ):
                observed_generation = self._topup_generation
                print(
                    f"{get_timestamp()} [X402GATE] Prepaid balance insufficient, "
                    f"auto top-up ${X402GATE_PREPAID_TOPUP_AMOUNT:.2f}..."
                )
                self._prepaid_balance = await self._run_auto_topup(
                    observed_generation=observed_generation
                )

                # Retry с новыми заголовками
                print(f"{get_timestamp()} [X402GATE] Retry {path} after auto top-up...")
                headers = self._prepaid_headers(path)
                response = await http.post(url, json=body, headers=headers, timeout=req_timeout)

            if response.status_code != 200:
                error_text = response.text[:500]
                error_cls = (
                    NonRetriableRequestError
                    if 400 <= response.status_code < 500 and response.status_code not in (408, 409, 425, 429)
                    else RuntimeError
                )
                raise error_cls(f"x402gate returned {response.status_code}: {error_text}")

            result = response.json()

            # Обновляем кэшированный prepaid-баланс из response header
            prepaid_balance_header = response.headers.get("X-Prepaid-Balance")
            if prepaid_balance_header is not None:
                try:
                    self._prepaid_balance = float(prepaid_balance_header)
                except (ValueError, TypeError):
                    pass

            return result


# Глобальный экземпляр клиента
x402gate_client = X402GateClient()

if not x402gate_client.available:
    print("⚠️  WARNING: EVM_PRIVATE_KEY not set — x402gate.io недоступен.")
