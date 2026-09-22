from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from upi_payment.domain import IdempotencyKey, Payment, ResolvedVPA, VPA


class PaymentRepository(Protocol):
    def find_by_idempotency_key(
        self, idempotency_key: IdempotencyKey
    ) -> Payment | None: ...

    def create_or_get(self, payment: Payment) -> tuple[Payment, bool]: ...

    def find_by_id(self, payment_id: UUID) -> Payment | None: ...

    def update_with_status_change(
        self,
        *,
        previous: Payment,
        updated: Payment,
        source: str,
        reason_code: str | None = None,
    ) -> Payment: ...


class VpaResolver(Protocol):
    def resolve(self, vpa: VPA) -> ResolvedVPA | None: ...


class AuthorizationVerifier(Protocol):
    def verify(self, token: str) -> bool: ...


class GatewayOutcome(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    PENDING = "PENDING"


@dataclass(frozen=True)
class GatewayResult:
    outcome: GatewayOutcome
    network_reference: str | None = None
    failure_code: str | None = None


class UpiGateway(Protocol):
    def submit(
        self, transaction_reference: UUID, payment: Payment
    ) -> GatewayResult: ...

    def get_status(self, transaction_reference: UUID) -> GatewayResult | None: ...
