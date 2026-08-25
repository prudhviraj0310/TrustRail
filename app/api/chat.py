"""Chat API — Conversational AI Buyer endpoint.

Provides the POST /chat endpoint that powers the conversational checkout
experience. The AI agent receives natural language, reasons about products,
and executes purchases through TrustRail's integrity pipeline.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.chat import ChatMessageIn, ChatMessageOut
from app.services.ai_agent import process_chat_message

router = APIRouter(tags=["AI Conversational Commerce"])


@router.post(
    "/chat",
    response_model=ChatMessageOut,
    summary="Conversational AI buyer — send a message, get intelligent commerce responses",
)
def chat(
    payload: ChatMessageIn,
    db: Session = Depends(get_db),
) -> ChatMessageOut:
    """Process a natural language message through the AI buyer agent.

    The agent discovers products, recommends bundles, evaluates budget constraints
    through TrustRail's growth policy engine, and executes bounded purchases.
    """
    return process_chat_message(
        message=payload.message,
        session_id=payload.session_id,
        budget=payload.budget,
        currency=payload.currency,
        db=db,
    )
