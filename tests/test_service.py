from datetime import UTC, datetime

import pytest
from uuid6 import uuid7

from upi_payment.errors import IdempotencyConflict, PayeeNotFound
from upi_payment.repository import SqlitePaymentRepository
from upi_payment.resolver import InMemoryVpaResolver
from upi_payment.service import CreatePaymentCommand, PaymentService


def command(key: str, *, amount_minor: int = 50000) -> CreatePaymentCommand:
    return CreatePaymentCommand(
        idempotency_key=key,
        payer_vpa="alice@bank",
        payee_vpa="bob@bank",
        amount_minor=amount_minor,
        currency="INR",
        note="Dinner",
    )


def service(repository: SqlitePaymentRepository) -> PaymentService:
    return PaymentService(
        repository,
        InMemoryVpaResolver({"bob@bank": "Bob"}),
        clock=lambda: datetime(2026, 9, 22, 10, 0, tzinfo=UTC),
    )


def test_same_key_and_request_returns_existing_payment(
    repository: SqlitePaymentRepository,
) -> None:
    payment_service = service(repository)
    key = str(uuid7())

    first = payment_service.create_payment(command(key))
    replay = payment_service.create_payment(command(key))

    assert first.created is True
    assert replay.created is False
    assert replay.payment.id == first.payment.id


def test_same_key_with_different_request_is_rejected(
    repository: SqlitePaymentRepository,
) -> None:
    payment_service = service(repository)
    key = str(uuid7())
    payment_service.create_payment(command(key))

    with pytest.raises(IdempotencyConflict):
        payment_service.create_payment(command(key, amount_minor=60000))


def test_unresolved_payee_is_rejected(
    repository: SqlitePaymentRepository,
) -> None:
    payment_service = service(repository)
    missing_payee = CreatePaymentCommand(
        idempotency_key=str(uuid7()),
        payer_vpa="alice@bank",
        payee_vpa="unknown@bank",
        amount_minor=50000,
        currency="INR",
        note=None,
    )

    with pytest.raises(PayeeNotFound):
        payment_service.create_payment(missing_payee)

