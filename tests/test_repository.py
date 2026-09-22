from datetime import UTC, datetime
from concurrent.futures import ThreadPoolExecutor

import pytest
from uuid6 import uuid7

from upi_payment.database import Database
from upi_payment.domain import IdempotencyKey, Money, Payment, PaymentStatus, VPA
from upi_payment.repository import SqlitePaymentRepository
from upi_payment.errors import ConcurrentPaymentUpdate


def test_creation_persists_payment_and_initial_history(database: Database) -> None:
    repository = SqlitePaymentRepository(database)
    now = datetime(2026, 9, 22, 10, 0, tzinfo=UTC)
    payment = Payment(
        id=uuid7(),
        idempotency_key=IdempotencyKey(str(uuid7())),
        payer_vpa=VPA.parse("alice@bank"),
        payee_vpa=VPA.parse("bob@bank"),
        payee_name="Bob",
        money=Money(50000, "INR"),
        note="Dinner",
        status=PaymentStatus.CREATED,
        created_at=now,
        updated_at=now,
    )

    stored, created = repository.create_or_get(payment)

    assert created is True
    assert stored == payment
    with database.connect() as connection:
        history = connection.execute(
            "SELECT * FROM payment_status_changes WHERE payment_id = ?",
            (str(payment.id),),
        ).fetchall()
    assert len(history) == 1
    assert history[0]["from_status"] is None
    assert history[0]["to_status"] == "CREATED"


def test_duplicate_idempotency_key_returns_existing_payment(
    database: Database,
) -> None:
    repository = SqlitePaymentRepository(database)
    now = datetime(2026, 9, 22, 10, 0, tzinfo=UTC)
    key = IdempotencyKey(str(uuid7()))
    original = Payment(
        id=uuid7(),
        idempotency_key=key,
        payer_vpa=VPA.parse("alice@bank"),
        payee_vpa=VPA.parse("bob@bank"),
        payee_name="Bob",
        money=Money(50000, "INR"),
        note=None,
        status=PaymentStatus.CREATED,
        created_at=now,
        updated_at=now,
    )
    repository.create_or_get(original)
    duplicate = Payment(
        id=uuid7(),
        idempotency_key=key,
        payer_vpa=original.payer_vpa,
        payee_vpa=original.payee_vpa,
        payee_name=original.payee_name,
        money=original.money,
        note=original.note,
        status=PaymentStatus.CREATED,
        created_at=now,
        updated_at=now,
    )

    stored, created = repository.create_or_get(duplicate)

    assert created is False
    assert stored.id == original.id


def test_concurrent_creation_keeps_one_payment(database: Database) -> None:
    repository = SqlitePaymentRepository(database)
    now = datetime(2026, 9, 22, 10, 0, tzinfo=UTC)
    key = IdempotencyKey(str(uuid7()))

    def create_payment() -> tuple[Payment, bool]:
        payment = Payment(
            id=uuid7(),
            idempotency_key=key,
            payer_vpa=VPA.parse("alice@bank"),
            payee_vpa=VPA.parse("bob@bank"),
            payee_name="Bob",
            money=Money(50000, "INR"),
            note=None,
            status=PaymentStatus.CREATED,
            created_at=now,
            updated_at=now,
        )
        return repository.create_or_get(payment)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: create_payment(), range(2)))

    assert sorted(created for _payment, created in results) == [False, True]
    assert len({payment.id for payment, _created in results}) == 1
    with database.connect() as connection:
        payment_count = connection.execute(
            "SELECT COUNT(*) FROM payments WHERE idempotency_key = ?",
            (key.value,),
        ).fetchone()[0]
        history_count = connection.execute(
            "SELECT COUNT(*) FROM payment_status_changes"
        ).fetchone()[0]
    assert payment_count == 1
    assert history_count == 1


def test_compare_and_set_persists_transition_and_history(
    database: Database,
) -> None:
    repository = SqlitePaymentRepository(database)
    now = datetime(2026, 9, 22, 10, 0, tzinfo=UTC)
    original = Payment(
        id=uuid7(),
        idempotency_key=IdempotencyKey(str(uuid7())),
        payer_vpa=VPA.parse("alice@bank"),
        payee_vpa=VPA.parse("bob@bank"),
        payee_name="Bob",
        money=Money(50000, "INR"),
        note=None,
        status=PaymentStatus.CREATED,
        created_at=now,
        updated_at=now,
    )
    repository.create_or_get(original)
    processing = original.start_processing(
        datetime(2026, 9, 22, 10, 1, tzinfo=UTC)
    )

    repository.update_with_status_change(
        previous=original,
        updated=processing,
        source="SUBMISSION",
    )

    stored = repository.find_by_id(original.id)
    assert stored is not None
    assert stored.status == PaymentStatus.PROCESSING
    assert stored.version == 1
    with database.connect() as connection:
        history = connection.execute(
            """
            SELECT from_status, to_status, source
            FROM payment_status_changes
            WHERE payment_id = ?
            ORDER BY id
            """,
            (str(original.id),),
        ).fetchall()
    assert [tuple(row) for row in history] == [
        (None, "CREATED", "CREATION"),
        ("CREATED", "PROCESSING", "SUBMISSION"),
    ]


def test_stale_update_is_rejected(database: Database) -> None:
    repository = SqlitePaymentRepository(database)
    now = datetime(2026, 9, 22, 10, 0, tzinfo=UTC)
    original = Payment(
        id=uuid7(),
        idempotency_key=IdempotencyKey(str(uuid7())),
        payer_vpa=VPA.parse("alice@bank"),
        payee_vpa=VPA.parse("bob@bank"),
        payee_name="Bob",
        money=Money(50000, "INR"),
        note=None,
        status=PaymentStatus.CREATED,
        created_at=now,
        updated_at=now,
    )
    repository.create_or_get(original)
    processing = original.start_processing(now)
    repository.update_with_status_change(
        previous=original,
        updated=processing,
        source="SUBMISSION",
    )

    with pytest.raises(ConcurrentPaymentUpdate):
        repository.update_with_status_change(
            previous=original,
            updated=processing,
            source="SUBMISSION",
        )
