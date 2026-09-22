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
            connection.executescript(
                """
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
                    version INTEGER NOT NULL CHECK (version >= 0),
                    network_reference TEXT,
                    failure_code TEXT,
                    submitted_at TEXT,
                    completed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS payment_status_changes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    payment_id TEXT NOT NULL,
                    from_status TEXT,
                    to_status TEXT NOT NULL,
                    source TEXT NOT NULL,
                    reason_code TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (payment_id) REFERENCES payments(id)
                );

                CREATE INDEX IF NOT EXISTS ix_payment_status_changes_payment_time
                ON payment_status_changes(payment_id, created_at);
                """
            )
            self._add_missing_payment_columns(connection)

    @staticmethod
    def _add_missing_payment_columns(connection: sqlite3.Connection) -> None:
        existing = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(payments)").fetchall()
        }
        additions = {
            "network_reference": "TEXT",
            "failure_code": "TEXT",
            "submitted_at": "TEXT",
            "completed_at": "TEXT",
        }
        for name, column_type in additions.items():
            if name not in existing:
                connection.execute(
                    f"ALTER TABLE payments ADD COLUMN {name} {column_type}"
                )
