"""Append-only audit events.

The audit trail is the product's core deliverable: it must let a judge answer
what the user authorized, what the AI proposed, what TrustRail allowed/blocked,
why, and what happened to the payment and the order. Rows are never updated or
deleted. ``seq`` gives a stable, gap-free ordering independent of clock skew.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    # Monotonic ordering key (independent of wall-clock).
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)

    transaction_id: Mapped[str | None] = mapped_column(
        String(64), index=True, nullable=True
    )
    intent_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    transaction_identity: Mapped[str | None] = mapped_column(String(96), nullable=True)

    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    actor: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")

    # ``metadata`` is reserved on Declarative classes, so the attribute is
    # ``meta`` while the column keeps the meaningful name "metadata".
    meta: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<AuditEvent seq={self.seq} {self.actor}:{self.action}={self.result}>"
