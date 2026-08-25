"""Unit tests for Phase 1 — canonicalisation & deterministic transaction identity.

These are pure-function tests (no DB, no HTTP): they pin down exactly what makes
two purchases "the same" and what makes them different.
"""

from __future__ import annotations

from app.schemas.intent import PurchaseIntentIn
from app.services.intent import CANONICALIZATION_VERSION, canonicalize


def _intent(make_payload, **kw) -> PurchaseIntentIn:
    return PurchaseIntentIn(**make_payload(**kw))


# --- identity is order/case/whitespace independent ------------------------- #
def test_item_order_does_not_change_identity(make_payload):
    a = canonicalize(
        _intent(
            make_payload,
            items=[{"sku": "SKU-001", "quantity": 1}, {"sku": "SKU-002", "quantity": 1}],
        )
    )
    b = canonicalize(
        _intent(
            make_payload,
            items=[{"sku": "SKU-002", "quantity": 1}, {"sku": "SKU-001", "quantity": 1}],
        )
    )
    assert a.transaction_identity == b.transaction_identity


def test_sku_case_and_whitespace_normalised(make_payload):
    a = canonicalize(_intent(make_payload, items=[{"sku": "SKU-001", "quantity": 1}]))
    b = canonicalize(_intent(make_payload, items=[{"sku": " sku-001 ", "quantity": 1}]))
    assert a.transaction_identity == b.transaction_identity


def test_duplicate_skus_merge_by_summing_quantity(make_payload):
    merged = canonicalize(
        _intent(
            make_payload,
            items=[{"sku": "SKU-001", "quantity": 1}, {"sku": "SKU-001", "quantity": 1}],
            max_quantity=5,
        )
    )
    single = canonicalize(
        _intent(make_payload, items=[{"sku": "SKU-001", "quantity": 2}], max_quantity=5)
    )
    assert merged.transaction_identity == single.transaction_identity
    assert merged.total_quantity == 2
    assert merged.canonical["items"] == [{"sku": "SKU-001", "quantity": 2}]


# --- material differences DO change identity ------------------------------- #
def test_quantity_change_changes_identity(make_payload):
    one = canonicalize(
        _intent(make_payload, items=[{"sku": "SKU-001", "quantity": 1}], max_quantity=5)
    )
    two = canonicalize(
        _intent(make_payload, items=[{"sku": "SKU-001", "quantity": 2}], max_quantity=5)
    )
    assert one.transaction_identity != two.transaction_identity


def test_sku_change_changes_identity(make_payload):
    a = canonicalize(_intent(make_payload, items=[{"sku": "SKU-001", "quantity": 1}]))
    b = canonicalize(_intent(make_payload, items=[{"sku": "SKU-002", "quantity": 1}]))
    assert a.transaction_identity != b.transaction_identity


def test_max_amount_change_changes_identity(make_payload):
    a = canonicalize(_intent(make_payload, max_amount=500000))
    b = canonicalize(_intent(make_payload, max_amount=1000000))
    assert a.transaction_identity != b.transaction_identity


def test_currency_change_changes_identity(make_payload):
    a = canonicalize(_intent(make_payload, currency="INR"))
    b = canonicalize(_intent(make_payload, currency="USD"))
    assert a.transaction_identity != b.transaction_identity


def test_merchant_change_changes_identity(make_payload):
    a = canonicalize(_intent(make_payload, merchant_id="MERCH_DEMO_001"))
    b = canonicalize(_intent(make_payload, merchant_id="MERCH_OTHER_999"))
    assert a.transaction_identity != b.transaction_identity


# --- who/when is deliberately EXCLUDED from identity ----------------------- #
def test_agent_and_expiry_excluded_from_identity(make_payload, frozen_now):
    from datetime import timedelta

    a = canonicalize(
        _intent(
            make_payload, agent_id="agent-A", expires_at=frozen_now + timedelta(hours=1)
        )
    )
    b = canonicalize(
        _intent(
            make_payload, agent_id="agent-Z", expires_at=frozen_now + timedelta(days=30)
        )
    )
    assert a.transaction_identity == b.transaction_identity
    # ...and the canonical form literally does not carry those fields.
    assert "agent_id" not in a.canonical
    assert "expires_at" not in str(a.canonical)


# --- shape of the artefacts ------------------------------------------------ #
def test_identity_is_prefixed_hash(make_payload):
    c = canonicalize(_intent(make_payload))
    assert c.transaction_identity.startswith("txid_")
    # sha256 hex is 64 chars; prefix + hex.
    assert len(c.transaction_identity) == len("txid_") + 64


def test_canonical_json_is_compact_and_sorted(make_payload):
    c = canonicalize(_intent(make_payload))
    # compact separators, no spaces
    assert ", " not in c.canonical_json
    assert '": ' not in c.canonical_json
    # version tag present so future algo changes don't collide with old identities
    assert c.canonical["canonicalization_version"] == CANONICALIZATION_VERSION


def test_identity_is_deterministic_across_calls(make_payload):
    p = make_payload()
    first = canonicalize(PurchaseIntentIn(**p)).transaction_identity
    second = canonicalize(PurchaseIntentIn(**p)).transaction_identity
    assert first == second
