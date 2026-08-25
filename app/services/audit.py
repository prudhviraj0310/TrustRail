"""Phase 6 — the append-only audit trail.

Every meaningful operation records exactly one immutable event. The trail is the
system's explainability guarantee: given a transaction, a judge can reconstruct
what the user authorized, what the AI proposed, what TrustRail allowed/blocked
and why, and what happened to the payment and the order.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import Clock, default_clock
from app.enums import Actor, AuditResult
from app.ids import new_event_id
from app.models.audit import AuditEvent


def record(
    db: Session,
    *,
    actor: Actor,
    action: str,
    result: AuditResult,
    reason: str = "",
    transaction_id: str | None = None,
    intent_id: str | None = None,
    transaction_identity: str | None = None,
    metadata: dict | None = None,
    clock: Clock = default_clock,
) -> AuditEvent:
    """Append one audit event. Flushes (to allocate ``seq``) but does not commit —
    the event is persisted atomically with the caller's state change."""
    event = AuditEvent(
        id=new_event_id(),
        transaction_id=transaction_id,
        intent_id=intent_id,
        transaction_identity=transaction_identity,
        timestamp=clock.now(),
        actor=actor.value,
        action=action,
        result=result.value,
        reason=reason,
        meta=metadata or {},
    )
    db.add(event)
    db.flush()
    return event


def list_for_transaction(db: Session, transaction_id: str) -> Sequence[AuditEvent]:
    return db.scalars(
        select(AuditEvent)
        .where(AuditEvent.transaction_id == transaction_id)
        .order_by(AuditEvent.seq)
    ).all()


def list_for_intent(db: Session, intent_id: str) -> Sequence[AuditEvent]:
    return db.scalars(
        select(AuditEvent)
        .where(AuditEvent.intent_id == intent_id)
        .order_by(AuditEvent.seq)
    ).all()
