"""PurchaseIntent — the canonical transaction contract (Phase 1)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ItemIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sku: str = Field(min_length=1, max_length=64)
    quantity: int = Field(ge=1, le=1_000_000)


class ConstraintsIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # Integer minor units (paise). ₹5,000.00 -> 500000.
    max_amount: int = Field(ge=0)
    currency: str = Field(default="INR", min_length=3, max_length=8)
    max_quantity: int = Field(default=1, ge=1, le=1_000_000)

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, v: str) -> str:
        return v.strip().upper()


class AuthorizationIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    expires_at: datetime


class PurchaseIntentIn(BaseModel):
    """What the *user* authorized — not free-form LLM text.

    Unknown fields are ignored on purpose: the AI may attach reasoning or chatter,
    but only these fields are financially meaningful.
    """

    model_config = ConfigDict(extra="ignore")

    agent_id: str = Field(min_length=1, max_length=128)
    merchant_id: str = Field(min_length=1, max_length=64)
    items: list[ItemIn] = Field(min_length=1)
    constraints: ConstraintsIn
    authorization: AuthorizationIn


class IntentCreatedOut(BaseModel):
    intent_id: str
    transaction_id: str
    transaction_identity: str
    state: str
    status: str
    canonical: dict
    canonical_json: str


class IntentSummaryOut(BaseModel):
    intent_id: str
    agent_id: str
    merchant_id: str
    status: str
    transaction_id: str | None
    transaction_identity: str
    max_amount: int
    currency: str
    expires_at: datetime
    created_at: datetime
