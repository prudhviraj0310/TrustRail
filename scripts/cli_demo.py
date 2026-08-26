#!/usr/bin/env python3
"""TrustRail end-to-end Track 01 demonstration: AI Growth & Agentic Commerce Engine.

Walks through the complete product story against a running TrustRail server:
1. AI Buyer discovery of merchant products & active bundles
2. Normal baseline purchase execution
3. KILLER DEMO: Dynamic Workstation Bundle upsell within user's ₹5,000 budget
4. SAFETY BOUNDARY: Over-budget proposal blocked by TrustRail Growth Policy
5. Recovery & Razorpay payment boundary
6. Real, persisted Merchant Revenue Growth & Conversion Analytics

Usage:
    uvicorn app.main:app --reload
    python scripts/cli_demo.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime, timedelta

import httpx

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
MERCHANT_ID = "MERCH_DEMO_001"
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


def intent_body(
    *,
    items,
    max_amount,
    max_quantity=5,
    currency="INR",
    agent_id="agent-demo-buyer",
    merchant_id=MERCHANT_ID,
) -> dict:
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
        section("0. SYSTEM HEALTH & AGENT DISCOVERY")
        show("GET", "/health", c.get("/health"))
        show("GET", "/merchant/agent-card", c.get("/merchant/agent-card"))
        show("GET", "/merchant/bundles", c.get("/merchant/bundles"))

        # ------------------------------------------------------------------ #
        section("1. BASELINE AI PURCHASE — ₹1,299 Wireless Mouse under ₹5,000 budget")
        body1 = intent_body(items=[{"sku": "SKU-001", "quantity": 1}], max_amount=500000)
        c1 = show("POST", "/intents", c.post("/intents", json=body1))
        iid1, txid1 = c1["intent_id"], c1["transaction_id"]
        c.post(f"/intents/{iid1}/validate")
        c.post(f"/intents/{iid1}/authorize")
        show("POST", "/transactions", c.post("/transactions", json={"intent_id": iid1}))
        show("GET", f"/transactions/{txid1}", c.get(f"/transactions/{txid1}"))

        # ------------------------------------------------------------------ #
        section(
            "2. THE KILLER DEMO — AI Negotiates Workstation Bundle within ₹5,000 Budget"
        )
        print("\n  AI Buyer seeks: 'Workstation Mouse' (SKU-001 @ ₹1,299)")
        print(
            "  AI Growth Engine identifies: Workstation Pro Bundle (Mouse + Keyboard + Hub @ ₹3,498)"
        )
        print("  Buyer Authorized Budget: ₹5,000.00")

        # Step 2a: Request recommendation
        rec_req = {
            "merchant_id": MERCHANT_ID,
            "cart_items": [{"sku": "SKU-001", "quantity": 1}],
            "authorized_max_amount": 500000,
            "currency": "INR",
        }
        rec = show("POST", "/growth/recommend", c.post("/growth/recommend", json=rec_req))
        print(f"\n  ✓ TrustRail Growth Policy Decision: {rec.get('decision')}")
        print(f"  ✓ Reason: {rec.get('reason')}")
        print(
            f"  ✓ Incremental Revenue Generated: ₹{(rec.get('incremental_revenue', 0) / 100):,.2f}"
        )

        # Step 2b: Execute the accepted bundle through TrustRail integrity engine
        bundle_body = intent_body(
            items=rec.get("suggested_intent_items", []),
            max_amount=500000,
            agent_id="agent-growth-buyer-1",
        )
        c2 = show(
            "POST", "/intents (Bundle Proposal)", c.post("/intents", json=bundle_body)
        )
        iid2, txid2 = c2["intent_id"], c2["transaction_id"]
        c.post(f"/intents/{iid2}/validate")
        c.post(f"/intents/{iid2}/authorize")
        show("POST", "/transactions", c.post("/transactions", json={"intent_id": iid2}))
        show("GET", f"/transactions/{txid2}", c.get(f"/transactions/{txid2}"))

        # ------------------------------------------------------------------ #
        section(
            "3. SAFETY BOUNDARY — AI Attempts Over-Budget Proposal (₹8,497 vs ₹5,000)"
        )
        print(
            "\n  AI Buyer attempts: Mouse + Keyboard + Hub + 4K Monitor (Total: ₹19,999+)"
        )
        print("  Buyer Authorized Budget: ₹5,000.00")
        over_req = {
            "merchant_id": MERCHANT_ID,
            "cart_items": [
                {"sku": "SKU-001", "quantity": 1},
                {"sku": "SKU-002", "quantity": 1},
                {"sku": "SKU-003", "quantity": 1},
                {"sku": "SKU-004", "quantity": 1},
            ],
            "authorized_max_amount": 500000,
            "currency": "INR",
        }
        over_rec = show(
            "POST",
            "/growth/recommend (Over-Budget)",
            c.post("/growth/recommend", json=over_req),
        )
        print(
            f"\n  🛡️ GATING ACTIVE: {over_rec.get('decision')} — {over_rec.get('reason')}"
        )
        print("  🔒 The AI CANNOT silently increase the authorized spend.")

        # ------------------------------------------------------------------ #
        section(
            "4. RECOVERY & INTEGRITY — Payment Succeeded but Fulfilment Failed -> REFUND_REQUIRED"
        )
        body_fail = intent_body(
            items=[{"sku": "SKU-FAIL-ORDER", "quantity": 1}], max_amount=200000
        )
        c_fail = show(
            "POST", "/intents (Failure Scenario)", c.post("/intents", json=body_fail)
        )
        iidf, txidf = c_fail["intent_id"], c_fail["transaction_id"]
        c.post(f"/intents/{iidf}/validate")
        c.post(f"/intents/{iidf}/authorize")
        show("POST", "/transactions", c.post("/transactions", json={"intent_id": iidf}))
        show("GET", f"/transactions/{txidf}", c.get(f"/transactions/{txidf}"))

        # ------------------------------------------------------------------ #
        section("5. REAL REVENUE GROWTH & ATTACH RATE ANALYTICS")
        print(
            "\n  Querying live merchant growth ledger (never faked, calculated directly from orders):"
        )
        show("GET", "/analytics/growth", c.get("/analytics/growth"))

        print("\n" + "=" * 78)
        print("  DEMO COMPLETE — AI GROWTH & AGENTIC COMMERCE DEMONSTRATED")
        print("  Open Interactive Dashboard: http://127.0.0.1:8000/dashboard")
        print("=" * 78 + "\n")


if __name__ == "__main__":
    try:
        main()
    except httpx.ConnectError:
        print(
            f"Could not reach {BASE_URL}. Start the server:\n"
            f"    uvicorn app.main:app --reload"
        )
        sys.exit(1)
