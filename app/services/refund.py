"""Refund execution for the money-owed boundary (Phase 2 / STEP 11).

When a payment is captured but the merchant order then fails, TrustRail lands in
``REFUND_REQUIRED``: we are holding the customer's money for goods we could not
deliver. This service discharges that obligation by issuing the refund through
Razorpay and resolving the transaction (``REFUND_REQUIRED`` → ``COMPLETED``).

Idempotency and honesty:

* **At-most-one refund, TrustRail-side.** The guarantee is a *persisted* refund
  id: before issuing, we check :attr:`Transaction.razorpay_refund_id`; if it is
  already set we never call Razorpay again. Immediately after Razorpay accepts
  the refund we persist the id and the terminal state together via
  :func:`app.services.transaction.complete_refund`.
* **We do NOT claim exactly-once.** The pinned Razorpay SDK (2.0.1) does not
  reliably expose a per-call refund idempotency key, so a crash in the narrow
  window between "Razorpay accepted the refund" and "we committed the refund id"
  could, on a blind retry, issue a second refund. We keep that window as small as
  possible and document it; a production system would attach a refund
  idempotency key. This is a known, stated limitation — not a hidden one.
* **Concurrency-safe.** The transaction row is locked (``FOR UPDATE`` on
  Postgres) before it is examined, so two workers cannot both issue a refund.
* **AI is not involved.** Refunds are decided by TrustRail's state, executed
  against the payment id *TrustRail* recorded at capture — never an id supplied
  by the AI buyer.

Refunds require a real Razorpay gateway (the mock cannot refund); under the mock
this reports ``not_capable`` and changes nothing.
"""

from __future__ import annotations

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
from app.services.transaction import complete_refund

PROVIDER = "razorpay"


@dataclass(frozen=True)
class RefundOutcome:
    transaction_id: str
    transaction_identity: str
    from_state: str
    to_state: str
    action: str  # refunded|already_refunded|skipped|not_capable|no_payment|error
    refund_id: str | None
    detail: str


def _is_capable(gateway) -> bool:
    return callable(getattr(gateway, "refund_payment", None))


def _payment_id_for(db: Session, txn: Transaction) -> str | None:
    if txn.razorpay_payment_id:
        return txn.razorpay_payment_id
    rp = db.get(RazorpayPayment, txn.transaction_identity)
    return rp.razorpay_payment_id if rp is not None else None


def _refund_amount_for(db: Session, txn: Transaction) -> int | None:
    """The amount to refund: exactly what we captured (full refund)."""
    if txn.amount_captured:
        return int(txn.amount_captured)
    rp = db.get(RazorpayPayment, txn.transaction_identity)
    return int(rp.amount) if rp is not None and rp.amount else None


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


def _outcome(
    txn: Transaction, from_state: str, action: str, detail: str, refund_id: str | None = None
) -> RefundOutcome:
    return RefundOutcome(
        transaction_id=txn.id,
        transaction_identity=txn.transaction_identity,
        from_state=from_state,
        to_state=txn.state,
        action=action,
        refund_id=refund_id or txn.razorpay_refund_id,
        detail=detail,
    )


def refund_transaction(
    db: Session,
    *,
    transaction_identity: str,
    gateway,
    clock: Clock = default_clock,
) -> RefundOutcome:
    """Issue the owed refund for one transaction and resolve it. Idempotent."""
    txn = lock_transaction_by_identity(db, transaction_identity)
    if txn is None:
        return RefundOutcome(
            transaction_id="<none>",
            transaction_identity=transaction_identity,
            from_state="<none>",
            to_state="<none>",
            action="skipped",
            refund_id=None,
            detail="no transaction for identity",
        )

    from_state = txn.state

    # Already resolved with a refund on record → nothing to do.
    if S(txn.state) == S.COMPLETED and txn.razorpay_refund_id:
        return _outcome(txn, from_state, "already_refunded", "refund already issued")

    if S(txn.state) != S.REFUND_REQUIRED:
        return _outcome(txn, from_state, "skipped", f"state {txn.state} does not owe a refund")

    if not _is_capable(gateway):
        return _outcome(txn, from_state, "not_capable", "gateway cannot refund (mock mode)")

    # Idempotency guard: a refund id already persisted means Razorpay already
    # accepted a refund (we may have crashed before completing the transition).
    # Complete the transition — never issue a second refund.
    if txn.razorpay_refund_id:
        complete_refund(
            db, txn, refund_id=txn.razorpay_refund_id, clock=clock, provider=PROVIDER
        )
        return _outcome(txn, from_state, "refunded", "completed a previously-issued refund")

    payment_id = _payment_id_for(db, txn)
    amount = _refund_amount_for(db, txn)
    if not payment_id or not amount:
        # We owe a refund but do not have a payment id / amount to refund against.
        _record(
            db,
            txn,
            action="REFUND_NO_PAYMENT_REF",
            reason="refund owed but no captured payment id/amount on record — operator required",
            clock=clock,
            result=AuditResult.FAILURE,
            metadata={"payment_id": payment_id, "amount": amount},
        )
        return _outcome(txn, from_state, "no_payment", "no captured payment reference")

    try:
        refund = gateway.refund_payment(
            payment_id,
            amount=int(amount),
            notes={"transaction_identity": txn.transaction_identity},
        )
    except Exception as exc:
        # Refund failed/uncertain. Stay in REFUND_REQUIRED so a later run retries;
        # we did not record a refund id, so no double-refund can result from this.
        _record(
            db,
            txn,
            action="REFUND_ERROR",
            reason="refund attempt failed; staying in REFUND_REQUIRED for retry",
            clock=clock,
            result=AuditResult.FAILURE,
            metadata={"payment_id": payment_id, "error_type": type(exc).__name__},
        )
        return _outcome(txn, from_state, "error", "refund call failed")

    refund_id = str(refund.get("id") or "")

    # Persist the refund id on both records before/with completing the transition.
    rp = db.get(RazorpayPayment, txn.transaction_identity)
    if rp is not None and refund_id:
        rp.razorpay_refund_id = refund_id
        db.flush()

    complete_refund(db, txn, refund_id=refund_id, clock=clock, provider=PROVIDER)
    return _outcome(
        txn, from_state, "refunded", f"refund {refund_id or '<none>'} issued; now {txn.state}", refund_id
    )


def refund_pending(
    db: Session,
    *,
    gateway,
    clock: Clock = default_clock,
    limit: int = 100,
) -> list[RefundOutcome]:
    """Discharge every outstanding ``REFUND_REQUIRED`` obligation. Idempotent."""
    if not _is_capable(gateway):
        return []
    identities = (
        db.execute(
            select(Transaction.transaction_identity)
            .where(Transaction.state == S.REFUND_REQUIRED.value)
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return [
        refund_transaction(db, transaction_identity=identity, gateway=gateway, clock=clock)
        for identity in identities
    ]
