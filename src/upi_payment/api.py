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
    InvalidPaymentState,
    InvalidVPA,
    MissingIdempotencyKey,
    PaymentAuthorizationFailed,
    PaymentNotFound,
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
    network_reference: str | None = Field(alias="networkReference")
    failure_code: str | None = Field(alias="failureCode")
    submitted_at: datetime | None = Field(alias="submittedAt")
    completed_at: datetime | None = Field(alias="completedAt")


class SubmitPaymentRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    authorization_token: str = Field(alias="authorizationToken")


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
        return JSONResponse(
            status_code=201 if result.created else 200,
            content=_payment_response(result.payment),
        )

    @app.get(
        "/v1/payments/{payment_id}",
        response_model=PaymentResponse,
        response_model_by_alias=True,
    )
    def get_payment(payment_id: str) -> JSONResponse:
        return JSONResponse(
            status_code=200,
            content=_payment_response(payment_service.get_payment(payment_id)),
        )

    @app.post(
        "/v1/payments/{payment_id}/submit",
        response_model=PaymentResponse,
        response_model_by_alias=True,
    )
    def submit_payment(
        payment_id: str, body: SubmitPaymentRequest
    ) -> JSONResponse:
        payment = payment_service.submit_payment(
            payment_id, body.authorization_token
        )
        return JSONResponse(
            status_code=202 if payment.status.value in {"PROCESSING", "PENDING"} else 200,
            content=_payment_response(payment),
        )

    @app.post(
        "/v1/payments/{payment_id}/refresh-status",
        response_model=PaymentResponse,
        response_model_by_alias=True,
    )
    def refresh_status(payment_id: str) -> JSONResponse:
        payment = payment_service.refresh_status(payment_id)
        return JSONResponse(
            status_code=202 if payment.status.value in {"PROCESSING", "PENDING"} else 200,
            content=_payment_response(payment),
        )

    return app


def _status_code_for(error: PaymentError) -> int:
    if isinstance(error, MissingIdempotencyKey):
        return 400
    if isinstance(error, IdempotencyConflict):
        return 409
    if isinstance(error, InvalidPaymentState):
        return 409
    if isinstance(error, PaymentNotFound):
        return 404
    if isinstance(error, PaymentAuthorizationFailed):
        return 403
    if isinstance(error, SamePayerAndPayee):
        return 422
    if isinstance(error, (InvalidVPA, InvalidAmount, InvalidIdempotencyKey)):
        return 422
    if isinstance(error, PayeeNotFound):
        return 422
    return 400


def _payment_response(payment) -> dict[str, object]:
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
        networkReference=payment.network_reference,
        failureCode=payment.failure_code,
        submittedAt=payment.submitted_at,
        completedAt=payment.completed_at,
    )
    return response.model_dump(mode="json", by_alias=True)
