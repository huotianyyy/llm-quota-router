"""Data models for multi-bucket LLM quota tracking and routing."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class QuotaBucket:
    """Tracks token or request allowance within a rolling reset window."""

    name: str
    capacity: int
    remaining: int
    window_seconds: float
    reset_at: float = 0.0

    def ensure_window(self, now: float | None = None) -> None:
        current = time.monotonic() if now is None else now
        if self.reset_at <= 0.0:
            self.reset_at = current + self.window_seconds
        elif current >= self.reset_at:
            self.remaining = self.capacity
            self.reset_at = current + self.window_seconds

    def can_consume(self, cost: int = 1, now: float | None = None) -> bool:
        self.ensure_window(now)
        return self.remaining >= cost

    def consume(self, cost: int = 1, now: float | None = None) -> bool:
        self.ensure_window(now)
        if self.remaining < cost:
            return False
        self.remaining -= cost
        return True

    @property
    def utilization_ratio(self) -> float:
        if self.capacity <= 0:
            return 1.0
        return max(0.0, min(1.0, 1.0 - (self.remaining / self.capacity)))


@dataclass
class ProviderAccount:
    """Represents an isolated upstream LLM provider credential/slot."""

    account_id: str
    provider: str
    models: set[str]
    buckets: list[QuotaBucket] = field(default_factory=list)
    priority: int = 100
    enabled: bool = True
    cooldown_until: float = 0.0

    def supports_model(self, model: str) -> bool:
        return "*" in self.models or model in self.models

    def in_cooldown(self, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else now
        return current < self.cooldown_until

    def has_quota(self, cost: int = 1, now: float | None = None) -> bool:
        return all(b.can_consume(cost=cost, now=now) for b in self.buckets)

    def min_remaining_ratio(self, now: float | None = None) -> float:
        if not self.buckets:
            return 1.0
        ratios = []
        for b in self.buckets:
            b.ensure_window(now)
            ratios.append(b.remaining / b.capacity if b.capacity > 0 else 0.0)
        return min(ratios)


@dataclass(frozen=True)
class RouteDecision:
    account_id: str
    provider: str
    model: str
    reason: str
