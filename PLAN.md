# UPI Payment LLD — Learning and Implementation Plan

## 1. Purpose

Study a basic UPI payment flow in depth and implement a small reference system using Python and FastAPI.

The system is a **simplified payer-facing UPI payment service**. It owns payment records and calls a simulated UPI gateway. It is not an NPCI-certified application, bank integration, PSP implementation, or real payment network.

## 2. Confirmed functional scope

The first version supports one payer-initiated P2P push payment:

1. Accept the payer VPA, payee VPA, amount and optional note.
2. Validate both VPA syntax locally.
3. Resolve the payee through a simulated external VPA lookup.
4. Create a payment before submission.
5. Simulate payer authorisation without receiving or storing a UPI PIN.
6. Submit the payment separately through a simulated UPI gateway.
7. Record a definitive success, definitive failure, or unresolved outcome.
8. Retrieve the current payment status.
9. Resolve an unresolved outcome through simulated status enquiry.
10. Prevent duplicate payment creation and duplicate effective submission.
11. Preserve each intentional execution as a separately identifiable payment attempt.
12. Permit another intentional attempt after a definitive failure.

Payer and payee VPAs are payment inputs. User onboarding, bank accounts and linked-VPA management are not part of this system.

## 3. Explicit exclusions

- QR-code initiation
- P2M merchant payments
- Collect requests
- AutoPay and mandates
- Refunds, reversals, disputes and chargebacks
- Merchant settlement
- Device binding, SIM verification and UPI registration
- Real UPI PIN capture or storage
- Real NPCI, PSP or bank connectivity
- Production-scale distributed infrastructure

## 4. Practical future scope

Revisit only after the basic lifecycle is complete:

- QR-based initiation by parsing a UPI payment URI.
- P2M payments with merchant and order references.
- Collect requests and mandate-based recurring payments.
- Refund, reversal and dispute workflows.
- Richer operational outcomes such as payer debited but payee not credited.
- PostgreSQL-based concurrency behaviour and production infrastructure.

## 5. Confirmed lifecycle

```text
CREATED
    → PROCESSING
        → SUCCEEDED
        → PENDING
              → SUCCEEDED
              → FAILED
        → FAILED
              → PROCESSING (new attempt)
```

`SUCCEEDED` is terminal for a payment. `FAILED` permits a new intentional attempt. Each attempt independently ends in `SUCCEEDED` or `FAILED`. Authorisation is a prerequisite for an attempt, not a persisted payment state.

## 6. Confirmed invariants

1. Payment amount, currency, payer and payee cannot change after creation.
2. Repeating the same creation request must not create another logical payment.
3. One logical payment must not cause multiple effective transfers.
4. A timeout or missing response is not automatically a failed payment.
5. A succeeded payment cannot return to a non-terminal state; a failed payment may start a new attempt.
6. The raw UPI PIN must never enter the API, persistence or logs.
7. Money is represented in integer paise, never floating-point rupees.
8. Every accepted state transition must be traceable.
9. A local database transaction cannot atomically include the external gateway call.
10. At most one attempt may be `PROCESSING` or `PENDING` for a payment.
11. Recovery must reuse the same attempt ID; it must not create another attempt.
12. Every attempt-related audit transition must carry its attempt ID.

## 7. Design principles

- Depth before breadth.
- Simplicity before extensibility.
- Standard UPI terminology only.
- Separate UPI facts from simulation decisions.
- Define contracts and transaction boundaries before coding.
- Persist authoritative business state; derive read representations where possible.
- Introduce abstractions only for confirmed variation points.

## 8. Working agreement

- Explain What, Why and How before each material step.
- Confirm material design decisions before implementation.
- Keep each phase coherent, runnable and reviewable.
- Provide exact file paths and copy-paste-ready Codespaces commands.
- Run focused tests first and the full suite second.
- Stop and diagnose the first execution failure before continuing.
- Keep the progress tracker to one concise line per phase.
- Keep `README.md` aligned with the system that actually exists.
- At each phase completion, update both `PLAN.md` and `README.md` and include them in the phase-completion commit.

## 9. Phases

### Phase 1 — Requirements, lifecycle and invariants

- Confirm terminology, actors and system boundary.
- Define use cases, validation, lifecycle, state transitions and failure meaning.
- Finalise requirements and invariants.

**Deliverable:** approved requirements and lifecycle, with no code.

**Gate:** every operation has a clear input, outcome, state change and failure behaviour.

### Phase 2 — Domain model, API and persistence design

- Define entities, value objects, read models and enums.
- Define API contracts, stable errors and idempotency behaviour.
- Define schema, responsibilities and transaction boundaries.

**Deliverable:** one consistent design ready for implementation.

**Gate:** requirements, model, API and persistence design agree.

### Phase 3 — Payment creation

- Establish the FastAPI project and SQLite database.
- Implement VPA validation/resolution and idempotent payment creation.
- Add domain, repository, service and API tests for this slice.

**Deliverable:** runnable payment-creation flow.

**Gate:** focused and full tests pass; review approved.

### Phase 4 — Submission and status resolution

- Add simulated authorisation and gateway submission.
- Handle success, failure and unresolved outcomes.
- Resolve unresolved payments through status enquiry.
- Verify duplicate and concurrent submission behaviour.

**Deliverable:** complete basic payment lifecycle.

**Gate:** lifecycle and important failure paths pass focused and full tests.

### Phase 5 — Consolidation and documentation

- Remove stale or superseded work.
- Complete the full-flow tests and setup verification.
- Finalise the skimmable `README.md`.
- Cross-check requirements, APIs, model, schema, code, tests and documentation.

**Deliverable:** clean and explainable reference implementation.

**Gate:** definition of done is satisfied.

### Phase 6 — Payment attempts

- Separate payment intent from downstream execution attempts.
- Add attempt-scoped idempotency, lifecycle, recovery and history.
- Permit a new attempt after definitive failure while preventing concurrent active attempts.
- Derive payment execution details from the latest attempt.

**Deliverable:** complete multi-attempt lifecycle with attempt-correlated audit history.

**Gate:** entity, API, schema, recovery, tests and documentation agree.

## 10. Decision log

| Decision | Status | Reason |
| --- | --- | --- |
| Build a simplified payer-facing payment service | Confirmed | Clear ownership without recreating the payment network |
| Support P2P push payment first | Confirmed | Smallest useful UPI lifecycle |
| Create and submit through separate operations | Confirmed | Makes lifecycle and idempotency explicit |
| Validate VPA syntax and simulate external resolution | Confirmed | Keeps validation realistic without real connectivity |
| Accept VPAs without modelling users or bank accounts | Confirmed | Onboarding and account linking are excluded |
| Represent an uncertain result as one unresolved outcome | Confirmed | Avoids premature reversal and dispute modelling |
| Require client idempotency for creation | Confirmed | Prevents duplicate logical payments |
| Use SQLite initially | Confirmed | Runs directly in Codespaces without another service |
| Defer PostgreSQL-specific concurrency behaviour | Confirmed | SQLite does not faithfully demonstrate it |
| Never accept or store a raw UPI PIN | Confirmed | Outside the service boundary and unsafe |
| Use `CREATED`, `PROCESSING`, `PENDING`, `SUCCEEDED`, and `FAILED` | Confirmed | Distinguishes local creation, active submission, uncertain outcome, and terminal outcomes |
| Do not persist a separate `AUTHORIZED` state | Confirmed | Authorisation is a submission precondition in this scope |
| Use a client-generated UUIDv7 idempotency key | Confirmed | Stable across retries and time-orderable without encoding mutable payment data |
| Use server-generated `payment_attempt_id` as the downstream transaction reference | Confirmed | Distinguishes intentional attempts while making recovery of each attempt idempotent |
| Make simulated gateway submission idempotent by `payment_attempt_id` | Confirmed | Retries one execution safely without preventing a later intentional attempt |
| Recover `PROCESSING` through status lookup, then same-reference resubmission only if not found | Confirmed | Handles crashes before and after gateway acceptance |
| Keep `PENDING` recovery status-enquiry only | Confirmed | Gateway has already acknowledged the transaction |
| Use Python's `sqlite3` directly for the initial repository | Confirmed | Keeps persistence and transaction behaviour visible without an ORM |
| Use an injected in-memory VPA resolver initially | Confirmed | Exercises the external resolution boundary without real connectivity |
| Enforce idempotency with service comparison and a database unique constraint | Confirmed | Handles both ordinary retries and concurrent creation |
| Use an injected authorisation verifier | Confirmed | Keeps the opaque token outside domain state and persistence |
| Use an in-memory gateway keyed by `payment_id` | Confirmed | Provides deterministic, idempotent external behaviour for learning and tests |
| Use optimistic compare-and-set for status changes | Confirmed | Prevents competing requests from applying the same transition |
| Model `PaymentAttempt` as a child entity of `Payment` | Confirmed | It has stable identity, lifecycle and gateway behaviour but cannot exist independently of its payment |
| Retain `PaymentStatusChange` as an immutable audit record | Confirmed | Preserves transition history and supports future operational analytics |
| Correlate attempt transitions through `attempt_id` | Confirmed | Allows analytics to group payment history by execution attempt |
| Keep attempt-specific data out of `Payment` | Confirmed | Avoids duplicated authoritative state; payment reads derive it from the latest attempt |

## 11. Progress tracker

| Phase | Status | Notes |
| --- | --- | --- |
| Phase 1 — Requirements, lifecycle and invariants | Completed | Requirements, lifecycle and invariants approved |
| Phase 2 — Domain model, API and persistence design | Completed | Model, contracts, schema and transaction boundaries approved |
| Phase 3 — Payment creation | Completed | Creation API and 17 tests passing |
| Phase 4 — Submission and status resolution | Completed | Complete lifecycle and 32 tests passing |
| Phase 5 — Consolidation and documentation | Completed | Clean setup, 33 tests and live HTTP flow verified |
| Phase 6 — Payment attempts | Completed | Attempt lifecycle, retry, recovery, audit correlation and derived reads verified |

## 12. Definition of done

- Scope, exclusions, actors, terminology, lifecycle and invariants are explicit.
- Domain model, APIs, schema and transaction boundaries are agreed before coding.
- Idempotency and valid state transitions are enforced and tested.
- Focused tests and ordinary full-suite discovery pass in Codespaces.
- At least one complete payment lifecycle and its important failures are tested.
- `PLAN.md` contains no stale pending decisions.
- `README.md` accurately describes only implemented behaviour.
- Deferred extensions remain clearly separated from current scope.

## 13. Authoritative references

- NPCI UPI overview: <https://www.npci.org.in/product/upi>
- NPCI UPI participants: <https://www.npci.org.in/product/upi/about-upi>
- NPCI UPI circulars: <https://www.npci.org.in/circulars/upi>
- NPCI UPI Safety Shield: <https://www.npci.org.in/safety-feature>
- RBI failed-transaction turnaround-time guidance: <https://www.rbi.org.in/commonman/english/scripts/Notification.aspx>
