import pytest
from uuid6 import uuid7

from upi_payment.domain import IdempotencyKey, Money, VPA


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

