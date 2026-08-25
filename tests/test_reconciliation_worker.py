"""Tests for the autonomous background reconciliation worker."""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app as fastapi_app
from app.services.reconciliation_worker import (
    get_worker_status,
    run_sweep,
    start_worker,
    stop_worker,
)


def test_reconciliation_sweep_execution(db_session: Session):
    """Test running an on-demand sweep synchronously."""
    stats = run_sweep(db_session)
    assert "reconciled_total" in stats
    assert "refunded_total" in stats
    assert isinstance(stats["reconciled_total"], int)
    assert isinstance(stats["refunded_total"], int)


def test_worker_status_telemetry():
    """Test the telemetry status reporting."""
    status = get_worker_status()
    assert "is_running" in status
    assert "runs" in status
    assert "reconciled_count" in status


def test_reconciliation_api_endpoints(client: TestClient):
    """Test the /reconciliation API endpoints."""
    # Status endpoint
    res = client.get("/reconciliation/status")
    assert res.status_code == 200
    data = res.json()
    assert "is_running" in data

    # Sweep trigger endpoint
    sweep_res = client.post("/reconciliation/sweep")
    assert sweep_res.status_code == 200
    sweep_data = sweep_res.json()
    assert sweep_data["status"] == "completed"
    assert "reconciled_total" in sweep_data
