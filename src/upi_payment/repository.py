from __future__ import annotations

import sqlite3
from uuid import UUID

from upi_payment.database import Database
from upi_payment.domain import (
    IdempotencyKey,
    Money,
    Payment,
    PaymentStatus,
    VPA,
)


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
        )

