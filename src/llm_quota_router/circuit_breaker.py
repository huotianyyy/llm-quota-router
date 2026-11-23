"""Circuit breaker protecting agent loops from flapping or rate-limited LLM endpoints."""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreaker:
    failure_threshold: int = 3
    recovery_timeout: float = 30.0
    consecutive_failures: int = 0
    opened_at: float = 0.0
    state: CircuitState = CircuitState.CLOSED

    def allow_request(self, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else now
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            if (current - self.opened_at) >= self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                return True
            return False
        # HALF_OPEN allows probe traffic
        return True

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.opened_at = 0.0
        self.state = CircuitState.CLOSED

    def record_failure(self, now: float | None = None) -> None:
        current = time.monotonic() if now is None else now
        self.consecutive_failures += 1
        if self.state == CircuitState.HALF_OPEN or self.consecutive_failures >= self.failure_threshold:
            self.state = CircuitState.OPEN
            self.opened_at = current
