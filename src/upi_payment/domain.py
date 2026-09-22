from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
import re
from uuid import UUID


_VPA_PATTERN = re.compile(r"^[a-z0-9._-]+@[a-z0-9.-]+$")


class PaymentStatus(StrEnum):
    CREATED = "CREATED"
    PROCESSING = "PROCESSING"
    PENDING = "PENDING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class VPA:
    value: str

    @classmethod
    def parse(cls, raw_value: str) -> VPA:
        normalised = raw_value.strip().lower()
        if not _VPA_PATTERN.fullmatch(normalised):
            raise ValueError("VPA must have the form local-part@handle")
        return cls(normalised)


@dataclass(frozen=True)
class Money:
    amount_minor: int
    currency: str

    def __post_init__(self) -> None:
        if self.amount_minor <= 0:
            raise ValueError("Amount must be greater than zero")
        if self.currency != "INR":
            raise ValueError("Only INR is supported")


@dataclass(frozen=True)
class IdempotencyKey:
    value: str

    @classmethod
    def parse(cls, raw_value: str) -> IdempotencyKey:
        try:
            parsed = UUID(raw_value)
        except (ValueError, AttributeError) as exc:
            raise ValueError("Idempotency key must be a UUIDv7") from exc
        if parsed.version != 7:
            raise ValueError("Idempotency key must be a UUIDv7")
        return cls(str(parsed))


@dataclass(frozen=True)
class Payment:
    id: UUID
    idempotency_key: IdempotencyKey
    payer_vpa: VPA
    payee_vpa: VPA
    payee_name: str
    money: Money
    note: str | None
    status: PaymentStatus
    created_at: datetime
    updated_at: datetime
    version: int = 0
    network_reference: str | None = None
    failure_code: str | None = None
    submitted_at: datetime | None = None
    completed_at: datetime | None = None

    def has_same_creation_request(
        self,
        *,
        payer_vpa: VPA,
        payee_vpa: VPA,
        money: Money,
        note: str | None,
    ) -> bool:
        return (
            self.payer_vpa == payer_vpa
            and self.payee_vpa == payee_vpa
            and self.money == money
            and self.note == note
        )

    def start_processing(self, at: datetime) -> Payment:
        self._require_status(PaymentStatus.CREATED)
        return replace(
            self,
            status=PaymentStatus.PROCESSING,
            submitted_at=self.submitted_at or at,
            updated_at=at,
            version=self.version + 1,
        )

    def mark_pending(
        self, at: datetime, network_reference: str | None = None
    ) -> Payment:
        self._require_status(PaymentStatus.PROCESSING)
        return replace(
            self,
            status=PaymentStatus.PENDING,
            network_reference=network_reference,
            updated_at=at,
            version=self.version + 1,
        )

    def mark_succeeded(self, at: datetime, network_reference: str) -> Payment:
        self._require_status(PaymentStatus.PROCESSING, PaymentStatus.PENDING)
        return replace(
            self,
            status=PaymentStatus.SUCCEEDED,
            network_reference=network_reference,
            failure_code=None,
            completed_at=at,
            updated_at=at,
            version=self.version + 1,
        )

    def mark_failed(
        self,
        at: datetime,
        failure_code: str,
        network_reference: str | None = None,
    ) -> Payment:
        self._require_status(PaymentStatus.PROCESSING, PaymentStatus.PENDING)
        return replace(
            self,
            status=PaymentStatus.FAILED,
            network_reference=network_reference,
            failure_code=failure_code,
            completed_at=at,
            updated_at=at,
            version=self.version + 1,
        )

    def _require_status(self, *allowed: PaymentStatus) -> None:
        if self.status not in allowed:
            allowed_values = ", ".join(status.value for status in allowed)
            raise ValueError(
                f"Payment in {self.status.value} must be in one of: {allowed_values}"
            )


@dataclass(frozen=True)
class ResolvedVPA:
    vpa: VPA
    display_name: str
