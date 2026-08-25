"""Reconciliation API endpoints for status monitoring and on-demand sweeps."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.reconciliation_worker import get_worker_status, run_sweep

router = APIRouter(prefix="/reconciliation", tags=["reconciliation"])


@router.get("/status")
def get_status() -> dict:
    """Get background reconciliation worker telemetry and stats."""
    return get_worker_status()


@router.post("/sweep")
def trigger_sweep(db: Session = Depends(get_db)) -> dict:
    """Trigger an immediate on-demand reconciliation and refund sweep."""
    stats = run_sweep(db)
    return {
        "status": "completed",
        **stats,
    }
