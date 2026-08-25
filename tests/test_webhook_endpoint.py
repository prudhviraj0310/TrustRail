"""HTTP-level tests for ``POST /webhooks/razorpay`` (Phase 2 / STEP 8).

Exercises authenticity (signature over the raw body), the mock-mode refusal, and
the safety of processing: confirm→fulfil, idempotent re-delivery, amount
mismatch, unmatched orders, and the non-terminalising ``payment.failed``.
"""

from __future__ import annotations

import hashlib
import hmac
import json

from app.enums import TransactionState as S
from tests.conftest import WEBHOOK_SECRET

CAPTURE_AMOUNT = 129900  # SKU-001 price in paise


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _sign(body: bytes) -> str:
    return hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()


def _drive_to_pending(client_rz, make_payload, **kw) -> dict:
    created = client_rz.post("/intents", json=make_payload(**kw)).json()
    client_rz.post(f"/intents/{created['intent_id']}/validate")
    client_rz.post(f"/intents/{created['intent_id']}/authorize")
    body = client_rz.post(
        "/transactions", json={"intent_id": created["intent_id"]}
    ).json()
    assert body["state"] == S.PAYMENT_PENDING.value
    return created


def _order_id(client_rz, transaction_id) -> str:
    return client_rz.get(f"/transactions/{transaction_id}").json()["razorpay_order_id"]


def _captured_event(
    order_id, *, payment_id="pay_1", amount=CAPTURE_AMOUNT, currency="INR"
) -> bytes:
    return json.dumps(
        {
            "event": "payment.captured",
            "payload": {
                "payment": {
                    "entity": {
                        "id": payment_id,
                        "order_id": order_id,
                        "amount": amount,
                        "currency": currency,
                        "status": "captured",
                    }
                }
            },
        }
    ).encode()


def _audit_actions(client_rz, transaction_id) -> list[str]:
    return [
        e["action"]
        for e in client_rz.get(f"/transactions/{transaction_id}/audit").json()["events"]
    ]


# --------------------------------------------------------------------------- #
# authenticity
# --------------------------------------------------------------------------- #
def test_webhook_returns_503_under_mock_gateway(client):
    # The default (mock) gateway has no webhook secret -> refuse, don't pretend.
    r = client.post(
        "/webhooks/razorpay", content=b"{}", headers={"X-Razorpay-Signature": "x"}
    )
    assert r.status_code == 503


def test_webhook_rejects_missing_signature(client_rz):
    r = client_rz.post("/webhooks/razorpay", content=b'{"event":"payment.captured"}')
    assert r.status_code == 400


def test_webhook_rejects_bad_signature(client_rz):
    body = b'{"event":"payment.captured"}'
    r = client_rz.post(
        "/webhooks/razorpay", content=body, headers={"X-Razorpay-Signature": "bad"}
    )
    assert r.status_code == 400


def test_webhook_rejects_malformed_body_with_valid_signature(client_rz):
    body = b"not json at all"
    r = client_rz.post(
        "/webhooks/razorpay", content=body, headers={"X-Razorpay-Signature": _sign(body)}
    )
    assert r.status_code == 400


# --------------------------------------------------------------------------- #
# processing
# --------------------------------------------------------------------------- #
def test_valid_capture_confirms_and_completes(client_rz, make_payload):
    created = _drive_to_pending(client_rz, make_payload, max_amount=500000)
    order_id = _order_id(client_rz, created["transaction_id"])

    body = _captured_event(order_id)
    r = client_rz.post(
        "/webhooks/razorpay", content=body, headers={"X-Razorpay-Signature": _sign(body)}
    )
    assert r.status_code == 200
    assert r.json()["status"] == "confirmed"

    txn = client_rz.get(f"/transactions/{created['transaction_id']}").json()
    assert txn["state"] == S.COMPLETED.value
    assert txn["payment_ref"] == "pay_1"
    assert txn["merchant_order_id"] is not None
    assert txn["amount_captured"] == CAPTURE_AMOUNT
    assert "PAYMENT_CONFIRMED" in _audit_actions(client_rz, created["transaction_id"])


def test_duplicate_delivery_is_idempotent(client_rz, make_payload):
    created = _drive_to_pending(client_rz, make_payload, max_amount=500000)
    order_id = _order_id(client_rz, created["transaction_id"])
    body = _captured_event(order_id)
    headers = {"X-Razorpay-Signature": _sign(body)}

    client_rz.post("/webhooks/razorpay", content=body, headers=headers)
    first = client_rz.get(f"/transactions/{created['transaction_id']}").json()
    # deliver the exact same event again
    r2 = client_rz.post("/webhooks/razorpay", content=body, headers=headers)
    second = client_rz.get(f"/transactions/{created['transaction_id']}").json()

    assert r2.status_code == 200
    assert first["state"] == second["state"] == S.COMPLETED.value
    assert first["merchant_order_id"] == second["merchant_order_id"]  # no second order
    assert "PAYMENT_CONFIRM_DUPLICATE_IGNORED" in _audit_actions(
        client_rz, created["transaction_id"]
    )


def test_amount_mismatch_is_refused(client_rz, make_payload):
    created = _drive_to_pending(client_rz, make_payload, max_amount=500000)
    order_id = _order_id(client_rz, created["transaction_id"])
    body = _captured_event(order_id, amount=999)  # wrong amount
    r = client_rz.post(
        "/webhooks/razorpay", content=body, headers={"X-Razorpay-Signature": _sign(body)}
    )

    assert r.status_code == 200
    assert r.json()["status"] == "mismatch"
    txn = client_rz.get(f"/transactions/{created['transaction_id']}").json()
    assert txn["state"] == S.PAYMENT_PENDING.value  # unchanged, not confirmed
    assert "WEBHOOK_AMOUNT_MISMATCH" in _audit_actions(
        client_rz, created["transaction_id"]
    )


def test_unmatched_order_is_recorded_not_acted_on(client_rz):
    body = _captured_event("order_does_not_exist")
    r = client_rz.post(
        "/webhooks/razorpay", content=body, headers={"X-Razorpay-Signature": _sign(body)}
    )
    assert r.status_code == 200
    assert r.json()["status"] == "unmatched"


def test_payment_failed_does_not_terminalise(client_rz, make_payload):
    # A single payment.failed is only a failed attempt: a later capture may arrive
    # (e.g. a UPI retry). The transaction must NOT move to PAYMENT_FAILED here.
    created = _drive_to_pending(client_rz, make_payload, max_amount=500000)
    order_id = _order_id(client_rz, created["transaction_id"])
    failed = json.dumps(
        {
            "event": "payment.failed",
            "payload": {
                "payment": {
                    "entity": {"id": "pay_x", "order_id": order_id, "status": "failed"}
                }
            },
        }
    ).encode()
    r = client_rz.post(
        "/webhooks/razorpay",
        content=failed,
        headers={"X-Razorpay-Signature": _sign(failed)},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "attempt_failed"

    txn = client_rz.get(f"/transactions/{created['transaction_id']}").json()
    assert txn["state"] == S.PAYMENT_PENDING.value  # still recoverable

    # a capture that arrives AFTER the failed attempt still succeeds
    body = _captured_event(order_id)
    client_rz.post(
        "/webhooks/razorpay", content=body, headers={"X-Razorpay-Signature": _sign(body)}
    )
    txn2 = client_rz.get(f"/transactions/{created['transaction_id']}").json()
    assert txn2["state"] == S.COMPLETED.value
