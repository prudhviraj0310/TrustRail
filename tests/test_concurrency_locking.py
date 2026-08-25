"""Tests for concurrency hardening (Phase 2 / STEP 12).

The dangerous race is two resolvers (a webhook and a reconciliation sweep) acting
on the *same* order at once. On Postgres a ``SELECT … FOR UPDATE`` row lock
serialises them; on SQLite (tests) the single-writer model does. What we can
assert deterministically is the *correctness invariant the lock protects*: no
matter the order in which the two authorities run, the transaction converges to a
single COMPLETED with exactly one merchant order and one confirmation — the
second authority always takes the idempotent no-op path.
"""

from __future__ import annotations

import hashlib
import hmac
import json

from app.enums import TransactionState as S
from app.services import reconciliation
from app.services.locking import (
    _locking,
    lock_transaction_by_identity,
    lock_transaction_by_order_id,
)
from tests.conftest import WEBHOOK_SECRET

CAPTURE_AMOUNT = 129900  # SKU-001


def _drive_to_pending(client_rz, make_payload, **kw) -> dict:
    created = client_rz.post("/intents", json=make_payload(**kw)).json()
    client_rz.post(f"/intents/{created['intent_id']}/validate")
    client_rz.post(f"/intents/{created['intent_id']}/authorize")
    client_rz.post("/transactions", json={"intent_id": created["intent_id"]})
    return created


def _order_id(client_rz, transaction_id) -> str:
    return client_rz.get(f"/transactions/{transaction_id}").json()["razorpay_order_id"]


def _sign(body: bytes) -> str:
    return hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()


def _audit_actions(client_rz, transaction_id) -> list[str]:
    events = client_rz.get(f"/transactions/{transaction_id}/audit").json()["events"]
    return [e["action"] for e in events]


# --------------------------------------------------------------------------- #
# locking helpers
# --------------------------------------------------------------------------- #
def test_locking_is_disabled_on_sqlite(db_session):
    # FOR UPDATE is unsupported on SQLite; the helper must report locking off.
    assert _locking(db_session) is False


def test_lock_helpers_return_the_right_row(client_rz, make_payload, svc_session):
    created = _drive_to_pending(client_rz, make_payload, max_amount=500000)
    order_id = _order_id(client_rz, created["transaction_id"])

    by_identity = lock_transaction_by_identity(svc_session, created["transaction_identity"])
    by_order = lock_transaction_by_order_id(svc_session, order_id)
    assert by_identity is not None
    assert by_order is not None
    assert by_identity.id == by_order.id == created["transaction_id"]


def test_lock_helpers_return_none_for_missing(svc_session):
    assert lock_transaction_by_identity(svc_session, "txid_nope") is None
    assert lock_transaction_by_order_id(svc_session, "order_nope") is None


# --------------------------------------------------------------------------- #
# convergence invariant the lock protects
# --------------------------------------------------------------------------- #
def test_webhook_then_reconcile_converge_to_single_completion(
    client_rz, make_payload, rz_client, razorpay_gateway, svc_session, clock
):
    created = _drive_to_pending(client_rz, make_payload, max_amount=500000)
    order_id = _order_id(client_rz, created["transaction_id"])
    rz_client.add_captured(order_id, payment_id="pay_1", amount=CAPTURE_AMOUNT)

    # 1) the webhook resolves it first
    body = json.dumps(
        {"event": "payment.captured",
         "payload": {"payment": {"entity": {
             "id": "pay_1", "order_id": order_id,
             "amount": CAPTURE_AMOUNT, "currency": "INR", "status": "captured"}}}}
    ).encode()
    client_rz.post("/webhooks/razorpay", content=body, headers={"X-Razorpay-Signature": _sign(body)})

    # 2) a reconciliation sweep runs afterwards for the same order
    out = reconciliation.reconcile_transaction(
        svc_session, transaction_identity=created["transaction_identity"],
        gateway=razorpay_gateway, clock=clock,
    )
    assert out.action == "skipped"  # nothing left to do; no second confirm/order

    txn = client_rz.get(f"/transactions/{created['transaction_id']}").json()
    assert txn["state"] == S.COMPLETED.value
    actions = _audit_actions(client_rz, created["transaction_id"])
    assert actions.count("PAYMENT_CONFIRMED") == 1
    assert actions.count("ORDER_CONFIRMED") == 1


def test_reconcile_then_webhook_converge_to_single_completion(
    client_rz, make_payload, rz_client, razorpay_gateway, svc_session, clock
):
    created = _drive_to_pending(client_rz, make_payload, max_amount=500000)
    order_id = _order_id(client_rz, created["transaction_id"])
    rz_client.add_captured(order_id, payment_id="pay_1", amount=CAPTURE_AMOUNT)

    # 1) reconciliation resolves it first
    reconciliation.reconcile_transaction(
        svc_session, transaction_identity=created["transaction_identity"],
        gateway=razorpay_gateway, clock=clock,
    )
    # 2) the (delayed) webhook arrives afterwards -> idempotent no-op
    body = json.dumps(
        {"event": "payment.captured",
         "payload": {"payment": {"entity": {
             "id": "pay_1", "order_id": order_id,
             "amount": CAPTURE_AMOUNT, "currency": "INR", "status": "captured"}}}}
    ).encode()
    r = client_rz.post("/webhooks/razorpay", content=body, headers={"X-Razorpay-Signature": _sign(body)})
    assert r.status_code == 200

    txn = client_rz.get(f"/transactions/{created['transaction_id']}").json()
    assert txn["state"] == S.COMPLETED.value
    actions = _audit_actions(client_rz, created["transaction_id"])
    assert actions.count("PAYMENT_CONFIRMED") == 1
    assert "PAYMENT_CONFIRM_DUPLICATE_IGNORED" in actions
