"""Policy engine output schemas (Phase 2)."""

from __future__ import annotations

from pydantic import BaseModel

from app.enums import Decision


class PolicyCheckOut(BaseModel):
    name: str
    passed: bool
    detail: str | None = None


class PolicyDecisionOut(BaseModel):
    decision: Decision
    reason: str
    policy_checks: list[PolicyCheckOut]
