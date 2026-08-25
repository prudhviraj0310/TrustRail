"""API endpoints for AI Revenue Growth, dynamic upsells, and conversion analytics."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.growth import (
    CartRecoveryIn,
    CartRecoveryOut,
    GrowthMetricsOut,
    GrowthRecommendationOut,
    GrowthRecommendIn,
)
from app.services.growth import evaluate_growth_offer, generate_cart_recovery_incentive
from app.services.growth_analytics import compute_growth_metrics

router = APIRouter(tags=["AI Growth & Agentic Commerce"])


@router.post(
    "/growth/recommend",
    response_model=GrowthRecommendationOut,
    status_code=status.HTTP_200_OK,
    summary="Get budget-gated cross-sell & bundle recommendations for an AI buyer",
)
def recommend_growth_offer(
    payload: GrowthRecommendIn,
    db: Session = Depends(get_db),
) -> GrowthRecommendationOut:
    """Evaluate applicable merchant bundles and cross-sells against user budget."""
    return evaluate_growth_offer(
        cart_items=payload.cart_items,
        authorized_max_amount=payload.authorized_max_amount,
        currency=payload.currency,
        db=db,
    )


@router.post(
    "/growth/abandonment/recover",
    response_model=CartRecoveryOut,
    status_code=status.HTTP_200_OK,
    summary="Generate a bounded incentive offer to re-engage an abandoned intent",
)
def recover_abandoned_intent(
    payload: CartRecoveryIn,
    db: Session = Depends(get_db),
) -> CartRecoveryOut:
    """Generate a bounded discount voucher for an unexecuted or stalled intent."""
    return generate_cart_recovery_incentive(
        intent_id=payload.intent_id,
        max_discount_percentage=payload.max_discount_percentage,
        db=db,
    )


@router.get(
    "/analytics/growth",
    response_model=GrowthMetricsOut,
    summary="Real-time merchant revenue uplift and agentic commerce metrics",
)
def get_growth_analytics(
    db: Session = Depends(get_db),
) -> GrowthMetricsOut:
    """Retrieve GMV, AOV uplift, attach rate, and incremental revenue."""
    return compute_growth_metrics(db)
