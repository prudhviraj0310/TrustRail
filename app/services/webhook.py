"""Razorpay webhook processing (Phase 2 / STEP 8).

The HTTP route (:mod:`app.api.webhooks`) is responsible for **authenticity**: it
verifies the ``X-Razorpay-Signature`` HMAC over the raw body before this module
ever runs. This module is responsible for **authority and safety**:

* It never lets an event *create* transaction state. An event for an unknown
  order is recorded-and-ignored, not acted upon.
* Before confirming money, it validates that the event's amount and currency
  match what TrustRail actually told Razorpay (defence-in-depth on top of the
  signature). A mismatch is audited and refused.
* State only moves through the shared, legality-checked transitions in
  :mod:`app.services.transaction`, so a duplicate delivery is an idempotent
  no-op rather than a second charge or a second order.
* A ``payment.failed`` event is treated as a failed *attempt*, not a verdict:
  Razorpay may still deliver a later ``payment.captured`` for the same order
  (e.g. a UPI retry). We record the attempt and let authoritative reconciliation
  decide failure. This directly reflects Razorpay's "webhook order is not fixed"
  behaviour.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.clock import Clock, default_clock
from app.enums import Actor, AuditResult
from app.models.merchant import RazorpayPayment
from app.services import audit
from app.services.locking import lock_transaction_by_order_id
from app.services.transaction import confirm_payment_and_fulfil

# Payment events we act on. Anything else is recorded (if we can match it to a
# transaction) and otherwise ignored.
_CAPTURED = "payment.captured"
_FAILED = "payment.failed"
_AUTHORIZED = "payment.authorized"


def _payment_entity(event: dict) -> dict:
    return ((event.get("payload") or {}).get("payment") or {}).get("entity") or {}


def process_razorpay_event(
    db: Session, event: dict, *, clock: Clock = default_clock
) -> dict:
    """Process a signature-verified Razorpay webhook event. Idempotent.

    Returns a small status dict (always safe to return with HTTP 200 so Razorpay
    stops retrying). Raising is reserved for authenticity failures in the route.
    """
    event_type = str(event.get("event") or "")
    entity = _payment_entity(event)
    order_id = entity.get("order_id")
    payment_id = entity.get("id")
    amount = entity.get("amount")
    currency = entity.get("currency")

    if not order_id:
        # Nothing to safely anchor on. (Non-payment events land here too.)
        return {"status": "ignored", "reason": "no order_id", "event": event_type}

    # Lock the matching transaction row (FOR UPDATE on Postgres) so a concurrent
    # reconciliation cannot resolve the same transaction in parallel.
    txn = lock_transaction_by_order_id(db, str(order_id))
    if txn is None:
        # Never fabricate state from an unmatched event.
        return {"status": "unmatched", "reason": "no transaction for order", "order_id": order_id}

    # Record receipt of every webhook (including retries) for the audit trail.
    audit.record(
        db,
        actor=Actor.RAZORPAY,
        action="WEBHOOK_RECEIVED",
        result=AuditResult.INFO,
        reason=f"razorpay webhook received: {event_type or '<none>'}",
        transaction_id=txn.id,
        transaction_identity=txn.transaction_identity,
        metadata={
            "event": event_type,
            "order_id": order_id,
            "payment_id": payment_id,
            "amount": amount,
            "currency": currency,
        },
        clock=clock,
    )
    db.commit()

    if event_type == _CAPTURED:
        return _handle_captured(
            db, txn, payment_id=payment_id, amount=amount, currency=currency, clock=clock
        )

    if event_type == _FAILED:
        # A failed attempt — NOT a terminal verdict (a later capture may arrive).
        audit.record(
            db,
            actor=Actor.RAZORPAY,
            action="PAYMENT_ATTEMPT_FAILED",
            result=AuditResult.INFO,
            reason=(
                "razorpay reported a failed payment attempt; not terminalising — "
                "a later capture may arrive; reconciliation is authoritative"
            ),
            transaction_id=txn.id,
            transaction_identity=txn.transaction_identity,
            metadata={"payment_id": payment_id},
            clock=clock,
        )
        db.commit()
        return {"status": "attempt_failed", "state": txn.state, "transaction_id": txn.id}

    if event_type == _AUTHORIZED:
        audit.record(
            db,
            actor=Actor.RAZORPAY,
            action="PAYMENT_AUTHORIZED",
            result=AuditResult.INFO,
            reason="payment authorized; awaiting capture/confirmation",
            transaction_id=txn.id,
            transaction_identity=txn.transaction_identity,
            metadata={"payment_id": payment_id},
            clock=clock,
        )
        db.commit()
        return {"status": "authorized", "state": txn.state, "transaction_id": txn.id}

    return {"status": "ignored", "reason": "unhandled event", "event": event_type}


def _handle_captured(
    db: Session,
    txn,
    *,
    payment_id,
    amount,
    currency,
    clock: Clock,
) -> dict:
    """Validate the captured amount/currency, then confirm + fulfil (idempotent)."""
    rp = db.get(RazorpayPayment, txn.transaction_identity)
    expected_amount = rp.amount if rp is not None else txn.quoted_total
    expected_currency = rp.currency if rp is not None else txn.currency

    if amount != expected_amount or (currency and currency != expected_currency):
        # Defence-in-depth beyond the signature: never confirm money that does not
        # match what we authorized/ordered.
        audit.record(
            db,
            actor=Actor.RAZORPAY,
            action="WEBHOOK_AMOUNT_MISMATCH",
            result=AuditResult.FAILURE,
            reason="captured amount/currency does not match the authorized order",
            transaction_id=txn.id,
            transaction_identity=txn.transaction_identity,
            metadata={
                "expected_amount": expected_amount,
                "got_amount": amount,
                "expected_currency": expected_currency,
                "got_currency": currency,
                "payment_id": payment_id,
            },
            clock=clock,
        )
        db.commit()
        return {"status": "mismatch", "state": txn.state, "transaction_id": txn.id}

    if rp is not None:
        rp.status = "captured"
        if payment_id:
            rp.razorpay_payment_id = str(payment_id)

    txn2, result = confirm_payment_and_fulfil(
        db,
        txn,
        payment_ref=str(payment_id or ""),
        amount=int(amount),
        clock=clock,
        provider="razorpay",
    )
    return {
        "status": "confirmed",
        "state": txn2.state,
        "transaction_id": txn2.id,
        "decision": result.decision.value,
    }
