"""Audit trail response schemas (Phase 6)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class AuditEventOut(BaseModel):
    event_id: str
    seq: int
    transaction_id: str | None
    intent_id: str | None
    transaction_identity: str | None
    timestamp: datetime
    actor: str
    action: str
    result: str
    reason: str
    metadata: dict


class AuditTrailOut(BaseModel):
    transaction_id: str
    events: list[AuditEventOut]
