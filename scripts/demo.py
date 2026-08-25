#!/usr/bin/env python3
"""TrustRail end-to-end demo.

Walks the full lifecycle against a *running* TrustRail server and prints every
request/response so you can see the deterministic decisions and the audit trail.

Start the server first:

    uvicorn app.main:app --reload

then, in another terminal:

    python scripts/demo.py                 # defaults to http://127.0.0.1:8000
    python scripts/demo.py http://host:8000

It demonstrates: (1) AI-readable merchant discovery, (2) the ALLOW happy path to
COMPLETED, (3) an over-budget BLOCK, (4) transaction-identity determinism (same
purchase -> same transaction), (5) the paid-but-unfulfilled REFUND_REQUIRED
recovery path, and (6) the Phase 2 asynchronous payment boundary
(PENDING -> webhook -> CONFIRMED).

Section 5 adapts to the server's configured gateway:

* Default **mock** mode: it explains what the async boundary would do, since the
  mock confirms synchronously and the webhook endpoint is disabled (503).
* **Razorpay Test Mode** (``PAYMENT_GATEWAY=razorpay``): execution returns
  ``PAYMENT_PENDING`` with a real ``order_...`` id; the demo then delivers a
  locally *signed* ``payment.captured`` webhook to drive it to CONFIRMED. Signing
  needs the same ``RAZORPAY_WEBHOOK_SECRET`` the server uses — this is a
  dev/test convenience that stands in for Razorpay's servers; in production the
  signature is produced by Razorpay, never by us.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
from datetime import UTC, datetime, timedelta

import httpx

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
MERCHANT_ID = "MERCH_DEMO_001"
# Only used by section 5 in Razorpay mode, to sign the stand-in webhook the way
# Razorpay's servers would. Never a real credential in mock mode.
WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")


def expires_in(hours: int = 1) -> str:
    return (datetime.now(UTC) + timedelta(hours=hours)).isoformat()


def section(title: str) -> None:
    print("\n" + "=" * 78)
    print(f"  {title}")
    print("=" * 78)


def show(method: str, path: str, resp: httpx.Response) -> dict:
    print(f"\n$ {method} {path}  ->  {resp.status_code}")
    try:
        body = resp.json()
        print(json.dumps(body, indent=2)[:2000])
        return body
    except Exception:
        print(resp.text)
        return {}


def intent_body(*, items, max_amount, max_quantity=1, currency="INR",
                agent_id="agent-demo-1", merchant_id=MERCHANT_ID) -> dict:
    return {
        "agent_id": agent_id,
        "merchant_id": merchant_id,
        "items": items,
        "constraints": {
            "max_amount": max_amount,
            "currency": currency,
            "max_quantity": max_quantity,
        },
        "authorization": {"expires_at": expires_in()},
    }


def main() -> None:
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as c:
        section("0. Health")
        show("GET", "/health", c.get("/health"))

        section("0b. AI BUYER DISCOVERY — machine-readable merchant card")
        show("GET", "/merchant/agent-card", c.get("/merchant/agent-card"))

        # ------------------------------------------------------------------ #
        section("1. HAPPY PATH — ₹1,299 mouse under a ₹5,000 budget -> COMPLETED")
        body = intent_body(items=[{"sku": "SKU-001", "quantity": 1}], max_amount=500000)
        created = show("POST", "/intents", c.post("/intents", json=body))
        iid, txid = created["intent_id"], created["transaction_id"]

        show("POST", f"/intents/{iid}/validate", c.post(f"/intents/{iid}/validate"))
        show("POST", f"/intents/{iid}/authorize", c.post(f"/intents/{iid}/authorize"))
        show("POST", "/transactions", c.post("/transactions", json={"intent_id": iid}))
        show("GET", f"/transactions/{txid}", c.get(f"/transactions/{txid}"))

        section("1b. AUDIT TRAIL — the full explainable history")
        show("GET", f"/transactions/{txid}/audit", c.get(f"/transactions/{txid}/audit"))

        # ------------------------------------------------------------------ #
        section("2. POLICY BLOCK — ₹1,299 mouse but only ₹1,000 authorized -> BLOCK")
        body = intent_body(items=[{"sku": "SKU-001", "quantity": 1}], max_amount=100000)
        created = show("POST", "/intents", c.post("/intents", json=body))
        show(
            "POST",
            f"/intents/{created['intent_id']}/validate",
            c.post(f"/intents/{created['intent_id']}/validate"),
        )

        # ------------------------------------------------------------------ #
        section("3. IDENTITY DETERMINISM — same purchase twice -> same transaction")
        # A distinct basket (SKU-003) so this section stands alone; only the
        # agent_id and item ordering differ, which are excluded from identity.
        b1 = intent_body(items=[{"sku": "SKU-003", "quantity": 1}], max_amount=500000,
                         agent_id="agent-A")
        b2 = intent_body(items=[{"sku": "SKU-003", "quantity": 1}], max_amount=500000,
                         agent_id="agent-B")
        r1 = show("POST", "/intents (agent-A)", c.post("/intents", json=b1))
        r2 = show("POST", "/intents (agent-B)", c.post("/intents", json=b2))
        same = r1.get("transaction_id") == r2.get("transaction_id")
        print(f"\n  same transaction_id? {same}  "
              f"({r1.get('transaction_identity')})")

        # ------------------------------------------------------------------ #
        section("4. RECOVERY — payment captured but fulfilment fails -> REFUND_REQUIRED")
        body = intent_body(items=[{"sku": "SKU-FAIL-ORDER", "quantity": 1}],
                           max_amount=200000)
        created = show("POST", "/intents", c.post("/intents", json=body))
        iid, txid = created["intent_id"], created["transaction_id"]
        c.post(f"/intents/{iid}/validate")
        c.post(f"/intents/{iid}/authorize")
        show("POST", "/transactions", c.post("/transactions", json={"intent_id": iid}))
        show("GET", f"/transactions/{txid}", c.get(f"/transactions/{txid}"))

        # ------------------------------------------------------------------ #
        section("5. ASYNC PAYMENT BOUNDARY (Phase 2) — PENDING -> webhook -> CONFIRMED")
        body = intent_body(items=[{"sku": "SKU-001", "quantity": 1}], max_amount=500002)
        created = show("POST", "/intents", c.post("/intents", json=body))
        iid, txid = created["intent_id"], created["transaction_id"]
        c.post(f"/intents/{iid}/validate")
        c.post(f"/intents/{iid}/authorize")
        show("POST", "/transactions", c.post("/transactions", json={"intent_id": iid}))
        txn = show("GET", f"/transactions/{txid}", c.get(f"/transactions/{txid}"))

        provider = txn.get("payment_provider")
        order_id = txn.get("razorpay_order_id")
        if provider == "razorpay" and txn.get("state") == "PAYMENT_PENDING" and order_id:
            print(
                "\n  Razorpay Test Mode: money is NOT captured at order creation —\n"
                "  the transaction is PAYMENT_PENDING until an authoritative signal.\n"
                f"  razorpay_order_id = {order_id}"
            )
            if not WEBHOOK_SECRET:
                print(
                    "\n  (Set RAZORPAY_WEBHOOK_SECRET to the server's secret to let this\n"
                    "   demo deliver a signed payment.captured webhook and reach CONFIRMED.\n"
                    "   Otherwise the same effect is achieved by the reconciliation sweep.)"
                )
            else:
                # Deliver a *signed* payment.captured, exactly as Razorpay's servers
                # would. The amount MUST match the quoted order total or the handler
                # refuses it (defence-in-depth beyond the signature).
                amount = txn.get("quoted_total")
                event = {
                    "event": "payment.captured",
                    "payload": {
                        "payment": {
                            "entity": {
                                "id": "pay_demo_" + txid[:12],
                                "order_id": order_id,
                                "amount": amount,
                                "currency": txn.get("currency", "INR"),
                            }
                        }
                    },
                }
                raw = json.dumps(event).encode("utf-8")
                sig = hmac.new(
                    WEBHOOK_SECRET.encode("utf-8"), raw, hashlib.sha256
                ).hexdigest()
                show(
                    "POST",
                    "/webhooks/razorpay",
                    c.post(
                        "/webhooks/razorpay",
                        content=raw,
                        headers={
                            "X-Razorpay-Signature": sig,
                            "Content-Type": "application/json",
                        },
                    ),
                )
                show("GET", f"/transactions/{txid}", c.get(f"/transactions/{txid}"))
                print(
                    "\n  A duplicate delivery of the same webhook is an idempotent no-op —\n"
                    "  it never confirms twice and never opens a second order.\n"
                    "  An AMBIGUOUS failure would instead land in PAYMENT_UNKNOWN, which\n"
                    "  never auto-recharges and is resolved only by authoritative reconciliation."
                )
        else:
            print(
                "\n  Server is in the default MOCK gateway, which confirms synchronously,\n"
                "  so this transaction is already resolved above. In Razorpay Test Mode\n"
                "  (PAYMENT_GATEWAY=razorpay) the same call returns PAYMENT_PENDING with a\n"
                "  real order id and is confirmed asynchronously by a signed\n"
                "  payment.captured webhook or by the reconciliation sweep — never by the\n"
                "  AI buyer, and never by capturing money at order-creation time.\n"
                "  An AMBIGUOUS gateway failure lands in PAYMENT_UNKNOWN (no re-charge)."
            )

        print("\nDemo complete.\n")


if __name__ == "__main__":
    try:
        main()
    except httpx.ConnectError:
        print(f"Could not reach {BASE_URL}. Start the server:\n"
              f"    uvicorn app.main:app --reload")
        sys.exit(1)
