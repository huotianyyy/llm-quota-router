"""Multi-bucket quota-aware router with sticky session preference and automatic failover."""

from __future__ import annotations

import time
from typing import Callable, TypeVar

from .circuit_breaker import CircuitBreaker
from .models import ProviderAccount, RouteDecision

T = TypeVar("T")


class NoAvailableAccountError(RuntimeError):
    """Raised when all candidate accounts are exhausted, cooled down, or circuit-broken."""


class QuotaRouter:
    """Routes LLM calls across multiple quota-tracked accounts with circuit breakers."""

    def __init__(
        self,
        accounts: list[ProviderAccount],
        failure_threshold: int = 3,
        recovery_timeout: float = 30.0,
        rate_limit_cooldown: float = 60.0,
    ) -> None:
        self._accounts: dict[str, ProviderAccount] = {acc.account_id: acc for acc in accounts}
        self._breakers: dict[str, CircuitBreaker] = {
            acc.account_id: CircuitBreaker(
                failure_threshold=failure_threshold,
                recovery_timeout=recovery_timeout,
            )
            for acc in accounts
        }
        self.rate_limit_cooldown = rate_limit_cooldown

    def select(
        self,
        model: str,
        cost: int = 1,
        preferred_account: str | None = None,
        exclude: set[str] | None = None,
        now: float | None = None,
    ) -> RouteDecision:
        current = time.monotonic() if now is None else now
        excluded = exclude or set()

        # Sticky preference first to preserve prompt caching locality when healthy
        if preferred_account and preferred_account not in excluded:
            acc = self._accounts.get(preferred_account)
            if acc and self._is_eligible(acc, model, cost, current):
                return RouteDecision(
                    account_id=acc.account_id,
                    provider=acc.provider,
                    model=model,
                    reason="sticky_preferred",
                )

        candidates = [
            acc
            for acc in self._accounts.values()
            if acc.account_id not in excluded and self._is_eligible(acc, model, cost, current)
        ]
        if not candidates:
            raise NoAvailableAccountError(f"No healthy account with remaining quota for model '{model}'.")

        # Sort by highest priority first, then highest minimum remaining bucket ratio
        candidates.sort(
            key=lambda a: (a.priority, a.min_remaining_ratio(current)),
            reverse=True,
        )
        chosen = candidates[0]
        return RouteDecision(
            account_id=chosen.account_id,
            provider=chosen.provider,
            model=model,
            reason="highest_headroom",
        )

    def _is_eligible(self, acc: ProviderAccount, model: str, cost: int, now: float) -> bool:
        if not acc.enabled or not acc.supports_model(model):
            return False
        if acc.in_cooldown(now):
            return False
        if not self._breakers[acc.account_id].allow_request(now):
            return False
        return acc.has_quota(cost=cost, now=now)

    def record_success(self, account_id: str, cost: int = 1, now: float | None = None) -> None:
        current = time.monotonic() if now is None else now
        acc = self._accounts[account_id]
        for bucket in acc.buckets:
            bucket.consume(cost=cost, now=current)
        self._breakers[account_id].record_success()

    def record_rate_limit(
        self,
        account_id: str,
        retry_after: float | None = None,
        now: float | None = None,
    ) -> None:
        current = time.monotonic() if now is None else now
        delay = self.rate_limit_cooldown if retry_after is None else retry_after
        self._accounts[account_id].cooldown_until = current + max(1.0, delay)

    def record_failure(self, account_id: str, now: float | None = None) -> None:
        self._breakers[account_id].record_failure(now=now)

    def execute_with_failover(
        self,
        model: str,
        fn: Callable[[RouteDecision], T],
        cost: int = 1,
        preferred_account: str | None = None,
        max_attempts: int = 3,
    ) -> T:
        """Executes a provider call with automatic failover across healthy accounts."""
        tried: set[str] = set()
        last_exc: Exception | None = None

        for _ in range(max_attempts):
            decision = self.select(
                model=model,
                cost=cost,
                preferred_account=preferred_account,
                exclude=tried,
            )
            tried.add(decision.account_id)
            try:
                result = fn(decision)
                self.record_success(decision.account_id, cost=cost)
                return result
            except Exception as exc:
                last_exc = exc
                if "429" in str(exc) or "rate limit" in str(exc).lower():
                    self.record_rate_limit(decision.account_id)
                else:
                    self.record_failure(decision.account_id)

        assert last_exc is not None
        raise last_exc
