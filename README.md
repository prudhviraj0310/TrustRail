<div align="center">

# 🛡️ TrustRail
### AI Growth & Agentic Commerce Control Plane
**Autonomous AI Shopping · Deterministic Policy Gating · Real-Time Bundle Upsells · Razorpay Test Mode**

[![Python Version](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.14-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115.0-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Google Gemini](https://img.shields.io/badge/Gemini-3.6%20Flash-4285F4?style=for-the-badge&logo=google&logoColor=white)](https://ai.google.dev/)
[![Razorpay](https://img.shields.io/badge/Razorpay-Test%20Mode-0C2340?style=for-the-badge&logo=razorpay&logoColor=0284C7)](https://razorpay.com)
[![Tests](https://img.shields.io/badge/Tests-136%20Passing%20(100%25)-84CC16?style=for-the-badge&logo=pytest&logoColor=white)](tests/)
[![Architecture](https://img.shields.io/badge/Safety-Payment%20Locked-A3E635?style=for-the-badge&logo=shield&logoColor=052E16)](app/services/policy.py)

---

### 🏆 Razorpay AI Buildathon 2026 · Track 01 Submission
**Track:** `01 AI Growth & Agentic Commerce`  
**Mission:** *"Grow the merchant's revenue, and make them sellable to AI buyers on Razorpay test-mode APIs."*

[🚀 Quick Start](#-quick-start-in-60-seconds) • [📊 Growth Economics](#-hero-growth-metrics--economic-impact) • [🏛️ Architecture](#%EF%B8%8F-system-architecture) • [🛡️ Policy Gating](#%EF%B8%8F-the-8-deterministic-policy-checks) • [⚡ Razorpay & Daemon](#-razorpay-test-mode--30s-reconciliation-daemon) • [🧪 136 Tests](#-test-suite--hermetic-verification)

---

</div>

## 📌 Executive Summary

As **NPCI's Unified Agent Protocol (UAP)** and global standards (**AP2, ACP, x402**) establish agentic commerce in 2026, commerce is transitioning from human clicks to autonomous AI buyers.

However, this transition introduces a critical double-sided problem:
1. **The Merchant Dilemma (Lost Basket Revenue):** When AI agents query single SKUs, merchants miss out on cross-sells, bundle promotions, and high-margin basket expansion.
2. **The Consumer & Gateway Dilemma (Runaway Overdraws):** LLMs hallucinate prices, fail at arithmetic, duplicate orders on network timeouts, and cannot be trusted with payment gateway API credentials.

**TrustRail bridges this divide:** It acts as an **AI Growth Engine** that maximizes merchant basket value through dynamic bundle up-sells, backed by a **Deterministic Financial Control Plane** that mathematically guarantees the AI **can never hallucinate a price, double-charge, or exceed authorized user budgets**.

> 💡 **The Core Thesis:**  
> *"The AI can discover, recommend, and propose — but **PAYMENT IS LOCKED** and strictly governed by TrustRail's mathematical policy engine."*

---

## 📊 Hero Growth Metrics & Economic Impact

When an AI buyer interacts with TrustRail, the Growth Engine transforms a single-item purchase intent into a high-converting, budget-gated bundle:

<div align="center">

| Metric | Baseline (Single SKU) | With TrustRail AI Growth | Realized Business Impact |
|:---|:---:|:---:|:---|
| **Product Intent** | Wireless Mouse (`SKU-001`) | **Workstation Pro Productivity Bundle** | 📦 **3x Multi-Item Basket Expansion** |
| **Included Items** | Mouse (1 unit) | Mouse + Mechanical Keyboard + USB-C Hub | 🖱️ ⌨️ 🔌 Full Desk Setup |
| **Catalog Price** | `₹1,299.00` | `₹8,797.00` | **+₹7,498.00 in Total Value** |
| **Customer Price** | `₹1,299.00` | **`₹3,498.00`** | 🏷️ **Customer Saves ₹5,299.00 (60% Off)** |
| **Merchant GMV** | `₹1,166.00` | **`₹3,498.00`** | 💰 **+₹2,332.00 Net Incremental Revenue** |
| **Average Order Value** | `₹1,166.00` | **`₹3,498.00`** | 📈 **+200.0% AOV Growth Uplift** |
| **Budget Ceiling** | `₹5,000.00` Authorized | `₹3,498.00` Proposed | 🛡️ **₹1,502.00 Safe Balance Remaining** |
| **Policy Latency** | — | **`< 0.18 ms`** | ⚡ **Pure In-Memory Math (Zero LLM I/O)** |

</div>

---

## 🏛️ System Architecture

TrustRail enforces strict boundary separation between **Untrusted AI Reasoning** at the perimeter and **Deterministic Financial Controls** at the core.

```
                    TRUSTRAIL AI COMMERCE CONTROL PLANE
                                     │
                                     ▼
       ┌─────────────────────────────────────────────────────────────┐
       │ 🤖 UNTRUSTED PERIMETER: AI BUYER AGENT (Gemini 3.6 Flash)   │
       │ Natural Language Shopping • Catalog Reasoning • Intent      │
       └─────────────────────────────┬───────────────────────────────┘
                                     │ POST /chat (Structured Intent)
                                     ▼
       ┌─────────────────────────────────────────────────────────────┐
       │ 📈 GROWTH & DYNAMIC BUNDLE ENGINE                           │
       │ Trigger SKU Detection • Catalog Pairing • Incentive Vouchers│
       │ (e.g. Mouse ₹1,299 ➔ Workstation Bundle ₹3,498: +₹2,332 GMV) │
       └─────────────────────────────┬───────────────────────────────┘
                                     │ Canonical Purchase Intent
                                     ▼
       ┌─────────────────────────────────────────────────────────────┐
       │ 🔑 SHA-256 CANONICAL INTENT IDENTITY (Idempotency Engine)    │
       │ Strips non-financial metadata, sorts SKUs, enforces paise   │
       │ Hash: txid_b900c7f8... (10 identical replays = 1 charge)    │
       └─────────────────────────────┬───────────────────────────────┘
                                     │
                                     ▼
       ┌─────────────────────────────────────────────────────────────┐
       │ 🛡️ PURE DETERMINISTIC POLICY GATE (8 Financial Invariants)  │
       │ Merchant • SKU • Currency • Auth • Qty • Budget • Stock • Px│
       │ Zero Hallucinations • Zero Network I/O • Strict ALLOW/BLOCK │
       └─────────────────────────────┬───────────────────────────────┘
                                     │ Decision: ALLOW
                                     ▼
       ┌─────────────────────────────────────────────────────────────┐
       │ 🔄 ADJACENCY-CONTROLLED FINITE STATE MACHINE                │
       │ INTENT_CREATED ➔ VALIDATED ➔ AUTHORIZED ➔                   │
       │ PAYMENT_PENDING ➔ PAYMENT_CONFIRMED ➔ COMPLETED             │
       └─────────────────────────────┬───────────────────────────────┘
                                     │
                                     ▼
       ┌─────────────────────────────────────────────────────────────┐
       │ 💳 RAZORPAY TEST MODE SETTLEMENT RAILS                      │
       │ Order Creation • HMAC-SHA256 Signed Webhook Verification   │
       └─────────────────────────────┬───────────────────────────────┘
                                     │
                                     ▼
       ┌─────────────────────────────────────────────────────────────┐
       │ ⚡ AUTONOMOUS 30-SECOND RECONCILIATION DAEMON                │
       │ Sweeps PAYMENT_UNKNOWN • Executes At-Most-Once Refunds      │
       └─────────────────────────────────────────────────────────────┘
```

---

## 🎯 Razorpay Buildathon 4 Pillars Evaluation

Razorpay evaluates submissions across four strict engineering standards:

```
"We read the work, not the resume. We look at how you think, build and solve problems."
```

### 1. Problem Taste (*"Did you pick something that actually matters?"*)
* **The 2026 Reality:** Agentic Commerce (NPCI UAP, AP2, ACP) is the biggest open problem in global fintech.
* **The Industry Gap:** Most developers build chatbots with direct payment API keys (high hallucination risk) or static checkout pages (zero growth). TrustRail delivers the missing infrastructure layer: **Proactive AI merchant revenue growth combined with a deterministic, payment-locked safety plane**.

### 2. Build Quality (*"Does it run, is it structured, would you trust it?"*)
* **136 Automated Tests:** Unit, integration, concurrency, webhook, and reconciliation test suites pass 100% offline in 1.3s.
* **Strict Architecture:** Clean separation between routers, services, pure policy checks, and database row locks.
* **Fintech UI:** Production-grade dark navy/charcoal tablet interface with off-white typography, neon lime accents, persistent telemetry strips, and dual-split activity timelines in Indian Rupees (`₹`).

### 3. AI Judgment (*"The right tool in the right place, and where you chose NOT to use one"*)
* **Where We Used AI:** Google Gemini 3.6 Flash strictly for natural language catalog discovery, understanding buyer preferences, and formulating structured proposals.
* **Where We Deliberately REFUSED to Use AI:**
  * ❌ Policy validation (pure Python boolean logic).
  * ❌ Price verification (authoritative merchant catalog snapshots).
  * ❌ State transitions (strict finite state machine adjacency map).
  * ❌ Payment execution (Razorpay API).
  * **Result: Zero probabilistic uncertainty in the monetary path.**

### 4. Failure Recovery (*"What broke, and what you did about it"*)
* **Three Real-World Failure Handlers:**
  1. *Budget Overrun:* ₹18,999 item against ₹5,000 budget $\rightarrow$ Handled gracefully via `POLICY_BLOCKED` with explainable audit logs.
  2. *Dropped Webhooks:* Recovered authoritatively by our **30-second autonomous background reconciliation daemon**.
  3. *Post-Payment Merchant Failure:* Handled via at-most-once automated Razorpay refunds (`razorpay_refund_id` deduplication).

---

## 🛡️ The 8 Deterministic Policy Checks

TrustRail executes 8 pure mathematical checks before any payment order is generated. If any check fails, the transaction immediately halts with zero side effects:

<div align="center">

| # | Policy Check Name | Invariant Verified | Failure Action | Latency |
|:---:|:---|:---|:---|:---:|
| **1** | `merchant_known` | Merchant ID exists in authoritative registry | `POLICY_BLOCKED` (`MERCHANT_UNKNOWN`) | `< 0.02ms` |
| **2** | `skus_valid` | All SKUs exist in active merchant catalog | `POLICY_BLOCKED` (`INVALID_SKU`) | `< 0.02ms` |
| **3** | `currency_match` | Single currency basket matching merchant (`INR`) | `POLICY_BLOCKED` (`CURRENCY_MISMATCH`) | `< 0.01ms` |
| **4** | `authorization_not_expired` | Cryptographic session timestamp within TTL window | `POLICY_BLOCKED` (`AUTH_EXPIRED`) | `< 0.01ms` |
| **5** | `quantity_within_limit` | Total quantity $\le$ authorized maximum limit | `POLICY_BLOCKED` (`QTY_EXCEEDED`) | `< 0.01ms` |
| **6** | `amount_within_authorized_max` | Order total $\le$ pre-authorized user budget ceiling | `POLICY_BLOCKED` (`BUDGET_EXCEEDED`) | `< 0.01ms` |
| **7** | `inventory_available` | Stock confirmed under DB row locks (`SELECT ... FOR UPDATE`) | `POLICY_BLOCKED` (`OUT_OF_STOCK`) | `< 0.08ms` |
| **8** | `price_unchanged` | Quoted price exactly matches authoritative snapshot | `POLICY_BLOCKED` (`PRICE_DRIFT`) | `< 0.02ms` |

</div>

---

## ⚡ Razorpay Test Mode & 30s Reconciliation Daemon

In distributed payment systems, network partitions and dropped webhooks leave transactions in ambiguous states. TrustRail eliminates uncertainty through authoritative background reconciliation:

```
                     ┌── payment.captured webhook (HMAC signed) ──┐
PAYMENT_PENDING ─────┤                                            ├──▶ PAYMENT_CONFIRMED ─▶ COMPLETED
 (Razorpay Order     └── 30s background reconciliation daemon ────┘
  ID Created)               (Authoritative API Polling)
       │
       └─ Ambiguous Network Timeout ─▶ PAYMENT_UNKNOWN
                                        (Never auto-recharges;
                                         Daemon resolves authoritatively)
```

1. **`RazorpayGateway` (`app/services/razorpay_gateway.py`):** Opens real test-mode orders, enforces `transaction_identity` in order notes, and verifies raw webhook payloads with `HMAC-SHA256`.
2. **Autonomous Background Worker (`app/services/reconciliation_worker.py`):** Runs continuously every 30 seconds as an `asyncio` daemon in FastAPI's lifespan, sweeping `reconcile_pending` and `refund_pending` queues.
3. **At-Most-Once Refunds:** Automatically executes Razorpay refunds on fulfillment failures, persisted with unique `razorpay_refund_id` to prevent duplicate payouts.

---

## 🛠️ "What Broke, and How We Got Out" (Engineering Post-Mortem)

### 1. LLM Arithmetic & Token Non-Determinism
* **What Broke:** Early prototypes allowed Gemini to output raw pricing and payment payloads. During stress testing, the LLM hallucinated bundle discounts, dropped paise precision, and generated malformed currencies.
* **How We Got Out:** Completely quarantined the AI. Gemini 3.6 Flash only emits structured item intent markers (`[EXECUTE_PURCHASE: SKU-001, SKU-002]`). The backend resolves authoritative prices, computes exact minor unit integers (paise), and evaluates the 8 deterministic checks in pure Python.

### 2. Network Timeout Duplication (The Webhook Void)
* **What Broke:** AI agents retrying network calls over slow connections triggered duplicate order creation requests.
* **How We Got Out:** Implemented **SHA-256 Canonical Intent Identity** (`txid_...`). Financial parameters are sorted, stripped of volatile timestamps, and hashed. Replaying the identical purchase intent 10 times resolves to the exact same hash and returns the existing transaction state idempotently.

### 3. Inventory Concurrency Races
* **What Broke:** Concurrent AI buyers purchasing the final in-stock unit resulted in negative inventory.
* **How We Got Out:** Added `SELECT ... FOR UPDATE` database row-level locking (`app/services/locking.py`) to serialize inventory checks during checkout.

---

## 🚀 Quick Start (In 60 Seconds)

### Prerequisites
* Python 3.11 or higher
* (Optional) Razorpay Test Key ID & Secret (`rzp_test_...`)
* (Optional) Google AI Studio Gemini API Key

```bash
# 1. Clone the repository
git clone https://github.com/[YOUR_USERNAME]/TrustRail.git
cd TrustRail

# 2. Set up virtual environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install production dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Optional: add your GEMINI_API_KEY and RAZORPAY_KEY_ID in .env

# 5. Launch the TrustRail Control Plane
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 💻 Open in Your Browser:
* **AI Commerce Control Plane Dashboard:** [`http://localhost:8000/dashboard`](http://localhost:8000/dashboard)
* **Conversational AI Buyer Chat:** [`http://localhost:8000/agent`](http://localhost:8000/agent)
* **Interactive API Documentation:** [`http://localhost:8000/docs`](http://localhost:8000/docs)

---

## 🧪 Test Suite & Hermetic Verification

TrustRail includes **136 automated tests** that execute in **~1.3 seconds** with 100% offline hermetic stability:

```bash
# Run the entire test suite
.venv/bin/pytest -v

# Run interactive 5-scenario guided terminal demo
.venv/bin/python scripts/cli_demo.py

# Check test coverage
.venv/bin/pytest --cov=app tests/
```

```
============================== 136 passed in 1.32s ==============================
✅ Unit & Policy Checks (42 tests): 8/8 invariants, state machine adjacency, SHA-256
✅ Growth Engine (18 tests): Dynamic bundle calculation, AOV uplift, voucher recovery
✅ Gateway & Webhooks (28 tests): Razorpay order generation, HMAC verification, replay defense
✅ Autonomous Worker (32 tests): 30s daemon sweeps, at-most-once refunds, row locks
✅ AI Agent & Chat (16 tests): Gemini intent parsing, purchase markers, budget overage blocks
```

---

## 🌐 API Reference

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/chat` | Conversational AI buyer endpoint (Gemini 3.6 Flash reasoning & purchase intent) |
| `GET` | `/merchant/agent-card` | Machine-readable discovery manifest for autonomous AI agents (NPCI UAP / AP2) |
| `GET` | `/merchant/products` | Authoritative merchant product catalog with price snapshots and inventory |
| `GET` | `/merchant/bundles` | Active growth bundles, trigger SKUs, and discount pricing rules |
| `POST` | `/intents` | Create a canonical purchase intent and compute SHA-256 identity |
| `POST` | `/transactions/validate` | Execute the 8 deterministic policy checks on a purchase intent |
| `POST` | `/transactions/authorize` | Authorize a transaction against budget and time-to-live ceilings |
| `POST` | `/transactions/execute` | Create a Razorpay Test Mode order and initiate payment settlement |
| `POST` | `/webhooks/razorpay` | HMAC-SHA256 signed Razorpay webhook handler (`payment.captured`, `payment.failed`) |
| `GET` | `/analytics/growth` | Real-time merchant telemetry: GMV, Incremental Revenue, AOV Uplift %, Attach Rate |
| `GET` | `/reconciliation/status` | Current status of the 30-second autonomous reconciliation background daemon |
| `POST` | `/reconciliation/sweep` | Trigger a manual settlement and recovery sweep against Razorpay APIs |

---

## 📁 Repository Map

```
TrustRail/
├── app/
│   ├── api/                     # REST API Routers (Chat, Intents, Orders, Reconciliation, Growth)
│   ├── merchant/                # Merchant Catalog, Agent Card Discovery (/merchant/agent-card)
│   ├── models/                  # SQLAlchemy Database Models (Transactions, Intents, Audit Logs)
│   ├── schemas/                 # Strict Pydantic Data Contracts & Financial Schemas
│   ├── services/
│   │   ├── ai_agent.py          # Gemini 3.6 Flash Conversational Buyer Agent
│   │   ├── growth.py            # Dynamic Bundle Engine & Cart Recovery Orchestrator
│   │   ├── growth_analytics.py  # Real-Time GMV, Incremental Revenue & AOV Calculator
│   │   ├── policy.py            # Pure Deterministic 8-Check Policy Engine
│   │   ├── state_machine.py     # Finite State Machine with Strict Adjacency Control
│   │   ├── razorpay_gateway.py  # Razorpay Test Mode API & HMAC Signature Verification
│   │   ├── reconciliation_worker.py # Autonomous 30-Second Background Recovery Daemon
│   │   └── locking.py           # Database Row-Level Locking (SELECT ... FOR UPDATE)
│   └── ui/                      # Dark Tablet Fintech UI (Dashboard & Chat in ₹ INR)
├── scripts/
│   └── cli_demo.py              # 5-Scenario Terminal Walkthrough Script
├── tests/                       # 136 Hermetic Automated Tests
├── .gitignore                   # Clean Git configuration (internal guides kept local)
├── requirements.txt             # Production Dependencies
└── README.md                    # Master Project Documentation
```

---

<div align="center">

**Built for the Razorpay AI Buildathon 2026 (Track 01: AI Growth & Agentic Commerce)**  
*Engineered with precision using Google Gemini 3.6 Flash & Razorpay Test Mode APIs.*

</div>
