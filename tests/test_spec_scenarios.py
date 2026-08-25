"""Phase 7 — the ten required scenarios, mapped 1:1 to the spec.

Each test is labelled TEST N to match the brief. Decisions come back in the
DecisionEnvelope:  {intent_id, transaction_id, transaction_identity, state,
decision: {decision, reason, policy_checks}}.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.enums import TransactionState as S
from app.errors import InvalidStateTransition
from app.services.intent import canonicalize
from app.services.state_machine import assert_transition, can_transition


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def create(client, payload) -> dict:
    r = client.post("/intents", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def validate(client, intent_id) -> dict:
    r = client.post(f"/intents/{intent_id}/validate")
    assert r.status_code == 200, r.text
    return r.json()


def failed_checks(decision: dict) -> list[str]:
    return [c["name"] for c in decision["decision"]["policy_checks"] if not c["passed"]]


# --------------------------------------------------------------------------- #
# TEST 1 — valid purchase under budget -> ALLOW
# --------------------------------------------------------------------------- #
def test_1_valid_under_budget_allows(client, make_payload):
    created = create(client, make_payload(max_amount=500000))  # ₹5,000 budget
    result = validate(client, created["intent_id"])
    assert result["decision"]["decision"] == "ALLOW"
    assert result["state"] == S.VALIDATED.value


# --------------------------------------------------------------------------- #
# TEST 2 — purchase exceeds budget -> BLOCK
# --------------------------------------------------------------------------- #
def test_2_exceeds_budget_blocks(client, make_payload):
    # SKU-001 costs ₹1,299.00 (129900) but the user only authorized ₹1,000.00.
    created = create(client, make_payload(max_amount=100000))
    result = validate(client, created["intent_id"])
    assert result["decision"]["decision"] == "BLOCK"
    assert result["state"] == S.POLICY_BLOCKED.value
    assert "amount_within_authorized_max" in failed_checks(result)
    assert "exceeds authorized maximum" in result["decision"]["reason"]


# --------------------------------------------------------------------------- #
# TEST 3 — expired authorization -> BLOCK
# --------------------------------------------------------------------------- #
def test_3_expired_authorization_blocks(client, make_payload, frozen_now):
    payload = make_payload(expires_at=frozen_now - timedelta(hours=1))
    created = create(client, payload)
    result = validate(client, created["intent_id"])
    assert result["decision"]["decision"] == "BLOCK"
    assert result["state"] == S.AUTH_EXPIRED.value
    assert "authorization_not_expired" in failed_checks(result)


# --------------------------------------------------------------------------- #
# TEST 4 — wrong merchant -> BLOCK
# --------------------------------------------------------------------------- #
def test_4_wrong_merchant_blocks(client, make_payload):
    created = create(client, make_payload(merchant_id="MERCH_NOT_ME"))
    result = validate(client, created["intent_id"])
    assert result["decision"]["decision"] == "BLOCK"
    assert result["state"] == S.INVALID.value
    assert "merchant_known" in failed_checks(result)


# --------------------------------------------------------------------------- #
# TEST 5 — wrong SKU -> BLOCK
# --------------------------------------------------------------------------- #
def test_5_wrong_sku_blocks(client, make_payload):
    created = create(
        client, make_payload(items=[{"sku": "SKU-DOES-NOT-EXIST", "quantity": 1}])
    )
    result = validate(client, created["intent_id"])
    assert result["decision"]["decision"] == "BLOCK"
    assert result["state"] == S.INVALID.value
    assert "skus_valid" in failed_checks(result)


# --------------------------------------------------------------------------- #
# TEST 6 — quantity exceeds limit -> BLOCK
# --------------------------------------------------------------------------- #
def test_6_quantity_exceeds_limit_blocks(client, make_payload):
    # 2 units but only 1 authorized; budget is generous so quantity is the fault.
    payload = make_payload(
        items=[{"sku": "SKU-001", "quantity": 2}], max_amount=500000, max_quantity=1
    )
    created = create(client, payload)
    result = validate(client, created["intent_id"])
    assert result["decision"]["decision"] == "BLOCK"
    assert result["state"] == S.POLICY_BLOCKED.value
    assert "quantity_within_limit" in failed_checks(result)


# --------------------------------------------------------------------------- #
# TEST 7 — same canonical intent repeated -> same transaction identity
# --------------------------------------------------------------------------- #
def test_7_same_canonical_intent_same_identity(client, make_payload, frozen_now):
    # Identical financial fields; different agent, expiry, and item ordering.
    a = create(
        client,
        make_payload(
            agent_id="agent-A",
            items=[{"sku": "SKU-001", "quantity": 1}, {"sku": "SKU-002", "quantity": 1}],
            expires_at=frozen_now + timedelta(hours=1),
        ),
    )
    b = create(
        client,
        make_payload(
            agent_id="agent-B",
            items=[{"sku": "SKU-002", "quantity": 1}, {"sku": "SKU-001", "quantity": 1}],
            expires_at=frozen_now + timedelta(hours=5),
        ),
    )
    assert a["transaction_identity"] == b["transaction_identity"]
    # ...and they collapse onto the very same transaction (idempotency).
    assert a["transaction_id"] == b["transaction_id"]
    assert a["intent_id"] != b["intent_id"]


# --------------------------------------------------------------------------- #
# TEST 8 — different quantity -> different transaction identity
# --------------------------------------------------------------------------- #
def test_8_different_quantity_different_identity(client, make_payload):
    one = create(
        client, make_payload(items=[{"sku": "SKU-001", "quantity": 1}], max_quantity=5)
    )
    two = create(
        client, make_payload(items=[{"sku": "SKU-001", "quantity": 2}], max_quantity=5)
    )
    assert one["transaction_identity"] != two["transaction_identity"]
    assert one["transaction_id"] != two["transaction_id"]


# --------------------------------------------------------------------------- #
# TEST 9 — invalid state transition -> BLOCK (impossible)
# --------------------------------------------------------------------------- #
def test_9_invalid_state_transition_is_impossible(client, make_payload):
    # (a) the state machine itself forbids the shortcut
    assert can_transition(S.INTENT_CREATED, S.COMPLETED) is False
    with pytest.raises(InvalidStateTransition):
        assert_transition(S.INTENT_CREATED, S.COMPLETED)

    # (b) the API refuses to execute a transaction that was never authorized:
    #     it returns REQUIRES_AUTHORIZATION and never jumps to COMPLETED.
    created = create(client, make_payload())
    r = client.post("/transactions", json={"intent_id": created["intent_id"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["decision"]["decision"] == "REQUIRES_AUTHORIZATION"
    assert body["state"] == S.INTENT_CREATED.value  # unchanged, not COMPLETED


# --------------------------------------------------------------------------- #
# TEST 10 — every decision creates an audit event
# --------------------------------------------------------------------------- #
def test_10_every_decision_is_audited(client, make_payload):
    created = create(client, make_payload())
    validate(client, created["intent_id"])

    audit = client.get(f"/transactions/{created['transaction_id']}/audit")
    assert audit.status_code == 200, audit.text
    events = audit.json()["events"]

    actions = [e["action"] for e in events]
    actors = {e["actor"] for e in events}

    # The AI proposal and the policy decision are both on the record.
    assert "INTENT_CREATED" in actions
    assert any(a.startswith("POLICY_EVALUATED") for a in actions)
    assert "POLICY_ENGINE" in actors
    # Events are ordered by a monotonic sequence.
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs)


def test_pure_canonicalization_identity_is_stable(make_payload):
    """Belt-and-braces: the identity function is a pure, order-independent hash."""
    from app.schemas.intent import PurchaseIntentIn

    p1 = PurchaseIntentIn(**make_payload(items=[{"sku": "sku-001", "quantity": 1}]))
    p2 = PurchaseIntentIn(**make_payload(items=[{"sku": " SKU-001 ", "quantity": 1}]))
    assert canonicalize(p1).transaction_identity == canonicalize(p2).transaction_identity
