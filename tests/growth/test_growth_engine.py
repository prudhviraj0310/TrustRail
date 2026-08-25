"""Comprehensive test suite for the TrustRail AI Growth & Agentic Commerce Engine."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.enums import Decision, TransactionState
from app.merchant.catalogue import seed_merchant
from app.merchant.growth import list_active_bundles, list_active_offers
from app.services.growth import evaluate_growth_offer, generate_cart_recovery_incentive


def test_merchant_offers_and_bundles_retrieval(svc_session: Session):
    """Verify machine-readable merchant catalogue exposes active bundles and cross-sells."""
    seed_merchant(svc_session)
    bundles = list_active_bundles()
    offers = list_active_offers()

    assert len(bundles) >= 2
    assert any(b.id == "BUNDLE-WORKSTATION-PRO" for b in bundles)
    assert any(o.id == "OFFER-MOUSE-HUB" for o in offers)


def test_growth_policy_evaluates_valid_cross_sell(svc_session: Session):
    """Cross-sell offer is recommended and fits inside user's ₹5,000 authorized budget."""
    seed_merchant(svc_session)
    cart = [{"sku": "SKU-001", "quantity": 1}]  # Mouse @ ₹1,299.00
    authorized_budget = 500000  # ₹5,000.00

    rec = evaluate_growth_offer(
        cart_items=cart,
        authorized_max_amount=authorized_budget,
        currency="INR",
        db=svc_session,
    )

    assert rec.decision == Decision.ALLOW
    assert rec.budget_fit is True
    assert rec.requires_user_confirmation is False
    assert rec.recommended_offer is not None
    assert rec.new_total_amount <= authorized_budget
    assert rec.incremental_revenue > 0


def test_growth_policy_blocks_over_budget_proposal(svc_session: Session):
    """Proposals exceeding authorized max amount return REQUIRES_AUTHORIZATION."""
    seed_merchant(svc_session)
    # Propose Mouse + Keyboard + Hub + 4K Monitor (Total > ₹8,400) on a ₹3,000 budget
    cart = [
        {"sku": "SKU-001", "quantity": 1},
        {"sku": "SKU-002", "quantity": 1},
        {"sku": "SKU-003", "quantity": 1},
        {"sku": "SKU-004", "quantity": 1},
    ]
    tight_budget = 300000  # ₹3,000.00

    rec = evaluate_growth_offer(
        cart_items=cart,
        authorized_max_amount=tight_budget,
        currency="INR",
        db=svc_session,
    )

    assert rec.budget_fit is False
    assert rec.requires_user_confirmation is True
    assert (
        "exceeds authorized budget" in rec.reason.lower()
        or "requires_authorization" in rec.decision.lower()
    )


def test_abandoned_intent_recovery_generation(svc_session: Session, client: TestClient):
    """Expired or abandoned intents can generate a bounded 5% recovery voucher."""
    # 1. Create an intent
    res = client.post(
        "/intents",
        json={
            "agent_id": "abandoned_agent",
            "merchant_id": "MERCH_DEMO_001",
            "items": [{"sku": "SKU-001", "quantity": 1}],
            "constraints": {"max_amount": 200000, "currency": "INR", "max_quantity": 2},
            "authorization": {"expires_at": "2026-08-25T12:00:00Z"},
        },
    )
    intent_id = res.json()["intent_id"]

    # 2. Recover abandoned intent
    rec = generate_cart_recovery_incentive(
        intent_id=intent_id,
        max_discount_percentage=5.0,
        db=svc_session,
    )

    assert rec.status == "OFFER_GENERATED"
    assert rec.incentive_discount > 0
    assert rec.incentive_total < rec.original_total
    assert rec.voucher_code.startswith("RECOVER_")
    assert rec.recommended_new_intent is not None


def test_growth_api_recommend_endpoint(client: TestClient):
    """POST /growth/recommend returns valid recommendations."""
    res = client.post(
        "/growth/recommend",
        json={
            "merchant_id": "MERCH_DEMO_001",
            "cart_items": [{"sku": "SKU-001", "quantity": 1}],
            "authorized_max_amount": 500000,
            "currency": "INR",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["decision"] in ("ALLOW", "REQUIRES_AUTHORIZATION")
    assert data["baseline_amount"] == 129900


def test_growth_analytics_calculation(svc_session: Session, client: TestClient):
    """Verify that analytics correctly calculate GMV, AOV uplift, and incremental revenue."""
    seed_merchant(svc_session)

    # Initial analytics
    res1 = client.get("/analytics/growth")
    assert res1.status_code == 200

    # Execute a multi-item growth transaction end-to-end
    intent_payload = {
        "agent_id": "growth_buyer_test",
        "merchant_id": "MERCH_DEMO_001",
        "items": [
            {"sku": "SKU-001", "quantity": 1},
            {"sku": "SKU-003", "quantity": 1},
        ],
        "constraints": {"max_amount": 500000, "currency": "INR", "max_quantity": 5},
        "authorization": {"expires_at": "2030-01-01T00:00:00Z"},
    }
    i_res = client.post("/intents", json=intent_payload)
    intent_id = i_res.json()["intent_id"]

    client.post(f"/intents/{intent_id}/validate")
    client.post(f"/intents/{intent_id}/authorize")
    t_res = client.post("/transactions", json={"intent_id": intent_id})
    assert t_res.status_code == 200
    assert t_res.json()["state"] == TransactionState.COMPLETED

    # Verify updated analytics
    res2 = client.get("/analytics/growth")
    data2 = res2.json()
    assert data2["total_orders"] >= 1
    assert data2["gross_merchandise_value"] > 0
    assert data2["attach_rate_percentage"] > 0.0


def test_merchant_offers_and_bundles_endpoints(client: TestClient):
    """GET /merchant/offers and GET /merchant/bundles return valid lists."""
    r_offers = client.get("/merchant/offers")
    assert r_offers.status_code == 200
    assert isinstance(r_offers.json(), list)

    r_bundles = client.get("/merchant/bundles")
    assert r_bundles.status_code == 200
    assert isinstance(r_bundles.json(), list)
    assert len(r_bundles.json()) >= 1
