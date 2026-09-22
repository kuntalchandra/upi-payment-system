from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable

from uuid6 import uuid7

from upi_payment.domain import (
    IdempotencyKey,
    Money,
    Payment,
    PaymentStatus,
    VPA,
)
from upi_payment.errors import (
    IdempotencyConflict,
    InvalidAmount,
    InvalidIdempotencyKey,
    InvalidVPA,
    PayeeNotFound,
    SamePayerAndPayee,
)
from upi_payment.ports import PaymentRepository, VpaResolver


@dataclass(frozen=True)
class CreatePaymentCommand:
    idempotency_key: str
    payer_vpa: str
    payee_vpa: str
    amount_minor: int
    currency: str
    note: str | None


@dataclass(frozen=True)
class CreatePaymentResult:
    payment: Payment
    created: bool


class PaymentService:
    def __init__(
        self,
        repository: PaymentRepository,
        resolver: VpaResolver,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._resolver = resolver
        self._clock = clock

    def create_payment(self, command: CreatePaymentCommand) -> CreatePaymentResult:
        idempotency_key = self._parse_idempotency_key(command.idempotency_key)
        payer_vpa = self._parse_vpa(command.payer_vpa)
        payee_vpa = self._parse_vpa(command.payee_vpa)
        money = self._parse_money(command.amount_minor, command.currency)
        note = command.note.strip() if command.note and command.note.strip() else None

        if payer_vpa == payee_vpa:
            raise SamePayerAndPayee("Payer and payee VPA must differ")

        existing = self._repository.find_by_idempotency_key(idempotency_key)
        if existing is not None:
            self._verify_same_request(existing, payer_vpa, payee_vpa, money, note)
            return CreatePaymentResult(existing, created=False)

        resolved_payee = self._resolver.resolve(payee_vpa)
        if resolved_payee is None:
            raise PayeeNotFound("Payee VPA could not be resolved")

        now = self._clock()
        proposed = Payment(
            id=uuid7(),
            idempotency_key=idempotency_key,
            payer_vpa=payer_vpa,
            payee_vpa=resolved_payee.vpa,
            payee_name=resolved_payee.display_name,
            money=money,
            note=note,
            status=PaymentStatus.CREATED,
            created_at=now,
            updated_at=now,
        )
        stored, created = self._repository.create_or_get(proposed)
        self._verify_same_request(stored, payer_vpa, payee_vpa, money, note)
        return CreatePaymentResult(stored, created)

    @staticmethod
    def _parse_idempotency_key(raw_value: str) -> IdempotencyKey:
        try:
            return IdempotencyKey.parse(raw_value)
        except ValueError as exc:
            raise InvalidIdempotencyKey(str(exc)) from exc

    @staticmethod
    def _parse_vpa(raw_value: str) -> VPA:
        try:
            return VPA.parse(raw_value)
        except ValueError as exc:
            raise InvalidVPA(str(exc)) from exc

    @staticmethod
    def _parse_money(amount_minor: int, currency: str) -> Money:
        try:
            return Money(amount_minor, currency)
        except ValueError as exc:
            raise InvalidAmount(str(exc)) from exc

    @staticmethod
    def _verify_same_request(
        payment: Payment,
        payer_vpa: VPA,
        payee_vpa: VPA,
        money: Money,
        note: str | None,
    ) -> None:
        if not payment.has_same_creation_request(
            payer_vpa=payer_vpa,
            payee_vpa=payee_vpa,
            money=money,
            note=note,
        ):
            raise IdempotencyConflict(
                "Idempotency key was already used with a different request"
            )

