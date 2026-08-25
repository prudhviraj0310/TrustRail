"""Chat message schemas for conversational AI buyer interface."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ChatMessageIn(BaseModel):
    """Incoming chat message from the user."""

    message: str = Field(..., min_length=1, max_length=2000)
    session_id: str = Field(default="default", max_length=100)
    budget: int = Field(
        default=500000,
        description="User's authorized budget ceiling in paise (default ₹5,000)",
    )
    currency: str = Field(default="INR")


class TransactionDetail(BaseModel):
    """Details of a completed or attempted transaction."""

    transaction_id: str | None = None
    state: str | None = None
    amount: int | None = None
    currency: str = "INR"
    items: list[dict[str, Any]] = []
    policy_checks: list[dict[str, Any]] = []


class ChatMessageOut(BaseModel):
    """Outgoing chat message from the AI agent."""

    role: str = "assistant"
    message: str
    session_id: str = "default"
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    # Structured action data (populated when AI takes actions)
    action: str | None = None  # "recommend", "purchase", "blocked", "info"
    products_shown: list[dict[str, Any]] = []
    recommendation: dict[str, Any] | None = None
    transaction: TransactionDetail | None = None

    # Growth metrics surfaced in conversation
    growth_insight: str | None = None


class ChatHistoryOut(BaseModel):
    """Full chat history for a session."""

    session_id: str
    messages: list[ChatMessageOut]
