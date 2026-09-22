from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from uuid6 import uuid7

from upi_payment.authorization import InMemoryAuthorizationVerifier
from upi_payment.domain import (
    IdempotencyKey,
    PaymentAttempt,
    PaymentAttemptStatus,
    PaymentStatus,
)
from upi_payment.errors import InvalidPaymentState, PaymentAuthorizationFailed
from upi_payment.gateway import InMemoryUpiGateway
from upi_payment.ports import GatewayOutcome, GatewayResult
from upi_payment.repository import SqlitePaymentRepository
from upi_payment.resolver import InMemoryVpaResolver
from upi_payment.service import CreatePaymentCommand, PaymentService


def build_service(repository, gateway):
    return PaymentService(
        repository,
        InMemoryVpaResolver({"bob@bank": "Bob"}),
        InMemoryAuthorizationVerifier({"test-approved-token"}),
        gateway,
        clock=lambda: datetime(2026, 9, 22, 10, 0, tzinfo=UTC),
    )


def create_payment(service):
    return service.create_payment(
        CreatePaymentCommand(
            str(uuid7()), "alice@bank", "bob@bank", 50000, "INR", "Dinner"
        )
    ).payment


def test_successful_attempt_is_idempotent(repository) -> None:
    gateway = InMemoryUpiGateway(GatewayOutcome.SUCCEEDED)
    service = build_service(repository, gateway)
    payment = create_payment(service)
    key = str(uuid7())
    first = service.create_payment_attempt(str(payment.id), key, "test-approved-token")
    replay = service.create_payment_attempt(str(payment.id), key, "test-approved-token")
    assert first.attempt.status == PaymentAttemptStatus.SUCCEEDED
    assert replay.attempt.id == first.attempt.id
    assert [first.created, replay.created] == [True, False]
    assert gateway.submission_count == 1


def test_failed_authorisation_creates_no_attempt(repository) -> None:
    gateway = InMemoryUpiGateway()
    service = build_service(repository, gateway)
    payment = create_payment(service)
    with pytest.raises(PaymentAuthorizationFailed):
        service.create_payment_attempt(str(payment.id), str(uuid7()), "wrong")
    assert service.list_payment_attempts(str(payment.id)) == []


def test_failure_allows_a_new_attempt(repository) -> None:
    gateway = InMemoryUpiGateway(GatewayOutcome.FAILED)
    service = build_service(repository, gateway)
    payment = create_payment(service)
    first = service.create_payment_attempt(
        str(payment.id), str(uuid7()), "test-approved-token"
    )
    second = service.create_payment_attempt(
        str(payment.id), str(uuid7()), "test-approved-token"
    )
    assert [first.attempt.attempt_number, second.attempt.attempt_number] == [1, 2]
    assert gateway.submission_count == 2


def test_pending_attempt_resolves_to_success(repository) -> None:
    gateway = InMemoryUpiGateway(GatewayOutcome.PENDING)
    service = build_service(repository, gateway)
    payment = create_payment(service)
    pending = service.create_payment_attempt(
        str(payment.id), str(uuid7()), "test-approved-token"
    )
    gateway.set_result(
        pending.attempt.id,
        GatewayResult(
            GatewayOutcome.SUCCEEDED, network_reference=f"SIM-{pending.attempt.id}"
        ),
    )
    resolved = service.refresh_payment_attempt(str(payment.id), str(pending.attempt.id))
    assert resolved.status == PaymentAttemptStatus.SUCCEEDED
    assert service.get_payment(str(payment.id)).status == PaymentStatus.SUCCEEDED


def test_processing_recovery_reuses_attempt_id(repository) -> None:
    gateway = InMemoryUpiGateway(GatewayOutcome.SUCCEEDED)
    service = build_service(repository, gateway)
    payment = create_payment(service)
    now = datetime(2026, 9, 22, 10, 0, tzinfo=UTC)
    attempt = PaymentAttempt(
        uuid7(),
        payment.id,
        IdempotencyKey(str(uuid7())),
        1,
        PaymentAttemptStatus.PROCESSING,
        now,
        now,
    )
    repository.start_attempt(
        previous_payment=payment,
        processing_payment=payment.start_processing(now),
        attempt=attempt,
    )
    recovered = service.refresh_payment_attempt(str(payment.id), str(attempt.id))
    assert recovered.network_reference == f"SIM-{attempt.id}"
    assert gateway.submission_count == 1


def test_new_attempt_is_rejected_while_pending(repository) -> None:
    service = build_service(repository, InMemoryUpiGateway(GatewayOutcome.PENDING))
    payment = create_payment(service)
    service.create_payment_attempt(str(payment.id), str(uuid7()), "test-approved-token")
    with pytest.raises(InvalidPaymentState):
        service.create_payment_attempt(
            str(payment.id), str(uuid7()), "test-approved-token"
        )


def test_concurrent_replay_calls_gateway_once(repository) -> None:
    gateway = InMemoryUpiGateway(GatewayOutcome.SUCCEEDED)
    service = build_service(repository, gateway)
    payment = create_payment(service)
    key = str(uuid7())

    def submit(_):
        return service.create_payment_attempt(
            str(payment.id), key, "test-approved-token"
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(submit, range(2)))
    assert len({result.attempt.id for result in results}) == 1
    assert gateway.submission_count == 1


def test_terminal_attempt_refresh_is_idempotent(repository) -> None:
    gateway = InMemoryUpiGateway(GatewayOutcome.SUCCEEDED)
    service = build_service(repository, gateway)
    payment = create_payment(service)
    result = service.create_payment_attempt(
        str(payment.id), str(uuid7()), "test-approved-token"
    )
    refreshed = service.refresh_payment_attempt(str(payment.id), str(result.attempt.id))
    assert refreshed == result.attempt
    assert gateway.submission_count == 1
