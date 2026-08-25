"""The Intent record — an immutable snapshot of a single submitted PurchaseIntent.

Every ``POST /intents`` writes one row. Two semantically identical submissions
produce two Intent rows that share the same ``transaction_identity`` (and thus
point at the same Transaction). The raw payload is retained verbatim so the
audit trail can always answer "what did the AI actually propose?".
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.enums import IntentStatus


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Intent(Base):
    __tablename__ = "intents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    agent_id: Mapped[str] = mapped_column(String(128), nullable=False)
    merchant_id: Mapped[str] = mapped_column(String(64), nullable=False)

    # Normalised, financially-relevant fields (post-canonicalisation input).
    items: Mapped[list] = mapped_column(JSON, nullable=False)
    constraints: Mapped[dict] = mapped_column(JSON, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Untrusted input, retained verbatim for the audit trail.
    raw_payload: Mapped[dict] = mapped_column(JSON, nullable=False)

    # Canonical form + the exact serialized string that was hashed.
    canonical: Mapped[dict] = mapped_column(JSON, nullable=False)
    canonical_json: Mapped[str] = mapped_column(Text, nullable=False)
    transaction_identity: Mapped[str] = mapped_column(
        String(96), index=True, nullable=False
    )

    transaction_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("transactions.id"), nullable=True, index=True
    )

    status: Mapped[str] = mapped_column(
        String(32), default=IntentStatus.CREATED.value, nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    # Convenience mirror of the authorized ceiling (integer minor units).
    max_amount: Mapped[int] = mapped_column(BigInteger, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Intent {self.id} status={self.status} tid={self.transaction_identity[:14]}…>"
