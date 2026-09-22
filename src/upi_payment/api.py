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
    PaymentAttemptNotFound,
    PaymentAuthorizationFailed,
    PaymentError,
    PaymentNotFound,
    PayeeNotFound,
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


class PaymentAttemptResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)
    id: UUID
    payment_id: UUID = Field(alias="paymentId")
    attempt_number: int = Field(alias="attemptNumber")
    status: str
    network_reference: str | None = Field(alias="networkReference")
    failure_code: str | None = Field(alias="failureCode")
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")
    completed_at: datetime | None = Field(alias="completedAt")


class SubmitPaymentRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    authorization_token: str = Field(alias="authorizationToken")


def create_app(service: PaymentService) -> FastAPI:
    app = FastAPI(title="UPI Payment System")

    @app.exception_handler(PaymentError)
    async def payment_error_handler(
        _request: Request, exc: PaymentError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=_status_code_for(exc),
            content={"error": {"code": exc.code, "message": str(exc)}},
        )

    @app.post(
        "/v1/payments", response_model=PaymentResponse, response_model_by_alias=True
    )
    def create_payment(
        body: CreatePaymentRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> JSONResponse:
        if idempotency_key is None:
            raise MissingIdempotencyKey("Idempotency-Key header is required")
        result = service.create_payment(
            CreatePaymentCommand(
                idempotency_key,
                body.payer_vpa,
                body.payee_vpa,
                body.amount_minor,
                body.currency,
                body.note,
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
        payment = service.get_payment(payment_id)
        attempt = service.get_latest_payment_attempt(payment_id)
        return JSONResponse(
            status_code=200, content=_payment_response(payment, attempt)
        )

    @app.post(
        "/v1/payments/{payment_id}/attempts",
        response_model=PaymentAttemptResponse,
        response_model_by_alias=True,
    )
    def create_attempt(
        payment_id: str,
        body: SubmitPaymentRequest,
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> JSONResponse:
        if idempotency_key is None:
            raise MissingIdempotencyKey("Idempotency-Key header is required")
        result = service.create_payment_attempt(
            payment_id, idempotency_key, body.authorization_token
        )
        status = (
            200
            if not result.created
            else (
                202 if result.attempt.status.value in {"PROCESSING", "PENDING"} else 201
            )
        )
        return JSONResponse(
            status_code=status, content=_attempt_response(result.attempt)
        )

    @app.get(
        "/v1/payments/{payment_id}/attempts",
        response_model=list[PaymentAttemptResponse],
        response_model_by_alias=True,
    )
    def list_attempts(payment_id: str) -> JSONResponse:
        return JSONResponse(
            status_code=200,
            content=[
                _attempt_response(item)
                for item in service.list_payment_attempts(payment_id)
            ],
        )

    @app.get(
        "/v1/payments/{payment_id}/attempts/{attempt_id}",
        response_model=PaymentAttemptResponse,
        response_model_by_alias=True,
    )
    def get_attempt(payment_id: str, attempt_id: str) -> JSONResponse:
        return JSONResponse(
            status_code=200,
            content=_attempt_response(
                service.get_payment_attempt(payment_id, attempt_id)
            ),
        )

    @app.post(
        "/v1/payments/{payment_id}/attempts/{attempt_id}/refresh-status",
        response_model=PaymentAttemptResponse,
        response_model_by_alias=True,
    )
    def refresh_attempt(payment_id: str, attempt_id: str) -> JSONResponse:
        attempt = service.refresh_payment_attempt(payment_id, attempt_id)
        status = 202 if attempt.status.value in {"PROCESSING", "PENDING"} else 200
        return JSONResponse(status_code=status, content=_attempt_response(attempt))

    return app


def _status_code_for(error: PaymentError) -> int:
    if isinstance(error, MissingIdempotencyKey):
        return 400
    if isinstance(error, (IdempotencyConflict, InvalidPaymentState)):
        return 409
    if isinstance(error, (PaymentNotFound, PaymentAttemptNotFound)):
        return 404
    if isinstance(error, PaymentAuthorizationFailed):
        return 403
    if isinstance(
        error,
        (
            SamePayerAndPayee,
            InvalidVPA,
            InvalidAmount,
            InvalidIdempotencyKey,
            PayeeNotFound,
        ),
    ):
        return 422
    return 400


def _attempt_response(attempt) -> dict[str, object]:
    return PaymentAttemptResponse(
        id=attempt.id,
        paymentId=attempt.payment_id,
        attemptNumber=attempt.attempt_number,
        status=attempt.status.value,
        networkReference=attempt.network_reference,
        failureCode=attempt.failure_code,
        createdAt=attempt.created_at,
        updatedAt=attempt.updated_at,
        completedAt=attempt.completed_at,
    ).model_dump(mode="json", by_alias=True)


def _payment_response(payment, attempt=None) -> dict[str, object]:
    return PaymentResponse(
        id=payment.id,
        payerVpa=payment.payer_vpa.value,
        payeeVpa=payment.payee_vpa.value,
        payeeName=payment.payee_name,
        amountMinor=payment.money.amount_minor,
        currency=payment.money.currency,
        note=payment.note,
        status=payment.status.value,
        createdAt=payment.created_at,
        networkReference=attempt.network_reference if attempt else None,
        failureCode=attempt.failure_code if attempt else None,
        submittedAt=attempt.created_at if attempt else None,
        completedAt=attempt.completed_at if attempt else None,
    ).model_dump(mode="json", by_alias=True)
