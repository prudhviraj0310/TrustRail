"""Transaction endpoints: execute, read, audit (Phases 5 & 6)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import decision_envelope, transaction_out
from app.clock import Clock, get_clock
from app.db import get_db
from app.schemas.audit import AuditEventOut, AuditTrailOut
from app.schemas.transaction import (
    DecisionEnvelopeOut,
    TransactionCreateIn,
    TransactionOut,
)
from app.services import transaction as txn_service
from app.services.gateway import get_gateway
from app.services.payment import PaymentGateway

router = APIRouter(prefix="/transactions", tags=["trustrail: transactions"])


@router.post("", response_model=DecisionEnvelopeOut)
def execute_transaction(
    payload: TransactionCreateIn,
    db: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
    gateway: PaymentGateway = Depends(get_gateway),
) -> DecisionEnvelopeOut:
    txn, result = txn_service.execute_transaction(
        db,
        intent_id=payload.intent_id,
        transaction_identity=payload.transaction_identity,
        clock=clock,
        gateway=gateway,
    )
    return decision_envelope(payload.intent_id, txn, result)


@router.get("/{transaction_id}", response_model=TransactionOut)
def get_transaction(transaction_id: str, db: Session = Depends(get_db)) -> TransactionOut:
    txn = txn_service.get_transaction(db, transaction_id)
    return transaction_out(txn)


@router.get("/{transaction_id}/audit", response_model=AuditTrailOut)
def get_audit(transaction_id: str, db: Session = Depends(get_db)) -> AuditTrailOut:
    events = txn_service.list_audit(db, transaction_id)
    return AuditTrailOut(
        transaction_id=transaction_id,
        events=[
            AuditEventOut(
                event_id=e.id,
                seq=e.seq,
                transaction_id=e.transaction_id,
                intent_id=e.intent_id,
                transaction_identity=e.transaction_identity,
                timestamp=e.timestamp,
                actor=e.actor,
                action=e.action,
                result=e.result,
                reason=e.reason,
                metadata=e.meta,
            )
            for e in events
        ],
    )
