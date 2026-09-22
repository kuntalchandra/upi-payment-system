from datetime import UTC, datetime

import pytest
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


def test_vpa_is_normalised() -> None:
    assert VPA.parse(" Alice@Bank ").value == "alice@bank"


@pytest.mark.parametrize("value", ["alice", "@bank", "alice@", "alice @bank"])
def test_invalid_vpa_is_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        VPA.parse(value)


def test_money_requires_positive_inr_amount() -> None:
    with pytest.raises(ValueError):
        Money(0, "INR")
    with pytest.raises(ValueError):
        Money(100, "USD")


def test_idempotency_key_requires_uuidv7() -> None:
    assert IdempotencyKey.parse(str(uuid7()))
    with pytest.raises(ValueError):
        IdempotencyKey.parse("not-a-uuid")


def payment(status: PaymentStatus = PaymentStatus.CREATED) -> Payment:
    now = datetime(2026, 9, 22, 10, 0, tzinfo=UTC)
    return Payment(
        uuid7(),
        IdempotencyKey(str(uuid7())),
        VPA.parse("alice@bank"),
        VPA.parse("bob@bank"),
        "Bob",
        Money(50000, "INR"),
        None,
        status,
        now,
        now,
    )


def test_payment_can_retry_after_failure_but_success_is_terminal() -> None:
    at = datetime(2026, 9, 22, 10, 1, tzinfo=UTC)
    failed = payment().start_processing(at).mark_failed(at)
    retried = failed.start_processing(at)
    succeeded = retried.mark_succeeded(at)
    assert succeeded.status == PaymentStatus.SUCCEEDED
    with pytest.raises(ValueError):
        succeeded.start_processing(at)


def attempt() -> PaymentAttempt:
    now = datetime(2026, 9, 22, 10, 1, tzinfo=UTC)
    return PaymentAttempt(
        uuid7(),
        uuid7(),
        IdempotencyKey(str(uuid7())),
        1,
        PaymentAttemptStatus.PROCESSING,
        now,
        now,
    )


def test_attempt_follows_pending_success_path() -> None:
    at = datetime(2026, 9, 22, 10, 2, tzinfo=UTC)
    succeeded = attempt().mark_pending(at, "SIM-1").mark_succeeded(at, "SIM-1")
    assert succeeded.status == PaymentAttemptStatus.SUCCEEDED
    assert succeeded.completed_at == at


def test_failed_attempt_is_terminal() -> None:
    at = datetime(2026, 9, 22, 10, 2, tzinfo=UTC)
    failed = attempt().mark_failed(at, "DECLINED")
    with pytest.raises(ValueError):
        failed.mark_succeeded(at, "SIM-1")


def test_attempt_number_must_be_positive() -> None:
    now = datetime(2026, 9, 22, 10, 0, tzinfo=UTC)
    with pytest.raises(ValueError):
        PaymentAttempt(
            uuid7(),
            uuid7(),
            IdempotencyKey(str(uuid7())),
            0,
            PaymentAttemptStatus.PROCESSING,
            now,
            now,
        )


def test_created_payment_cannot_skip_processing() -> None:
    with pytest.raises(ValueError):
        payment().mark_succeeded(datetime(2026, 9, 22, 10, 1, tzinfo=UTC))


def test_pending_attempt_cannot_return_to_processing() -> None:
    at = datetime(2026, 9, 22, 10, 2, tzinfo=UTC)
    pending = attempt().mark_pending(at)
    with pytest.raises(ValueError):
        pending.mark_pending(at)
