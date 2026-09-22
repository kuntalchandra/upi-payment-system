from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from upi_payment.errors import (
    IdempotencyConflict,
    InvalidAmount,
    InvalidIdempotencyKey,
    InvalidVPA,
    MissingIdempotencyKey,
    PayeeNotFound,
    PaymentError,
    SamePayerAndPayee,
)
from upi_payment.service import CreatePaymentCommand, PaymentService


class CreatePaymentRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    payer_vpa: str = Field(alias="payerVpa")
    payee_vpa: str = Field(alias="payeeVpa")
    amount_minor: int = Field(alias="amountMinor")
    currency: str
    note: str | None = None


class PaymentResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    id: UUID
    payer_vpa: str = Field(alias="payerVpa")
    payee_vpa: str = Field(alias="payeeVpa")
    payee_name: str = Field(alias="payeeName")
    amount_minor: int = Field(alias="amountMinor")
    currency: str
    note: str | None
    status: str
    created_at: datetime = Field(alias="createdAt")


def create_app(payment_service: PaymentService) -> FastAPI:
    app = FastAPI(title="UPI Payment System")

    @app.exception_handler(PaymentError)
    async def payment_error_handler(
        _request: Request, exc: PaymentError
    ) -> JSONResponse:
        status_code = _status_code_for(exc)
        return JSONResponse(
            status_code=status_code,
            content={"error": {"code": exc.code, "message": str(exc)}},
        )

    @app.post(
        "/v1/payments",
        response_model=PaymentResponse,
        response_model_by_alias=True,
    )
    def create_payment(
        body: CreatePaymentRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> JSONResponse:
        if idempotency_key is None:
            raise MissingIdempotencyKey("Idempotency-Key header is required")
        result = payment_service.create_payment(
            CreatePaymentCommand(
                idempotency_key=idempotency_key,
                payer_vpa=body.payer_vpa,
                payee_vpa=body.payee_vpa,
                amount_minor=body.amount_minor,
                currency=body.currency,
                note=body.note,
            )
        )
        payment = result.payment
        response = PaymentResponse(
            id=payment.id,
            payerVpa=payment.payer_vpa.value,
            payeeVpa=payment.payee_vpa.value,
            payeeName=payment.payee_name,
            amountMinor=payment.money.amount_minor,
            currency=payment.money.currency,
            note=payment.note,
            status=payment.status.value,
            createdAt=payment.created_at,
        )
        return JSONResponse(
            status_code=201 if result.created else 200,
            content=response.model_dump(mode="json", by_alias=True),
        )

    return app


def _status_code_for(error: PaymentError) -> int:
    if isinstance(error, MissingIdempotencyKey):
        return 400
    if isinstance(error, IdempotencyConflict):
        return 409
    if isinstance(error, SamePayerAndPayee):
        return 422
    if isinstance(error, (InvalidVPA, InvalidAmount, InvalidIdempotencyKey)):
        return 422
    if isinstance(error, PayeeNotFound):
        return 422
    return 400
