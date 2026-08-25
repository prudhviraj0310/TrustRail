"""Transaction orchestration — the conductor that wires every phase together.

Flow:  create_intent -> validate_intent -> authorize_intent -> execute_transaction

Invariants enforced here:
* State only ever moves through :mod:`app.services.state_machine`.
* Every decision (allow/block/requires-auth) writes at least one audit event.
* Money movement is idempotent on the transaction identity.
* The policy engine — never the caller — decides allow/block.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import Clock, default_clock
from app.enums import Actor, AuditResult, Decision, IntentStatus, PolicyPhase
from app.enums import TransactionState as S
from app.errors import (
    InsufficientInventory,
    IntentNotFound,
    InvalidLifecycleState,
    MerchantOrderFailed,
    ProductNotFound,
    TransactionNotFound,
)
from app.ids import new_intent_id, new_transaction_id
from app.merchant.catalogue import MERCHANT_ID
from app.merchant.client import InProcessMerchantClient
from app.models.intent import Intent
from app.models.transaction import Transaction
from app.schemas.intent import PurchaseIntentIn
from app.schemas.merchant import CheckoutValidateOut
from app.services import audit, policy
from app.services.intent import canonicalize
from app.services.payment import (
    PAYMENT_FAILED,
    PAYMENT_PENDING,
    PAYMENT_UNKNOWN,
    PaymentGateway,
    default_gateway,
)
from app.services.policy import CHECK_TO_STATE, PolicyResult
from app.services.state_machine import assert_transition, can_transition

# States from which execution must not (re)start money movement.
_EXECUTION_IN_FLIGHT_OR_DONE = {
    S.PAYMENT_PENDING,
    S.PAYMENT_CONFIRMED,
    S.ORDER_CONFIRMED,
    S.COMPLETED,
    S.PAYMENT_UNKNOWN,
    S.RECOVERY_PENDING,
    S.REFUND_REQUIRED,
    S.ORDER_FAILED,
    S.PAYMENT_FAILED,
}


# --------------------------------------------------------------------------- #
# internal helpers
# --------------------------------------------------------------------------- #
def _transition(
    db: Session,
    txn: Transaction,
    to_state: S,
    *,
    actor: Actor,
    action: str,
    reason: str,
    clock: Clock,
    intent_id: str | None = None,
    result: AuditResult = AuditResult.SUCCESS,
    metadata: dict | None = None,
) -> None:
    """Move a transaction to ``to_state`` (asserting legality) and audit it."""
    from_state = S(txn.state)
    assert_transition(from_state, to_state)  # hard guarantee — raises if illegal
    txn.state = to_state.value
    meta = {"from_state": from_state.value, "to_state": to_state.value}
    if metadata:
        meta.update(metadata)
    audit.record(
        db,
        actor=actor,
        action=action,
        result=result,
        reason=reason,
        transaction_id=txn.id,
        intent_id=intent_id,
        transaction_identity=txn.transaction_identity,
        metadata=meta,
        clock=clock,
    )


def _intent_status_for(state: S) -> str:
    if state == S.INVALID:
        return IntentStatus.INVALID.value
    if state == S.AUTH_EXPIRED:
        return IntentStatus.EXPIRED.value
    return IntentStatus.BLOCKED.value


def _resolve_failure_state(current: S, desired: S) -> S | None:
    """Pick a reachable failure state; fall back to POLICY_BLOCKED; else None."""
    if can_transition(current, desired):
        return desired
    if can_transition(current, S.POLICY_BLOCKED):
        return S.POLICY_BLOCKED
    return None  # current is terminal — nothing to do


def _build_context(
    intent: Intent,
    quote: CheckoutValidateOut,
    phase: PolicyPhase,
    clock: Clock,
    *,
    prior_quoted_total: int | None,
    is_authorized: bool,
) -> policy.PolicyContext:
    oos = [line.sku for line in quote.lines if line.known and not line.in_stock]
    total_quantity = sum(int(i["quantity"]) for i in intent.items)
    return policy.PolicyContext(
        phase=phase,
        merchant_id_intent=intent.merchant_id,
        merchant_known=(intent.merchant_id == MERCHANT_ID),
        currency_intent=intent.constraints["currency"],
        merchant_currency=quote.currency,
        currency_conflict=quote.currency_conflict,
        unknown_skus=list(quote.unknown_skus),
        oos_skus=oos,
        all_available=quote.all_available and quote.all_known,
        order_total=quote.total,
        max_amount=intent.max_amount,
        total_quantity=total_quantity,
        max_quantity=int(intent.constraints["max_quantity"]),
        now=clock.now(),
        expires_at=intent.expires_at,
        is_authorized=is_authorized,
        prior_quoted_total=prior_quoted_total,
    )


def _policy_step(
    db: Session,
    intent: Intent,
    txn: Transaction,
    phase: PolicyPhase,
    clock: Clock,
    *,
    prior_quoted_total: int | None = None,
    is_authorized: bool = False,
) -> tuple[PolicyResult, CheckoutValidateOut]:
    """Quote the merchant, run the policy engine, and audit the decision.

    Guarantees an audit event per decision (satisfies "every decision is logged").
    """
    quote = InProcessMerchantClient(db).checkout_validate(intent.items)
    ctx = _build_context(
        intent,
        quote,
        phase,
        clock,
        prior_quoted_total=prior_quoted_total,
        is_authorized=is_authorized,
    )
    result = policy.evaluate(ctx)

    audit_result = (
        AuditResult.SUCCESS if result.decision == Decision.ALLOW else AuditResult.BLOCKED
    )
    audit.record(
        db,
        actor=Actor.POLICY_ENGINE,
        action=f"POLICY_EVALUATED_{phase.value}",
        result=audit_result,
        reason=result.reason,
        transaction_id=txn.id,
        intent_id=intent.id,
        transaction_identity=txn.transaction_identity,
        metadata={
            "phase": phase.value,
            "decision": result.decision.value,
            "failed_check": result.failed_check,
            "order_total": quote.total,
            "authorized_max_amount": intent.max_amount,
            "currency": intent.constraints["currency"],
            "policy_checks": [c.model_dump() for c in result.checks],
        },
        clock=clock,
    )
    return result, quote


# --------------------------------------------------------------------------- #
# public operations
# --------------------------------------------------------------------------- #
def create_intent(
    db: Session,
    payload: PurchaseIntentIn,
    raw_payload: dict,
    clock: Clock = default_clock,
) -> tuple[Intent, Transaction]:
    """Persist an intent and get-or-create its Transaction (idempotent on identity)."""
    canon = canonicalize(payload)
    currency = payload.constraints.currency

    # One Transaction per identity. Identical intents attach to the same txn.
    txn = db.scalar(
        select(Transaction).where(
            Transaction.transaction_identity == canon.transaction_identity
        )
    )
    if txn is None:
        txn = Transaction(
            id=new_transaction_id(),
            transaction_identity=canon.transaction_identity,
            merchant_id=payload.merchant_id.strip(),
            state=S.INTENT_CREATED.value,
            currency=currency,
            authorized_max_amount=payload.constraints.max_amount,
            max_quantity=payload.constraints.max_quantity,
        )
        db.add(txn)
        db.flush()

    normalised_items = list(canon.canonical["items"])
    intent = Intent(
        id=new_intent_id(),
        agent_id=payload.agent_id,
        merchant_id=payload.merchant_id.strip(),
        items=normalised_items,
        constraints={
            "max_amount": payload.constraints.max_amount,
            "currency": currency,
            "max_quantity": payload.constraints.max_quantity,
        },
        expires_at=payload.authorization.expires_at,
        raw_payload=raw_payload,
        canonical=canon.canonical,
        canonical_json=canon.canonical_json,
        transaction_identity=canon.transaction_identity,
        transaction_id=txn.id,
        status=IntentStatus.CREATED.value,
        max_amount=payload.constraints.max_amount,
    )
    db.add(intent)

    audit.record(
        db,
        actor=Actor.AI_BUYER,
        action="INTENT_CREATED",
        result=AuditResult.SUCCESS,
        reason="AI buyer submitted a purchase intent",
        transaction_id=txn.id,
        intent_id=intent.id,
        transaction_identity=txn.transaction_identity,
        metadata={
            "agent_id": payload.agent_id,
            "proposed_items": normalised_items,
            "authorized_max_amount": payload.constraints.max_amount,
            "authorized_max_quantity": payload.constraints.max_quantity,
            "currency": currency,
            "expires_at": payload.authorization.expires_at.isoformat(),
            "canonical": canon.canonical,
        },
        clock=clock,
    )
    db.commit()
    return intent, txn


def _load(db: Session, intent_id: str) -> tuple[Intent, Transaction]:
    intent = db.get(Intent, intent_id)
    if intent is None:
        raise IntentNotFound(intent_id)
    txn = db.get(Transaction, intent.transaction_id)
    if txn is None:  # pragma: no cover - defensive
        raise TransactionNotFound(intent.transaction_id or "<none>")
    return intent, txn


def validate_intent(
    db: Session, intent_id: str, clock: Clock = default_clock
) -> tuple[Intent, Transaction, PolicyResult]:
    """Merchant validation + policy engine (Phase 5 validate step)."""
    intent, txn = _load(db, intent_id)
    current = S(txn.state)

    result, quote = _policy_step(db, intent, txn, PolicyPhase.VALIDATE, clock)

    # Capture the merchant's quote so later steps can detect PRICE_CHANGED.
    if txn.quoted_total is None:
        txn.quoted_total = quote.total

    if result.decision == Decision.ALLOW:
        if current == S.INTENT_CREATED:
            _transition(
                db,
                txn,
                S.VALIDATED,
                actor=Actor.TRUSTRAIL,
                action="INTENT_VALIDATED",
                reason=result.reason,
                clock=clock,
                intent_id=intent.id,
            )
        intent.status = IntentStatus.VALIDATED.value
    else:
        target = _resolve_failure_state(current, CHECK_TO_STATE[result.failed_check])
        if target is not None and current != target:
            _transition(
                db,
                txn,
                target,
                actor=Actor.POLICY_ENGINE,
                action="INTENT_VALIDATION_BLOCKED",
                reason=result.reason,
                clock=clock,
                intent_id=intent.id,
                result=AuditResult.BLOCKED,
            )
        intent.status = _intent_status_for(target or current)

    db.commit()
    return intent, txn, result


def authorize_intent(
    db: Session, intent_id: str, clock: Clock = default_clock
) -> tuple[Intent, Transaction, PolicyResult]:
    """Grant authorization (Phase 5 authorize step). Re-affirms policy first."""
    intent, txn = _load(db, intent_id)
    current = S(txn.state)

    if current == S.INTENT_CREATED:
        raise InvalidLifecycleState(
            "intent must be validated before it can be authorized"
        )

    result, _ = _policy_step(
        db,
        intent,
        txn,
        PolicyPhase.AUTHORIZE,
        clock,
        prior_quoted_total=txn.quoted_total,
    )

    if result.decision == Decision.ALLOW:
        if current == S.VALIDATED:
            txn.authorized_expires_at = intent.expires_at
            txn.authorizing_intent_id = intent.id
            _transition(
                db,
                txn,
                S.AUTHORIZED,
                actor=Actor.TRUSTRAIL,
                action="AUTHORIZED",
                reason="authorization granted; transaction is executable",
                clock=clock,
                intent_id=intent.id,
            )
        intent.status = IntentStatus.AUTHORIZED.value
    else:
        target = _resolve_failure_state(current, CHECK_TO_STATE[result.failed_check])
        if target is not None and current != target:
            _transition(
                db,
                txn,
                target,
                actor=Actor.POLICY_ENGINE,
                action="AUTHORIZATION_BLOCKED",
                reason=result.reason,
                clock=clock,
                intent_id=intent.id,
                result=AuditResult.BLOCKED,
            )
        intent.status = _intent_status_for(target or current)

    db.commit()
    return intent, txn, result


def _resolve_txn(
    db: Session, intent_id: str | None, transaction_identity: str | None
) -> tuple[Transaction, Intent | None]:
    if intent_id:
        intent, txn = _load(db, intent_id)
        return txn, intent
    txn = db.scalar(
        select(Transaction).where(
            Transaction.transaction_identity == transaction_identity
        )
    )
    if txn is None:
        raise TransactionNotFound(transaction_identity or "<none>")
    return txn, None


def _authorizing_intent(db: Session, txn: Transaction, hint: Intent | None) -> Intent:
    if hint is not None:
        return hint
    if txn.authorizing_intent_id:
        intent = db.get(Intent, txn.authorizing_intent_id)
        if intent is not None:
            return intent
    intent = db.scalar(
        select(Intent)
        .where(Intent.transaction_id == txn.id)
        .order_by(Intent.created_at.desc())
    )
    if intent is None:  # pragma: no cover - defensive
        raise IntentNotFound("<none for transaction>")
    return intent


def _payment_actor(provider: str) -> Actor:
    """Attribute payment audit events to the concrete gateway."""
    return Actor.RAZORPAY if provider == "razorpay" else Actor.PAYMENT_GATEWAY


def _fulfil_after_payment(
    db: Session,
    txn: Transaction,
    intent: Intent,
    clock: Clock,
    *,
    checks: Sequence = (),
) -> tuple[Transaction, PolicyResult]:
    """Drive the post-payment fulfilment tail: order → COMPLETED, or refund owed.

    Precondition: ``txn`` is in :data:`PAYMENT_CONFIRMED`. This is the single
    definition of "what happens once money is confirmed captured", shared by the
    synchronous execute path, the asynchronous webhook, and reconciliation — so
    all three converge to exactly the same states and audit events. The merchant
    ``create_order`` is idempotent on the transaction identity, so a re-entry
    that already produced an order does not create a second one.
    """
    client = InProcessMerchantClient(db)
    try:
        order = client.create_order(
            intent.items, idempotency_key=txn.transaction_identity
        )
    except (MerchantOrderFailed, InsufficientInventory, ProductNotFound) as exc:
        _transition(
            db,
            txn,
            S.ORDER_FAILED,
            actor=Actor.MERCHANT,
            action="ORDER_FAILED",
            reason=str(exc),
            clock=clock,
            intent_id=intent.id,
            result=AuditResult.FAILURE,
        )
        db.commit()
        # Paid but not fulfilled -> we owe a refund.
        _transition(
            db,
            txn,
            S.REFUND_REQUIRED,
            actor=Actor.TRUSTRAIL,
            action="REFUND_REQUIRED",
            reason="payment captured but merchant order failed; refund owed",
            clock=clock,
            intent_id=intent.id,
            result=AuditResult.FAILURE,
            metadata={"payment_ref": txn.payment_ref},
        )
        db.commit()
        return txn, PolicyResult(
            decision=Decision.ALLOW,
            reason=f"order failed after payment: {exc}",
            checks=checks,
        )

    txn.merchant_order_id = order.id
    _transition(
        db,
        txn,
        S.ORDER_CONFIRMED,
        actor=Actor.MERCHANT,
        action="ORDER_CONFIRMED",
        reason="merchant confirmed the order",
        clock=clock,
        intent_id=intent.id,
        metadata={"merchant_order_id": order.id},
    )
    db.commit()

    _transition(
        db,
        txn,
        S.COMPLETED,
        actor=Actor.TRUSTRAIL,
        action="TRANSACTION_COMPLETED",
        reason="payment captured and order confirmed",
        clock=clock,
        intent_id=intent.id,
    )
    intent.status = IntentStatus.CONSUMED.value
    db.commit()
    return txn, PolicyResult(
        decision=Decision.ALLOW,
        reason="transaction completed",
        checks=checks,
    )


# States at or beyond a confirmed capture: a *duplicate* confirmation for one of
# these is an idempotent no-op (money already accounted for; do not re-charge or
# re-fulfil).
_ALREADY_CONFIRMED = {
    S.PAYMENT_CONFIRMED,
    S.ORDER_CONFIRMED,
    S.COMPLETED,
    S.ORDER_FAILED,
    S.REFUND_REQUIRED,
}
# States from which an authoritative signal may *conclude* the payment. These are
# exactly the non-terminal payment/recovery states.
_RESOLVABLE = {S.PAYMENT_PENDING, S.PAYMENT_UNKNOWN, S.RECOVERY_PENDING}


def confirm_payment_and_fulfil(
    db: Session,
    txn: Transaction,
    *,
    payment_ref: str,
    amount: int,
    clock: Clock,
    provider: str = "razorpay",
    intent: Intent | None = None,
) -> tuple[Transaction, PolicyResult]:
    """Authoritatively confirm a captured payment, then fulfil. Idempotent.

    This is the single entrypoint used by BOTH the asynchronous webhook and the
    reconciliation sweep to converge a transaction to CONFIRMED (and onward to
    COMPLETED / REFUND_REQUIRED). It never initiates a charge — it only records a
    capture that an authoritative source (a signed webhook, or a direct Razorpay
    query) has already established.

    Idempotency & safety:
    * Already at/after PAYMENT_CONFIRMED  → no-op (no second charge, no second order).
    * A resolvable state (PENDING/UNKNOWN/RECOVERY) → confirm + fulfil.
    * A terminal state that is not confirmable (e.g. PAYMENT_FAILED after we had
      concluded failure, yet a capture now appears) → recorded as an anomaly for
      review; we do NOT silently rewrite terminal state.
    """
    current = S(txn.state)
    if current in _ALREADY_CONFIRMED:
        audit.record(
            db,
            actor=_payment_actor(provider),
            action="PAYMENT_CONFIRM_DUPLICATE_IGNORED",
            result=AuditResult.INFO,
            reason=f"duplicate confirmation ignored; already in state {current.value}",
            transaction_id=txn.id,
            transaction_identity=txn.transaction_identity,
            metadata={"provider": provider, "payment_ref": payment_ref},
            clock=clock,
        )
        db.commit()
        return txn, PolicyResult(
            decision=Decision.ALLOW,
            reason=f"payment already confirmed; state is {current.value}",
        )

    if current not in _RESOLVABLE or not can_transition(current, S.PAYMENT_CONFIRMED):
        # e.g. terminal PAYMENT_FAILED but money was in fact captured. Surface it
        # loudly rather than forcing an illegal transition.
        audit.record(
            db,
            actor=_payment_actor(provider),
            action="PAYMENT_CONFIRM_ANOMALY",
            result=AuditResult.FAILURE,
            reason=(
                f"capture reported while transaction is {current.value}; "
                "cannot auto-confirm — manual review required"
            ),
            transaction_id=txn.id,
            transaction_identity=txn.transaction_identity,
            metadata={"provider": provider, "payment_ref": payment_ref, "amount": amount},
            clock=clock,
        )
        db.commit()
        return txn, PolicyResult(
            decision=Decision.BLOCK,
            reason=f"cannot confirm payment from terminal/illegal state {current.value}",
        )

    txn.payment_ref = payment_ref
    txn.amount_captured = int(amount)
    txn.payment_status = "captured"
    txn.payment_provider = provider
    if provider == "razorpay" and payment_ref:
        txn.razorpay_payment_id = payment_ref
    _transition(
        db,
        txn,
        S.PAYMENT_CONFIRMED,
        actor=_payment_actor(provider),
        action="PAYMENT_CONFIRMED",
        reason=f"payment captured ({provider} gateway)",
        clock=clock,
        metadata={
            "payment_ref": payment_ref,
            "amount": int(amount),
            "provider": provider,
        },
    )
    db.commit()

    if intent is None:
        intent = _authorizing_intent(db, txn, None)
    return _fulfil_after_payment(db, txn, intent, clock)


def fail_payment(
    db: Session,
    txn: Transaction,
    *,
    clock: Clock,
    provider: str = "razorpay",
    reason: str = "payment failed",
) -> tuple[Transaction, PolicyResult]:
    """Authoritatively conclude a payment as FAILED from a resolvable state.

    Used by reconciliation (the authority), NOT by the webhook: a single
    ``payment.failed`` webhook is only a failed *attempt* and must not terminalise
    the transaction, because Razorpay may still deliver a later capture for the
    same order (e.g. a UPI retry). Only an authoritative order query concludes
    failure. Idempotent: a no-op if already terminal-failed.
    """
    current = S(txn.state)
    if current == S.PAYMENT_FAILED:
        return txn, PolicyResult(decision=Decision.BLOCK, reason="payment already failed")
    if current not in _RESOLVABLE or not can_transition(current, S.PAYMENT_FAILED):
        audit.record(
            db,
            actor=_payment_actor(provider),
            action="PAYMENT_FAIL_SKIPPED",
            result=AuditResult.INFO,
            reason=f"cannot conclude FAILED from state {current.value}",
            transaction_id=txn.id,
            transaction_identity=txn.transaction_identity,
            metadata={"provider": provider},
            clock=clock,
        )
        db.commit()
        return txn, PolicyResult(
            decision=Decision.BLOCK,
            reason=f"cannot fail payment from state {current.value}",
        )

    txn.payment_status = "failed"
    txn.payment_provider = provider
    _transition(
        db,
        txn,
        S.PAYMENT_FAILED,
        actor=_payment_actor(provider),
        action="PAYMENT_FAILED",
        reason=reason,
        clock=clock,
        result=AuditResult.FAILURE,
        metadata={"provider": provider},
    )
    db.commit()
    return txn, PolicyResult(decision=Decision.ALLOW, reason=reason)


def begin_recovery(
    db: Session,
    txn: Transaction,
    *,
    clock: Clock,
    provider: str = "razorpay",
    reason: str = "entering active recovery",
) -> Transaction:
    """Move a transaction into ``RECOVERY_PENDING`` (idempotent).

    Reconciliation calls this — rather than transitioning directly — so that every
    state change still flows through this conductor and the state machine. It is a
    no-op if the transaction is already recovering, and it never forces an illegal
    transition (only ``PAYMENT_UNKNOWN``/``ORDER_FAILED`` may legally begin
    recovery; from anywhere else this records a skip and leaves state untouched).
    """
    current = S(txn.state)
    if current == S.RECOVERY_PENDING:
        return txn
    if not can_transition(current, S.RECOVERY_PENDING):
        audit.record(
            db,
            actor=_payment_actor(provider),
            action="RECOVERY_START_SKIPPED",
            result=AuditResult.INFO,
            reason=f"cannot begin recovery from state {current.value}",
            transaction_id=txn.id,
            transaction_identity=txn.transaction_identity,
            metadata={"provider": provider},
            clock=clock,
        )
        db.commit()
        return txn
    _transition(
        db,
        txn,
        S.RECOVERY_PENDING,
        actor=_payment_actor(provider),
        action="RECOVERY_STARTED",
        reason=reason,
        clock=clock,
        result=AuditResult.INFO,
        metadata={"provider": provider},
    )
    db.commit()
    return txn


def complete_refund(
    db: Session,
    txn: Transaction,
    *,
    refund_id: str,
    clock: Clock,
    provider: str = "razorpay",
    reason: str = "refund issued for a payment we could not fulfil; transaction resolved",
) -> Transaction:
    """Record an issued refund and resolve the transaction (``REFUND_REQUIRED`` →
    ``COMPLETED``). Idempotent.

    The refund service calls this immediately after Razorpay accepts the refund,
    so the refund id and the terminal state are persisted together. If the
    transaction is already ``COMPLETED`` this is a no-op (it only backfills the
    refund id if missing), so a re-run never issues or records a second refund.
    """
    current = S(txn.state)
    if current == S.COMPLETED:
        if refund_id and not txn.razorpay_refund_id:
            txn.razorpay_refund_id = refund_id
            db.commit()
        return txn
    if current != S.REFUND_REQUIRED or not can_transition(current, S.COMPLETED):
        audit.record(
            db,
            actor=_payment_actor(provider),
            action="REFUND_COMPLETE_SKIPPED",
            result=AuditResult.INFO,
            reason=f"cannot complete refund from state {current.value}",
            transaction_id=txn.id,
            transaction_identity=txn.transaction_identity,
            metadata={"provider": provider, "refund_id": refund_id},
            clock=clock,
        )
        db.commit()
        return txn
    if refund_id:
        txn.razorpay_refund_id = refund_id
    txn.payment_status = "refunded"
    _transition(
        db,
        txn,
        S.COMPLETED,
        actor=_payment_actor(provider),
        action="REFUND_COMPLETED",
        reason=reason,
        clock=clock,
        result=AuditResult.SUCCESS,
        metadata={"provider": provider, "refund_id": refund_id},
    )
    db.commit()
    return txn


def execute_transaction(
    db: Session,
    *,
    intent_id: str | None = None,
    transaction_identity: str | None = None,
    clock: Clock = default_clock,
    gateway: PaymentGateway = default_gateway,
) -> tuple[Transaction, PolicyResult]:
    """Execute an authorized purchase: pay (mock) then order. Idempotent on identity."""
    txn, hint = _resolve_txn(db, intent_id, transaction_identity)
    current = S(txn.state)

    # Idempotency: never re-run money movement for a txn already in flight/done.
    if current in _EXECUTION_IN_FLIGHT_OR_DONE:
        decision = (
            Decision.ALLOW
            if current
            in {S.PAYMENT_PENDING, S.PAYMENT_CONFIRMED, S.ORDER_CONFIRMED, S.COMPLETED}
            else Decision.BLOCK
        )
        result = PolicyResult(
            decision=decision,
            reason=f"idempotent replay: transaction already in state {current.value}",
        )
        audit.record(
            db,
            actor=Actor.TRUSTRAIL,
            action="EXECUTE_IDEMPOTENT_REPLAY",
            result=AuditResult.INFO,
            reason=result.reason,
            transaction_id=txn.id,
            transaction_identity=txn.transaction_identity,
            metadata={"state": current.value},
            clock=clock,
        )
        db.commit()
        return txn, result

    if current != S.AUTHORIZED:
        # e.g. INTENT_CREATED or VALIDATED — not executable yet.
        result = PolicyResult(
            decision=Decision.REQUIRES_AUTHORIZATION,
            reason=f"transaction is {current.value}; it must be AUTHORIZED before execution",
        )
        audit.record(
            db,
            actor=Actor.TRUSTRAIL,
            action="EXECUTE_REJECTED",
            result=AuditResult.BLOCKED,
            reason=result.reason,
            transaction_id=txn.id,
            transaction_identity=txn.transaction_identity,
            metadata={"state": current.value},
            clock=clock,
        )
        db.commit()
        return txn, result

    intent = _authorizing_intent(db, txn, hint)

    # Final deterministic gate at execution time (catches expiry, price/inventory drift).
    result, quote = _policy_step(
        db,
        intent,
        txn,
        PolicyPhase.EXECUTE,
        clock,
        prior_quoted_total=txn.quoted_total,
        is_authorized=True,
    )
    if result.decision != Decision.ALLOW:
        target = (
            _resolve_failure_state(current, CHECK_TO_STATE[result.failed_check])
            if result.failed_check
            else S.POLICY_BLOCKED
        )
        if target is not None and current != target:
            _transition(
                db,
                txn,
                target,
                actor=Actor.POLICY_ENGINE,
                action="EXECUTION_BLOCKED",
                reason=result.reason,
                clock=clock,
                intent_id=intent.id,
                result=AuditResult.BLOCKED,
            )
        db.commit()
        return txn, result

    # ---- money movement (gateway: mock in Phase 1, Razorpay in Phase 2) --- #
    client = InProcessMerchantClient(db)
    products = [client.get_product(i["sku"]) for i in intent.items]
    force_decline = any(p is not None and p.force_payment_decline for p in products)

    _transition(
        db,
        txn,
        S.PAYMENT_PENDING,
        actor=Actor.TRUSTRAIL,
        action="PAYMENT_INITIATED",
        reason="initiating payment for authorized transaction",
        clock=clock,
        intent_id=intent.id,
        metadata={"amount": txn.quoted_total, "currency": txn.currency},
    )
    db.commit()

    payment = gateway.create_payment(
        db,
        idempotency_key=txn.transaction_identity,
        amount=int(txn.quoted_total or quote.total),
        currency=txn.currency,
        force_decline=force_decline,
    )

    # Persist provider linkage (never trusted as identity — it exists so an async
    # webhook or a reconciliation sweep can map an external event back to *this*
    # transaction and validate it before any state change).
    txn.payment_provider = payment.provider
    if payment.order_ref:
        txn.razorpay_order_id = payment.order_ref
    actor = _payment_actor(payment.provider)

    # --- one additive branch per payment status --------------------------- #
    if payment.status == PAYMENT_FAILED:
        txn.payment_status = "failed"
        _transition(
            db,
            txn,
            S.PAYMENT_FAILED,
            actor=actor,
            action="PAYMENT_FAILED",
            reason=f"payment gateway declined the charge ({payment.provider})",
            clock=clock,
            intent_id=intent.id,
            result=AuditResult.FAILURE,
            metadata={"payment_ref": payment.payment_ref, "provider": payment.provider},
        )
        db.commit()
        return txn, PolicyResult(
            decision=Decision.ALLOW,  # policy allowed; execution failed operationally
            reason="policy allowed the transaction but the payment was declined",
            checks=result.checks,
        )

    if payment.status == PAYMENT_UNKNOWN:
        # UNKNOWN ≠ FAILED. We do not know whether money moved, so we must NOT
        # charge again. Park here; authoritative reconciliation resolves it.
        txn.payment_status = "unknown"
        _transition(
            db,
            txn,
            S.PAYMENT_UNKNOWN,
            actor=actor,
            action="PAYMENT_UNKNOWN",
            reason=(
                "payment outcome unknown (no definitive gateway response); "
                "awaiting reconciliation — NOT re-charging"
            ),
            clock=clock,
            intent_id=intent.id,
            result=AuditResult.INFO,
            metadata={"provider": payment.provider, "order_ref": payment.order_ref},
        )
        db.commit()
        return txn, PolicyResult(
            decision=Decision.ALLOW,
            reason=(
                "payment outcome is UNKNOWN; reconciliation will determine the "
                "authoritative result without re-charging"
            ),
            checks=result.checks,
        )

    if payment.status == PAYMENT_PENDING:
        # A real gateway opened an order; money has NOT moved. Confirmation
        # arrives asynchronously (webhook / reconciliation). Stay PAYMENT_PENDING.
        txn.payment_status = "created"
        audit.record(
            db,
            actor=actor,
            action="PAYMENT_PENDING",
            result=AuditResult.INFO,
            reason="payment order created; awaiting asynchronous confirmation",
            transaction_id=txn.id,
            intent_id=intent.id,
            transaction_identity=txn.transaction_identity,
            metadata={"provider": payment.provider, "order_ref": payment.order_ref},
            clock=clock,
        )
        db.commit()
        return txn, PolicyResult(
            decision=Decision.ALLOW,
            reason=(
                "payment order created; awaiting asynchronous confirmation "
                "(webhook / reconciliation)"
            ),
            checks=result.checks,
        )

    # --- PAYMENT_CONFIRMED: synchronous capture (mock), or a replayed confirm #
    txn.payment_ref = payment.payment_ref
    txn.amount_captured = payment.amount
    txn.payment_status = "captured"
    if payment.provider == "razorpay" and payment.payment_ref:
        txn.razorpay_payment_id = payment.payment_ref
    _transition(
        db,
        txn,
        S.PAYMENT_CONFIRMED,
        actor=actor,
        action="PAYMENT_CONFIRMED",
        reason=f"payment captured ({payment.provider} gateway)",
        clock=clock,
        intent_id=intent.id,
        metadata={
            "payment_ref": payment.payment_ref,
            "amount": payment.amount,
            "idempotent_replay": payment.idempotent_replay,
            "provider": payment.provider,
        },
    )
    db.commit()

    # ---- fulfilment via merchant (shared tail) --------------------------- #
    return _fulfil_after_payment(db, txn, intent, clock, checks=result.checks)


# --------------------------------------------------------------------------- #
# read helpers
# --------------------------------------------------------------------------- #
def get_transaction(db: Session, transaction_id: str) -> Transaction:
    txn = db.get(Transaction, transaction_id)
    if txn is None:
        raise TransactionNotFound(transaction_id)
    return txn


def list_audit(db: Session, transaction_id: str) -> Sequence:
    # Ensure the transaction exists (404 otherwise).
    get_transaction(db, transaction_id)
    return audit.list_for_transaction(db, transaction_id)
