import pytest
from uuid6 import uuid7

from datetime import UTC, datetime

from upi_payment.domain import (
    IdempotencyKey,
    Money,
    Payment,
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
    key = uuid7()
    assert IdempotencyKey.parse(str(key)).value == str(key)

    with pytest.raises(ValueError):
        IdempotencyKey.parse("not-a-uuid")


def payment(status: PaymentStatus = PaymentStatus.CREATED) -> Payment:
    now = datetime(2026, 9, 22, 10, 0, tzinfo=UTC)
    return Payment(
        id=uuid7(),
        idempotency_key=IdempotencyKey(str(uuid7())),
        payer_vpa=VPA.parse("alice@bank"),
        payee_vpa=VPA.parse("bob@bank"),
        payee_name="Bob",
        money=Money(50000, "INR"),
        note=None,
        status=status,
        created_at=now,
        updated_at=now,
    )


def test_payment_follows_valid_success_path() -> None:
    processing = payment().start_processing(
        datetime(2026, 9, 22, 10, 1, tzinfo=UTC)
    )
    succeeded = processing.mark_succeeded(
        datetime(2026, 9, 22, 10, 2, tzinfo=UTC),
        "SIM-REFERENCE",
    )

    assert processing.status == PaymentStatus.PROCESSING
    assert succeeded.status == PaymentStatus.SUCCEEDED
    assert succeeded.version == 2
    assert succeeded.completed_at is not None


def test_terminal_payment_rejects_another_transition() -> None:
    processing = payment().start_processing(
        datetime(2026, 9, 22, 10, 1, tzinfo=UTC)
    )
    succeeded = processing.mark_succeeded(
        datetime(2026, 9, 22, 10, 2, tzinfo=UTC),
        "SIM-REFERENCE",
    )

    with pytest.raises(ValueError):
        succeeded.mark_failed(
            datetime(2026, 9, 22, 10, 3, tzinfo=UTC),
            "DECLINED",
        )
