from datetime import UTC, datetime
from uuid import UUID

from fastapi.testclient import TestClient
from uuid6 import uuid7

from upi_payment.api import create_app
from upi_payment.authorization import InMemoryAuthorizationVerifier
from upi_payment.gateway import InMemoryUpiGateway
from upi_payment.ports import GatewayOutcome, GatewayResult
from upi_payment.repository import SqlitePaymentRepository
from upi_payment.resolver import InMemoryVpaResolver
from upi_payment.service import PaymentService


def client(
    repository: SqlitePaymentRepository,
    gateway: InMemoryUpiGateway | None = None,
) -> TestClient:
    service = PaymentService(
        repository,
        InMemoryVpaResolver({"bob@bank": "Bob"}),
        InMemoryAuthorizationVerifier({"test-approved-token"}),
        gateway or InMemoryUpiGateway(GatewayOutcome.SUCCEEDED),
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


def test_create_retrieve_and_submit_payment(
    repository: SqlitePaymentRepository,
) -> None:
    api = client(repository)
    created = api.post(
        "/v1/payments",
        headers={"Idempotency-Key": str(uuid7())},
        json=request_body(),
    )
    payment_id = created.json()["id"]

    retrieved = api.get(f"/v1/payments/{payment_id}")
    submitted = api.post(
        f"/v1/payments/{payment_id}/submit",
        json={"authorizationToken": "test-approved-token"},
    )

    assert retrieved.status_code == 200
    assert retrieved.json()["status"] == "CREATED"
    assert submitted.status_code == 200
    assert submitted.json()["status"] == "SUCCEEDED"
    assert submitted.json()["networkReference"] is not None


def test_rejected_authorisation_has_stable_error(
    repository: SqlitePaymentRepository,
) -> None:
    api = client(repository)
    created = api.post(
        "/v1/payments",
        headers={"Idempotency-Key": str(uuid7())},
        json=request_body(),
    )

    response = api.post(
        f"/v1/payments/{created.json()['id']}/submit",
        json={"authorizationToken": "wrong-token"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PAYMENT_AUTHORIZATION_FAILED"


def test_pending_payment_refreshes_to_success(
    repository: SqlitePaymentRepository,
) -> None:
    gateway = InMemoryUpiGateway(GatewayOutcome.PENDING)
    api = client(repository, gateway)
    created = api.post(
        "/v1/payments",
        headers={"Idempotency-Key": str(uuid7())},
        json=request_body(),
    )
    payment_id = created.json()["id"]
    submitted = api.post(
        f"/v1/payments/{payment_id}/submit",
        json={"authorizationToken": "test-approved-token"},
    )
    gateway.set_result(
        UUID(payment_id),
        GatewayResult(
            GatewayOutcome.SUCCEEDED,
            network_reference=f"SIM-{payment_id}",
        ),
    )

    refreshed = api.post(f"/v1/payments/{payment_id}/refresh-status")

    assert submitted.status_code == 202
    assert submitted.json()["status"] == "PENDING"
    assert refreshed.status_code == 200
    assert refreshed.json()["status"] == "SUCCEEDED"


def test_unknown_payment_returns_stable_error(
    repository: SqlitePaymentRepository,
) -> None:
    response = client(repository).get(f"/v1/payments/{uuid7()}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "PAYMENT_NOT_FOUND"
