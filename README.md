# TrustRail — Agentic Transaction Integrity & Recovery Engine

> **Razorpay AI Buildathon 2026 · Track 01 — AI Growth & Agentic Commerce**

TrustRail is a **deterministic safety & orchestration layer** that sits between an
autonomous **AI buyer**, a **merchant** backend, and **Razorpay** (Test Mode).

The AI can *propose* a purchase. TrustRail decides whether that purchase is
*executable* — and every decision is deterministic, bounded, gated, idempotent,
state-aware, recoverable, and fully auditable.

```
   AI buyer  ──proposes──▶  TrustRail  ──quotes/orders──▶  Merchant
 (untrusted)                (deterministic)                (mock)
                                 │
                                 └──pays via──▶  Payment Gateway
                                                 (mock by default │ Razorpay Test Mode)
                                                        ▲
                                        webhook + reconciliation resolve
                                        the asynchronous payment outcome
```

The one-sentence thesis: **the LLM never authorizes payment and never sets state.**
It hands TrustRail a structured intent; TrustRail's pure, testable policy engine
and explicit state machine do everything that touches money.

---

## Table of contents
1. [Why this exists](#1-why-this-exists)
2. [Architecture](#2-architecture)
3. [The transaction lifecycle](#3-the-transaction-lifecycle)
4. [Running locally](#4-running-locally)
5. [API reference & examples](#5-api-reference--examples)
6. [Tests](#6-tests)
7. [Design decisions](#7-design-decisions)
8. [Known weaknesses](#8-known-weaknesses)
9. [Phase 2 — Razorpay Test Mode](#9-phase-2--razorpay-test-mode)
10. [Track 01 submission story](#10-track-01-submission-story)

---

## 1. Why this exists

When an AI agent buys things on a user's behalf, the dangerous questions are not
about the model — they are about **control**:

- What exactly did the user authorize? (a budget, a merchant, a quantity)
- What did the AI actually propose, verbatim?
- Did TrustRail allow it or block it — and *why*, checkably?
- Are two "identical" purchases actually the same transaction, or a double charge?
- If payment succeeds but the order fails, what do we owe the user?

TrustRail answers all of these with **deterministic backend logic**, never with
LLM text. The model's output is treated as untrusted input at the boundary.

---

## 2. Architecture

TrustRail is a small FastAPI application with a clean separation between the
**orchestrator** (which has side effects and sequences the phases) and the
**pure decision core** (canonicalisation, policy engine, state machine).

```
app/
├── main.py                 FastAPI app, error→HTTP mapping, lifespan (create tables + seed)
├── config.py               Settings (DATABASE_URL, flags) via pydantic-settings
├── db.py                   SQLAlchemy engine/session; SQLite ⇄ PostgreSQL interchangeable
├── clock.py                Injectable Clock (deterministic time in tests)
├── ids.py                  Prefixed IDs + identity_from_canonical() (SHA-256)
├── money.py                Integer minor units (paise); ₹ formatting
├── enums.py                TransactionState, Decision, Actor, AuditResult, …
├── errors.py               Domain errors (mapped to HTTP status in main.py)
│
├── schemas/                Pydantic request/response contracts (the API boundary)
│   ├── intent.py           PurchaseIntentIn — the transaction CONTRACT (extra fields ignored)
│   ├── policy.py           PolicyDecisionOut, PolicyCheckOut
│   ├── transaction.py      DecisionEnvelopeOut (state + decision + why)
│   ├── audit.py            AuditEventOut, AuditTrailOut
│   └── merchant.py         Merchant DTOs
│
├── models/                 SQLAlchemy ORM (PostgreSQL is the source of truth)
│   ├── transaction.py      Transaction — one per transaction_identity (unique)
│   ├── intent.py           Intent — many intents may map to one transaction
│   ├── audit.py            AuditEvent — append-only, ordered by `seq`
│   └── merchant.py         MerchantProduct / MerchantOrder / MockPayment / RazorpayPayment
│
├── services/               ── the brain ──
│   ├── intent.py           canonicalize() → deterministic transaction identity  (Phase 1)
│   ├── policy.py           evaluate()  — PURE function, no I/O, no LLM text      (Phase 2)
│   ├── state_machine.py    ALLOWED_TRANSITIONS adjacency map + guards            (Phase 3)
│   ├── payment.py          PaymentGateway Protocol + MockPaymentGateway (the seam)
│   ├── razorpay_gateway.py RazorpayGateway — the ONLY module that imports the SDK / holds secrets
│   ├── gateway.py          get_gateway() DI seam — picks mock vs razorpay from settings
│   ├── webhook.py          signature-verified webhook → legality-checked state moves
│   ├── reconciliation.py   authoritative status sweep for PENDING/UNKNOWN/RECOVERY
│   ├── refund.py           REFUND_REQUIRED → Razorpay refund → COMPLETED (at-most-once)
│   ├── locking.py          SELECT … FOR UPDATE row locks (Postgres) / no-op on SQLite
│   ├── audit.py            append-only audit recorder                            (Phase 6)
│   └── transaction.py      ORCHESTRATOR — the only place recovery state changes funnel through
│
├── merchant/               Mock external merchant system (deliberately separate) (Phase 4)
│   ├── catalogue.py        Synthetic catalogue + idempotent seeding
│   ├── service.py          Pricing, stock, idempotent orders, cancellation
│   ├── client.py           MerchantClient Protocol + InProcessMerchantClient seam
│   └── router.py           /merchant/* endpoints
│
└── api/                    TrustRail HTTP surface                                (Phase 5)
    ├── intents.py          POST /intents, /validate, /authorize, GET /intents/{id}
    ├── transactions.py     POST /transactions, GET /transactions/{id}, /audit
    ├── webhooks.py         POST /webhooks/razorpay (signature-verified ingress)
    └── deps.py             response assembling helpers
```

### The three deterministic guarantees

1. **Identity (Phase 1).** A purchase is reduced to only its financially-relevant
   fields — `{merchant_id, items(sku+qty), currency, max_amount, max_quantity}` —
   canonicalised (SKUs upper-cased & merged, items sorted, integers not floats),
   serialised with sorted keys, and hashed with SHA-256. That hash is the
   **transaction identity** and the system-wide idempotency key. It is **never** a
   hash of raw LLM JSON. `agent_id` and `expires_at` are deliberately excluded —
   *who* and *when* proposed a purchase does not change *what* is being bought.

2. **Policy (Phase 2).** `policy.evaluate(ctx)` is a **pure function**. Given a
   fully-resolved context (merchant quote + authorized constraints + clock) it
   returns the same `{decision, reason, policy_checks[]}` every time. It performs
   no I/O and never sees LLM text. The LLM can propose; only this function decides.

3. **State (Phase 3).** State lives in the database and only ever moves through the
   explicit `ALLOWED_TRANSITIONS` adjacency map. `INTENT_CREATED → COMPLETED` is
   simply not in the map, so shortcutting the lifecycle is impossible. Failure and
   recovery states are first-class.

---

## 3. The transaction lifecycle

```
                 ┌─────────────────────────────────────────────────────────────┐
                 │                      happy path                              │
  INTENT_CREATED ─▶ VALIDATED ─▶ AUTHORIZED ─▶ PAYMENT_PENDING ─▶ PAYMENT_CONFIRMED
                 │                                     │                 │
                 │                                     ▼                 ▼
                 │                               PAYMENT_FAILED     ORDER_CONFIRMED ─▶ COMPLETED
                 │                               PAYMENT_UNKNOWN          │
                 │                                                        ▼
   early rejections (terminal):                                     ORDER_FAILED
   INVALID · POLICY_BLOCKED · AUTH_EXPIRED                               │
   INVENTORY_CHANGED · PRICE_CHANGED                                     ▼
                                                     RECOVERY_PENDING · REFUND_REQUIRED ─▶ COMPLETED
```

- **INTENT_CREATED** — the AI's proposal is recorded (audited as `AI_BUYER`).
- **VALIDATED** — merchant quote + policy engine pass at the VALIDATE phase.
- **AUTHORIZED** — policy re-affirmed; the transaction is now executable.
- **PAYMENT_PENDING → PAYMENT_CONFIRMED** — mock gateway captures (idempotent on identity).
- **ORDER_CONFIRMED → COMPLETED** — merchant fulfils; the transaction is done.
- **Failure/recovery** — e.g. paid-but-unfulfilled becomes **REFUND_REQUIRED**, not a
  silent loss. The reachable failure state depends on the current state (an
  out-of-stock item pre-authorization is `POLICY_BLOCKED`; post-authorization it is
  `INVENTORY_CHANGED`).

Every transition writes an audit event, so the state history is the audit history.

---

## 4. Running locally

Requires **Python ≥ 3.11** (tested on 3.14).

### Option A — zero setup (SQLite)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# run the API (SQLite file trustrail.db is created & seeded automatically)
.venv/bin/uvicorn app.main:app --reload
```

Open the interactive docs at **http://127.0.0.1:8000/docs**.

In a second terminal, run the guided end-to-end demo:

```bash
.venv/bin/python scripts/demo.py
```

It walks the ALLOW happy path to `COMPLETED`, an over-budget `BLOCK`, transaction
identity determinism, the `REFUND_REQUIRED` recovery path, and the Phase 2
asynchronous payment boundary (`PENDING → signed webhook → CONFIRMED`) — printing
every request and response. Section 5 adapts to the configured gateway: under the
default mock it explains the async boundary; in Razorpay mode it delivers a signed
stand-in `payment.captured` webhook to reach `CONFIRMED`.

### Option B — Postgres (production-like)

`config.py` defaults to SQLite but the code is backend-agnostic. To use PostgreSQL
as the source of truth:

```bash
docker compose up --build      # starts Postgres + the API
```

or point an existing server at it:

```bash
export DATABASE_URL="postgresql+psycopg://trustrail:trustrail@localhost:5432/trustrail"
.venv/bin/uvicorn app.main:app --reload
```

For real deployments, disable `AUTO_CREATE_TABLES` and use Alembic:

```bash
.venv/bin/alembic revision --autogenerate -m "initial schema"
.venv/bin/alembic upgrade head
```

### Make targets

```
make install   make run   make test   make demo   make compose-up   make migrate
```

---

## 5. API reference & examples

TrustRail's own API is under `/intents` and `/transactions`. The mock merchant is a
**separate** system under `/merchant`. Money is always **integer minor units**
(paise): ₹5,000.00 = `500000`.

### `POST /intents` — record what the AI proposes

```json
{
  "agent_id": "agent-openai-buyer-1",
  "merchant_id": "MERCH_DEMO_001",
  "items": [{"sku": "SKU-001", "quantity": 1}],
  "constraints": {"max_amount": 500000, "currency": "INR", "max_quantity": 1},
  "authorization": {"expires_at": "2026-08-24T13:42:31+00:00"}
}
```

Response `201` — note the deterministic identity and the echoed canonical form:

```json
{
  "intent_id": "int_c464db8f…",
  "transaction_id": "txn_a9d75ec8…",
  "transaction_identity": "txid_ee869c47845062…36e0",
  "state": "INTENT_CREATED",
  "status": "CREATED",
  "canonical": {
    "canonicalization_version": 1,
    "merchant_id": "MERCH_DEMO_001",
    "items": [{"sku": "SKU-001", "quantity": 1}],
    "constraints": {"currency": "INR", "max_amount": 500000, "max_quantity": 1}
  },
  "canonical_json": "{\"canonicalization_version\":1,\"constraints\":{…},\"items\":[…],\"merchant_id\":\"MERCH_DEMO_001\"}"
}
```

### `POST /intents/{id}/validate` — deterministic decision + explanation

Response `200` (a **DecisionEnvelope** — state + decision + every check):

```json
{
  "intent_id": "int_c464db8f…",
  "transaction_id": "txn_a9d75ec8…",
  "transaction_identity": "txid_ee869c47…",
  "state": "VALIDATED",
  "decision": {
    "decision": "ALLOW",
    "reason": "all policy checks passed",
    "policy_checks": [
      {"name": "merchant_known",               "passed": true, "detail": "merchant 'MERCH_DEMO_001' recognised"},
      {"name": "skus_valid",                   "passed": true, "detail": "all SKUs exist in merchant catalogue"},
      {"name": "currency_match",               "passed": true, "detail": "currency INR matches merchant"},
      {"name": "authorization_not_expired",    "passed": true, "detail": "authorization valid until 2026-08-24T13:42:31+00:00"},
      {"name": "quantity_within_limit",        "passed": true, "detail": "total quantity 1 within authorized max 1"},
      {"name": "amount_within_authorized_max", "passed": true, "detail": "order total ₹1,299.00 within authorized maximum ₹5,000.00"},
      {"name": "inventory_available",          "passed": true, "detail": "requested quantities are in stock"},
      {"name": "price_unchanged",              "passed": true, "detail": "price unchanged since validation"}
    ]
  }
}
```

A **BLOCK** looks the same, but the failing check is explicit and drives the state:

```json
{
  "state": "POLICY_BLOCKED",
  "decision": {
    "decision": "BLOCK",
    "reason": "transaction total ₹1,299.00 exceeds authorized maximum ₹1,000.00",
    "policy_checks": [ …, {"name": "amount_within_authorized_max", "passed": false,
                            "detail": "transaction total ₹1,299.00 exceeds authorized maximum ₹1,000.00"}, … ]
  }
}
```

### `POST /intents/{id}/authorize` → `AUTHORIZED`
Re-affirms policy, then grants authorization. Cannot be called before validation (`409`).

### `POST /transactions` — execute an authorized purchase

```json
{"intent_id": "int_c464db8f…"}          // or: {"transaction_identity": "txid_…"}
```

Response `200` on success → `state: "COMPLETED"`. Executing an unauthorized
transaction returns `decision: "REQUIRES_AUTHORIZATION"` and does **not** change state.
Re-executing a completed transaction is an idempotent replay — no double charge.

### `GET /transactions/{id}` and `GET /transactions/{id}/audit`

The audit trail answers the seven questions end-to-end — the AI proposal
(`AI_BUYER / INTENT_CREATED`), the policy decision (`POLICY_ENGINE /
POLICY_EVALUATED_*`), the payment (`PAYMENT_GATEWAY / PAYMENT_CONFIRMED`), the order
(`MERCHANT / ORDER_CONFIRMED`), and any recovery (`REFUND_REQUIRED`) — each with a
timestamp, actor, result, reason, and metadata, ordered by a monotonic `seq`.

### Merchant mock API (separate system)

```
GET  /merchant/agent-card           GET  /merchant/products
GET  /merchant/products/{sku}
GET  /merchant/inventory/{sku}      POST /merchant/checkout/validate
POST /merchant/orders               GET  /merchant/orders/{order_id}
POST /merchant/orders/{order_id}/cancel
```

The catalogue includes deliberately-crafted SKUs to make failure/recovery
demonstrable: `SKU-OOS` (out of stock), `SKU-USD` (currency mismatch),
`SKU-FAIL-PAY` (gateway declines), `SKU-FAIL-ORDER` (fulfilment fails after payment).

### `GET /merchant/agent-card` — AI buyer discovery

The merchant exposes a deterministic, versioned discovery document at
`/merchant/agent-card`. It includes the live synthetic catalogue, price/inventory
facts, checkout and intent endpoints, the required `PurchaseIntent` fields, and
the controls TrustRail owns. It is **not** a claim of ACP, AP2, x402, or UAP
compatibility; it is the small agent-readable contract used by this demo.
The root metadata endpoint (`GET /`) links to the card for first-request discovery.

An AI buyer can discover products and propose a bounded structured intent. It
cannot directly set state, declare payment success, bypass policy, or access
Razorpay credentials. That clean split is what makes the merchant safely
transactable by an AI buyer end to end.

---

## 6. Tests

```bash
.venv/bin/python -m pytest
```

**126 tests pass, fully offline.** The suite is hermetic — an in-memory SQLite DB
and a frozen clock per test, with FastAPI dependency overrides — so it is fully
deterministic. The Razorpay path is exercised through an **injected fake client**
and a stdlib reproduction of Razorpay's HMAC signing, so no network or credentials
are ever touched by `make test`.

The ten required scenarios live in `tests/test_spec_scenarios.py`, labelled
`test_1 … test_10`:

| # | Scenario | Expected |
|---|----------|----------|
| 1 | valid purchase under budget | `ALLOW` → `VALIDATED` |
| 2 | purchase exceeds budget | `BLOCK` (amount) → `POLICY_BLOCKED` |
| 3 | expired authorization | `BLOCK` (expiry) → `AUTH_EXPIRED` |
| 4 | wrong merchant | `BLOCK` (merchant) → `INVALID` |
| 5 | wrong SKU | `BLOCK` (skus) → `INVALID` |
| 6 | quantity exceeds limit | `BLOCK` (quantity) → `POLICY_BLOCKED` |
| 7 | same canonical intent | identical `transaction_identity` (one transaction) |
| 8 | different quantity | different `transaction_identity` |
| 9 | invalid state transition | forbidden by the map; execute-before-auth → `REQUIRES_AUTHORIZATION` |
| 10 | every decision | writes a `POLICY_ENGINE` audit event |

Phase 1 supporting suites: `test_canonicalization.py` (identity purity),
`test_state_machine.py` (adjacency map completeness & terminals),
`test_policy_unit.py` (the pure engine, check ordering), `test_flow.py`
(happy path, idempotency, payment/order failure & recovery), and
`test_merchant_api.py` (the mock merchant).

Phase 2 suites (all offline): `test_razorpay_gateway.py` (status mapping,
PENDING-on-create, definite→FAILED vs ambiguous→UNKNOWN taxonomy, receipt cap,
signature maths, secret redaction), `test_webhook_endpoint.py` (503 in mock mode,
signature rejection, amount-mismatch refusal, `payment.failed` is a non-terminal
attempt), `test_reconciliation.py` (confirm/capture/needs-reference/opt-in-fail),
`test_refund.py` (at-most-once refund, retry on error), and
`test_concurrency_locking.py` (webhook + reconcile in either order converge to a
single `COMPLETED`).

**Network-gated contract tests** live in `tests/razorpay/` and are OFF by default.
They talk to real Razorpay Test Mode and skip cleanly unless enabled:

```bash
export RAZORPAY_CONTRACT_TESTS=1
export RAZORPAY_KEY_ID=rzp_test_xxx RAZORPAY_KEY_SECRET=xxx RAZORPAY_WEBHOOK_SECRET=xxx
.venv/bin/python -m pytest tests/razorpay -v
```

---

## 7. Design decisions

- **Money is integer paise**, never floats — Razorpay-native and exact.
- **Identity excludes `agent_id` and `expires_at`** — they describe who/when, not what.
- **Many intents → one transaction**, unique on identity — natural idempotency.
- **The gateway is a `Protocol` seam.** The default `MockPaymentGateway` invents no
  Razorpay APIs; `RazorpayGateway` is the *only* module that imports the SDK or holds
  a secret. Which one is live is decided in one place (`get_gateway()` from
  `settings.payment_gateway`), so the orchestrator is identical in both modes.
- **The AI never touches money.** It cannot authorize payment, call Razorpay, supply
  credentials, set state, or decide success. Every recovery state change funnels
  through the same legality-checked transitions in `transaction.py`, whether the
  trigger is a synchronous execute, a webhook, reconciliation, or a refund.
- **Payment outcome is asynchronous and honest.** `create_payment` opens a Razorpay
  *Order* and returns `PAYMENT_PENDING` — **money is not captured at order creation**.
  A definite pre-money rejection → `PAYMENT_FAILED`; an ambiguous failure →
  `PAYMENT_UNKNOWN`, which **never** auto-recharges and is resolved only by
  authoritative reconciliation.
- **We claim idempotent, at-least-once** handling and **at-most-once** refund (via a
  persisted refund id) — **never** exactly-once distributed execution.
- **The merchant is a separate system** behind a `MerchantClient` protocol, so it can
  later become a real HTTP dependency.

---

## 8. Known weaknesses

Honest limitations of the current build (several are intentional scope choices).
This is **not** a production-ready payment system and does **not** provide
exactly-once execution:

1. **Row locking is Postgres-only.** Execution, webhooks and reconciliation take a
   `SELECT … FOR UPDATE` lock on the transaction row (see `services/locking.py`), so
   concurrent resolvers converge to a single `COMPLETED`. On SQLite the lock is a
   no-op — safe only because SQLite serialises writers; genuine concurrency needs
   Postgres. Redis is intentionally **not** introduced.
2. **Reconciliation is invocable, not scheduled.** `reconcile_pending()` /
   `refund_pending()` authoritatively drive `PAYMENT_PENDING` / `PAYMENT_UNKNOWN` /
   `RECOVERY_PENDING` to resolution, but nothing runs them on a timer yet — they must
   be triggered (e.g. by a cron/worker you add). The recovery logic is real and
   tested; the scheduler is out of scope.
3. **`PAYMENT_UNKNOWN` without an order reference cannot be auto-resolved.** If order
   creation itself returned an ambiguous error we have no Razorpay id to query, so
   reconciliation parks the transaction in `RECOVERY_PENDING` with a
   `RECONCILIATION_NEEDS_REFERENCE` audit note rather than *guessing*. This is the
   safe choice (never double-charge), but it needs a human/out-of-band step.
4. **At-most-once refund, not exactly-once.** The refund is guarded by a persisted
   `razorpay_refund_id`, so it is never re-issued once recorded. A crash in the
   narrow window between Razorpay accepting the refund and us persisting the id could
   leave a refund we must reconcile — we do **not** claim exactly-once here.
5. **Price/inventory checks are as-of-quote.** We snapshot the merchant quote at
   validation and re-check at execution, but there is no hold/reservation on stock,
   so a race with other buyers is possible between authorize and execute.
6. **`create_all()` on startup** is a dev convenience. Production must use the
   Alembic migrations and set `AUTO_CREATE_TABLES=false`.
7. **Single merchant, single currency-per-basket.** The policy engine rejects mixed
   currencies rather than converting; multi-merchant baskets are out of scope.
8. **No authn/z or rate limiting on the API itself** — assumed to sit behind a
   trusted gateway. The `/webhooks/razorpay` route is the exception: it authenticates
   every call by HMAC signature before parsing the body.

---

## 9. Phase 2 — Razorpay Test Mode

Phase 2 fills the `PaymentGateway` seam with a real **Razorpay Test Mode**
integration and makes the asynchronous payment boundary real. The whole point:

```
                       ┌── payment.captured webhook (signed) ──┐
  PAYMENT_PENDING ─────┤                                        ├──▶ PAYMENT_CONFIRMED ─▶ COMPLETED
   (order created,     └── reconciliation sweep (authoritative)┘
    money NOT captured)
        │
        └─ ambiguous gateway/network failure ─▶ PAYMENT_UNKNOWN
                                                 (never auto-recharges;
                                                  reconciliation is authoritative)
```

**The recovery boundary is the differentiator:**
`PAYMENT_UNKNOWN → DO NOT CHARGE AGAIN → RECONCILE AUTHORITATIVELY → CONFIRMED / FAILED / RECOVER`.

### What is built

1. **`RazorpayGateway`** (`app/services/razorpay_gateway.py`) — the only module that
   imports the SDK or holds a secret. `create_payment` opens a Razorpay **Order** and
   returns `PAYMENT_PENDING`; money is **not** captured at order creation. A definite
   `BadRequestError` (pre-money) → `PAYMENT_FAILED`; any ambiguous
   gateway/server/network error → `PAYMENT_UNKNOWN`.
2. **Config & DI seam.** `PAYMENT_GATEWAY` selects `mock` (default) or `razorpay`;
   `get_gateway()` builds the live gateway from `RAZORPAY_KEY_ID`,
   `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`. Selecting `razorpay` without
   complete credentials fails loudly at startup. Credentials live **only** server-side
   and are never returned, logged, or written to an audit event.
3. **Idempotency mapping.** `transaction_identity` is written to the order `notes` and
   persisted as `transaction_identity → razorpay_order_id` (`RazorpayPayment`). A
   repeated `create_payment` **reuses** the existing order instead of opening a second.
   The `TransactionOut` response now exposes `payment_provider`, `payment_status`,
   `razorpay_order_id`, `razorpay_payment_id`, `razorpay_refund_id` (null under mock).
4. **Webhook** `POST /webhooks/razorpay` — verifies the `X-Razorpay-Signature`
   HMAC-SHA256 over the **raw body** *before* parsing (503 in mock mode, 400 on bad
   signature). It validates amount + currency against what we ordered before
   confirming, is idempotent on duplicate delivery, and treats `payment.failed` as a
   non-terminal *attempt* (a later capture may still arrive).
5. **Reconciliation** (`app/services/reconciliation.py`) — the authoritative source of
   truth. It fetches the order/payments from Razorpay, confirms a captured payment,
   captures an authorized-but-uncaptured one, and (opt-in) concludes `FAILED`. It
   **never mints a new order**; an UNKNOWN-without-reference parks in
   `RECOVERY_PENDING`.
6. **Refund** (`app/services/refund.py`) — turns `REFUND_REQUIRED` into a real
   Razorpay refund, then `→ COMPLETED`. Guarded by a persisted refund id so it is
   **at-most-once**, never re-issued.
7. **Concurrency** (`app/services/locking.py`) — `SELECT … FOR UPDATE` on Postgres so
   a webhook and a reconciliation racing on the same transaction converge to a single
   `COMPLETED` (verified in `test_concurrency_locking.py`). No-op on SQLite.

The AI buyer plays **no** part in any of this: it never authorizes payment, never
calls Razorpay, never supplies credentials, never sets state, and never decides
whether a payment succeeded.

### Enabling Razorpay Test Mode

The default is `mock` — everything above runs and all 126 tests pass without any
Razorpay account. To run against real Razorpay **Test Mode** keys (`rzp_test_…`):

```bash
cp .env.example .env
# then set in .env (server-side only — never commit real values):
#   PAYMENT_GATEWAY=razorpay
#   RAZORPAY_KEY_ID=rzp_test_xxx
#   RAZORPAY_KEY_SECRET=xxx
#   RAZORPAY_WEBHOOK_SECRET=xxx        # the secret you set on the webhook in the dashboard
.venv/bin/uvicorn app.main:app --reload
```

Point a Razorpay **webhook** at `https://<your-host>/webhooks/razorpay` for the
`payment.captured`, `payment.failed` and `payment.authorized` events, using the same
`RAZORPAY_WEBHOOK_SECRET`. Locally, expose the port with a tunnel (e.g. ngrok) or use
the demo's signed stand-in webhook (section 5 of `scripts/demo.py`).

Verify the real integration end-to-end with the network-gated contract suite (see
§6) — it is the only code that talks to the live API, and it skips cleanly when the
`RAZORPAY_CONTRACT_TESTS` flag is unset.

> **Not production-ready.** This is a buildathon demonstrator. It does not schedule
> reconciliation, does not provide exactly-once semantics, and its locking is a no-op
> on SQLite (see §8).

---

## 10. Track 01 submission story

**Track:** AI Growth & Agentic Commerce — “make a merchant transactable by an AI
buyer end to end.”

TrustRail takes Track 01's transactable-merchant path. The judge-visible story is:

```text
GET /merchant/agent-card
    -> discover a machine-readable catalogue and bounded purchase contract
POST /intents
    -> AI buyer proposes a structured, user-bounded PurchaseIntent
POST /intents/{id}/validate -> POST /intents/{id}/authorize -> POST /transactions
    -> deterministic policy, legal state transitions, and payment orchestration
PAYMENT_PENDING
    -> signed Razorpay webhook OR authoritative reconciliation
PAYMENT_CONFIRMED -> merchant order -> COMPLETED
```

The differentiator is the failure boundary:

```text
ambiguous gateway failure -> PAYMENT_UNKNOWN -> DO NOT CHARGE AGAIN
    -> authoritative reconciliation -> confirmed / failed / recovery
```

For a live judge run: start the service, open `/docs`, run
`python scripts/demo.py`, and inspect `GET /transactions/{transaction_id}/audit`.
The demo shows discovery, an allowed purchase, a policy block, identity
idempotency, a paid-but-unfulfilled recovery, and the async Razorpay boundary.
TrustRail is not a generic shopping chatbot, an ACP/AP2/UAP implementation, or a
production-ready payment processor.
