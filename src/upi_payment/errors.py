class PaymentError(Exception):
    code = "PAYMENT_ERROR"


class InvalidVPA(PaymentError):
    code = "INVALID_VPA"


class InvalidAmount(PaymentError):
    code = "INVALID_AMOUNT"


class InvalidIdempotencyKey(PaymentError):
    code = "INVALID_IDEMPOTENCY_KEY"


class MissingIdempotencyKey(PaymentError):
    code = "MISSING_IDEMPOTENCY_KEY"


class PayeeNotFound(PaymentError):
    code = "PAYEE_NOT_FOUND"


class SamePayerAndPayee(PaymentError):
    code = "SAME_PAYER_AND_PAYEE"


class IdempotencyConflict(PaymentError):
    code = "IDEMPOTENCY_CONFLICT"
