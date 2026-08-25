"""Authoritative reconciliation of uncertain payments (Phase 2 / STEP 10).

This is the *recovery* half of TrustRail's differentiator. Money is asynchronous:
after ``execute`` a transaction can sit in ``PAYMENT_PENDING`` (order opened,
customer paying out-of-band) or ``PAYMENT_UNKNOWN`` (an ambiguous gateway error —
we do not know whether an order even exists). Webhooks *usually* resolve these,
but webhooks get lost, delayed, or arrive out of order. Reconciliation is the
authority of last resort: it asks Razorpay directly what actually happened and
converges TrustRail's state to the truth — **without ever charging again**.

Guarantees and safety properties:

* **Authoritative, not speculative.** State is decided from what Razorpay reports
  for a known order reference (its order status and the payments attached to it),
  never from anything the AI buyer said.
* **Never re-charges.** Reconciliation opens no new orders and initiates no new
  payments. Its only *write* to Razorpay is capturing a payment the customer has
  already authorized — completing an in-flight payment, not starting one — and
  even that is TrustRail-controlled, never AI-controlled.
* **Convergence flows through the conductor.** Every state change goes through
  :func:`app.services.transaction.confirm_payment_and_fulfil` /
  :func:`~app.services.transaction.fail_payment` /
  :func:`~app.services.transaction.begin_recovery`, so the state machine, audit
  trail and idempotency guarantees are identical to the synchronous and webhook
  paths. Running a sweep twice changes nothing the second time.
* **Concurrency-safe.** Each transaction is row-locked (``FOR UPDATE`` on
  Postgres) before it is examined, so a webhook and a sweep cannot both resolve
  the same order in parallel — the second waits, re-reads the resolved state, and
  takes the idempotent no-op path.
* **Defence-in-depth on amounts.** A captured/authorized payment is confirmed
  only after its amount and currency match what TrustRail told Razorpay.
* **UNKNOWN is honoured.** An UNKNOWN transaction with no order reference cannot
  be safely auto-resolved (re-creating an order could, in theory, duplicate a
  charge path). We move it into ``RECOVERY_PENDING`` and flag that it needs an
  order reference or operator action — we never guess it FAILED or CONFIRMED.

Reconciliation only functions with a real Razorpay gateway (it needs the
authoritative read APIs). Under the default mock gateway it reports ``skipped``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import Clock, default_clock
from app.enums import Actor, AuditResult
from app.enums import TransactionState as S
from app.models.merchant import RazorpayPayment
from app.models.transaction import Transaction
from app.services import audit
from app.services.locking import lock_transaction_by_identity
from app.services.payment import PAYMENT_CONFIRMED
from app.services.razorpay_gateway import status_from_provider
from app.services.transaction import (
    begin_recovery,
    confirm_payment_and_fulfil,
    fail_payment,
)

# The non-terminal payment/recovery states a sweep will attempt to resolve.
RESOLVABLE_STATES: tuple[S, ...] = (
    S.PAYMENT_PENDING,
    S.PAYMENT_UNKNOWN,
    S.RECOVERY_PENDING,
)

# Razorpay payment-entity statuses.
_CAPTURED_STATUSES = {"captured"}
_AUTHORIZED_STATUSES = {"authorized"}

PROVIDER = "razorpay"


@dataclass(frozen=True)
class ReconcileOutcome:
    """The result of examining one transaction. Purely informational."""

    transaction_id: str
    transaction_identity: str
    from_state: str
    to_state: str
    action: str  # confirmed|captured_and_confirmed|failed|still_pending|
    #              needs_reference|skipped|not_capable|error
    detail: str


# --------------------------------------------------------------------------- #
# gateway capability + small helpers
# --------------------------------------------------------------------------- #
def _is_capable(gateway) -> bool:
    """True only for a gateway exposing the authoritative Razorpay read APIs."""
    return all(
        callable(getattr(gateway, name, None))
        for name in ("fetch_order", "list_order_payments", "capture_payment")
    )


def _order_id_for(db: Session, txn: Transaction) -> str | None:
    """The Razorpay order reference for this transaction, if we ever got one."""
    if txn.razorpay_order_id:
        return txn.razorpay_order_id
    rp = db.get(RazorpayPayment, txn.transaction_identity)
    return rp.razorpay_order_id if rp is not None else None


def _expected(db: Session, txn: Transaction) -> tuple[int | None, str | None]:
    """What TrustRail told Razorpay to charge (authoritative expectation)."""
    rp = db.get(RazorpayPayment, txn.transaction_identity)
    if rp is not None:
        return rp.amount, rp.currency
    return txn.quoted_total, txn.currency


def _first_payment_with_status(payments: Sequence[dict], statuses: set[str]) -> dict | None:
    for p in payments:
        if str(p.get("status") or "").lower() in statuses:
            return p
    return None


def _amount_matches(got, expected) -> bool:
    """Compare minor-unit amounts. An unknown expectation cannot be validated."""
    if expected is None:
        return False
    try:
        return int(got) == int(expected)
    except (TypeError, ValueError):
        return False


def _outcome(txn: Transaction, from_state: str, action: str, detail: str) -> ReconcileOutcome:
    return ReconcileOutcome(
        transaction_id=txn.id,
        transaction_identity=txn.transaction_identity,
        from_state=from_state,
        to_state=txn.state,
        action=action,
        detail=detail,
    )


def _record(
    db: Session,
    txn: Transaction,
    *,
    action: str,
    reason: str,
    clock: Clock,
    result: AuditResult = AuditResult.INFO,
    metadata: dict | None = None,
) -> None:
    audit.record(
        db,
        actor=Actor.RAZORPAY,
        action=action,
        result=result,
        reason=reason,
        transaction_id=txn.id,
        transaction_identity=txn.transaction_identity,
        metadata=metadata or {},
        clock=clock,
    )
    db.commit()


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def reconcile_transaction(
    db: Session,
    *,
    transaction_identity: str,
    gateway,
    clock: Clock = default_clock,
    conclude_failed: bool = False,
) -> ReconcileOutcome:
    """Authoritatively reconcile a single transaction by its semantic identity.

    Row-locks the transaction, re-reads its (possibly already-resolved) state, and
    converges it against Razorpay's truth. Idempotent and concurrency-safe.

    ``conclude_failed`` opt-in: when the order exists but shows no captured or
    authorized payment, only then may the sweep conclude an authoritative
    ``PAYMENT_FAILED`` (Razorpay orders never auto-fail, so failure is a deliberate
    operator decision, never a default). Off by default so an unpaid-but-open
    order is left ``PENDING`` for the customer to complete.
    """
    txn = lock_transaction_by_identity(db, transaction_identity)
    if txn is None:
        # Nothing to reconcile; return a synthetic skipped outcome.
        return ReconcileOutcome(
            transaction_id="<none>",
            transaction_identity=transaction_identity,
            from_state="<none>",
            to_state="<none>",
            action="skipped",
            detail="no transaction for identity",
        )

    from_state = txn.state
    if S(txn.state) not in RESOLVABLE_STATES:
        return _outcome(txn, from_state, "skipped", f"state {txn.state} is not resolvable")

    if not _is_capable(gateway):
        return _outcome(
            txn, from_state, "not_capable", "gateway cannot query Razorpay (mock mode)"
        )

    return _reconcile_locked(
        db, txn, gateway=gateway, clock=clock, conclude_failed=conclude_failed
    )


def reconcile_pending(
    db: Session,
    *,
    gateway,
    clock: Clock = default_clock,
    conclude_failed: bool = False,
    limit: int = 100,
) -> list[ReconcileOutcome]:
    """Sweep every transaction in a resolvable state and reconcile each.

    The candidate scan takes no locks; each transaction is locked individually
    inside :func:`reconcile_transaction`, so a long sweep never holds a lock over
    unrelated rows and interleaves safely with live webhooks.
    """
    if not _is_capable(gateway):
        return []

    identities = (
        db.execute(
            select(Transaction.transaction_identity)
            .where(Transaction.state.in_([s.value for s in RESOLVABLE_STATES]))
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return [
        reconcile_transaction(
            db,
            transaction_identity=identity,
            gateway=gateway,
            clock=clock,
            conclude_failed=conclude_failed,
        )
        for identity in identities
    ]


# --------------------------------------------------------------------------- #
# core (assumes txn is the freshly-read, row-locked, resolvable row)
# --------------------------------------------------------------------------- #
def _reconcile_locked(
    db: Session,
    txn: Transaction,
    *,
    gateway,
    clock: Clock,
    conclude_failed: bool,
) -> ReconcileOutcome:
    from_state = txn.state
    order_id = _order_id_for(db, txn)

    # No order reference: an UNKNOWN whose order creation failed ambiguously. We
    # cannot safely auto-resolve (re-creating an order risks duplicating a charge
    # path), so we move it into active recovery and flag it for a reference /
    # operator. We never guess CONFIRMED or FAILED here.
    if not order_id:
        begin_recovery(
            db,
            txn,
            clock=clock,
            provider=PROVIDER,
            reason="no Razorpay order reference; cannot auto-resolve — recovery required",
        )
        _record(
            db,
            txn,
            action="RECONCILIATION_NEEDS_REFERENCE",
            reason=(
                "no Razorpay order reference for an uncertain payment; will not "
                "re-charge or guess an outcome — awaiting a reference or operator"
            ),
            clock=clock,
        )
        return _outcome(
            txn, from_state, "needs_reference", "no order reference; parked in recovery"
        )

    # Authoritative query. A failure here is itself uncertain: record and leave
    # state untouched (never fail/charge on a reconciliation error).
    try:
        order = gateway.fetch_order(order_id)
        payments = gateway.list_order_payments(order_id)
    except Exception as exc:
        _record(
            db,
            txn,
            action="RECONCILIATION_ERROR",
            reason="could not query Razorpay authoritatively; leaving state unchanged",
            clock=clock,
            metadata={"order_id": order_id, "error_type": type(exc).__name__},
        )
        return _outcome(txn, from_state, "error", "gateway query failed")

    exp_amount, exp_currency = _expected(db, txn)
    order_status = str(order.get("status") or "")

    # 1) Money already captured (a captured payment, or the order marked paid).
    captured = _first_payment_with_status(payments, _CAPTURED_STATUSES)
    if captured is not None or status_from_provider(order_status) == PAYMENT_CONFIRMED:
        pay = captured or (payments[0] if payments else {})
        got_amount = pay.get("amount", order.get("amount_paid", order.get("amount")))
        got_currency = pay.get("currency", order.get("currency"))
        if not _amount_matches(got_amount, exp_amount) or (
            got_currency and got_currency != exp_currency
        ):
            _record(
                db,
                txn,
                action="RECONCILIATION_AMOUNT_MISMATCH",
                reason="captured amount/currency does not match the authorized order",
                clock=clock,
                result=AuditResult.FAILURE,
                metadata={
                    "expected_amount": exp_amount,
                    "got_amount": got_amount,
                    "expected_currency": exp_currency,
                    "got_currency": got_currency,
                    "order_id": order_id,
                },
            )
            return _outcome(txn, from_state, "error", "amount/currency mismatch")

        rp = db.get(RazorpayPayment, txn.transaction_identity)
        if rp is not None:
            rp.status = "captured"
            if pay.get("id"):
                rp.razorpay_payment_id = str(pay["id"])
            db.flush()
        txn2, _ = confirm_payment_and_fulfil(
            db,
            txn,
            payment_ref=str(pay.get("id") or ""),
            amount=int(exp_amount),
            clock=clock,
            provider=PROVIDER,
        )
        return _outcome(
            txn2, from_state, "confirmed", f"captured payment confirmed; now {txn2.state}"
        )

    # 2) Authorized but not captured: TrustRail captures it (completing an
    #    already-authorized payment), then confirms.
    authorized = _first_payment_with_status(payments, _AUTHORIZED_STATUSES)
    if authorized is not None:
        if not _amount_matches(authorized.get("amount"), exp_amount) or (
            authorized.get("currency") and authorized.get("currency") != exp_currency
        ):
            _record(
                db,
                txn,
                action="RECONCILIATION_AMOUNT_MISMATCH",
                reason="authorized amount/currency does not match the authorized order",
                clock=clock,
                result=AuditResult.FAILURE,
                metadata={
                    "expected_amount": exp_amount,
                    "got_amount": authorized.get("amount"),
                    "order_id": order_id,
                },
            )
            return _outcome(txn, from_state, "error", "authorized amount/currency mismatch")

        payment_id = str(authorized.get("id") or "")
        try:
            gateway.capture_payment(payment_id, int(exp_amount), exp_currency or txn.currency)
        except Exception as exc:
            # Capture failed/uncertain — do NOT conclude anything. A later sweep or
            # webhook re-examines; if it was in fact captured we will see it then.
            _record(
                db,
                txn,
                action="RECONCILIATION_CAPTURE_ERROR",
                reason="capture attempt did not confirm; leaving state unchanged",
                clock=clock,
                metadata={"payment_id": payment_id, "error_type": type(exc).__name__},
            )
            return _outcome(txn, from_state, "error", "capture failed")

        rp = db.get(RazorpayPayment, txn.transaction_identity)
        if rp is not None:
            rp.status = "captured"
            rp.razorpay_payment_id = payment_id
            db.flush()
        txn2, _ = confirm_payment_and_fulfil(
            db,
            txn,
            payment_ref=payment_id,
            amount=int(exp_amount),
            clock=clock,
            provider=PROVIDER,
        )
        return _outcome(
            txn2,
            from_state,
            "captured_and_confirmed",
            f"authorized payment captured and confirmed; now {txn2.state}",
        )

    # 3) No captured/authorized payment. Optionally conclude an authoritative
    #    failure (opt-in); otherwise it is genuinely still pending.
    if conclude_failed:
        txn2, _ = fail_payment(
            db,
            txn,
            clock=clock,
            provider=PROVIDER,
            reason="no successful payment on the order; concluded failed by reconciliation",
        )
        return _outcome(txn2, from_state, "failed", f"concluded failed; now {txn2.state}")

    # 4) Still pending. If the transaction was UNKNOWN, record that we have now
    #    confirmed the order exists and are actively recovering it.
    if S(txn.state) == S.PAYMENT_UNKNOWN:
        begin_recovery(
            db,
            txn,
            clock=clock,
            provider=PROVIDER,
            reason="order exists but is unpaid; entering recovery pending payment",
        )
    _record(
        db,
        txn,
        action="RECONCILIATION_PENDING",
        reason="order exists but no captured/authorized payment yet; awaiting payment",
        clock=clock,
        metadata={"order_id": order_id, "order_status": order_status},
    )
    return _outcome(txn, from_state, "still_pending", "order open, no payment yet")
