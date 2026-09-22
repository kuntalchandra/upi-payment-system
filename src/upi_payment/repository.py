from __future__ import annotations

import sqlite3
from datetime import datetime
from uuid import UUID

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


class SqlitePaymentRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def find_by_idempotency_key(self, key: IdempotencyKey) -> Payment | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM payments WHERE idempotency_key = ?", (key.value,)
            ).fetchone()
        return self._to_payment(row) if row else None

    def create_or_get(self, payment: Payment) -> tuple[Payment, bool]:
        with self._database.connect() as connection:
            try:
                with connection:
                    connection.execute(
                        """INSERT INTO payments (
                            id, idempotency_key, payer_vpa, payee_vpa, payee_name,
                            amount_minor, currency, note, status, created_at,
                            updated_at, version
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            str(payment.id),
                            payment.idempotency_key.value,
                            payment.payer_vpa.value,
                            payment.payee_vpa.value,
                            payment.payee_name,
                            payment.money.amount_minor,
                            payment.money.currency,
                            payment.note,
                            payment.status.value,
                            payment.created_at.isoformat(),
                            payment.updated_at.isoformat(),
                            payment.version,
                        ),
                    )
                    connection.execute(
                        """INSERT INTO payment_status_changes (
                            payment_id, attempt_id, from_status, to_status,
                            source, reason_code, created_at
                        ) VALUES (?, NULL, NULL, ?, 'CREATION', NULL, ?)""",
                        (
                            str(payment.id),
                            payment.status.value,
                            payment.created_at.isoformat(),
                        ),
                    )
                return payment, True
            except sqlite3.IntegrityError:
                row = connection.execute(
                    "SELECT * FROM payments WHERE idempotency_key = ?",
                    (payment.idempotency_key.value,),
                ).fetchone()
                if row is None:
                    raise
                return self._to_payment(row), False

    def find_by_id(self, payment_id: UUID) -> Payment | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM payments WHERE id = ?", (str(payment_id),)
            ).fetchone()
        return self._to_payment(row) if row else None

    def find_attempt_by_idempotency_key(
        self, payment_id: UUID, key: IdempotencyKey
    ) -> PaymentAttempt | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM payment_attempts WHERE payment_id = ? AND idempotency_key = ?",
                (str(payment_id), key.value),
            ).fetchone()
        return self._to_attempt(row) if row else None

    def find_attempt_by_id(
        self, payment_id: UUID, attempt_id: UUID
    ) -> PaymentAttempt | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM payment_attempts WHERE payment_id = ? AND id = ?",
                (str(payment_id), str(attempt_id)),
            ).fetchone()
        return self._to_attempt(row) if row else None

    def find_latest_attempt(self, payment_id: UUID) -> PaymentAttempt | None:
        with self._database.connect() as connection:
            row = connection.execute(
                """SELECT * FROM payment_attempts WHERE payment_id = ?
                   ORDER BY attempt_number DESC LIMIT 1""",
                (str(payment_id),),
            ).fetchone()
        return self._to_attempt(row) if row else None

    def list_attempts(self, payment_id: UUID) -> list[PaymentAttempt]:
        with self._database.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM payment_attempts WHERE payment_id = ?
                   ORDER BY attempt_number""",
                (str(payment_id),),
            ).fetchall()
        return [self._to_attempt(row) for row in rows]

    def next_attempt_number(self, payment_id: UUID) -> int:
        with self._database.connect() as connection:
            row = connection.execute(
                """SELECT COALESCE(MAX(attempt_number), 0) + 1
                   FROM payment_attempts WHERE payment_id = ?""",
                (str(payment_id),),
            ).fetchone()
        return int(row[0])

    def start_attempt(
        self,
        *,
        previous_payment: Payment,
        processing_payment: Payment,
        attempt: PaymentAttempt,
    ) -> tuple[Payment, PaymentAttempt]:
        with self._database.connect() as connection:
            with connection:
                cursor = connection.execute(
                    """UPDATE payments SET status = ?, updated_at = ?, version = ?
                       WHERE id = ? AND version = ? AND status = ?""",
                    (
                        processing_payment.status.value,
                        processing_payment.updated_at.isoformat(),
                        processing_payment.version,
                        str(previous_payment.id),
                        previous_payment.version,
                        previous_payment.status.value,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ConcurrentPaymentUpdate(
                        "Payment was updated by another request"
                    )
                connection.execute(
                    """INSERT INTO payment_attempts (
                        id, payment_id, idempotency_key, attempt_number, status,
                        network_reference, failure_code, created_at, updated_at,
                        completed_at, version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(attempt.id),
                        str(attempt.payment_id),
                        attempt.idempotency_key.value,
                        attempt.attempt_number,
                        attempt.status.value,
                        attempt.network_reference,
                        attempt.failure_code,
                        attempt.created_at.isoformat(),
                        attempt.updated_at.isoformat(),
                        self._datetime_value(attempt.completed_at),
                        attempt.version,
                    ),
                )
                connection.execute(
                    """INSERT INTO payment_status_changes (
                        payment_id, attempt_id, from_status, to_status,
                        source, reason_code, created_at
                    ) VALUES (?, ?, ?, ?, 'ATTEMPT_SUBMISSION', NULL, ?)""",
                    (
                        str(previous_payment.id),
                        str(attempt.id),
                        previous_payment.status.value,
                        processing_payment.status.value,
                        processing_payment.updated_at.isoformat(),
                    ),
                )
        return processing_payment, attempt

    def update_attempt_with_payment_status_change(
        self,
        *,
        previous_payment: Payment,
        updated_payment: Payment,
        previous_attempt: PaymentAttempt,
        updated_attempt: PaymentAttempt,
        source: str,
        reason_code: str | None = None,
    ) -> tuple[Payment, PaymentAttempt]:
        with self._database.connect() as connection:
            with connection:
                attempt_cursor = connection.execute(
                    """UPDATE payment_attempts
                       SET status = ?, network_reference = ?, failure_code = ?,
                           updated_at = ?, completed_at = ?, version = ?
                       WHERE id = ? AND payment_id = ? AND version = ? AND status = ?""",
                    (
                        updated_attempt.status.value,
                        updated_attempt.network_reference,
                        updated_attempt.failure_code,
                        updated_attempt.updated_at.isoformat(),
                        self._datetime_value(updated_attempt.completed_at),
                        updated_attempt.version,
                        str(previous_attempt.id),
                        str(previous_attempt.payment_id),
                        previous_attempt.version,
                        previous_attempt.status.value,
                    ),
                )
                if attempt_cursor.rowcount != 1:
                    raise ConcurrentPaymentUpdate(
                        "Payment attempt was updated by another request"
                    )
                payment_cursor = connection.execute(
                    """UPDATE payments SET status = ?, updated_at = ?, version = ?
                       WHERE id = ? AND version = ? AND status = ?""",
                    (
                        updated_payment.status.value,
                        updated_payment.updated_at.isoformat(),
                        updated_payment.version,
                        str(previous_payment.id),
                        previous_payment.version,
                        previous_payment.status.value,
                    ),
                )
                if payment_cursor.rowcount != 1:
                    raise ConcurrentPaymentUpdate(
                        "Payment was updated by another request"
                    )
                connection.execute(
                    """INSERT INTO payment_status_changes (
                        payment_id, attempt_id, from_status, to_status,
                        source, reason_code, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(previous_payment.id),
                        str(previous_attempt.id),
                        previous_payment.status.value,
                        updated_payment.status.value,
                        source,
                        reason_code,
                        updated_payment.updated_at.isoformat(),
                    ),
                )
        return updated_payment, updated_attempt

    @staticmethod
    def _to_payment(row: sqlite3.Row) -> Payment:
        return Payment(
            id=UUID(row["id"]),
            idempotency_key=IdempotencyKey(row["idempotency_key"]),
            payer_vpa=VPA(row["payer_vpa"]),
            payee_vpa=VPA(row["payee_vpa"]),
            payee_name=row["payee_name"],
            money=Money(row["amount_minor"], row["currency"]),
            note=row["note"],
            status=PaymentStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            version=row["version"],
        )

    @staticmethod
    def _to_attempt(row: sqlite3.Row) -> PaymentAttempt:
        return PaymentAttempt(
            id=UUID(row["id"]),
            payment_id=UUID(row["payment_id"]),
            idempotency_key=IdempotencyKey(row["idempotency_key"]),
            attempt_number=row["attempt_number"],
            status=PaymentAttemptStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            version=row["version"],
            network_reference=row["network_reference"],
            failure_code=row["failure_code"],
            completed_at=SqlitePaymentRepository._parse_datetime(row["completed_at"]),
        )

    @staticmethod
    def _datetime_value(value: datetime | None) -> str | None:
        return value.isoformat() if value else None

    @staticmethod
    def _parse_datetime(value: str | None) -> datetime | None:
        return datetime.fromisoformat(value) if value else None
