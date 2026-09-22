from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from upi_payment.domain import IdempotencyKey, Payment, PaymentAttempt, ResolvedVPA, VPA


class PaymentRepository(Protocol):
    def find_by_idempotency_key(self, key: IdempotencyKey) -> Payment | None: ...
    def create_or_get(self, payment: Payment) -> tuple[Payment, bool]: ...
    def find_by_id(self, payment_id: UUID) -> Payment | None: ...
    def find_attempt_by_idempotency_key(
        self, payment_id: UUID, key: IdempotencyKey
    ) -> PaymentAttempt | None: ...
    def find_attempt_by_id(
        self, payment_id: UUID, attempt_id: UUID
    ) -> PaymentAttempt | None: ...
    def find_latest_attempt(self, payment_id: UUID) -> PaymentAttempt | None: ...
    def list_attempts(self, payment_id: UUID) -> list[PaymentAttempt]: ...
    def next_attempt_number(self, payment_id: UUID) -> int: ...
    def start_attempt(
        self,
        *,
        previous_payment: Payment,
        processing_payment: Payment,
        attempt: PaymentAttempt,
    ) -> tuple[Payment, PaymentAttempt]: ...
    def update_attempt_with_payment_status_change(
        self,
        *,
        previous_payment: Payment,
        updated_payment: Payment,
        previous_attempt: PaymentAttempt,
        updated_attempt: PaymentAttempt,
        source: str,
        reason_code: str | None = None,
    ) -> tuple[Payment, PaymentAttempt]: ...


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
