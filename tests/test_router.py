"""Unit tests for llm-quota-router (compatible with both unittest and pytest)."""

from __future__ import annotations

import unittest

from llm_quota_router import (
    CircuitState,
    NoAvailableAccountError,
    ProviderAccount,
    QuotaBucket,
    QuotaRouter,
)


def make_account(acc_id: str, remaining: int = 100, priority: int = 100) -> ProviderAccount:
    return ProviderAccount(
        account_id=acc_id,
        provider="openai-compatible",
        models={"gpt-4o", "claude-sonnet", "gemini-pro"},
        priority=priority,
        buckets=[QuotaBucket(name="rolling_window", capacity=100, remaining=remaining, window_seconds=3600)],
    )


class TestQuotaRouter(unittest.TestCase):
    def test_prefers_sticky_account_when_healthy(self) -> None:
        router = QuotaRouter([make_account("slot-a", 40), make_account("slot-b", 95)])
        decision = router.select(model="gemini-pro", preferred_account="slot-a", now=100.0)
        self.assertEqual(decision.account_id, "slot-a")
        self.assertEqual(decision.reason, "sticky_preferred")

    def test_selects_highest_headroom_within_same_priority(self) -> None:
        router = QuotaRouter([make_account("slot-a", 20), make_account("slot-b", 85)])
        decision = router.select(model="gemini-pro", now=100.0)
        self.assertEqual(decision.account_id, "slot-b")
        self.assertEqual(decision.reason, "highest_headroom")

    def test_rate_limit_cooldown_shifts_traffic(self) -> None:
        router = QuotaRouter([make_account("slot-a", 90), make_account("slot-b", 50)], rate_limit_cooldown=60.0)
        router.record_rate_limit("slot-a", now=100.0)
        decision = router.select(model="gemini-pro", preferred_account="slot-a", now=120.0)
        self.assertEqual(decision.account_id, "slot-b")

        # After cooldown expires, slot-a becomes eligible again
        recovered = router.select(model="gemini-pro", preferred_account="slot-a", now=165.0)
        self.assertEqual(recovered.account_id, "slot-a")

    def test_circuit_breaker_opens_and_recovers(self) -> None:
        router = QuotaRouter([make_account("slot-a", 90)], failure_threshold=2, recovery_timeout=30.0)
        router.record_failure("slot-a", now=10.0)
        router.record_failure("slot-a", now=11.0)
        self.assertEqual(router._breakers["slot-a"].state, CircuitState.OPEN)

        with self.assertRaises(NoAvailableAccountError):
            router.select(model="gemini-pro", now=20.0)

        # Recovery timeout elapsed -> half-open probe allowed
        decision = router.select(model="gemini-pro", now=45.0)
        self.assertEqual(decision.account_id, "slot-a")
        router.record_success("slot-a", cost=5, now=45.0)
        self.assertEqual(router._breakers["slot-a"].state, CircuitState.CLOSED)

    def test_execute_with_failover_recovers_from_429(self) -> None:
        router = QuotaRouter([make_account("slot-a", 100), make_account("slot-b", 80)])
        calls: list[str] = []

        def fake_call(decision):
            calls.append(decision.account_id)
            if decision.account_id == "slot-a":
                raise RuntimeError("HTTP 429 rate limit exceeded")
            return f"ok:{decision.account_id}"

        result = router.execute_with_failover(model="gemini-pro", fn=fake_call, preferred_account="slot-a")
        self.assertEqual(result, "ok:slot-b")
        self.assertEqual(calls, ["slot-a", "slot-b"])

    def test_bucket_resets_after_window(self) -> None:
        acc = make_account("slot-a", remaining=0)
        acc.buckets[0].reset_at = 200.0
        router = QuotaRouter([acc])
        with self.assertRaises(NoAvailableAccountError):
            router.select(model="gemini-pro", now=150.0)
        decision = router.select(model="gemini-pro", now=205.0)
        self.assertEqual(decision.account_id, "slot-a")


if __name__ == "__main__":
    unittest.main()
