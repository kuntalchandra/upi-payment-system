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
    PaymentAttempt,
    PaymentAttemptStatus,
    PaymentStatus,
    VPA,
)
from upi_payment.errors import (
    ConcurrentPaymentUpdate,
    IdempotencyConflict,
    InvalidAmount,
    InvalidIdempotencyKey,
    InvalidPaymentState,
    InvalidVPA,
    PaymentAttemptNotFound,
    PaymentAuthorizationFailed,
    PaymentNotFound,
    PayeeNotFound,
    SamePayerAndPayee,
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


@dataclass(frozen=True)
class CreatePaymentAttemptResult:
    attempt: PaymentAttempt
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
        key = self._parse_idempotency_key(command.idempotency_key)
        payer = self._parse_vpa(command.payer_vpa)
        payee = self._parse_vpa(command.payee_vpa)
        money = self._parse_money(command.amount_minor, command.currency)
        note = command.note.strip() if command.note and command.note.strip() else None
        if payer == payee:
            raise SamePayerAndPayee("Payer and payee VPA must differ")
        existing = self._repository.find_by_idempotency_key(key)
        if existing:
            self._verify_same_request(existing, payer, payee, money, note)
            return CreatePaymentResult(existing, False)
        resolved = self._resolver.resolve(payee)
        if resolved is None:
            raise PayeeNotFound("Payee VPA could not be resolved")
        now = self._clock()
        proposed = Payment(
            uuid7(),
            key,
            payer,
            resolved.vpa,
            resolved.display_name,
            money,
            note,
            PaymentStatus.CREATED,
            now,
            now,
        )
        stored, created = self._repository.create_or_get(proposed)
        self._verify_same_request(stored, payer, payee, money, note)
        return CreatePaymentResult(stored, created)

    def get_payment(self, payment_id: str) -> Payment:
        return self._find_payment(payment_id)

    def get_latest_payment_attempt(self, payment_id: str) -> PaymentAttempt | None:
        payment = self._find_payment(payment_id)
        return self._repository.find_latest_attempt(payment.id)

    def create_payment_attempt(
        self, payment_id: str, idempotency_key: str, authorization_token: str
    ) -> CreatePaymentAttemptResult:
        payment = self._find_payment(payment_id)
        key = self._parse_idempotency_key(idempotency_key)
        existing = self._repository.find_attempt_by_idempotency_key(payment.id, key)
        if existing:
            return CreatePaymentAttemptResult(existing, False)
        if payment.status in {
            PaymentStatus.PROCESSING,
            PaymentStatus.PENDING,
            PaymentStatus.SUCCEEDED,
        }:
            raise InvalidPaymentState(
                f"Cannot create an attempt for payment in {payment.status.value}"
            )
        if not self._authorization_verifier.verify(authorization_token):
            raise PaymentAuthorizationFailed("Payment authorisation was rejected")
        now = self._clock()
        processing = payment.start_processing(now)
        attempt = PaymentAttempt(
            uuid7(),
            payment.id,
            key,
            self._repository.next_attempt_number(payment.id),
            PaymentAttemptStatus.PROCESSING,
            now,
            now,
        )
        try:
            processing, attempt = self._repository.start_attempt(
                previous_payment=payment,
                processing_payment=processing,
                attempt=attempt,
            )
        except ConcurrentPaymentUpdate as exc:
            existing = self._repository.find_attempt_by_idempotency_key(payment.id, key)
            if existing:
                return CreatePaymentAttemptResult(existing, False)
            raise InvalidPaymentState(
                "Another payment attempt is already active"
            ) from exc
        result = self._gateway.submit(attempt.id, processing)
        return CreatePaymentAttemptResult(
            self._apply_gateway_result(
                processing, attempt, result, source="ATTEMPT_SUBMISSION"
            ),
            True,
        )

    def get_payment_attempt(self, payment_id: str, attempt_id: str) -> PaymentAttempt:
        payment = self._find_payment(payment_id)
        return self._find_attempt(payment.id, attempt_id)

    def list_payment_attempts(self, payment_id: str) -> list[PaymentAttempt]:
        return self._repository.list_attempts(self._find_payment(payment_id).id)

    def refresh_payment_attempt(
        self, payment_id: str, attempt_id: str
    ) -> PaymentAttempt:
        payment = self._find_payment(payment_id)
        attempt = self._find_attempt(payment.id, attempt_id)
        if attempt.status in {
            PaymentAttemptStatus.SUCCEEDED,
            PaymentAttemptStatus.FAILED,
        }:
            return attempt
        result = self._gateway.get_status(attempt.id)
        if attempt.status == PaymentAttemptStatus.PROCESSING:
            if result is None:
                result = self._gateway.submit(attempt.id, payment)
        elif result is None or result.outcome == GatewayOutcome.PENDING:
            return attempt
        return self._apply_gateway_result(
            payment, attempt, result, source="STATUS_ENQUIRY"
        )

    def _apply_gateway_result(
        self,
        payment: Payment,
        attempt: PaymentAttempt,
        result: GatewayResult,
        *,
        source: str,
    ) -> PaymentAttempt:
        now = self._clock()
        if result.outcome == GatewayOutcome.PENDING:
            if attempt.status == PaymentAttemptStatus.PENDING:
                return attempt
            updated_attempt = attempt.mark_pending(now, result.network_reference)
            updated_payment = payment.mark_pending(now)
        elif result.outcome == GatewayOutcome.SUCCEEDED:
            if result.network_reference is None:
                raise ValueError("Successful gateway result requires a reference")
            updated_attempt = attempt.mark_succeeded(now, result.network_reference)
            updated_payment = payment.mark_succeeded(now)
        else:
            if result.failure_code is None:
                raise ValueError("Failed gateway result requires a failure code")
            updated_attempt = attempt.mark_failed(
                now, result.failure_code, result.network_reference
            )
            updated_payment = payment.mark_failed(now)
        try:
            _, stored = self._repository.update_attempt_with_payment_status_change(
                previous_payment=payment,
                updated_payment=updated_payment,
                previous_attempt=attempt,
                updated_attempt=updated_attempt,
                source=source,
                reason_code=result.failure_code,
            )
            return stored
        except ConcurrentPaymentUpdate:
            return self._find_attempt(payment.id, str(attempt.id))

    def _find_payment(self, raw_id: str) -> Payment:
        try:
            payment_id = UUID(raw_id)
        except (ValueError, AttributeError) as exc:
            raise PaymentNotFound("Payment was not found") from exc
        payment = self._repository.find_by_id(payment_id)
        if payment is None:
            raise PaymentNotFound("Payment was not found")
        return payment

    def _find_attempt(self, payment_id: UUID, raw_id: str) -> PaymentAttempt:
        try:
            attempt_id = UUID(raw_id)
        except (ValueError, AttributeError) as exc:
            raise PaymentAttemptNotFound("Payment attempt was not found") from exc
        attempt = self._repository.find_attempt_by_id(payment_id, attempt_id)
        if attempt is None:
            raise PaymentAttemptNotFound("Payment attempt was not found")
        return attempt

    @staticmethod
    def _parse_idempotency_key(value: str) -> IdempotencyKey:
        try:
            return IdempotencyKey.parse(value)
        except ValueError as exc:
            raise InvalidIdempotencyKey(str(exc)) from exc

    @staticmethod
    def _parse_vpa(value: str) -> VPA:
        try:
            return VPA.parse(value)
        except ValueError as exc:
            raise InvalidVPA(str(exc)) from exc

    @staticmethod
    def _parse_money(amount: int, currency: str) -> Money:
        try:
            return Money(amount, currency)
        except ValueError as exc:
            raise InvalidAmount(str(exc)) from exc

    @staticmethod
    def _verify_same_request(
        payment: Payment, payer: VPA, payee: VPA, money: Money, note: str | None
    ) -> None:
        if not payment.has_same_creation_request(
            payer_vpa=payer, payee_vpa=payee, money=money, note=note
        ):
            raise IdempotencyConflict(
                "Idempotency key was already used with a different request"
            )
