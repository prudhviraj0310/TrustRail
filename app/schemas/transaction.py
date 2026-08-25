"""Transaction request/response schemas (Phase 5)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, model_validator

from app.schemas.policy import PolicyDecisionOut


class TransactionCreateIn(BaseModel):
    """Trigger execution of an authorized purchase.

    Reference the purchase either by the intent that authorized it or directly
    by its deterministic transaction identity.
    """

    intent_id: str | None = None
    transaction_identity: str | None = None

    @model_validator(mode="after")
    def _one_reference(self) -> TransactionCreateIn:
        if not self.intent_id and not self.transaction_identity:
            raise ValueError("provide either intent_id or transaction_identity")
        return self


class TransactionOut(BaseModel):
    transaction_id: str
    transaction_identity: str
    state: str
    merchant_id: str
    currency: str
    authorized_max_amount: int | None
    quoted_total: int | None
    max_quantity: int | None
    authorized_expires_at: datetime | None
    payment_ref: str | None
    merchant_order_id: str | None
    amount_captured: int | None
    # Phase 2 — Razorpay linkage (null under the default mock gateway).
    payment_provider: str | None = None
    payment_status: str | None = None
    razorpay_order_id: str | None = None
    razorpay_payment_id: str | None = None
    razorpay_refund_id: str | None = None
    created_at: datetime
    updated_at: datetime


class DecisionEnvelopeOut(BaseModel):
    """The combined "transaction state + ALLOW/BLOCK + why" response.

    Returned by validate / authorize / execute so a caller sees, in one object,
    both the deterministic decision and the resulting transaction state.
    """

    intent_id: str | None
    transaction_id: str
    transaction_identity: str
    state: str
    decision: PolicyDecisionOut
