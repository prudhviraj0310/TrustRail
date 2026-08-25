"""Tests for refund execution (Phase 2 / STEP 11).

When money is captured but the merchant order fails, TrustRail owes a refund. The
refund service discharges it, records the refund id on both the transaction and
the RazorpayPayment, resolves the transaction to COMPLETED, and — critically —
never issues a second refund.
"""

from __future__ import annotations

from app.enums import TransactionState as S
from app.models.merchant import RazorpayPayment
from app.models.transaction import Transaction
from app.services import reconciliation, refund
from app.services.payment import default_gateway

FAIL_ORDER_AMOUNT = 100000  # SKU-FAIL-ORDER price in paise


def _drive_to_refund_required(client_rz, make_payload, rz_client, razorpay_gateway, svc_session, clock):
    """Reach REFUND_REQUIRED: capture succeeds, then the merchant order fails."""
    created = client_rz.post(
        "/intents",
        json=make_payload(items=[{"sku": "SKU-FAIL-ORDER", "quantity": 1}], max_amount=200000),
    ).json()
    client_rz.post(f"/intents/{created['intent_id']}/validate")
    client_rz.post(f"/intents/{created['intent_id']}/authorize")
    client_rz.post("/transactions", json={"intent_id": created["intent_id"]})
    order_id = client_rz.get(f"/transactions/{created['transaction_id']}").json()["razorpay_order_id"]
    rz_client.add_captured(order_id, payment_id="pay_cap", amount=FAIL_ORDER_AMOUNT)
    reconciliation.reconcile_transaction(
        svc_session, transaction_identity=created["transaction_identity"],
        gateway=razorpay_gateway, clock=clock,
    )
    assert client_rz.get(f"/transactions/{created['transaction_id']}").json()["state"] == S.REFUND_REQUIRED.value
    return created


def test_refund_resolves_to_completed(
    client_rz, make_payload, rz_client, razorpay_gateway, svc_session, clock
):
    created = _drive_to_refund_required(
        client_rz, make_payload, rz_client, razorpay_gateway, svc_session, clock
    )
    out = refund.refund_transaction(
        svc_session, transaction_identity=created["transaction_identity"],
        gateway=razorpay_gateway, clock=clock,
    )
    assert out.action == "refunded"
    assert out.refund_id and out.refund_id.startswith("rfnd_")
    assert len(rz_client.refund_calls) == 1
    assert rz_client.refund_calls[0][0] == "pay_cap"

    txn = client_rz.get(f"/transactions/{created['transaction_id']}").json()
    assert txn["state"] == S.COMPLETED.value
    # refund id persisted on both records; RazorpayPayment amount fully refunded
    svc_session.expire_all()
    t = svc_session.get(Transaction, created["transaction_id"])
    rp = svc_session.get(RazorpayPayment, created["transaction_identity"])
    assert t.razorpay_refund_id == out.refund_id
    assert t.payment_status == "refunded"
    assert rp.razorpay_refund_id == out.refund_id
    assert rz_client.refund_calls[0][1]["amount"] == FAIL_ORDER_AMOUNT


def test_refund_is_idempotent_never_double_refunds(
    client_rz, make_payload, rz_client, razorpay_gateway, svc_session, clock
):
    created = _drive_to_refund_required(
        client_rz, make_payload, rz_client, razorpay_gateway, svc_session, clock
    )
    refund.refund_transaction(
        svc_session, transaction_identity=created["transaction_identity"],
        gateway=razorpay_gateway, clock=clock,
    )
    again = refund.refund_transaction(
        svc_session, transaction_identity=created["transaction_identity"],
        gateway=razorpay_gateway, clock=clock,
    )
    assert again.action == "already_refunded"
    assert len(rz_client.refund_calls) == 1  # NOT two


def test_refund_error_stays_in_refund_required(
    client_rz, make_payload, rz_client, razorpay_gateway, svc_session, clock
):
    created = _drive_to_refund_required(
        client_rz, make_payload, rz_client, razorpay_gateway, svc_session, clock
    )
    rz_client.raise_on_refund = RuntimeError("refund endpoint down")
    out = refund.refund_transaction(
        svc_session, transaction_identity=created["transaction_identity"],
        gateway=razorpay_gateway, clock=clock,
    )
    assert out.action == "error"
    txn = client_rz.get(f"/transactions/{created['transaction_id']}").json()
    assert txn["state"] == S.REFUND_REQUIRED.value  # still owes a refund -> retryable

    # once the gateway recovers, a retry succeeds and issues exactly one refund
    rz_client.raise_on_refund = None
    out2 = refund.refund_transaction(
        svc_session, transaction_identity=created["transaction_identity"],
        gateway=razorpay_gateway, clock=clock,
    )
    assert out2.action == "refunded"


def test_refund_skips_transactions_that_owe_nothing(
    client_rz, make_payload, razorpay_gateway, svc_session, clock
):
    # A happy-path COMPLETED transaction is not in REFUND_REQUIRED -> skipped.
    created = client_rz.post("/intents", json=make_payload(max_amount=500000)).json()
    client_rz.post(f"/intents/{created['intent_id']}/validate")
    client_rz.post(f"/intents/{created['intent_id']}/authorize")
    client_rz.post("/transactions", json={"intent_id": created["intent_id"]})
    out = refund.refund_transaction(
        svc_session, transaction_identity=created["transaction_identity"],
        gateway=razorpay_gateway, clock=clock,
    )
    assert out.action == "skipped"


def test_refund_mock_gateway_is_not_capable(
    client_rz, make_payload, rz_client, razorpay_gateway, svc_session, clock
):
    created = _drive_to_refund_required(
        client_rz, make_payload, rz_client, razorpay_gateway, svc_session, clock
    )
    out = refund.refund_transaction(
        svc_session, transaction_identity=created["transaction_identity"],
        gateway=default_gateway, clock=clock,
    )
    assert out.action == "not_capable"


def test_refund_pending_sweep(
    client_rz, make_payload, rz_client, razorpay_gateway, svc_session, clock
):
    created = _drive_to_refund_required(
        client_rz, make_payload, rz_client, razorpay_gateway, svc_session, clock
    )
    outcomes = refund.refund_pending(svc_session, gateway=razorpay_gateway, clock=clock)
    assert any(o.action == "refunded" and o.transaction_identity == created["transaction_identity"]
               for o in outcomes)
    # nothing left to refund on a second sweep
    assert refund.refund_pending(svc_session, gateway=razorpay_gateway, clock=clock) == []
