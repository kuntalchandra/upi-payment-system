from __future__ import annotations

from threading import Lock
from uuid import UUID

from upi_payment.domain import Payment
from upi_payment.ports import GatewayOutcome, GatewayResult


class InMemoryUpiGateway:
    def __init__(
        self, default_outcome: GatewayOutcome = GatewayOutcome.SUCCEEDED
    ) -> None:
        self._default_outcome = default_outcome
        self._results: dict[UUID, GatewayResult] = {}
        self._lock = Lock()
        self.submission_count = 0

    def submit(
        self, transaction_reference: UUID, payment: Payment
    ) -> GatewayResult:
        with self._lock:
            existing = self._results.get(transaction_reference)
            if existing is not None:
                return existing

            self.submission_count += 1
            result = self._new_result(transaction_reference)
            self._results[transaction_reference] = result
            return result

    def get_status(self, transaction_reference: UUID) -> GatewayResult | None:
        with self._lock:
            return self._results.get(transaction_reference)

    def set_result(
        self, transaction_reference: UUID, result: GatewayResult
    ) -> None:
        with self._lock:
            self._results[transaction_reference] = result

    def _new_result(self, transaction_reference: UUID) -> GatewayResult:
        reference = f"SIM-{transaction_reference}"
        if self._default_outcome == GatewayOutcome.SUCCEEDED:
            return GatewayResult(
                GatewayOutcome.SUCCEEDED,
                network_reference=reference,
            )
        if self._default_outcome == GatewayOutcome.FAILED:
            return GatewayResult(
                GatewayOutcome.FAILED,
                network_reference=reference,
                failure_code="SIMULATED_DECLINE",
            )
        return GatewayResult(
            GatewayOutcome.PENDING,
            network_reference=reference,
        )
