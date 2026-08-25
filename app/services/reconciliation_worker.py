"""Background reconciliation and recovery worker.

Periodically sweeps:
1. PAYMENT_PENDING / PAYMENT_UNKNOWN transactions to authoritatively resolve status via Razorpay
2. RECOVERY_PENDING / REFUND_REQUIRED transactions to execute at-most-once refunds

Runs as an autonomous background task in the FastAPI lifespan.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.services.gateway import get_gateway
from app.services.reconciliation import reconcile_pending
from app.services.refund import refund_pending

logger = logging.getLogger(__name__)

_worker_task: asyncio.Task | None = None
_last_sweep_stats: dict[str, Any] = {
    "runs": 0,
    "last_run_timestamp": None,
    "reconciled_count": 0,
    "refunded_count": 0,
    "last_status": "idle",
}


def run_sweep(db: Session, gateway=None) -> dict[str, Any]:
    """Execute a single synchronous sweep of pending reconciliation and refunds."""
    if gateway is None:
        gateway = get_gateway()

    reconcile_outcomes = reconcile_pending(db, gateway=gateway)
    refund_outcomes = refund_pending(db, gateway=gateway)

    stats = {
        "reconciled_total": len(reconcile_outcomes),
        "reconciled_details": [
            {"identity": o.identity, "outcome": o.outcome, "reason": o.reason}
            for o in reconcile_outcomes
        ],
        "refunded_total": len(refund_outcomes),
        "refunded_details": [
            {"identity": o.identity, "outcome": o.outcome, "reason": o.reason}
            for o in refund_outcomes
        ],
    }
    return stats


async def _worker_loop(db_factory: Callable[[], Session], interval_seconds: int = 30) -> None:
    """Continuous async loop executing sweeps periodically."""
    logger.info(f"TrustRail Reconciliation Worker started (interval={interval_seconds}s)")
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            db = db_factory()
            try:
                stats = run_sweep(db)
                _last_sweep_stats["runs"] += 1
                from datetime import datetime, timezone
                _last_sweep_stats["last_run_timestamp"] = datetime.now(timezone.utc).isoformat()
                _last_sweep_stats["reconciled_count"] += stats["reconciled_total"]
                _last_sweep_stats["refunded_count"] += stats["refunded_total"]
                _last_sweep_stats["last_status"] = "healthy"
                if stats["reconciled_total"] > 0 or stats["refunded_total"] > 0:
                    logger.info(
                        f"Reconciliation sweep completed: {stats['reconciled_total']} reconciled, "
                        f"{stats['refunded_total']} refunded"
                    )
            finally:
                db.close()
        except asyncio.CancelledError:
            logger.info("Reconciliation worker received cancellation request, stopping.")
            break
        except Exception as exc:
            logger.error(f"Error in background reconciliation worker: {exc}", exc_info=True)
            _last_sweep_stats["last_status"] = f"error: {str(exc)}"


def start_worker(db_factory: Callable[[], Session], interval_seconds: int = 30) -> asyncio.Task | None:
    """Start the background worker task if not already running."""
    global _worker_task
    if _worker_task is not None and not _worker_task.done():
        return _worker_task

    _worker_task = asyncio.create_task(_worker_loop(db_factory, interval_seconds))
    return _worker_task


async def stop_worker() -> None:
    """Gracefully stop the background worker task."""
    global _worker_task
    if _worker_task is not None and not _worker_task.done():
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            pass
        _worker_task = None


def get_worker_status() -> dict[str, Any]:
    """Return status telemetry about the background worker."""
    is_running = _worker_task is not None and not _worker_task.done()
    return {
        "is_running": is_running,
        **_last_sweep_stats,
    }
