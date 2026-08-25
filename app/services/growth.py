"""Growth Policy & Upsell Decision Engine.

Pure, deterministic evaluation of merchant growth offers, dynamic bundles, and
budget-gated cross-sells. The engine guarantees that an AI buyer can negotiate
revenue-growing additions only within the user's explicit authorization constraints.
"""

from __future__ import annotations

import secrets
from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.enums import Decision
from app.merchant.growth import MERCHANT_BUNDLES, list_active_offers
from app.models.intent import Intent
from app.models.merchant import MerchantProduct
from app.schemas.growth import (
    CartRecoveryOut,
    GrowthRecommendationOut,
    OfferItemOut,
    OfferOut,
)


def evaluate_growth_offer(
    cart_items: Sequence[dict],
    authorized_max_amount: int,
    currency: str,
    db: Session,
) -> GrowthRecommendationOut:
    """Evaluate upsell / cross-sell / bundle opportunities against cart and budget.

    Deterministic rules:
    1. Calculate baseline cost of items currently in the cart.
    2. Check matching product bundles (e.g. Workstation Pro) or cross-sells (e.g. USB-C Hub).
    3. Verify stock availability for proposed items.
    4. Compute total cost with bundled/discounted pricing.
    5. Compare against user's authorized budget ceiling.
    """
    if not cart_items:
        return GrowthRecommendationOut(
            decision=Decision.BLOCK,
            reason="Cart is empty; no growth offer applicable",
            baseline_amount=0,
            recommended_offer=None,
            new_total_amount=0,
            incremental_revenue=0,
            budget_fit=False,
            budget_remaining=0,
            requires_user_confirmation=False,
        )

    # 1. Baseline cost
    cart_skus = {item.get("sku", "").upper() for item in cart_items}
    baseline_total = 0

    for item in cart_items:
        sku = item.get("sku", "").upper()
        qty = int(item.get("quantity", 1))
        product = db.get(MerchantProduct, sku)
        if not product:
            continue
        baseline_total += product.price * qty

    # 2. Check for Bundle Opportunities first
    matched_offer: OfferOut | None = None
    new_total = baseline_total
    incremental_revenue = 0
    combined_items: list[dict] = []

    for b in MERCHANT_BUNDLES:
        trigger_skus = b.get("trigger_skus", [])
        if any(ts in cart_skus for ts in trigger_skus):
            bundle_skus = {it["sku"] for it in b["skus"]}
            if not bundle_skus.issubset(cart_skus):
                # Verify stock for bundle items
                in_stock = True
                for bit in b["skus"]:
                    p = db.get(MerchantProduct, bit["sku"])
                    if not p or p.inventory < bit.get("quantity", 1):
                        in_stock = False
                        break
                if in_stock:
                    bundle_items = [
                        OfferItemOut(
                            sku=it["sku"],
                            name=it["name"],
                            original_price=it["original_price"],
                            discounted_price=it["discounted_price"],
                            quantity=it.get("quantity", 1),
                        )
                        for it in b["skus"]
                    ]
                    orig_sum = sum(it.original_price * it.quantity for it in bundle_items)
                    matched_offer = OfferOut(
                        id=b["id"],
                        type="BUNDLE",
                        title=b["name"],
                        description=b["description"],
                        trigger_skus=trigger_skus,
                        suggested_items=bundle_items,
                        discount_amount=orig_sum - b["bundle_price"],
                        total_original_price=orig_sum,
                        total_discounted_price=b["bundle_price"],
                        currency=b.get("currency", "INR"),
                    )
                    new_total = b["bundle_price"]
                    incremental_revenue = max(0, new_total - baseline_total)
                    combined_items = [
                        {"sku": it.sku, "quantity": it.quantity} for it in bundle_items
                    ]
                    break

    # 3. If no bundle matched, check Cross-Sell offers
    if not matched_offer:
        all_offers = list_active_offers()
        for offer in all_offers:
            if any(ts in cart_skus for ts in offer.trigger_skus):
                suggested_skus = {it.sku for it in offer.suggested_items}
                if not suggested_skus.issubset(cart_skus):
                    in_stock = True
                    for sug in offer.suggested_items:
                        p = db.get(MerchantProduct, sug.sku)
                        if not p or p.inventory < sug.quantity:
                            in_stock = False
                            break
                    if in_stock:
                        matched_offer = offer
                        incremental_revenue = offer.total_discounted_price
                        new_total = baseline_total + incremental_revenue
                        combined_items = [
                            {"sku": item["sku"], "quantity": item.get("quantity", 1)}
                            for item in cart_items
                        ]
                        for sug in offer.suggested_items:
                            combined_items.append(
                                {"sku": sug.sku, "quantity": sug.quantity}
                            )
                        break

    # 4. Handle No Offer Case (or cart already has items)
    if not matched_offer:
        budget_fit = baseline_total <= authorized_max_amount
        budget_remaining = max(0, authorized_max_amount - baseline_total)
        if budget_fit:
            decision = Decision.ALLOW
            reason = "Current cart fits within authorized budget"
            requires_confirmation = False
        else:
            decision = Decision.REQUIRES_AUTHORIZATION
            excess = baseline_total - authorized_max_amount
            reason = (
                f"Cart total of ₹{baseline_total / 100:,.2f} exceeds authorized budget of "
                f"₹{authorized_max_amount / 100:,.2f} by ₹{excess / 100:,.2f}. Explicit user authorization required."
            )
            requires_confirmation = True

        return GrowthRecommendationOut(
            decision=decision,
            reason=reason,
            baseline_amount=baseline_total,
            recommended_offer=None,
            new_total_amount=baseline_total,
            incremental_revenue=0,
            budget_fit=budget_fit,
            budget_remaining=budget_remaining,
            requires_user_confirmation=requires_confirmation,
            suggested_intent_items=[
                {"sku": item["sku"], "quantity": item.get("quantity", 1)}
                for item in cart_items
            ],
        )

    # 5. Enforce deterministic budget gating
    budget_fit = new_total <= authorized_max_amount
    budget_remaining = max(0, authorized_max_amount - new_total)

    if budget_fit:
        decision = Decision.ALLOW
        reason = (
            f"Offer '{matched_offer.title}' total of ₹{new_total / 100:,.2f} fits within authorized "
            f"budget of ₹{authorized_max_amount / 100:,.2f}. Incremental revenue: +₹{incremental_revenue / 100:,.2f}."
        )
        requires_confirmation = False
    else:
        decision = Decision.REQUIRES_AUTHORIZATION
        excess = new_total - authorized_max_amount
        reason = (
            f"Offer '{matched_offer.title}' total of ₹{new_total / 100:,.2f} exceeds authorized "
            f"budget of ₹{authorized_max_amount / 100:,.2f} by ₹{excess / 100:,.2f}. "
            f"Explicit user authorization required."
        )
        requires_confirmation = True

    return GrowthRecommendationOut(
        decision=decision,
        reason=reason,
        baseline_amount=baseline_total,
        recommended_offer=matched_offer,
        new_total_amount=new_total,
        incremental_revenue=incremental_revenue,
        budget_fit=budget_fit,
        budget_remaining=budget_remaining,
        requires_user_confirmation=requires_confirmation,
        suggested_intent_items=combined_items,
    )


def generate_cart_recovery_incentive(
    intent_id: str,
    max_discount_percentage: float,
    db: Session,
) -> CartRecoveryOut:
    """Generate a bounded promotional incentive to re-engage an abandoned intent."""
    intent = db.get(Intent, intent_id)
    if not intent:
        return CartRecoveryOut(
            intent_id=intent_id,
            status="INELIGIBLE",
            reason="Intent record not found",
            original_total=0,
            incentive_discount=0,
            incentive_total=0,
            currency="INR",
            voucher_code="",
        )

    discount_pct = min(
        max(max_discount_percentage, 1.0), 15.0
    )  # capped at 15% safety limit
    original_amount = intent.max_amount
    discount_amount = int(original_amount * (discount_pct / 100.0))
    incentive_total = max(1, original_amount - discount_amount)
    voucher = f"RECOVER_{secrets.token_hex(3).upper()}"
    currency = (
        intent.constraints.get("currency", "INR")
        if isinstance(intent.constraints, dict)
        else "INR"
    )
    max_qty = (
        intent.constraints.get("max_quantity", 1)
        if isinstance(intent.constraints, dict)
        else 1
    )

    new_intent_proposal = {
        "agent_id": intent.agent_id,
        "merchant_id": intent.merchant_id,
        "items": intent.items,
        "constraints": {
            "max_amount": incentive_total,
            "currency": currency,
            "max_quantity": max_qty,
        },
        "authorization": {
            "expires_at": "2030-01-01T00:00:00Z",
        },
        "metadata": {
            "recovery_voucher": voucher,
            "recovered_from_intent_id": intent_id,
            "discount_applied": discount_amount,
        },
    }

    return CartRecoveryOut(
        intent_id=intent_id,
        status="OFFER_GENERATED",
        reason=f"Generated {discount_pct:.1f}% re-engagement incentive for abandoned intent",
        original_total=original_amount,
        incentive_discount=discount_amount,
        incentive_total=incentive_total,
        currency=currency,
        voucher_code=voucher,
        validity_minutes=30,
        recommended_new_intent=new_intent_proposal,
    )
