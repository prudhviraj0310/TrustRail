"""Phase 1 — deterministic canonicalisation and transaction identity.

This module answers the central question: *when are two purchases "the same"?*

We reduce a PurchaseIntent to only its financially-relevant fields, normalise
them into a canonical form, serialise that form deterministically, and hash it.
The hash is the transaction identity — the idempotency key for the whole system.

What is included in identity (financially relevant):
    merchant_id, items (sku + quantity), currency, max_amount, max_quantity

What is deliberately excluded:
    intent_id, agent_id, authorization.expires_at, and any free-form LLM text.
    These describe *who/when* proposed a purchase, not *what* is being bought.

Canonicalisation rules:
    * SKUs are upper-cased and stripped (SKU-001 == sku-001 == " SKU-001 ").
    * Duplicate SKUs are merged by summing their quantities.
    * Items are sorted by SKU so ordering never affects identity.
    * Amounts are integers (minor units) — no float ambiguity.
    * A version tag is included so a future change to this algorithm produces a
      fresh identity space instead of silently colliding with old identities.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from app.ids import identity_from_canonical
from app.schemas.intent import PurchaseIntentIn

# Bump this if the canonicalisation algorithm ever changes.
CANONICALIZATION_VERSION = 1


def _normalise_sku(sku: str) -> str:
    return sku.strip().upper()


@dataclass(frozen=True)
class CanonicalIntent:
    canonical: dict
    canonical_json: str
    transaction_identity: str
    total_quantity: int


def canonicalize(intent: PurchaseIntentIn) -> CanonicalIntent:
    """Reduce an intent to its canonical financial form + deterministic identity."""
    # Merge duplicate SKUs, summing quantities: "1x SKU-001 + 1x SKU-001" == "2x SKU-001".
    merged: dict[str, int] = {}
    for item in intent.items:
        sku = _normalise_sku(item.sku)
        merged[sku] = merged.get(sku, 0) + int(item.quantity)

    items = [{"sku": sku, "quantity": merged[sku]} for sku in sorted(merged)]
    total_quantity = sum(merged.values())

    canonical = {
        "canonicalization_version": CANONICALIZATION_VERSION,
        "merchant_id": intent.merchant_id.strip(),
        "items": items,
        "constraints": {
            "currency": intent.constraints.currency,  # already upper-cased by schema
            "max_amount": int(intent.constraints.max_amount),
            "max_quantity": int(intent.constraints.max_quantity),
        },
    }

    # Deterministic serialisation: sorted keys, no incidental whitespace.
    canonical_json = json.dumps(
        canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    identity = identity_from_canonical(canonical_json)

    return CanonicalIntent(
        canonical=canonical,
        canonical_json=canonical_json,
        transaction_identity=identity,
        total_quantity=total_quantity,
    )
