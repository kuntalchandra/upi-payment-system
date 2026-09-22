from __future__ import annotations

import sqlite3
from datetime import datetime
from uuid import UUID

from upi_payment.database import Database
from upi_payment.domain import (
    IdempotencyKey,
    Money,
    Payment,
    PaymentStatus,
    VPA,
)
from upi_payment.errors import ConcurrentPaymentUpdate


class SqlitePaymentRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    def find_by_idempotency_key(
        self, idempotency_key: IdempotencyKey
    ) -> Payment | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM payments WHERE idempotency_key = ?",
                (idempotency_key.value,),
            ).fetchone()
        return self._to_payment(row) if row is not None else None

    def create_or_get(self, payment: Payment) -> tuple[Payment, bool]:
        with self._database.connect() as connection:
            try:
                with connection:
                    connection.execute(
                        """
                        INSERT INTO payments (
                            id, idempotency_key, payer_vpa, payee_vpa,
                            payee_name, amount_minor, currency, note, status,
                            created_at, updated_at, version
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
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
                        """
                        INSERT INTO payment_status_changes (
                            payment_id, from_status, to_status, source,
                            reason_code, created_at
                        ) VALUES (?, NULL, ?, 'CREATION', NULL, ?)
                        """,
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
                "SELECT * FROM payments WHERE id = ?",
                (str(payment_id),),
            ).fetchone()
        return self._to_payment(row) if row is not None else None

    def update_with_status_change(
        self,
        *,
        previous: Payment,
        updated: Payment,
        source: str,
        reason_code: str | None = None,
    ) -> Payment:
        with self._database.connect() as connection:
            with connection:
                cursor = connection.execute(
                    """
                    UPDATE payments
                    SET status = ?,
                        network_reference = ?,
                        failure_code = ?,
                        submitted_at = ?,
                        completed_at = ?,
                        updated_at = ?,
                        version = ?
                    WHERE id = ? AND version = ? AND status = ?
                    """,
                    (
                        updated.status.value,
                        updated.network_reference,
                        updated.failure_code,
                        self._datetime_value(updated.submitted_at),
                        self._datetime_value(updated.completed_at),
                        updated.updated_at.isoformat(),
                        updated.version,
                        str(previous.id),
                        previous.version,
                        previous.status.value,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ConcurrentPaymentUpdate(
                        "Payment was updated by another request"
                    )
                connection.execute(
                    """
                    INSERT INTO payment_status_changes (
                        payment_id, from_status, to_status, source,
                        reason_code, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(previous.id),
                        previous.status.value,
                        updated.status.value,
                        source,
                        reason_code,
                        updated.updated_at.isoformat(),
                    ),
                )
        return updated

    @staticmethod
    def _to_payment(row: sqlite3.Row) -> Payment:
        from datetime import datetime

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
            network_reference=row["network_reference"],
            failure_code=row["failure_code"],
            submitted_at=SqlitePaymentRepository._parse_datetime(
                row["submitted_at"]
            ),
            completed_at=SqlitePaymentRepository._parse_datetime(
                row["completed_at"]
            ),
        )

    @staticmethod
    def _datetime_value(value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None

    @staticmethod
    def _parse_datetime(value: str | None) -> datetime | None:
        if value is None:
            return None
        return datetime.fromisoformat(value)
