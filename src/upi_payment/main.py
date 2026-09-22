from pathlib import Path

from upi_payment.api import create_app
from upi_payment.authorization import InMemoryAuthorizationVerifier
from upi_payment.database import Database
from upi_payment.repository import SqlitePaymentRepository
from upi_payment.gateway import InMemoryUpiGateway
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
authorization_verifier = InMemoryAuthorizationVerifier({"test-approved-token"})
gateway = InMemoryUpiGateway()
service = PaymentService(
    repository,
    resolver,
    authorization_verifier,
    gateway,
)
app = create_app(service)
