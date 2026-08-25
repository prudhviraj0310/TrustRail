"""Intent endpoints: create, validate, authorize (Phase 5)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.deps import decision_envelope
from app.clock import Clock, get_clock
from app.db import get_db
from app.errors import IntentNotFound
from app.schemas.intent import IntentCreatedOut, IntentSummaryOut, PurchaseIntentIn
from app.schemas.transaction import DecisionEnvelopeOut
from app.services import transaction as txn_service

router = APIRouter(prefix="/intents", tags=["trustrail: intents"])


@router.post("", response_model=IntentCreatedOut, status_code=status.HTTP_201_CREATED)
async def create_intent(
    payload: PurchaseIntentIn,
    request: Request,
    db: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
) -> IntentCreatedOut:
    # Keep the *verbatim* body so the audit trail records exactly what the AI sent.
    try:
        raw_payload = await request.json()
    except Exception:  # pragma: no cover - body already validated by FastAPI
        raw_payload = payload.model_dump(mode="json")

    intent, txn = txn_service.create_intent(db, payload, raw_payload, clock=clock)
    return IntentCreatedOut(
        intent_id=intent.id,
        transaction_id=txn.id,
        transaction_identity=txn.transaction_identity,
        state=txn.state,
        status=intent.status,
        canonical=intent.canonical,
        canonical_json=intent.canonical_json,
    )


@router.post("/{intent_id}/validate", response_model=DecisionEnvelopeOut)
def validate_intent(
    intent_id: str,
    db: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
) -> DecisionEnvelopeOut:
    intent, txn, result = txn_service.validate_intent(db, intent_id, clock=clock)
    return decision_envelope(intent.id, txn, result)


@router.post("/{intent_id}/authorize", response_model=DecisionEnvelopeOut)
def authorize_intent(
    intent_id: str,
    db: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
) -> DecisionEnvelopeOut:
    intent, txn, result = txn_service.authorize_intent(db, intent_id, clock=clock)
    return decision_envelope(intent.id, txn, result)


@router.get("/{intent_id}", response_model=IntentSummaryOut)
def get_intent(intent_id: str, db: Session = Depends(get_db)) -> IntentSummaryOut:
    from app.models.intent import Intent

    intent = db.get(Intent, intent_id)
    if intent is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(IntentNotFound(intent_id)))
    return IntentSummaryOut(
        intent_id=intent.id,
        agent_id=intent.agent_id,
        merchant_id=intent.merchant_id,
        status=intent.status,
        transaction_id=intent.transaction_id,
        transaction_identity=intent.transaction_identity,
        max_amount=intent.max_amount,
        currency=intent.constraints["currency"],
        expires_at=intent.expires_at,
        created_at=intent.created_at,
    )
