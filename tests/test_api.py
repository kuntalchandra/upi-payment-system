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


def client(repository, gateway=None):
    service = PaymentService(
        repository,
        InMemoryVpaResolver({"bob@bank": "Bob"}),
        InMemoryAuthorizationVerifier({"test-approved-token"}),
        gateway or InMemoryUpiGateway(),
        clock=lambda: datetime(2026, 9, 22, 10, 0, tzinfo=UTC),
    )
    return TestClient(create_app(service))


def body(amount=50000):
    return {
        "payerVpa": "alice@bank",
        "payeeVpa": "bob@bank",
        "amountMinor": amount,
        "currency": "INR",
        "note": "Dinner",
    }


def create_payment(api):
    return api.post(
        "/v1/payments", headers={"Idempotency-Key": str(uuid7())}, json=body()
    )


def test_create_and_replay_payment(repository) -> None:
    api = client(repository)
    key = str(uuid7())
    first = api.post("/v1/payments", headers={"Idempotency-Key": key}, json=body())
    replay = api.post("/v1/payments", headers={"Idempotency-Key": key}, json=body())
    assert [first.status_code, replay.status_code] == [201, 200]
    assert first.json()["id"] == replay.json()["id"]


def test_payment_creation_validation_errors(repository) -> None:
    api = client(repository)
    assert api.post("/v1/payments", json=body()).status_code == 400
    assert (
        api.post(
            "/v1/payments", headers={"Idempotency-Key": "bad"}, json=body()
        ).status_code
        == 422
    )


def test_payment_creation_idempotency_conflict(repository) -> None:
    api = client(repository)
    key = str(uuid7())
    api.post("/v1/payments", headers={"Idempotency-Key": key}, json=body())
    response = api.post(
        "/v1/payments", headers={"Idempotency-Key": key}, json=body(60000)
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_create_replay_and_list_attempt(repository) -> None:
    gateway = InMemoryUpiGateway(GatewayOutcome.SUCCEEDED)
    api = client(repository, gateway)
    payment_id = create_payment(api).json()["id"]
    key = str(uuid7())
    first = api.post(
        f"/v1/payments/{payment_id}/attempts",
        headers={"Idempotency-Key": key},
        json={"authorizationToken": "test-approved-token"},
    )
    replay = api.post(
        f"/v1/payments/{payment_id}/attempts",
        headers={"Idempotency-Key": key},
        json={"authorizationToken": "test-approved-token"},
    )
    attempts = api.get(f"/v1/payments/{payment_id}/attempts")
    assert [first.status_code, replay.status_code] == [201, 200]
    assert first.json()["id"] == replay.json()["id"]
    assert len(attempts.json()) == 1


def test_attempt_can_be_retrieved(repository) -> None:
    api = client(repository)
    payment_id = create_payment(api).json()["id"]
    created = api.post(
        f"/v1/payments/{payment_id}/attempts",
        headers={"Idempotency-Key": str(uuid7())},
        json={"authorizationToken": "test-approved-token"},
    )
    retrieved = api.get(f"/v1/payments/{payment_id}/attempts/{created.json()['id']}")
    assert retrieved.status_code == 200
    assert retrieved.json() == created.json()


def test_rejected_authorisation_has_stable_error(repository) -> None:
    api = client(repository)
    payment_id = create_payment(api).json()["id"]
    response = api.post(
        f"/v1/payments/{payment_id}/attempts",
        headers={"Idempotency-Key": str(uuid7())},
        json={"authorizationToken": "wrong-token"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PAYMENT_AUTHORIZATION_FAILED"


def test_failed_payment_can_create_second_attempt(repository) -> None:
    api = client(repository, InMemoryUpiGateway(GatewayOutcome.FAILED))
    payment_id = create_payment(api).json()["id"]
    responses = [
        api.post(
            f"/v1/payments/{payment_id}/attempts",
            headers={"Idempotency-Key": str(uuid7())},
            json={"authorizationToken": "test-approved-token"},
        )
        for _ in range(2)
    ]
    assert [item.json()["attemptNumber"] for item in responses] == [1, 2]


def test_pending_attempt_refreshes_and_payment_read_is_derived(repository) -> None:
    gateway = InMemoryUpiGateway(GatewayOutcome.PENDING)
    api = client(repository, gateway)
    payment_id = create_payment(api).json()["id"]
    pending = api.post(
        f"/v1/payments/{payment_id}/attempts",
        headers={"Idempotency-Key": str(uuid7())},
        json={"authorizationToken": "test-approved-token"},
    )
    attempt_id = pending.json()["id"]
    gateway.set_result(
        UUID(attempt_id),
        GatewayResult(GatewayOutcome.SUCCEEDED, network_reference=f"SIM-{attempt_id}"),
    )
    refreshed = api.post(
        f"/v1/payments/{payment_id}/attempts/{attempt_id}/refresh-status"
    )
    payment = api.get(f"/v1/payments/{payment_id}").json()
    assert pending.status_code == 202 and refreshed.status_code == 200
    assert payment["status"] == "SUCCEEDED"
    assert payment["networkReference"] == f"SIM-{attempt_id}"
    assert payment["submittedAt"] == pending.json()["createdAt"]


def test_attempt_errors_are_stable(repository) -> None:
    api = client(repository)
    payment_id = create_payment(api).json()["id"]
    missing_key = api.post(
        f"/v1/payments/{payment_id}/attempts",
        json={"authorizationToken": "test-approved-token"},
    )
    unknown = api.get(f"/v1/payments/{payment_id}/attempts/{uuid7()}")
    assert missing_key.json()["error"]["code"] == "MISSING_IDEMPOTENCY_KEY"
    assert unknown.json()["error"]["code"] == "PAYMENT_ATTEMPT_NOT_FOUND"


def test_unknown_payment_has_stable_error(repository) -> None:
    response = client(repository).get(f"/v1/payments/{uuid7()}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "PAYMENT_NOT_FOUND"
