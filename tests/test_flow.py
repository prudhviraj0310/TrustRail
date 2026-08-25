"""End-to-end lifecycle tests (Phases 3-6): happy path, idempotency, recovery.

Drives the full API surface with the frozen clock and in-memory DB, exercising
the state machine, the mock payment gateway, and the failure/recovery branches.
"""

from __future__ import annotations

from datetime import timedelta

from app.enums import TransactionState as S


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


def authorize(client, intent_id) -> dict:
    r = client.post(f"/intents/{intent_id}/authorize")
    assert r.status_code == 200, r.text
    return r.json()


def execute(client, **body) -> dict:
    r = client.post("/transactions", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def audit_actions(client, transaction_id) -> list[str]:
    r = client.get(f"/transactions/{transaction_id}/audit")
    assert r.status_code == 200, r.text
    return [e["action"] for e in r.json()["events"]]


# --------------------------------------------------------------------------- #
# happy path
# --------------------------------------------------------------------------- #
def test_full_happy_path_reaches_completed(client, make_payload):
    created = create(client, make_payload(max_amount=500000))
    iid = created["intent_id"]

    assert validate(client, iid)["state"] == S.VALIDATED.value
    assert authorize(client, iid)["state"] == S.AUTHORIZED.value

    done = execute(client, intent_id=iid)
    assert done["decision"]["decision"] == "ALLOW"
    assert done["state"] == S.COMPLETED.value

    txn = client.get(f"/transactions/{created['transaction_id']}").json()
    assert txn["state"] == S.COMPLETED.value
    assert txn["payment_ref"] is not None
    assert txn["merchant_order_id"] is not None
    assert txn["amount_captured"] == 129900


def test_happy_path_audit_answers_the_seven_questions(client, make_payload):
    created = create(client, make_payload(max_amount=500000))
    iid = created["intent_id"]
    validate(client, iid)
    authorize(client, iid)
    execute(client, intent_id=iid)

    r = client.get(f"/transactions/{created['transaction_id']}/audit")
    events = r.json()["events"]
    actors = {e["actor"] for e in events}
    actions = [e["action"] for e in events]

    # Who proposed, who decided, who paid, who fulfilled.
    assert {"AI_BUYER", "POLICY_ENGINE", "PAYMENT_GATEWAY", "MERCHANT", "TRUSTRAIL"} <= actors
    assert "INTENT_CREATED" in actions          # what the AI proposed
    assert any(a.startswith("POLICY_EVALUATED") for a in actions)  # what TrustRail allowed
    assert "PAYMENT_CONFIRMED" in actions        # what happened to payment
    assert "ORDER_CONFIRMED" in actions          # what happened to the order
    assert "TRANSACTION_COMPLETED" in actions
    # strictly ordered
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs)


# --------------------------------------------------------------------------- #
# idempotency
# --------------------------------------------------------------------------- #
def test_execute_is_idempotent(client, make_payload):
    created = create(client, make_payload(max_amount=500000))
    iid = created["intent_id"]
    validate(client, iid)
    authorize(client, iid)

    first = execute(client, intent_id=iid)
    txn_after_first = client.get(f"/transactions/{created['transaction_id']}").json()

    second = execute(client, intent_id=iid)
    txn_after_second = client.get(f"/transactions/{created['transaction_id']}").json()

    assert first["state"] == second["state"] == S.COMPLETED.value
    # No double charge, no second order.
    assert txn_after_first["payment_ref"] == txn_after_second["payment_ref"]
    assert txn_after_first["merchant_order_id"] == txn_after_second["merchant_order_id"]
    assert "EXECUTE_IDEMPOTENT_REPLAY" in audit_actions(client, created["transaction_id"])


def test_duplicate_intent_collapses_onto_one_transaction(client, make_payload):
    a = create(client, make_payload())
    b = create(client, make_payload())  # identical financial fields
    assert a["transaction_id"] == b["transaction_id"]
    assert a["intent_id"] != b["intent_id"]


# --------------------------------------------------------------------------- #
# execution can be triggered by transaction_identity too
# --------------------------------------------------------------------------- #
def test_execute_by_transaction_identity(client, make_payload):
    created = create(client, make_payload(max_amount=500000))
    validate(client, created["intent_id"])
    authorize(client, created["intent_id"])

    done = execute(client, transaction_identity=created["transaction_identity"])
    assert done["state"] == S.COMPLETED.value


# --------------------------------------------------------------------------- #
# guard rails
# --------------------------------------------------------------------------- #
def test_cannot_authorize_before_validation(client, make_payload):
    created = create(client, make_payload())
    r = client.post(f"/intents/{created['intent_id']}/authorize")
    assert r.status_code == 409  # InvalidLifecycleState


def test_execute_before_authorize_requires_authorization(client, make_payload):
    created = create(client, make_payload())
    validate(client, created["intent_id"])  # validated but NOT authorized
    r = client.post("/transactions", json={"intent_id": created["intent_id"]})
    assert r.status_code == 200
    body = r.json()
    assert body["decision"]["decision"] == "REQUIRES_AUTHORIZATION"
    assert body["state"] == S.VALIDATED.value  # unchanged


# --------------------------------------------------------------------------- #
# failure & recovery
# --------------------------------------------------------------------------- #
def test_payment_declined_moves_to_payment_failed(client, make_payload):
    # SKU-FAIL-PAY: mock gateway declines. Price ₹1,000.00.
    payload = make_payload(
        items=[{"sku": "SKU-FAIL-PAY", "quantity": 1}], max_amount=200000, max_quantity=1
    )
    created = create(client, payload)
    validate(client, created["intent_id"])
    authorize(client, created["intent_id"])
    done = execute(client, intent_id=created["intent_id"])

    assert done["state"] == S.PAYMENT_FAILED.value
    txn = client.get(f"/transactions/{created['transaction_id']}").json()
    assert txn["merchant_order_id"] is None
    assert "PAYMENT_FAILED" in audit_actions(client, created["transaction_id"])


def test_order_failure_after_payment_requires_refund(client, make_payload):
    # SKU-FAIL-ORDER: payment succeeds, fulfilment fails -> refund owed.
    payload = make_payload(
        items=[{"sku": "SKU-FAIL-ORDER", "quantity": 1}], max_amount=200000, max_quantity=1
    )
    created = create(client, payload)
    validate(client, created["intent_id"])
    authorize(client, created["intent_id"])
    done = execute(client, intent_id=created["intent_id"])

    assert done["state"] == S.REFUND_REQUIRED.value
    txn = client.get(f"/transactions/{created['transaction_id']}").json()
    assert txn["payment_ref"] is not None   # we DID capture money
    assert txn["merchant_order_id"] is None  # but there is no order
    actions = audit_actions(client, created["transaction_id"])
    assert "PAYMENT_CONFIRMED" in actions
    assert "ORDER_FAILED" in actions
    assert "REFUND_REQUIRED" in actions


def test_out_of_stock_blocks_at_validation(client, make_payload):
    payload = make_payload(items=[{"sku": "SKU-OOS", "quantity": 1}], max_amount=500000)
    created = create(client, payload)
    result = validate(client, created["intent_id"])
    assert result["decision"]["decision"] == "BLOCK"
    # At INTENT_CREATED the reachable failure state is POLICY_BLOCKED.
    assert result["state"] == S.POLICY_BLOCKED.value


def test_expired_after_authorization_blocks_at_execute(client, make_payload, clock, frozen_now):
    # Authorize while valid, then let the clock roll past expiry before executing.
    payload = make_payload(max_amount=500000, expires_at=frozen_now + timedelta(minutes=30))
    created = create(client, payload)
    validate(client, created["intent_id"])
    authorize(client, created["intent_id"])

    clock.set(frozen_now + timedelta(hours=2))  # now past expiry
    done = execute(client, intent_id=created["intent_id"])

    assert done["decision"]["decision"] == "BLOCK"
    assert done["state"] == S.AUTH_EXPIRED.value
