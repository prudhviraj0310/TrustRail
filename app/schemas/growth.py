"""Pydantic schemas for the AI Revenue Growth and Agentic Commerce engine."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class OfferItemOut(BaseModel):
    sku: str
    name: str
    original_price: int  # minor units (paise)
    discounted_price: int  # minor units (paise)
    quantity: int = 1


class OfferOut(BaseModel):
    id: str
    type: str  # BUNDLE, CROSS_SELL, INCENTIVE
    title: str
    description: str
    trigger_skus: list[str] = Field(default_factory=list)
    suggested_items: list[OfferItemOut]
    discount_amount: int  # minor units (paise) saved
    total_original_price: int
    total_discounted_price: int
    currency: str = "INR"
    valid_until: datetime | None = None


class BundleOut(BaseModel):
    id: str
    name: str
    description: str
    items: list[OfferItemOut]
    total_original_price: int
    bundle_price: int
    savings: int
    currency: str = "INR"


class GrowthRecommendIn(BaseModel):
    """Input payload from an AI buyer requesting growth/upsell recommendations."""

    merchant_id: str
    cart_items: list[dict] = Field(
        ...,
        description="List of current cart items with sku and quantity",
        examples=[[{"sku": "SKU-001", "quantity": 1}]],
    )
    authorized_max_amount: int = Field(
        ...,
        description="User-authorized budget ceiling in minor units",
        examples=[500000],
    )
    currency: str = Field("INR", description="Currency code")


class GrowthRecommendationOut(BaseModel):
    """Deterministic recommendation output from the TrustRail Growth Policy engine."""

    decision: str  # ALLOW, REQUIRES_AUTHORIZATION, NO_OFFER
    reason: str
    baseline_amount: int
    recommended_offer: OfferOut | None = None
    new_total_amount: int
    incremental_revenue: int
    budget_fit: bool
    budget_remaining: int
    requires_user_confirmation: bool
    suggested_intent_items: list[dict] | None = None


class CartRecoveryIn(BaseModel):
    """Request to generate a bounded re-engagement incentive for an abandoned or expired intent."""

    intent_id: str
    max_discount_percentage: float = Field(
        default=5.0,
        description="Maximum merchant-approved discount percentage (e.g. 5.0 for 5%)",
    )


class CartRecoveryOut(BaseModel):
    intent_id: str
    status: str  # OFFER_GENERATED, INELIGIBLE
    reason: str
    original_total: int
    incentive_discount: int
    incentive_total: int
    currency: str
    voucher_code: str
    validity_minutes: int = 30
    recommended_new_intent: dict | None = None


class GrowthMetricsOut(BaseModel):
    """Real-time analytics proving merchant revenue uplift from agentic commerce."""

    total_orders: int
    gross_merchandise_value: int  # GMV in minor units
    baseline_gmv: int
    incremental_growth_revenue: (
        int  # Additional revenue driven by agentic upsells/bundles
    )
    aov_baseline: int  # Average Order Value without growth engine
    aov_with_growth: int  # Average Order Value with growth engine
    aov_uplift_percentage: float  # e.g. +28.5%
    attach_rate_percentage: (
        float  # percentage of transactions with accepted add-ons/bundles
    )
    abandoned_intents_recovered: int
    recovered_revenue: int
    currency: str = "INR"
