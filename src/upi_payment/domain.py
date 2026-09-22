from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True)
class ResolvedVPA:
    vpa: VPA
    display_name: str

