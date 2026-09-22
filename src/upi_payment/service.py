from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable
from uuid import UUID

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
    InvalidPaymentState,
    PaymentAuthorizationFailed,
    PaymentNotFound,
    PayeeNotFound,
    SamePayerAndPayee,
    ConcurrentPaymentUpdate,
)
from upi_payment.ports import (
    AuthorizationVerifier,
    GatewayOutcome,
    GatewayResult,
    PaymentRepository,
    UpiGateway,
    VpaResolver,
)


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
        authorization_verifier: AuthorizationVerifier,
        gateway: UpiGateway,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._resolver = resolver
        self._authorization_verifier = authorization_verifier
        self._gateway = gateway
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

    def get_payment(self, payment_id: str) -> Payment:
        return self._find_payment(payment_id)

    def submit_payment(self, payment_id: str, authorization_token: str) -> Payment:
        payment = self._find_payment(payment_id)

        if payment.status in {
            PaymentStatus.SUCCEEDED,
            PaymentStatus.FAILED,
            PaymentStatus.PENDING,
        }:
            return payment

        if payment.status == PaymentStatus.PROCESSING:
            return self._recover_processing(payment)

        if not self._authorization_verifier.verify(authorization_token):
            raise PaymentAuthorizationFailed("Payment authorisation was rejected")

        processing = payment.start_processing(self._clock())
        try:
            processing = self._repository.update_with_status_change(
                previous=payment,
                updated=processing,
                source="SUBMISSION",
            )
        except ConcurrentPaymentUpdate:
            return self._find_payment(payment_id)

        result = self._gateway.submit(processing.id, processing)
        return self._apply_gateway_result(processing, result, source="SUBMISSION")

    def refresh_status(self, payment_id: str) -> Payment:
        payment = self._find_payment(payment_id)
        if payment.status == PaymentStatus.PROCESSING:
            return self._recover_processing(payment)
        if payment.status != PaymentStatus.PENDING:
            raise InvalidPaymentState(
                f"Cannot refresh payment in {payment.status.value}"
            )

        result = self._gateway.get_status(payment.id)
        if result is None or result.outcome == GatewayOutcome.PENDING:
            return payment
        return self._apply_gateway_result(
            payment, result, source="STATUS_ENQUIRY"
        )

    def _recover_processing(self, payment: Payment) -> Payment:
        result = self._gateway.get_status(payment.id)
        if result is None:
            result = self._gateway.submit(payment.id, payment)
        return self._apply_gateway_result(
            payment, result, source="STATUS_ENQUIRY"
        )

    def _apply_gateway_result(
        self,
        payment: Payment,
        result: GatewayResult,
        *,
        source: str,
    ) -> Payment:
        if result.outcome == GatewayOutcome.PENDING:
            if payment.status == PaymentStatus.PENDING:
                return payment
            updated = payment.mark_pending(
                self._clock(), result.network_reference
            )
        elif result.outcome == GatewayOutcome.SUCCEEDED:
            if result.network_reference is None:
                raise ValueError("Successful gateway result requires a reference")
            updated = payment.mark_succeeded(
                self._clock(), result.network_reference
            )
        else:
            if result.failure_code is None:
                raise ValueError("Failed gateway result requires a failure code")
            updated = payment.mark_failed(
                self._clock(),
                result.failure_code,
                result.network_reference,
            )

        try:
            return self._repository.update_with_status_change(
                previous=payment,
                updated=updated,
                source=source,
                reason_code=result.failure_code,
            )
        except ConcurrentPaymentUpdate:
            return self._find_payment(str(payment.id))

    def _find_payment(self, raw_payment_id: str) -> Payment:
        try:
            payment_id = UUID(raw_payment_id)
        except (ValueError, AttributeError) as exc:
            raise PaymentNotFound("Payment was not found") from exc
        payment = self._repository.find_by_id(payment_id)
        if payment is None:
            raise PaymentNotFound("Payment was not found")
        return payment

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
