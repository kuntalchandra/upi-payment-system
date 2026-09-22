from datetime import UTC, datetime

from fastapi.testclient import TestClient
from uuid6 import uuid7

from upi_payment.api import create_app
from upi_payment.repository import SqlitePaymentRepository
from upi_payment.resolver import InMemoryVpaResolver
from upi_payment.service import PaymentService


def client(repository: SqlitePaymentRepository) -> TestClient:
    service = PaymentService(
        repository,
        InMemoryVpaResolver({"bob@bank": "Bob"}),
        clock=lambda: datetime(2026, 9, 22, 10, 0, tzinfo=UTC),
    )
    return TestClient(create_app(service))


def request_body(amount_minor: int = 50000) -> dict[str, object]:
    return {
        "payerVpa": "alice@bank",
        "payeeVpa": "bob@bank",
        "amountMinor": amount_minor,
        "currency": "INR",
        "note": "Dinner",
    }


def test_create_and_replay_payment(repository: SqlitePaymentRepository) -> None:
    api = client(repository)
    key = str(uuid7())

    created = api.post(
        "/v1/payments",
        headers={"Idempotency-Key": key},
        json=request_body(),
    )
    replayed = api.post(
        "/v1/payments",
        headers={"Idempotency-Key": key},
        json=request_body(),
    )

    assert created.status_code == 201
    assert replayed.status_code == 200
    assert replayed.json()["id"] == created.json()["id"]
    assert created.json()["status"] == "CREATED"


def test_idempotency_conflict_has_stable_error(
    repository: SqlitePaymentRepository,
) -> None:
    api = client(repository)
    key = str(uuid7())
    api.post(
        "/v1/payments",
        headers={"Idempotency-Key": key},
        json=request_body(),
    )

    response = api.post(
        "/v1/payments",
        headers={"Idempotency-Key": key},
        json=request_body(amount_minor=60000),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_invalid_idempotency_key_has_stable_error(
    repository: SqlitePaymentRepository,
) -> None:
    response = client(repository).post(
        "/v1/payments",
        headers={"Idempotency-Key": "not-a-uuid"},
        json=request_body(),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_IDEMPOTENCY_KEY"


def test_missing_idempotency_key_has_stable_error(
    repository: SqlitePaymentRepository,
) -> None:
    response = client(repository).post(
        "/v1/payments",
        json=request_body(),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "MISSING_IDEMPOTENCY_KEY"
