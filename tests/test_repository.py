from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
from uuid6 import uuid7

from upi_payment.database import Database
from upi_payment.domain import (
    IdempotencyKey,
    Money,
    Payment,
    PaymentAttempt,
    PaymentAttemptStatus,
    PaymentStatus,
    VPA,
)
from upi_payment.errors import ConcurrentPaymentUpdate
from upi_payment.repository import SqlitePaymentRepository


def make_payment(key=None):
    now = datetime(2026, 9, 22, 10, 0, tzinfo=UTC)
    return Payment(
        uuid7(),
        key or IdempotencyKey(str(uuid7())),
        VPA.parse("alice@bank"),
        VPA.parse("bob@bank"),
        "Bob",
        Money(50000, "INR"),
        "Dinner",
        PaymentStatus.CREATED,
        now,
        now,
    )


def test_creation_persists_payment_and_initial_history(database: Database) -> None:
    repository = SqlitePaymentRepository(database)
    payment = make_payment()
    stored, created = repository.create_or_get(payment)
    assert created and stored == payment
    with database.connect() as connection:
        row = connection.execute(
            "SELECT attempt_id, from_status, to_status FROM payment_status_changes"
        ).fetchone()
    assert tuple(row) == (None, None, "CREATED")


def test_concurrent_creation_keeps_one_payment(database: Database) -> None:
    repository = SqlitePaymentRepository(database)
    key = IdempotencyKey(str(uuid7()))
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _: repository.create_or_get(make_payment(key)), range(2)
            )
        )
    assert sorted(created for _, created in results) == [False, True]
    assert len({payment.id for payment, _ in results}) == 1


def test_duplicate_creation_key_returns_existing_payment(database: Database) -> None:
    repository = SqlitePaymentRepository(database)
    key = IdempotencyKey(str(uuid7()))
    first, _ = repository.create_or_get(make_payment(key))
    replay, created = repository.create_or_get(make_payment(key))
    assert created is False
    assert replay.id == first.id


def test_attempt_state_and_audit_are_updated_atomically(database: Database) -> None:
    repository = SqlitePaymentRepository(database)
    payment = make_payment()
    repository.create_or_get(payment)
    at = datetime(2026, 9, 22, 10, 1, tzinfo=UTC)
    attempt = PaymentAttempt(
        uuid7(),
        payment.id,
        IdempotencyKey(str(uuid7())),
        1,
        PaymentAttemptStatus.PROCESSING,
        at,
        at,
    )
    processing, stored_attempt = repository.start_attempt(
        previous_payment=payment,
        processing_payment=payment.start_processing(at),
        attempt=attempt,
    )
    failed_at = datetime(2026, 9, 22, 10, 2, tzinfo=UTC)
    repository.update_attempt_with_payment_status_change(
        previous_payment=processing,
        updated_payment=processing.mark_failed(failed_at),
        previous_attempt=stored_attempt,
        updated_attempt=stored_attempt.mark_failed(failed_at, "DECLINED"),
        source="ATTEMPT_SUBMISSION",
        reason_code="DECLINED",
    )
    with database.connect() as connection:
        history = connection.execute(
            """SELECT attempt_id, from_status, to_status, reason_code
               FROM payment_status_changes ORDER BY id"""
        ).fetchall()
    assert [tuple(row) for row in history] == [
        (None, None, "CREATED", None),
        (str(attempt.id), "CREATED", "PROCESSING", None),
        (str(attempt.id), "PROCESSING", "FAILED", "DECLINED"),
    ]


def test_stale_attempt_update_rolls_back_payment_update(database: Database) -> None:
    repository = SqlitePaymentRepository(database)
    payment = make_payment()
    repository.create_or_get(payment)
    at = datetime(2026, 9, 22, 10, 1, tzinfo=UTC)
    attempt = PaymentAttempt(
        uuid7(),
        payment.id,
        IdempotencyKey(str(uuid7())),
        1,
        PaymentAttemptStatus.PROCESSING,
        at,
        at,
    )
    processing, attempt = repository.start_attempt(
        previous_payment=payment,
        processing_payment=payment.start_processing(at),
        attempt=attempt,
    )
    updated_attempt = attempt.mark_pending(at)
    updated_payment = processing.mark_pending(at)
    repository.update_attempt_with_payment_status_change(
        previous_payment=processing,
        updated_payment=updated_payment,
        previous_attempt=attempt,
        updated_attempt=updated_attempt,
        source="STATUS_ENQUIRY",
    )
    with pytest.raises(ConcurrentPaymentUpdate):
        repository.update_attempt_with_payment_status_change(
            previous_payment=processing,
            updated_payment=updated_payment,
            previous_attempt=attempt,
            updated_attempt=updated_attempt,
            source="STATUS_ENQUIRY",
        )


def test_attempt_queries_preserve_sequence(database: Database) -> None:
    repository = SqlitePaymentRepository(database)
    payment = make_payment()
    repository.create_or_get(payment)
    at = datetime(2026, 9, 22, 10, 1, tzinfo=UTC)
    first = PaymentAttempt(
        uuid7(),
        payment.id,
        IdempotencyKey(str(uuid7())),
        1,
        PaymentAttemptStatus.PROCESSING,
        at,
        at,
    )
    processing, first = repository.start_attempt(
        previous_payment=payment,
        processing_payment=payment.start_processing(at),
        attempt=first,
    )
    failed_at = datetime(2026, 9, 22, 10, 2, tzinfo=UTC)
    failed_payment, _ = repository.update_attempt_with_payment_status_change(
        previous_payment=processing,
        updated_payment=processing.mark_failed(failed_at),
        previous_attempt=first,
        updated_attempt=first.mark_failed(failed_at, "DECLINED"),
        source="ATTEMPT_SUBMISSION",
    )
    second = PaymentAttempt(
        uuid7(),
        payment.id,
        IdempotencyKey(str(uuid7())),
        2,
        PaymentAttemptStatus.PROCESSING,
        at,
        at,
    )
    repository.start_attempt(
        previous_payment=failed_payment,
        processing_payment=failed_payment.start_processing(at),
        attempt=second,
    )
    assert [item.attempt_number for item in repository.list_attempts(payment.id)] == [
        1,
        2,
    ]
    assert repository.find_latest_attempt(payment.id).id == second.id
