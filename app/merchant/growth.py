"""Merchant growth catalogue, dynamic bundles, and cross-sell rules."""

from __future__ import annotations

from typing import Any

from app.schemas.growth import BundleOut, OfferItemOut, OfferOut

MERCHANT_BUNDLES: list[dict[str, Any]] = [
    {
        "id": "BUNDLE-WORKSTATION-PRO",
        "name": "Workstation Pro Productivity Bundle",
        "description": "Wireless Mouse (SKU-001) + Mechanical Keyboard (SKU-002) + USB-C Hub (SKU-003)",
        "trigger_skus": ["SKU-001", "SKU-002"],
        "skus": [
            {
                "sku": "SKU-001",
                "name": "Wireless Mouse",
                "original_price": 129900,
                "discounted_price": 99900,
                "quantity": 1,
            },
            {
                "sku": "SKU-002",
                "name": "Mechanical Keyboard",
                "original_price": 499900,
                "discounted_price": 149900,
                "quantity": 1,
            },
            {
                "sku": "SKU-003",
                "name": "USB-C Hub",
                "original_price": 249900,
                "discounted_price": 100000,
                "quantity": 1,
            },
        ],
        "bundle_price": 349800,  # ₹3,498.00 (Saves ₹5,299 vs buying separately!)
        "currency": "INR",
    },
    {
        "id": "BUNDLE-CREATOR-EXPANSION",
        "name": "Creator 4K Display & Hub Pack",
        "description": "27-inch 4K Monitor (SKU-004) + High-Speed USB-C Hub (SKU-003)",
        "trigger_skus": ["SKU-004"],
        "skus": [
            {
                "sku": "SKU-004",
                "name": "27-inch 4K Monitor",
                "original_price": 1899900,
                "discounted_price": 1749900,
                "quantity": 1,
            },
            {
                "sku": "SKU-003",
                "name": "USB-C Hub",
                "original_price": 249900,
                "discounted_price": 150000,
                "quantity": 1,
            },
        ],
        "bundle_price": 1899900,  # ₹18,999.00 (Free USB-C Hub with Monitor)
        "currency": "INR",
    },
]

MERCHANT_CROSS_SELLS: list[dict[str, Any]] = [
    {
        "id": "OFFER-MOUSE-HUB",
        "type": "CROSS_SELL",
        "title": "USB-C Hub Companion Upgrade",
        "description": "Add a Multi-Port USB-C Hub to your Wireless Mouse order and save ₹300",
        "trigger_skus": ["SKU-001"],
        "suggested_items": [
            {
                "sku": "SKU-003",
                "name": "USB-C Hub",
                "original_price": 249900,
                "discounted_price": 219900,
                "quantity": 1,
            }
        ],
        "discount_amount": 30000,  # ₹300.00
        "currency": "INR",
    },
    {
        "id": "OFFER-KEYBOARD-MOUSE",
        "type": "CROSS_SELL",
        "title": "Ergonomic Pair: Precision Mouse Add-on",
        "description": "Pair your Mechanical Keyboard with a Precision Wireless Mouse for ₹1,099 (Save ₹200)",
        "trigger_skus": ["SKU-002"],
        "suggested_items": [
            {
                "sku": "SKU-001",
                "name": "Wireless Mouse",
                "original_price": 129900,
                "discounted_price": 109900,
                "quantity": 1,
            }
        ],
        "discount_amount": 20000,  # ₹200.00
        "currency": "INR",
    },
]


def list_active_bundles() -> list[BundleOut]:
    """Return all active merchant bundles."""
    result = []
    for b in MERCHANT_BUNDLES:
        items = [
            OfferItemOut(
                sku=it["sku"],
                name=it["name"],
                original_price=it["original_price"],
                discounted_price=it["discounted_price"],
                quantity=it.get("quantity", 1),
            )
            for it in b["skus"]
        ]
        total_orig = sum(it.original_price * it.quantity for it in items)
        savings = total_orig - b["bundle_price"]
        result.append(
            BundleOut(
                id=b["id"],
                name=b["name"],
                description=b["description"],
                items=items,
                total_original_price=total_orig,
                bundle_price=b["bundle_price"],
                savings=savings,
                currency=b.get("currency", "INR"),
            )
        )
    return result


def list_active_offers() -> list[OfferOut]:
    """Return all active cross-sells and promotional offers."""
    result = []
    for o in MERCHANT_CROSS_SELLS:
        items = [
            OfferItemOut(
                sku=it["sku"],
                name=it["name"],
                original_price=it["original_price"],
                discounted_price=it["discounted_price"],
                quantity=it.get("quantity", 1),
            )
            for it in o["suggested_items"]
        ]
        total_orig = sum(it.original_price * it.quantity for it in items)
        total_disc = sum(it.discounted_price * it.quantity for it in items)
        result.append(
            OfferOut(
                id=o["id"],
                type=o["type"],
                title=o["title"],
                description=o["description"],
                trigger_skus=o.get("trigger_skus", []),
                suggested_items=items,
                discount_amount=o["discount_amount"],
                total_original_price=total_orig,
                total_discounted_price=total_disc,
                currency=o.get("currency", "INR"),
            )
        )
    return result
