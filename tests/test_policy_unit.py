"""Unit tests for Phase 2 — the deterministic Policy Engine (pure function).

``evaluate()`` never touches I/O or LLM text; we build a PolicyContext by hand
and assert the decision, the failing check, and the fixed check ordering.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.enums import Decision, PolicyPhase
from app.services import policy
from app.services.policy import (
    CHECK_AMOUNT,
    CHECK_CURRENCY,
    CHECK_EXPIRY,
    CHECK_MERCHANT,
    CHECK_ORDER,
    CHECK_PRICE,
    CHECK_QUANTITY,
    CHECK_SKUS,
)

NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
LATER = datetime(2026, 1, 1, 13, 0, 0, tzinfo=UTC)
EARLIER = datetime(2026, 1, 1, 11, 0, 0, tzinfo=UTC)


def ctx(**overrides) -> policy.PolicyContext:
    """A context in which every check passes; override to induce failures."""
    base = {
        "phase": PolicyPhase.VALIDATE,
        "merchant_id_intent": "MERCH_DEMO_001",
        "merchant_known": True,
        "currency_intent": "INR",
        "merchant_currency": "INR",
        "currency_conflict": False,
        "unknown_skus": [],
        "oos_skus": [],
        "all_available": True,
        "order_total": 100000,
        "max_amount": 500000,
        "total_quantity": 1,
        "max_quantity": 5,
        "now": NOW,
        "expires_at": LATER,
        "is_authorized": False,
        "prior_quoted_total": None,
    }
    base.update(overrides)
    return policy.PolicyContext(**base)


def failed(result) -> set[str]:
    return {c.name for c in result.checks if not c.passed}


def test_all_pass_validate_allows():
    r = policy.evaluate(ctx())
    assert r.decision == Decision.ALLOW
    assert r.failed_check is None
    assert failed(r) == set()


def test_amount_over_budget_blocks_on_amount_check():
    r = policy.evaluate(ctx(order_total=700000, max_amount=500000))
    assert r.decision == Decision.BLOCK
    assert r.failed_check == CHECK_AMOUNT
    assert "exceeds authorized maximum" in r.reason


def test_quantity_over_limit_blocks():
    r = policy.evaluate(ctx(total_quantity=3, max_quantity=1))
    assert r.decision == Decision.BLOCK
    assert r.failed_check == CHECK_QUANTITY


def test_expired_authorization_blocks():
    r = policy.evaluate(ctx(now=NOW, expires_at=EARLIER))
    assert r.decision == Decision.BLOCK
    assert r.failed_check == CHECK_EXPIRY


def test_unknown_merchant_blocks():
    r = policy.evaluate(ctx(merchant_known=False))
    assert r.decision == Decision.BLOCK
    assert r.failed_check == CHECK_MERCHANT


def test_unknown_sku_blocks():
    r = policy.evaluate(ctx(unknown_skus=["SKU-XYZ"]))
    assert r.decision == Decision.BLOCK
    assert r.failed_check == CHECK_SKUS


def test_currency_conflict_blocks():
    r = policy.evaluate(ctx(currency_conflict=True))
    assert r.decision == Decision.BLOCK
    assert r.failed_check == CHECK_CURRENCY


def test_currency_mismatch_with_merchant_blocks():
    r = policy.evaluate(ctx(currency_intent="INR", merchant_currency="USD"))
    assert r.decision == Decision.BLOCK
    assert r.failed_check == CHECK_CURRENCY


def test_out_of_stock_blocks_on_inventory():
    r = policy.evaluate(ctx(all_available=False, oos_skus=["SKU-OOS"]))
    assert r.decision == Decision.BLOCK
    # Note: SKUs are known, so it fails at inventory, not skus_valid.
    assert r.failed_check == "inventory_available"


def test_price_change_blocks_when_prior_quote_differs():
    r = policy.evaluate(ctx(order_total=120000, prior_quoted_total=100000))
    assert r.decision == Decision.BLOCK
    assert r.failed_check == CHECK_PRICE


def test_first_failing_check_wins_in_fixed_order():
    # Merchant unknown AND amount over budget: merchant is earlier in CHECK_ORDER.
    r = policy.evaluate(ctx(merchant_known=False, order_total=999999, max_amount=1))
    assert r.failed_check == CHECK_MERCHANT
    assert CHECK_ORDER.index(CHECK_MERCHANT) < CHECK_ORDER.index(CHECK_AMOUNT)


def test_checks_are_reported_in_canonical_order():
    r = policy.evaluate(ctx())
    assert [c.name for c in r.checks] == CHECK_ORDER


def test_execute_without_authorization_requires_authorization():
    r = policy.evaluate(ctx(phase=PolicyPhase.EXECUTE, is_authorized=False))
    assert r.decision == Decision.REQUIRES_AUTHORIZATION


def test_execute_with_authorization_allows():
    r = policy.evaluate(ctx(phase=PolicyPhase.EXECUTE, is_authorized=True))
    assert r.decision == Decision.ALLOW


def test_evaluate_is_pure_same_input_same_output():
    c = ctx(order_total=700000)
    a = policy.evaluate(c)
    b = policy.evaluate(c)
    assert a.decision == b.decision == Decision.BLOCK
    assert a.reason == b.reason
    assert a.failed_check == b.failed_check
