"""FastAPI router to serve the TrustRail UI pages."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["dashboard"])

_UI_DIR = Path(__file__).parent
_DASHBOARD_PATH = _UI_DIR / "index.html"
_CHAT_PATH = _UI_DIR / "chat.html"


@router.get(
    "/dashboard",
    response_class=HTMLResponse,
    summary="Interactive Track 01 Growth & Integrity Dashboard",
)
def get_dashboard() -> HTMLResponse:
    """Serve the TrustRail live demo and analytics dashboard."""
    if _DASHBOARD_PATH.exists():
        content = _DASHBOARD_PATH.read_text(encoding="utf-8")
        return HTMLResponse(content=content)
    return HTMLResponse(content="<h1>Dashboard file missing</h1>", status_code=404)


@router.get(
    "/agent",
    response_class=HTMLResponse,
    summary="AI Commerce Agent — Conversational Checkout",
)
def get_chat() -> HTMLResponse:
    """Serve the conversational AI buyer chat interface."""
    if _CHAT_PATH.exists():
        content = _CHAT_PATH.read_text(encoding="utf-8")
        return HTMLResponse(content=content)
    return HTMLResponse(content="<h1>Chat UI file missing</h1>", status_code=404)
