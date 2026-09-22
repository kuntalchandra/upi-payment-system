from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from upi_payment.database import Database
from upi_payment.repository import SqlitePaymentRepository


@pytest.fixture
def database(tmp_path: Path) -> Database:
    database = Database(tmp_path / "test.db")
    database.initialise()
    return database


@pytest.fixture
def repository(database: Database) -> Iterator[SqlitePaymentRepository]:
    yield SqlitePaymentRepository(database)

