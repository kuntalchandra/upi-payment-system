from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
from typing import Iterator


class Database:
    def __init__(self, path: str | Path) -> None:
        self._path = str(path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
        finally:
            connection.close()

    def initialise(self) -> None:
        with self.connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS payments (
                    id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    payer_vpa TEXT NOT NULL,
                    payee_vpa TEXT NOT NULL,
                    payee_name TEXT NOT NULL,
                    amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
                    currency TEXT NOT NULL CHECK (currency = 'INR'),
                    note TEXT,
                    status TEXT NOT NULL CHECK (
                        status IN (
                            'CREATED',
                            'PROCESSING',
                            'PENDING',
                            'SUCCEEDED',
                            'FAILED'
                        )
                    ),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    version INTEGER NOT NULL CHECK (version >= 0)
                );

                CREATE TABLE IF NOT EXISTS payment_attempts (
                    id TEXT PRIMARY KEY,
                    payment_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
                    status TEXT NOT NULL CHECK (
                        status IN ('PROCESSING', 'PENDING', 'SUCCEEDED', 'FAILED')
                    ),
                    network_reference TEXT,
                    failure_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    version INTEGER NOT NULL CHECK (version >= 0),
                    FOREIGN KEY (payment_id) REFERENCES payments(id),
                    UNIQUE (payment_id, idempotency_key),
                    UNIQUE (payment_id, attempt_number)
                );

                CREATE TABLE IF NOT EXISTS payment_status_changes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    payment_id TEXT NOT NULL,
                    attempt_id TEXT,
                    from_status TEXT,
                    to_status TEXT NOT NULL,
                    source TEXT NOT NULL,
                    reason_code TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (payment_id) REFERENCES payments(id),
                    FOREIGN KEY (attempt_id) REFERENCES payment_attempts(id)
                );

                CREATE UNIQUE INDEX IF NOT EXISTS ux_payment_attempts_one_active
                ON payment_attempts(payment_id)
                WHERE status IN ('PROCESSING', 'PENDING');

                CREATE INDEX IF NOT EXISTS ix_payment_attempts_payment_number
                ON payment_attempts(payment_id, attempt_number);

                CREATE INDEX IF NOT EXISTS ix_payment_status_changes_payment_time
                ON payment_status_changes(payment_id, created_at);
                """)
            self._add_missing_status_change_columns(connection)
            connection.execute("""
                CREATE INDEX IF NOT EXISTS ix_payment_status_changes_attempt_time
                ON payment_status_changes(attempt_id, created_at)
                """)

    @staticmethod
    def _add_missing_status_change_columns(
        connection: sqlite3.Connection,
    ) -> None:
        existing = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(payment_status_changes)"
            ).fetchall()
        }
        if "attempt_id" not in existing:
            connection.execute(
                "ALTER TABLE payment_status_changes ADD COLUMN attempt_id TEXT"
            )
