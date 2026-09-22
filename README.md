# UPI Payment System

A phased Python and FastAPI reference implementation for studying a basic UPI payment lifecycle. The project starts with low-level design fundamentals and can later extend into practical high-level architecture.

## Current status

The planned five phases are complete. The repository implements the complete basic P2P lifecycle: idempotent creation, retrieval, simulated authorisation, idempotent gateway submission, definitive and pending outcomes, status refresh, crash recovery, transition history and concurrent-update protection.

## System boundary

The system represents a simplified payer-facing UPI payment service. It owns local payment records and coordinates with simulated external components.

It does not reproduce NPCI, a PSP, a bank, or a real payment network.

## Implemented behaviour

`POST /v1/payments`:

1. Requires a client-generated UUIDv7 `Idempotency-Key`.
2. Normalises and structurally validates payer and payee VPAs.
3. Rejects a payment to the same VPA.
4. Validates a positive INR amount represented in paise.
5. Resolves the payee through an injected in-memory resolver.
6. Atomically stores the payment and initial status-history row.
7. Returns the existing payment when the same key and payload are replayed.
8. Rejects the same key with a different payload.
9. Uses a database uniqueness constraint to protect concurrent creation.
10. Retrieves the authoritative local payment state.
11. Verifies an opaque simulated authorisation token without storing it.
12. Claims a payment through an atomic `CREATED → PROCESSING` transition.
13. Submits once to an idempotent simulated gateway using `payment_id`.
14. Records `SUCCEEDED`, `FAILED` or `PENDING`.
15. Resolves pending payments through status enquiry.
16. Recovers stuck `PROCESSING` payments by querying first and safely resubmitting only when not found.

The default runtime resolver recognises:

| VPA | Display name |
| --- | --- |
| `alice@bank` | Alice |
| `bob@bank` | Bob |

These entries only support local demonstration and are not a user or bank-account model.

## Implemented lifecycle

```text
CREATED
    → PROCESSING
        → SUCCEEDED
        → FAILED
        → PENDING
              → SUCCEEDED
              → FAILED
```

## Entities and schemas

### Domain entity

#### Payment

`Payment` is the only domain entity and the aggregate root. It has a stable identity, owns the payment lifecycle and protects its state transitions.

| Field | Type | Purpose |
| --- | --- | --- |
| `id` | UUIDv7 | Server-generated aggregate identity and downstream idempotency reference |
| `idempotency_key` | `IdempotencyKey` | Identifies one client creation intent |
| `payer_vpa` | `VPA` | Normalised payer address snapshot |
| `payee_vpa` | `VPA` | Normalised payee address snapshot |
| `payee_name` | string | Display name captured when the payee is resolved |
| `money` | `Money` | Immutable amount and currency |
| `note` | string or null | Optional payment note |
| `status` | `PaymentStatus` | Current lifecycle state |
| `created_at` | UTC datetime | Creation time |
| `updated_at` | UTC datetime | Last accepted state-change time |
| `version` | integer | Optimistic-concurrency version |
| `network_reference` | string or null | Reference returned by the gateway |
| `failure_code` | string or null | Definitive gateway failure reason |
| `submitted_at` | UTC datetime or null | First downstream-submission time |
| `completed_at` | UTC datetime or null | Definitive success or failure time |

Payer, payee, bank account and gateway are not modelled as local entities. This service stores only the payment data it owns or needs as a snapshot.

### Value objects

Value objects have no independent identity or lifecycle. They are compared by value and validated when constructed.

| Value object | Fields | Invariant |
| --- | --- | --- |
| `VPA` | `value` | Normalised lowercase `local-part@handle` with structural validation |
| `Money` | `amount_minor`, `currency` | Positive integer amount in paise; currency is `INR` |
| `IdempotencyKey` | `value` | Client-generated UUIDv7 reused for every retry of one creation intent |

`PaymentStatus` is the lifecycle enum: `CREATED`, `PROCESSING`, `PENDING`, `SUCCEEDED` or `FAILED`.

### Persisted audit record

#### PaymentStatusChange

`payment_status_changes` is an append-only persistence record, not another aggregate. Its database row identity supports ordering and audit, while all transition decisions remain inside `Payment` and `PaymentService`.

| Field | Type | Purpose |
| --- | --- | --- |
| `id` | integer | Database-generated row identity |
| `payment_id` | UUID | Owning payment |
| `from_status` | status or null | Previous status; null for creation |
| `to_status` | status | Accepted new status |
| `source` | string | Component or operation that caused the transition |
| `reason_code` | string or null | Optional failure or transition reason |
| `created_at` | UTC datetime | Transition time |

Creation records `NULL → CREATED` with source `CREATION`. The payment write and its history write share one SQLite transaction; every later accepted transition updates the payment and appends its history row atomically.

### Derived and read models

Derived models present existing facts and do not own an independent lifecycle.

| Model | Derived from | Use |
| --- | --- | --- |
| `PaymentResponse` | `Payment` | API representation returned by create, retrieve, submit and refresh operations |

There is deliberately no stored `PaymentReceipt` entity. For a successful payment, `PaymentResponse` already exposes the receipt-like facts: payment ID, parties, amount, status, timestamps and network reference. A distinct receipt should be introduced only if future requirements give it an independent issuance, numbering, legal or retention lifecycle.

### Transport and application schemas

These structures move data across layers; they are not domain entities.

| Schema | Layer | Purpose |
| --- | --- | --- |
| `CreatePaymentRequest` | API | Parses the JSON creation body |
| `SubmitPaymentRequest` | API | Carries the opaque simulated authorisation token |
| `CreatePaymentCommand` | Application | Carries validated creation input into the service |
| `CreatePaymentResult` | Application | Returns the payment plus replay information to the API |

### Integration contract types

| Type | Produced by | Purpose |
| --- | --- | --- |
| `ResolvedVPA` | `VpaResolver` | Verified VPA and display name returned by payee resolution |
| `GatewayResult` | `UpiGateway` | Gateway outcome, network reference and optional failure code |
| `GatewayOutcome` | `UpiGateway` | Enumerates `SUCCEEDED`, `FAILED` and `PENDING`; a missing enquiry result is represented by `None` |

The dependency boundaries are expressed by the `PaymentRepository`, `VpaResolver`, `AuthorizationVerifier` and `UpiGateway` protocols. Implementations are replaceable infrastructure, not entities.

## Identity and idempotency

```text
Client idempotency key
    UUIDv7 generated once when the payer initiates the action
    Reused for every creation retry

Payment ID
    UUIDv7 generated by the server
    Identifies the Payment aggregate
```

- Same key and same normalised request return the existing payment.
- Same key and different payer, payee, money or note return `IDEMPOTENCY_CONFLICT`.
- The service checks for an ordinary replay before resolving the payee again.
- The SQLite unique constraint handles concurrent requests that pass the initial lookup together.
- Mutable request fields and time buckets are not encoded into the key.

## Implemented API

### Create payment

```http
POST /v1/payments
Idempotency-Key: <client-generated UUIDv7>
Content-Type: application/json
```

```json
{
  "payerVpa": "alice@bank",
  "payeeVpa": "bob@bank",
  "amountMinor": 50000,
  "currency": "INR",
  "note": "Dinner"
}
```

Successful first response:

```http
201 Created
```

```json
{
  "id": "0199f2d8-6a31-7b42-9c15-8f7a63d80121",
  "payerVpa": "alice@bank",
  "payeeVpa": "bob@bank",
  "payeeName": "Bob",
  "amountMinor": 50000,
  "currency": "INR",
  "note": "Dinner",
  "status": "CREATED",
  "createdAt": "2026-09-22T10:00:00Z",
  "networkReference": null,
  "failureCode": null,
  "submittedAt": null,
  "completedAt": null
}
```

- First creation: `201 Created`.
- Same key and same payload: `200 OK` with the original payment.
- Same key and different payload: `409 Conflict`.

### Retrieve payment

```http
GET /v1/payments/{paymentId}
```

Returns local authoritative state without contacting the gateway.

### Submit payment

```http
POST /v1/payments/{paymentId}/submit
Content-Type: application/json
```

```json
{
  "authorizationToken": "test-approved-token"
}
```

- `200 OK` for definitive success or failure.
- `202 Accepted` for a pending outcome.
- Repeated submission returns current state and never creates another effective transfer.

### Refresh status

```http
POST /v1/payments/{paymentId}/refresh-status
```

- `PENDING`: performs status enquiry only.
- `PROCESSING`: queries first and resubmits with the same payment ID only when the gateway returns not found.
- Returns `409 INVALID_PAYMENT_STATE` for states that do not need recovery.

## Implemented errors

Errors use:

```json
{
  "error": {
    "code": "IDEMPOTENCY_CONFLICT",
    "message": "Idempotency key was already used with a different request"
  }
}
```

| HTTP | Code | Meaning |
| --- | --- | --- |
| `400` | `MISSING_IDEMPOTENCY_KEY` | Header is absent |
| `403` | `PAYMENT_AUTHORIZATION_FAILED` | Simulated authorisation is rejected |
| `404` | `PAYMENT_NOT_FOUND` | Payment ID is unknown |
| `409` | `IDEMPOTENCY_CONFLICT` | Same key used with a different request |
| `409` | `INVALID_PAYMENT_STATE` | Operation is invalid from the current state |
| `422` | `INVALID_IDEMPOTENCY_KEY` | Key is not UUIDv7 |
| `422` | `INVALID_VPA` | VPA is structurally invalid |
| `422` | `PAYEE_NOT_FOUND` | Resolver cannot verify the payee |
| `422` | `SAME_PAYER_AND_PAYEE` | Self-payment is rejected |
| `422` | `INVALID_AMOUNT` | Amount is not positive or currency is not INR |

## Persistence

### payments

Important constraints:

- primary key on `id`;
- unique `idempotency_key`;
- positive `amount_minor`;
- currency restricted to `INR`;
- status restricted to approved lifecycle values;
- non-negative `version`.

### payment_status_changes

- primary key on `id`;
- foreign key to `payments.id`;
- index on `(payment_id, created_at)`.

The code uses one short-lived SQLite connection per repository operation. Development data is written to `data/upi_payments.db`, which is ignored by Git.

## Responsibility boundaries

```text
HTTP request
    → FastAPI schema and error mapping
    → PaymentService orchestration
    → VPA, Money and IdempotencyKey rules
    → VpaResolver / AuthorizationVerifier / UpiGateway boundaries
    → SqlitePaymentRepository
    → SQLite constraints and transaction
```

- **API:** transport fields, HTTP statuses and error responses.
- **Service:** creation, submission, recovery and dependency coordination.
- **Domain:** valid values, request equality and state transitions.
- **Resolver:** external payee lookup boundary.
- **Authorisation verifier:** opaque-token simulation.
- **Gateway:** idempotent external submission and status simulation.
- **Repository:** SQL mapping, atomic state/history writes and compare-and-set updates.
- **Database:** final relational constraints.

## Setup

From the repository root in GitHub Codespaces:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
```

## Run

```bash
uvicorn upi_payment.main:app --reload
```

Create a UUIDv7 in another terminal:

```bash
python -c "from uuid6 import uuid7; print(uuid7())"
```

Use the printed value:

```bash
curl -i -X POST http://127.0.0.1:8000/v1/payments \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: <paste-uuidv7-here>' \
  -d '{
    "payerVpa": "alice@bank",
    "payeeVpa": "bob@bank",
    "amountMinor": 50000,
    "currency": "INR",
    "note": "Dinner"
  }'
```

Repeat the same command with the same key to receive the original payment with `200 OK`.

Copy the `id` from the response and retrieve the payment:

```bash
curl -i http://127.0.0.1:8000/v1/payments/<payment-id>
```

Submit it using the approved simulation token:

```bash
curl -i -X POST http://127.0.0.1:8000/v1/payments/<payment-id>/submit \
  -H 'Content-Type: application/json' \
  -d '{"authorizationToken":"test-approved-token"}'
```

The default gateway returns `SUCCEEDED`. Retrieve the payment again to see its terminal state and simulated network reference.

## Test

Focused examples:

```bash
python -m pytest tests/test_domain.py
python -m pytest tests/test_repository.py
python -m pytest tests/test_service.py
python -m pytest tests/test_api.py
```

Full suite:

```bash
python -m pytest
```

Current full-suite result:

```text
33 passed
```

## Project structure

```text
.
├── PLAN.md
├── README.md
├── pyproject.toml
├── src/
│   └── upi_payment/
│       ├── api.py
│       ├── authorization.py
│       ├── database.py
│       ├── domain.py
│       ├── errors.py
│       ├── gateway.py
│       ├── main.py
│       ├── ports.py
│       ├── repository.py
│       ├── resolver.py
│       └── service.py
└── tests/
    ├── conftest.py
    ├── test_api.py
    ├── test_domain.py
    ├── test_repository.py
    ├── test_service.py
    └── test_submission.py
```

## Completion verification

- Fresh virtual environment installation completed successfully.
- Ordinary test discovery passes 33 tests.
- A live Uvicorn flow was verified over HTTP:

  ```text
  POST create  → 201 CREATED
  GET payment  → 200 CREATED
  POST submit  → 200 SUCCEEDED
  GET payment  → 200 SUCCEEDED
  ```

- Requirements, API contracts, domain state, schema, services, repositories and tests use consistent terminology.
- `PLAN.md` records every confirmed decision and all phases as complete.
- This `README.md` documents the implemented system rather than deferred extensions.

## Practical future scope

- QR initiation through UPI URI parsing.
- P2M payments with merchant and order references.
- Collect requests and recurring mandates.
- Refund, reversal and dispute workflows.
- Richer outcomes such as payer debited but payee not credited.
- PostgreSQL concurrency and production-level high-level architecture.

## References

- [NPCI UPI overview](https://www.npci.org.in/product/upi)
- [NPCI UPI participants](https://www.npci.org.in/product/upi/about-upi)
- [NPCI UPI circulars](https://www.npci.org.in/circulars/upi)
- [NPCI UPI Safety Shield](https://www.npci.org.in/safety-feature)
- [RBI failed-transaction turnaround-time guidance](https://www.rbi.org.in/commonman/English/scripts/Notification.aspx?Id=3074)
- [RFC 9562 — UUIDs, including UUIDv7](https://www.rfc-editor.org/rfc/rfc9562.html)
