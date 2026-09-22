from datetime import UTC, datetime
from concurrent.futures import ThreadPoolExecutor

import pytest
from uuid6 import uuid7

from upi_payment.authorization import InMemoryAuthorizationVerifier
from upi_payment.domain import PaymentStatus
from upi_payment.errors import (
    InvalidPaymentState,
    PaymentAuthorizationFailed,
)
from upi_payment.gateway import InMemoryUpiGateway
from upi_payment.ports import GatewayOutcome, GatewayResult
from upi_payment.repository import SqlitePaymentRepository
from upi_payment.resolver import InMemoryVpaResolver
from upi_payment.service import CreatePaymentCommand, PaymentService


def build_service(
    repository: SqlitePaymentRepository,
    gateway: InMemoryUpiGateway,
) -> PaymentService:
    return PaymentService(
        repository,
        InMemoryVpaResolver({"bob@bank": "Bob"}),
        InMemoryAuthorizationVerifier({"test-approved-token"}),
        gateway,
        clock=lambda: datetime(2026, 9, 22, 10, 0, tzinfo=UTC),
    )


def create_payment(service: PaymentService):
    return service.create_payment(
        CreatePaymentCommand(
            idempotency_key=str(uuid7()),
            payer_vpa="alice@bank",
            payee_vpa="bob@bank",
            amount_minor=50000,
            currency="INR",
            note="Dinner",
        )
    ).payment


def test_successful_submission_is_idempotent(
    repository: SqlitePaymentRepository,
) -> None:
    gateway = InMemoryUpiGateway(GatewayOutcome.SUCCEEDED)
    service = build_service(repository, gateway)
    payment = create_payment(service)

    submitted = service.submit_payment(
        str(payment.id), "test-approved-token"
    )
    replayed = service.submit_payment(
        str(payment.id), "test-approved-token"
    )

    assert submitted.status == PaymentStatus.SUCCEEDED
    assert replayed.status == PaymentStatus.SUCCEEDED
    assert replayed.network_reference == submitted.network_reference
    assert gateway.submission_count == 1


def test_failed_authorisation_leaves_payment_created(
    repository: SqlitePaymentRepository,
) -> None:
    gateway = InMemoryUpiGateway()
    service = build_service(repository, gateway)
    payment = create_payment(service)

    with pytest.raises(PaymentAuthorizationFailed):
        service.submit_payment(str(payment.id), "wrong-token")

    assert service.get_payment(str(payment.id)).status == PaymentStatus.CREATED
    assert gateway.submission_count == 0


def test_definitive_gateway_failure_is_terminal(
    repository: SqlitePaymentRepository,
) -> None:
    gateway = InMemoryUpiGateway(GatewayOutcome.FAILED)
    service = build_service(repository, gateway)
    payment = create_payment(service)

    failed = service.submit_payment(str(payment.id), "test-approved-token")
    replayed = service.submit_payment(str(payment.id), "test-approved-token")

    assert failed.status == PaymentStatus.FAILED
    assert failed.failure_code == "SIMULATED_DECLINE"
    assert replayed.status == PaymentStatus.FAILED
    assert gateway.submission_count == 1


def test_pending_payment_resolves_to_success(
    repository: SqlitePaymentRepository,
) -> None:
    gateway = InMemoryUpiGateway(GatewayOutcome.PENDING)
    service = build_service(repository, gateway)
    payment = create_payment(service)
    pending = service.submit_payment(str(payment.id), "test-approved-token")
    gateway.set_result(
        payment.id,
        GatewayResult(
            GatewayOutcome.SUCCEEDED,
            network_reference=f"SIM-{payment.id}",
        ),
    )

    resolved = service.refresh_status(str(payment.id))

    assert pending.status == PaymentStatus.PENDING
    assert resolved.status == PaymentStatus.SUCCEEDED
    assert gateway.submission_count == 1


def test_processing_recovery_resubmits_when_gateway_does_not_know_payment(
    repository: SqlitePaymentRepository,
) -> None:
    gateway = InMemoryUpiGateway(GatewayOutcome.SUCCEEDED)
    service = build_service(repository, gateway)
    payment = create_payment(service)
    processing = payment.start_processing(
        datetime(2026, 9, 22, 10, 0, tzinfo=UTC)
    )
    repository.update_with_status_change(
        previous=payment,
        updated=processing,
        source="SUBMISSION",
    )

    recovered = service.refresh_status(str(payment.id))

    assert recovered.status == PaymentStatus.SUCCEEDED
    assert gateway.submission_count == 1


def test_created_payment_cannot_refresh_status(
    repository: SqlitePaymentRepository,
) -> None:
    service = build_service(repository, InMemoryUpiGateway())
    payment = create_payment(service)

    with pytest.raises(InvalidPaymentState):
        service.refresh_status(str(payment.id))


def test_concurrent_submission_calls_gateway_once(
    repository: SqlitePaymentRepository,
) -> None:
    gateway = InMemoryUpiGateway(GatewayOutcome.SUCCEEDED)
    service = build_service(repository, gateway)
    payment = create_payment(service)

    def submit(_index: int):
        return service.submit_payment(str(payment.id), "test-approved-token")

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(submit, range(2)))

    assert gateway.submission_count == 1
    assert service.get_payment(str(payment.id)).status == PaymentStatus.SUCCEEDED
    assert all(
        result.status in {PaymentStatus.PROCESSING, PaymentStatus.SUCCEEDED}
        for result in results
    )


def test_gateway_is_idempotent_under_concurrent_direct_retries(
    repository: SqlitePaymentRepository,
) -> None:
    gateway = InMemoryUpiGateway(GatewayOutcome.SUCCEEDED)
    service = build_service(repository, gateway)
    payment = create_payment(service)

    def submit(_index: int):
        return gateway.submit(payment.id, payment)

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(submit, range(4)))

    assert gateway.submission_count == 1
    assert len(set(results)) == 1
