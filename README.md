# TrustRail — AI Growth & Agentic Commerce Engine
### Conversational AI Buyer · Gemini-Powered · Razorpay Test Mode

> **Razorpay AI Buildathon 2026 · Track 01 — AI Growth & Agentic Commerce**

TrustRail is an **AI-native commerce growth engine** with a **conversational AI buyer** powered by **Google Gemini**. It helps merchants **sell more to AI buyers** through intelligent bundle recommendations and cross-sells, while ensuring every monetary action remains bounded, explainable, gated, idempotent, and recoverable.

```
   User ──chats──▶  AI Agent (Gemini)  ──reasons──▶  Growth Engine  ──recommends──▶  TrustRail Policy
 (natural language)    (understands catalog)         (bundles/cross-sells)            (budget-gated)
                                                                                          │
                                        ┌─────────────────────────────────────────────────┘
                                        ▼
                                   TrustRail ──quotes/orders──▶  Merchant
                                 (8 policy checks)
                                        │
                                        └──pays via──▶  Razorpay Test Mode
                                                        (real orders + webhooks)
```

The core thesis: **AI discovers what the user needs, proposes the best deal, and executes the purchase — while TrustRail guarantees the AI can NEVER exceed the user's authorized budget.**

### 🚀 Try It Now

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload
# Open http://localhost:8000/agent     → AI Chat UI
# Open http://localhost:8000/dashboard  → Growth Analytics
```

---

## Table of contents
1. [Why this exists](#1-why-this-exists)
2. [AI Commerce Agent](#2-ai-commerce-agent)
3. [Architecture](#3-architecture)
4. [The transaction lifecycle](#4-the-transaction-lifecycle)
5. [Running locally](#5-running-locally)
6. [API reference & examples](#6-api-reference--examples)
7. [Tests](#7-tests)
8. [Design decisions](#8-design-decisions)
9. [Known weaknesses](#9-known-weaknesses)
10. [Phase 2 — Razorpay Test Mode](#10-phase-2--razorpay-test-mode)
11. [Track 01 submission story](#11-track-01-submission-story)

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

## 2. AI Commerce Agent

The **AI Commerce Agent** is TrustRail's conversational frontend — a Gemini-powered AI buyer that discovers products, reasons about the best deals, and executes purchases through TrustRail's deterministic pipeline.

### How it works

```
User: "I need peripherals for my home office"
  ↓
AI Agent reads merchant catalog → reasons about needs → proposes Workstation Bundle
  ↓
User: "Get the bundle!"
  ↓
AI triggers purchase → Growth Engine evaluates bundle at ₹3,498 (saves ₹5,299)
  ↓
TrustRail Pipeline: Intent → 8 Policy Checks → Authorize → Pay → Complete
  ↓
✅ Transaction COMPLETED | txn_abc123 | 8/8 checks passed
📊 Growth: +₹2,332 incremental revenue | 200% AOV uplift
```

### Key capabilities

| Capability | How |
|---|---|
| **Natural language shopping** | User describes needs, AI finds products |
| **Intelligent upselling** | AI recommends bundles when they save money |
| **Budget enforcement** | AI cannot exceed the user's authorized budget |
| **Live policy gating** | Every purchase passes 8 deterministic checks |
| **Growth analytics** | GMV, incremental revenue, AOV uplift, attach rate |
| **Gemini + fallback** | Real AI when API key is set; smart rule-based fallback otherwise |

### Endpoints

| Endpoint | What it does |
|---|---|
| `POST /chat` | Conversational AI buyer (JSON API) |
| `GET /agent` | Interactive chat UI |
| `GET /dashboard` | Growth analytics dashboard |
| `GET /analytics/growth` | Revenue metrics API |

---

## 3. Architecture

TrustRail is a small FastAPI application with a clean separation between the
**orchestrator** (which has side effects and sequences the phases) and the
**pure decision core** (canonicalisation, policy engine, state machine).

```
app/
├── main.py                 FastAPI app, error→HTTP mapping, lifespan (create tables + seed)
├── config.py               Settings (DATABASE_URL, GEMINI_API_KEY, flags) via pydantic-settings
├── db.py                   SQLAlchemy engine/session; SQLite ⇄ PostgreSQL interchangeable
├── clock.py                Injectable Clock (deterministic time in tests)
├── ids.py                  Prefixed IDs + identity_from_canonical() (SHA-256)
├── money.py                Integer minor units (paise); ₹ formatting
├── enums.py                TransactionState, Decision, Actor, AuditResult, …
├── errors.py               Domain errors (mapped to HTTP status in main.py)
│
├── schemas/                Pydantic request/response contracts (the API boundary)
│   ├── intent.py           PurchaseIntentIn — the transaction CONTRACT
│   ├── chat.py             ChatMessageIn/Out — conversational AI schemas
│   ├── growth.py           Growth recommendation & analytics schemas
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
│   ├── ai_agent.py         ★ Gemini-powered AI buyer agent (conversational commerce)
│   ├── growth.py           Growth policy: bundle/cross-sell evaluation + budget gating
│   ├── growth_analytics.py Revenue metrics: GMV, AOV uplift, attach rate
│   ├── intent.py           canonicalize() → deterministic transaction identity
│   ├── policy.py           evaluate() — PURE function, no I/O, no LLM text
│   ├── state_machine.py    ALLOWED_TRANSITIONS adjacency map + guards
│   ├── payment.py          PaymentGateway Protocol + MockPaymentGateway (the seam)
│   ├── razorpay_gateway.py RazorpayGateway — the ONLY module that imports the SDK
│   ├── gateway.py          get_gateway() DI seam — picks mock vs razorpay from settings
│   ├── webhook.py          signature-verified webhook → legality-checked state moves
│   ├── reconciliation.py   authoritative status sweep for PENDING/UNKNOWN/RECOVERY
│   ├── refund.py           REFUND_REQUIRED → Razorpay refund → COMPLETED
│   ├── locking.py          SELECT … FOR UPDATE row locks (Postgres) / no-op on SQLite
│   ├── audit.py            append-only audit recorder
│   └── transaction.py      ORCHESTRATOR — the only place state changes funnel through
│
├── merchant/               Mock external merchant system (deliberately separate)
│   ├── catalogue.py        Synthetic catalogue + idempotent seeding
│   ├── service.py          Pricing, stock, idempotent orders, cancellation
│   ├── client.py           MerchantClient Protocol + InProcessMerchantClient seam
│   └── router.py           /merchant/* endpoints
│
├── ui/                     ★ Interactive frontends
│   ├── chat.html           Conversational AI buyer chat interface
│   ├── dashboard.py        Dashboard + agent route handlers
│   └── index.html          Landing page
│
└── api/                    TrustRail HTTP surface
    ├── chat.py             ★ POST /chat — conversational AI buyer endpoint
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

## 4. The transaction lifecycle

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

## 5. Running locally

Requires **Python ≥ 3.11** (tested on 3.14).

### Quick start (SQLite + AI Agent)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Configure (optional — works without any keys using mock + fallback)
cp .env.example .env
# Edit .env to add:
#   GEMINI_API_KEY=your_google_ai_studio_key    # for real AI responses
#   PAYMENT_GATEWAY=razorpay                     # for Razorpay Test Mode
#   RAZORPAY_KEY_ID=rzp_test_xxx
#   RAZORPAY_KEY_SECRET=xxx

# Start the server
.venv/bin/uvicorn app.main:app --reload
```

Open in your browser:
- **http://localhost:8000/agent** → 🤖 AI Chat UI (conversational buyer)
- **http://localhost:8000/dashboard** → 📊 Growth Analytics Dashboard
- **http://localhost:8000/docs** → 📄 Interactive API docs

### Terminal demo

```bash
.venv/bin/python scripts/demo.py
```

Walks the ALLOW happy path, over-budget BLOCK, transaction identity, REFUND_REQUIRED recovery, and async payment boundary.

### Docker (Postgres)

```bash
docker compose up --build      # starts Postgres + the API
```

### Make targets

```
make install   make run   make test   make demo   make compose-up   make migrate
```

---

## 6. API reference & examples

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

## 7. Tests

```bash
.venv/bin/python -m pytest
```

**133 tests pass, fully offline.** The suite is hermetic — an in-memory SQLite DB,
a frozen clock, and the mock gateway per test — so it is fully deterministic even
when `.env` configures Razorpay. The Razorpay path is exercised through an
**injected fake client** and HMAC signature reproduction.

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

Additional suites cover canonicalization, state machine, policy engine, flow/recovery,
merchant API, Razorpay gateway, webhooks, reconciliation, refunds, concurrency locking,
and **growth engine** (bundle evaluation, analytics, cart recovery).

**Network-gated contract tests** live in `tests/razorpay/` and are OFF by default.
They talk to real Razorpay Test Mode and skip cleanly unless enabled:

```bash
export RAZORPAY_CONTRACT_TESTS=1
export RAZORPAY_KEY_ID=rzp_test_xxx RAZORPAY_KEY_SECRET=xxx RAZORPAY_WEBHOOK_SECRET=xxx
.venv/bin/python -m pytest tests/razorpay -v
```

---

## 8. Design decisions

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

## 9. Known weaknesses

Honest limitations of the current build (several are intentional scope choices).
This is **not** a production-ready payment system and does **not** provide
exactly-once execution:

1. **Row locking on Postgres.** Execution, webhooks, reconciliation, and merchant inventory decrements take `SELECT … FOR UPDATE` row locks (see `services/locking.py`), ensuring concurrent resolvers converge to a single `COMPLETED` and preventing overselling. On SQLite the lock is a graceful fallback because SQLite serialises writes.
2. **Autonomous Background Worker.** `reconciliation_worker.py` runs as an active background daemon in the FastAPI lifespan, periodically sweeping `PAYMENT_PENDING`, `PAYMENT_UNKNOWN`, and `RECOVERY_PENDING` transactions every 30s. Manual on-demand sweeps and telemetry are also available via `POST /reconciliation/sweep` and `GET /reconciliation/status`.
3. **`PAYMENT_UNKNOWN` without an order reference cannot be auto-resolved.** If order
   creation itself returned an ambiguous error we have no Razorpay id to query, so
   reconciliation parks the transaction in `RECOVERY_PENDING` with a
   `RECONCILIATION_NEEDS_REFERENCE` audit note rather than *guessing*. This is the
   safe choice (never double-charge), but it needs a human/out-of-band step.
4. **At-most-once refund, not exactly-once.** The refund is guarded by a persisted
   `razorpay_refund_id`, so it is never re-issued once recorded.
5. **Price/inventory checks are verified live.** We check the merchant quote at
   validation and re-verify stock under row lock during execution.
6. **`create_all()` on startup** is a dev convenience. Production must use the
   Alembic migrations and set `AUTO_CREATE_TABLES=false`.
7. **Single merchant, single currency-per-basket.** The policy engine rejects mixed
   currencies rather than converting; multi-merchant baskets are out of scope.
8. **No authn/z or rate limiting on the API itself** — assumed to sit behind a
   trusted gateway. The `/webhooks/razorpay` route is the exception: it authenticates
   every call by HMAC signature before parsing the body.

---

## 10. Phase 2 — Razorpay Test Mode

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

## 11. Track 01 submission story

**Track:** AI Growth & Agentic Commerce — *"Grow the merchant's revenue, and make them sellable to AI buyers."*

TrustRail demonstrates **BOTH** sides of Track 01 through ONE coherent product:

### Dimension 1: AI Revenue Growth & Agentic Commerce

| What | How |
|------|-----|
| **Conversational AI Buyer** | Gemini-powered agent discovers products, reasons about needs, proposes bundles |
| **Intelligent Upselling** | "I need a mouse" → AI recommends Workstation Bundle (saves ₹5,299) |
| **Budget-Gated Execution** | AI executes purchase through 8 deterministic policy checks |
| **Growth Analytics** | Real-time GMV, incremental revenue (+₹2,332), AOV uplift (+200%), attach rate |
| **Cart Recovery** | Bounded incentive vouchers re-engage abandoned intents |
| **Machine-Readable Catalog** | `/merchant/agent-card` for AI buyer discovery |

### Dimension 2: Deterministic Transaction Integrity

| What | How |
|------|-----|
| **Canonical Identity** | SHA-256 idempotency key — no double charges |
| **Pure Policy Engine** | 8 checks, no I/O, no LLM text — the AI cannot game it |
| **Strict State Machine** | Adjacency-controlled lifecycle, no shortcutting |
| **Razorpay Test Mode** | Real orders + HMAC-SHA256 webhook verification |
| **Authoritative Recovery** | PAYMENT_UNKNOWN → reconcile → never auto-recharge |
| **At-Most-Once Refunds** | Persisted refund ID prevents re-issue |

### The Demo

1. **AI Chat UI** → `http://localhost:8000/agent` — Talk to the AI, watch it recommend bundles, and see live transactions execute
2. **Growth Dashboard** → `http://localhost:8000/dashboard` — Real-time revenue metrics after each AI purchase
3. **Terminal Walkthrough** → `python scripts/demo.py` — 5-scenario lifecycle demo
4. **Audit Trail** → `GET /transactions/{id}/audit` — Full explainable justification for every state change
