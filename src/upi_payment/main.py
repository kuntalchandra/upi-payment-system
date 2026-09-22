from pathlib import Path

from upi_payment.api import create_app
from upi_payment.database import Database
from upi_payment.repository import SqlitePaymentRepository
from upi_payment.resolver import InMemoryVpaResolver
from upi_payment.service import PaymentService


database = Database(Path("data") / "upi_payments.db")
Path("data").mkdir(exist_ok=True)
database.initialise()

repository = SqlitePaymentRepository(database)
resolver = InMemoryVpaResolver(
    {
        "alice@bank": "Alice",
        "bob@bank": "Bob",
    }
)
service = PaymentService(repository, resolver)
app = create_app(service)

