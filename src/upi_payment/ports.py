from __future__ import annotations

from typing import Protocol

from upi_payment.domain import IdempotencyKey, Payment, ResolvedVPA, VPA


class PaymentRepository(Protocol):
    def find_by_idempotency_key(
        self, idempotency_key: IdempotencyKey
    ) -> Payment | None: ...

    def create_or_get(self, payment: Payment) -> tuple[Payment, bool]: ...


class VpaResolver(Protocol):
    def resolve(self, vpa: VPA) -> ResolvedVPA | None: ...

