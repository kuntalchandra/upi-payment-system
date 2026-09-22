# UPI Payment System

A Python and FastAPI reference implementation for studying a simplified P2P UPI payment lifecycle. It models payment intent separately from execution attempts and uses SQLite plus deterministic in-memory integrations so the behaviour is easy to run and review.

This is not an NPCI, PSP, bank or production payment-network implementation.

## Implemented scope

- Create an immutable P2P payment intent with client idempotency.
- Validate and normalise VPAs and positive INR amounts in paise.
- Resolve the payee through an injected resolver.
- Create separately identifiable and idempotent payment attempts.
- Simulate opaque payer authorisation without receiving or storing a UPI PIN.
- Record successful, failed and pending gateway outcomes.
- Retry a payment after a definitive failed attempt.
- Recover processing attempts and refresh pending attempts using the same attempt ID.
- Preserve an append-only, attempt-correlated payment status audit log.
- Protect state changes through database constraints and optimistic concurrency.

## System boundary

The service owns `Payment`, `PaymentAttempt` and their audit history. VPA resolution, authorisation verification and gateway execution are external boundaries represented by protocols and in-memory adapters.

Payers, payees, users, bank accounts and the gateway are not local domain entities.

## Lifecycle

`Payment` represents the overall intent:

```text
CREATED → PROCESSING → PENDING → SUCCEEDED
                    ↘ FAILED → PROCESSING ...
```

- `SUCCEEDED` is terminal.
- `FAILED` permits a new intentional attempt.
- Only one attempt may be active for a payment.

Each `PaymentAttempt` has an independent execution lifecycle:

```text
PROCESSING → PENDING → SUCCEEDED
                     ↘ FAILED
           ↘ SUCCEEDED
           ↘ FAILED
```

`SUCCEEDED` and `FAILED` are terminal for an individual attempt.

## Entities and schemas

### Domain entities

#### Payment

`Payment` is the aggregate root and represents one immutable transfer intent.

| Field | Type | Purpose |
| --- | --- | --- |
| `id` | UUIDv7 | Server-generated payment identity |
| `idempotency_key` | `IdempotencyKey` | Identifies one client creation intent |
| `payer_vpa` | `VPA` | Normalised payer address snapshot |
| `payee_vpa` | `VPA` | Normalised resolved payee address |
| `payee_name` | string | Resolved display-name snapshot |
| `money` | `Money` | Immutable amount and currency |
| `note` | string or null | Optional payment note |
| `status` | `PaymentStatus` | Aggregate lifecycle state |
| `created_at`, `updated_at` | UTC datetime | Lifecycle timestamps |
| `version` | integer | Optimistic-concurrency version |

#### PaymentAttempt

`PaymentAttempt` is a child entity inside the `Payment` aggregate. It represents one logical downstream execution, not each HTTP call.

| Field | Type | Purpose |
| --- | --- | --- |
| `id` | UUIDv7 | Attempt identity and gateway idempotency reference |
| `payment_id` | UUIDv7 | Owning payment |
| `idempotency_key` | `IdempotencyKey` | Identifies one intentional client attempt |
| `attempt_number` | positive integer | Sequence within the payment |
| `status` | `PaymentAttemptStatus` | Execution state |
| `network_reference` | string or null | Gateway reference |
| `failure_code` | string or null | Definitive failure reason |
| `created_at`, `updated_at` | UTC datetime | Lifecycle timestamps |
| `completed_at` | UTC datetime or null | Definitive completion time |
| `version` | integer | Optimistic-concurrency version |

### Value objects and enums

| Type | Rule |
| --- | --- |
| `VPA` | Lowercase `local-part@handle` with structural validation |
| `Money` | Positive integer amount in paise; currency restricted to `INR` |
| `IdempotencyKey` | Client-generated UUIDv7 |
| `PaymentStatus` | `CREATED`, `PROCESSING`, `PENDING`, `SUCCEEDED`, `FAILED` |
| `PaymentAttemptStatus` | `PROCESSING`, `PENDING`, `SUCCEEDED`, `FAILED` |

### Immutable audit log

`payment_status_changes` is an append-only audit record, not a domain entity. Creation rows have no attempt ID; execution transitions carry `attempt_id` for future operational analytics.

| Field | Purpose |
| --- | --- |
| `payment_id` | Aggregate being audited |
| `attempt_id` | Responsible attempt, or null for payment creation |
| `from_status`, `to_status` | Accepted transition |
| `source` | Creation, attempt submission or status enquiry |
| `reason_code` | Optional failure reason |
| `created_at` | Transition time |

The payment, attempt and audit changes for one transition share a SQLite transaction.

### Derived/read models

`PaymentResponse` is derived from `Payment` plus its latest `PaymentAttempt`. Attempt-specific fields are not duplicated in `Payment`.

There is no stored `PaymentReceipt`. A successful response already provides receipt-like facts. A separate receipt should be introduced only if requirements add an independent issuance, numbering, legal or retention lifecycle.

`PaymentAttemptResponse` is the API representation of one attempt. Request, command, result, resolver and gateway types are transport/application contracts—not entities.

## Idempotency

| Boundary | Key | Behaviour |
| --- | --- | --- |
| Payment creation | Client UUIDv7 | Same key and same payload return the payment; different payload conflicts |
| Attempt creation | Client UUIDv7 scoped to payment | Same key returns the same attempt |
| Gateway submission and enquiry | Server `PaymentAttempt.id` | Recovery reuses the same downstream execution |

A new attempt requires a new client key and is allowed only after a definitive failure. Network retries and crash recovery reuse the existing attempt ID.

## API

### Create a payment

```http
POST /v1/payments
Idempotency-Key: <client UUIDv7>
Content-Type: application/json

{
  "payerVpa": "alice@bank",
  "payeeVpa": "bob@bank",
  "amountMinor": 50000,
  "currency": "INR",
  "note": "Dinner"
}
```

- `201 Created`: first creation.
- `200 OK`: idempotent replay.
- `409 Conflict`: key reused with a different request.

### Retrieve a payment

```http
GET /v1/payments/{paymentId}
```

Returns the payment and a flattened view of its latest attempt.

### Create and execute an attempt

```http
POST /v1/payments/{paymentId}/attempts
Idempotency-Key: <client UUIDv7>
Content-Type: application/json

{
  "authorizationToken": "test-approved-token"
}
```

- `201 Created`: new definitive attempt.
- `202 Accepted`: new pending attempt.
- `200 OK`: idempotent replay.
- `409 Conflict`: payment state does not permit another attempt.

The opaque authorisation token is verified but never persisted.

### Read and refresh attempts

```http
GET  /v1/payments/{paymentId}/attempts
GET  /v1/payments/{paymentId}/attempts/{attemptId}
POST /v1/payments/{paymentId}/attempts/{attemptId}/refresh-status
```

A processing attempt queries the gateway and safely resubmits with the same attempt ID only when no result exists. A pending attempt performs status enquiry only.

## Important errors

```json
{
  "error": {
    "code": "INVALID_PAYMENT_STATE",
    "message": "Cannot create an attempt for payment in PENDING"
  }
}
```

| HTTP | Codes |
| --- | --- |
| `400` | `MISSING_IDEMPOTENCY_KEY` |
| `403` | `PAYMENT_AUTHORIZATION_FAILED` |
| `404` | `PAYMENT_NOT_FOUND`, `PAYMENT_ATTEMPT_NOT_FOUND` |
| `409` | `IDEMPOTENCY_CONFLICT`, `INVALID_PAYMENT_STATE` |
| `422` | `INVALID_IDEMPOTENCY_KEY`, `INVALID_VPA`, `INVALID_AMOUNT`, `PAYEE_NOT_FOUND`, `SAME_PAYER_AND_PAYEE` |

## Persistence invariants

- Unique payment-creation idempotency key.
- Unique `(payment_id, attempt_idempotency_key)`.
- Unique `(payment_id, attempt_number)`.
- Partial unique index permitting at most one `PROCESSING` or `PENDING` attempt per payment.
- Compare-and-set updates using state and version.
- Payment, attempt and audit transition persisted atomically.
- Gateway call remains outside the database transaction.

## Run in GitHub Codespaces

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[test]"
python -m pytest -q
```

Start the API:

```bash
uvicorn upi_payment.main:app --reload
```

The default resolver recognises `alice@bank` and `bob@bank`. The accepted simulated authorisation token is `test-approved-token`.

## Project structure

```text
src/upi_payment/
├── api.py            # HTTP contracts and read-model mapping
├── authorization.py  # Simulated authorisation adapter
├── database.py       # SQLite schema
├── domain.py         # Entities, value objects and transitions
├── errors.py         # Stable application errors
├── gateway.py        # Idempotent simulated gateway
├── main.py           # Runtime composition
├── ports.py          # Repository and integration contracts
├── repository.py     # Atomic persistence and audit writes
├── resolver.py       # Simulated VPA resolver
└── service.py        # Use-case orchestration
```

## Deferred extensions

- QR initiation by parsing a UPI payment URI.
- P2M merchant and order references.
- Collect requests and mandates.
- Refunds, reversals, disputes and chargebacks.
- Retry eligibility and attempt-limit policy by failure category.
- PostgreSQL and production-grade distributed concurrency.
- Event publication and an HLD analytics pipeline consuming the immutable audit history.
