"""TrustRail FastAPI application.

Run locally:  uvicorn app.main:app --reload
Interactive docs:  http://127.0.0.1:8000/docs
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import __version__
from app.api.chat import router as chat_router
from app.api.growth import router as growth_router
from app.api.intents import router as intents_router
from app.api.transactions import router as transactions_router
from app.api.webhooks import router as webhooks_router
from app.config import get_settings
from app.db import SessionLocal, create_all
from app.errors import (
    InsufficientInventory,
    IntentNotFound,
    InvalidLifecycleState,
    InvalidStateTransition,
    MerchantOrderFailed,
    OrderNotFound,
    ProductNotFound,
    TransactionNotFound,
    TrustRailError,
)
from app.merchant.catalogue import seed_merchant
from app.merchant.router import router as merchant_router
from app.ui.dashboard import router as dashboard_router

DESCRIPTION = """
**TrustRail** is an **AI Growth & Agentic Commerce Engine** built with a deterministic
safety, transaction-integrity, and recovery layer between an autonomous **AI buyer**,
a **merchant** backend, and **Razorpay** (Test Mode).

The AI helps the merchant **sell more** via bounded upsells, bundles, cross-sells,
and recovery offers, while TrustRail guarantees the AI can **never exceed** user authority.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.auto_create_tables:
        create_all()
    if settings.seed_merchant:
        db = SessionLocal()
        try:
            seed_merchant(db)
        finally:
            db.close()
    yield


app = FastAPI(
    title="TrustRail",
    version=__version__,
    description=DESCRIPTION,
    lifespan=lifespan,
)


# --- domain error -> HTTP status mapping ---------------------------------- #
_ERROR_STATUS: list[tuple[type[Exception], int]] = [
    (IntentNotFound, 404),
    (TransactionNotFound, 404),
    (ProductNotFound, 404),
    (OrderNotFound, 404),
    (InvalidLifecycleState, 409),
    (InvalidStateTransition, 409),
    (InsufficientInventory, 409),
    (MerchantOrderFailed, 502),
]


def _make_handler(status_code: int):
    async def handler(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=status_code,
            content={"error": exc.__class__.__name__, "detail": str(exc)},
        )

    return handler


for exc_cls, code in _ERROR_STATUS:
    app.add_exception_handler(exc_cls, _make_handler(code))


@app.exception_handler(TrustRailError)
async def _trustrail_error_handler(request: Request, exc: TrustRailError) -> JSONResponse:
    # Fallback for any TrustRail domain error not mapped above.
    return JSONResponse(
        status_code=400,
        content={"error": exc.__class__.__name__, "detail": str(exc)},
    )


# --- routers -------------------------------------------------------------- #
app.include_router(intents_router)
app.include_router(transactions_router)
app.include_router(merchant_router)
app.include_router(webhooks_router)
app.include_router(growth_router)
app.include_router(chat_router)
app.include_router(dashboard_router)


@app.get("/", tags=["meta"])
def root() -> dict:
    return {
        "name": "TrustRail",
        "version": __version__,
        "tagline": "AI Growth & Agentic Commerce Engine",
        "track": "Track 01: AI Growth & Agentic Commerce",
        "chat_agent": "/agent",
        "dashboard": "/dashboard",
        "docs": "/docs",
        "health": "/health",
        "agent_card": "/merchant/agent-card",
        "growth_analytics": "/analytics/growth",
    }


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok"}
