"""Tests for authoritative reconciliation (Phase 2 / STEP 10).

Reconciliation is the recovery authority: it asks Razorpay what really happened
and converges TrustRail's state — never re-charging. These tests drive a real
``RazorpayGateway`` (backed by the fake client) to a PENDING/UNKNOWN state via the
HTTP API, then run the sweep against a controlled order/payment picture.

The ``client_rz``, ``razorpay_gateway`` and ``rz_client`` fixtures all share one
underlying fake client, so state attached via ``rz_client`` is what the sweep
(driven through ``razorpay_gateway``) sees.
"""

from __future__ import annotations

from app.enums import TransactionState as S
from app.services import reconciliation
from app.services.payment import default_gateway

CAPTURE_AMOUNT = 129900  # SKU-001


def _drive_to_pending(client_rz, make_payload, **kw) -> dict:
    created = client_rz.post("/intents", json=make_payload(**kw)).json()
    client_rz.post(f"/intents/{created['intent_id']}/validate")
    client_rz.post(f"/intents/{created['intent_id']}/authorize")
    client_rz.post("/transactions", json={"intent_id": created["intent_id"]})
    return created


def _order_id(client_rz, transaction_id) -> str:
    return client_rz.get(f"/transactions/{transaction_id}").json()["razorpay_order_id"]


def _state(client_rz, transaction_id) -> str:
    return client_rz.get(f"/transactions/{transaction_id}").json()["state"]


# --------------------------------------------------------------------------- #
# missed webhook: the money is already captured on Razorpay's side
# --------------------------------------------------------------------------- #
def test_reconcile_confirms_a_captured_payment(
    client_rz, make_payload, rz_client, razorpay_gateway, svc_session, clock
):
    created = _drive_to_pending(client_rz, make_payload, max_amount=500000)
    order_id = _order_id(client_rz, created["transaction_id"])
    rz_client.add_captured(order_id, payment_id="pay_recon", amount=CAPTURE_AMOUNT)

    out = reconciliation.reconcile_transaction(
        svc_session, transaction_identity=created["transaction_identity"],
        gateway=razorpay_gateway, clock=clock,
    )
    assert out.action == "confirmed"
    assert _state(client_rz, created["transaction_id"]) == S.COMPLETED.value


def test_reconcile_is_idempotent(
    client_rz, make_payload, rz_client, razorpay_gateway, svc_session, clock
):
    created = _drive_to_pending(client_rz, make_payload, max_amount=500000)
    order_id = _order_id(client_rz, created["transaction_id"])
    rz_client.add_captured(order_id)

    reconciliation.reconcile_transaction(
        svc_session, transaction_identity=created["transaction_identity"],
        gateway=razorpay_gateway, clock=clock,
    )
    again = reconciliation.reconcile_transaction(
        svc_session, transaction_identity=created["transaction_identity"],
        gateway=razorpay_gateway, clock=clock,
    )
    assert again.action == "skipped"  # already COMPLETED, nothing to resolve
    assert _state(client_rz, created["transaction_id"]) == S.COMPLETED.value


# --------------------------------------------------------------------------- #
# authorized-but-uncaptured: TrustRail captures, then confirms
# --------------------------------------------------------------------------- #
def test_reconcile_captures_authorized_payment(
    client_rz, make_payload, rz_client, razorpay_gateway, svc_session, clock
):
    created = _drive_to_pending(client_rz, make_payload, max_amount=500000)
    order_id = _order_id(client_rz, created["transaction_id"])
    rz_client.add_authorized(order_id, payment_id="pay_auth", amount=CAPTURE_AMOUNT)

    out = reconciliation.reconcile_transaction(
        svc_session, transaction_identity=created["transaction_identity"],
        gateway=razorpay_gateway, clock=clock,
    )
    assert out.action == "captured_and_confirmed"
    assert len(rz_client.capture_calls) == 1  # TrustRail captured exactly once
    assert rz_client.capture_calls[0][0] == "pay_auth"
    assert _state(client_rz, created["transaction_id"]) == S.COMPLETED.value


# --------------------------------------------------------------------------- #
# still pending / conclude-failed
# --------------------------------------------------------------------------- #
def test_reconcile_open_order_stays_pending_by_default(
    client_rz, make_payload, razorpay_gateway, svc_session, clock
):
    created = _drive_to_pending(client_rz, make_payload, max_amount=500000)
    # no payment attached to the order at all
    out = reconciliation.reconcile_transaction(
        svc_session, transaction_identity=created["transaction_identity"],
        gateway=razorpay_gateway, clock=clock,
    )
    assert out.action == "still_pending"
    assert _state(client_rz, created["transaction_id"]) == S.PAYMENT_PENDING.value


def test_reconcile_conclude_failed_is_opt_in(
    client_rz, make_payload, razorpay_gateway, svc_session, clock
):
    created = _drive_to_pending(client_rz, make_payload, max_amount=500000)
    out = reconciliation.reconcile_transaction(
        svc_session, transaction_identity=created["transaction_identity"],
        gateway=razorpay_gateway, clock=clock, conclude_failed=True,
    )
    assert out.action == "failed"
    assert _state(client_rz, created["transaction_id"]) == S.PAYMENT_FAILED.value


# --------------------------------------------------------------------------- #
# UNKNOWN with no order reference: parked in recovery, never guessed
# --------------------------------------------------------------------------- #
def test_reconcile_unknown_without_reference_parks_in_recovery(
    client_rz, make_payload, rz_client, razorpay_gateway, svc_session, clock
):
    # Force an ambiguous order-creation failure so execute -> PAYMENT_UNKNOWN.
    rz_client.raise_on_create = RuntimeError("network blip")
    created = client_rz.post("/intents", json=make_payload(max_amount=500000)).json()
    client_rz.post(f"/intents/{created['intent_id']}/validate")
    client_rz.post(f"/intents/{created['intent_id']}/authorize")
    client_rz.post("/transactions", json={"intent_id": created["intent_id"]})
    assert _state(client_rz, created["transaction_id"]) == S.PAYMENT_UNKNOWN.value

    rz_client.raise_on_create = None  # gateway healthy again for the sweep
    out = reconciliation.reconcile_transaction(
        svc_session, transaction_identity=created["transaction_identity"],
        gateway=razorpay_gateway, clock=clock,
    )
    assert out.action == "needs_reference"
    assert _state(client_rz, created["transaction_id"]) == S.RECOVERY_PENDING.value

    # a second sweep never re-charges or mints a new order
    reconciliation.reconcile_transaction(
        svc_session, transaction_identity=created["transaction_identity"],
        gateway=razorpay_gateway, clock=clock,
    )
    assert _state(client_rz, created["transaction_id"]) == S.RECOVERY_PENDING.value
    assert len(rz_client.orders) == 0  # no NEW order was minted during recovery


# --------------------------------------------------------------------------- #
# safety: a query error leaves state untouched; mock gateway is not capable
# --------------------------------------------------------------------------- #
def test_reconcile_gateway_error_leaves_state_unchanged(
    client_rz, make_payload, svc_session, clock
):
    created = _drive_to_pending(client_rz, make_payload, max_amount=500000)

    class Boom:
        def fetch_order(self, *a, **k):
            raise RuntimeError("razorpay down")

        def list_order_payments(self, *a, **k):
            raise RuntimeError("razorpay down")

        def capture_payment(self, *a, **k):
            raise RuntimeError("razorpay down")

    out = reconciliation.reconcile_transaction(
        svc_session, transaction_identity=created["transaction_identity"], gateway=Boom(), clock=clock
    )
    assert out.action == "error"
    assert _state(client_rz, created["transaction_id"]) == S.PAYMENT_PENDING.value  # unchanged


def test_reconcile_mock_gateway_is_not_capable(client_rz, make_payload, svc_session, clock):
    created = _drive_to_pending(client_rz, make_payload, max_amount=500000)
    out = reconciliation.reconcile_transaction(
        svc_session, transaction_identity=created["transaction_identity"],
        gateway=default_gateway, clock=clock,
    )
    assert out.action == "not_capable"
