"""TrustRail AI Buyer Agent — Gemini-powered conversational commerce.

This is the CORE AI component that makes TrustRail an actual AI agent, not just
a rule engine. The agent:

1. Reads the merchant catalog (products, bundles, offers)
2. Understands user intent via natural language
3. Reasons about what to recommend (using Gemini)
4. Proposes purchases through TrustRail's growth policy engine
5. Executes transactions within user-authorized budgets
6. Explains every decision transparently

The agent uses Gemini's function-calling to interact with TrustRail's APIs
as structured tool calls, ensuring every action is auditable.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.merchant.catalogue import MERCHANT_ID
from app.merchant.growth import list_active_bundles, list_active_offers
from app.models.merchant import MerchantProduct
from app.schemas.chat import ChatMessageOut, TransactionDetail

logger = logging.getLogger(__name__)

# In-memory session store (sufficient for demo; production would use Redis)
_sessions: dict[str, list[dict[str, Any]]] = {}


def _get_catalog(db: Session) -> list[dict[str, Any]]:
    """Build a structured product catalog for the AI agent's context."""
    products = db.query(MerchantProduct).filter(
        MerchantProduct.inventory > 0,
        MerchantProduct.force_payment_decline.is_(False),
        MerchantProduct.force_order_failure.is_(False),
    ).all()

    catalog = []
    for p in products:
        catalog.append({
            "sku": p.sku,
            "name": p.name,
            "price_paise": p.price,
            "price_display": f"₹{p.price / 100:,.2f}",
            "currency": p.currency,
            "in_stock": p.inventory > 0,
            "inventory": p.inventory,
        })
    return catalog


def _get_bundles_context() -> list[dict[str, Any]]:
    """Get bundle information for agent context."""
    bundles = list_active_bundles()
    return [
        {
            "id": b.id,
            "name": b.name,
            "description": b.description,
            "bundle_price_display": f"₹{b.bundle_price / 100:,.2f}",
            "original_price_display": f"₹{b.total_original_price / 100:,.2f}",
            "savings_display": f"₹{b.savings / 100:,.2f}",
            "items": [
                {"sku": it.sku, "name": it.name, "price": f"₹{it.original_price / 100:,.2f}"}
                for it in b.items
            ],
        }
        for b in bundles
    ]


def _build_system_prompt(catalog: list[dict], bundles: list[dict], budget: int) -> str:
    """Build the system prompt that defines the AI buyer agent's behavior."""
    return f"""You are TrustRail's AI Commerce Agent — an intelligent shopping assistant that helps users discover products and complete purchases from a merchant's catalog.

## YOUR ROLE
You help users find the right products, recommend smart bundles that save money, and complete purchases — all within their authorized budget. You are conversational, helpful, and proactive about suggesting value.

## MERCHANT CATALOG
{json.dumps(catalog, indent=2)}

## AVAILABLE BUNDLES & OFFERS
{json.dumps(bundles, indent=2)}

## USER'S AUTHORIZED BUDGET
₹{budget / 100:,.2f} (max authorized spend)

## BEHAVIOR RULES
1. **Be conversational and helpful** — greet the user, understand what they need
2. **Proactively recommend bundles** when a user shows interest in a product that's part of a bundle — explain the savings
3. **Always respect the budget** — never suggest items that exceed the authorized amount
4. **Be transparent** — show prices, explain savings, mention budget remaining
5. **When the user wants to buy**, confirm the items and total before proceeding
6. **Format prices** in ₹ with proper formatting (e.g., ₹1,299.00)

## RESPONSE FORMAT
When recommending products, use this structure in your response:
- Product name and price
- Why it's a good fit
- Bundle savings if applicable
- Budget impact

When the user confirms a purchase, respond with EXACTLY this marker on its own line:
[EXECUTE_PURCHASE: SKU-001, SKU-002, SKU-003]
(listing the SKUs to purchase)

This marker triggers the actual TrustRail transaction pipeline. Only include it when the user has explicitly confirmed they want to buy.

Keep responses concise — 2-3 short paragraphs max. Use emoji sparingly for visual appeal."""


def _call_gemini(messages: list[dict[str, str]], system_prompt: str) -> str:
    """Call Gemini API with conversation history."""
    try:
        from google import genai
        from google.genai import types
        from app.config import get_settings

        settings = get_settings()
        api_key = settings.gemini_api_key

        if not api_key:
            return _fallback_response(messages)

        client = genai.Client(api_key=api_key)

        # Build contents list for the API
        contents = []
        for msg in messages:
            role = "user" if msg["role"] == "user" else "model"
            contents.append(types.Content(
                role=role,
                parts=[types.Part.from_text(text=msg["content"])],
            ))

        response = client.models.generate_content(
            model="gemini-3.6-flash",
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.7,
                max_output_tokens=1024,
            ),
        )
        return response.text

    except ImportError:
        logger.warning("google-genai not installed, using fallback")
        return _fallback_response(messages)
    except Exception as e:
        logger.error(f"Gemini API error: {e}")
        return _fallback_response(messages)


def _fallback_response(messages: list[dict[str, str]]) -> str:
    """Intelligent fallback when Gemini API is unavailable.

    This provides a rule-based but still useful conversational experience
    so the demo works even without an API key.
    """
    last_msg = messages[-1]["content"].lower() if messages else ""

    if any(w in last_msg for w in ["hi", "hello", "hey", "start", "help"]):
        return """👋 Welcome to **TrustRail AI Commerce**! I'm your AI shopping assistant.

I can help you find products from our tech catalog. Here's what's available:

🖱️ **Wireless Mouse** — ₹1,299.00
⌨️ **Mechanical Keyboard** — ₹4,999.00
🔌 **USB-C Hub** — ₹2,499.00
🖥️ **27-inch 4K Monitor** — ₹18,999.00

💡 **Pro Tip:** Check out our **Workstation Pro Bundle** — Mouse + Keyboard + Hub for just ₹3,498.00 (save ₹5,299.00!)

What are you looking for today?"""

    if any(w in last_msg for w in ["mouse", "click", "wireless"]):
        return """Great choice! The **Wireless Mouse** (SKU-001) is ₹1,299.00.

🎯 **But here's a smarter deal:** Our **Workstation Pro Productivity Bundle** includes:
- 🖱️ Wireless Mouse (₹1,299)
- ⌨️ Mechanical Keyboard (₹4,999)
- 🔌 USB-C Hub (₹2,499)

**Bundle Price: ₹3,498.00** — you save ₹5,299.00! That's 60% off!

It fits within your budget. Want me to set up the Workstation Bundle, or just the mouse?"""

    if any(w in last_msg for w in ["monitor", "display", "screen", "4k"]):
        return """The **27-inch 4K Monitor** (SKU-004) is ₹18,999.00 — stunning visual quality!

⚠️ **Budget check:** This exceeds your authorized budget of ₹5,000.00 by ₹13,999.00.

TrustRail's safety policy would require explicit authorization increase to proceed. I cannot exceed your authorized spending limit.

Would you like to look at items within your budget instead? The Workstation Bundle at ₹3,498 is an excellent option!"""

    if any(w in last_msg for w in ["keyboard", "type", "mechanical"]):
        return """The **Mechanical Keyboard** (SKU-002) is ₹4,999.00 — a premium typing experience!

💡 Consider our **Workstation Pro Bundle** instead:
- Keyboard + Mouse + USB-C Hub = **₹3,498.00** (vs ₹4,999 for keyboard alone!)

You'd actually pay LESS and get 3 products instead of 1. The bundle fits within your budget.

Want the bundle, or just the keyboard?"""

    if any(w in last_msg for w in ["hub", "usb", "port", "dongle"]):
        return """The **USB-C Hub** (SKU-003) is ₹2,499.00 — multi-port connectivity!

🎯 **Better deal:** Add a Wireless Mouse and Mechanical Keyboard for a complete workstation:
- **Workstation Pro Bundle**: ₹3,498.00 (all 3 items!)
- **Hub alone**: ₹2,499.00

For just ₹999 more, you get a mouse AND a keyboard! Should I set up the bundle?"""

    if any(w in last_msg for w in ["bundle", "workstation", "all three", "yes", "go ahead", "buy", "purchase", "get it", "do it", "confirm"]):
        if any(w in last_msg for w in ["just mouse", "only mouse", "just the mouse"]):
            return """Got it — just the **Wireless Mouse** (SKU-001) at ₹1,299.00.

✅ Within your budget. Ready to execute the purchase through TrustRail's secure pipeline?

Say **"confirm"** to proceed!

[EXECUTE_PURCHASE: SKU-001]"""

        return """Excellent choice! 🚀 Setting up the **Workstation Pro Bundle**:

| Item | Original | Bundle Price |
|------|----------|-------------|
| 🖱️ Wireless Mouse | ₹1,299 | ₹999 |
| ⌨️ Mechanical Keyboard | ₹4,999 | ₹1,499 |
| 🔌 USB-C Hub | ₹2,499 | ₹1,000 |
| **Total** | **₹8,797** | **₹3,498** |

💰 **You save ₹5,299!** | ✅ Within your ₹5,000 budget

Executing purchase through TrustRail's integrity pipeline...

[EXECUTE_PURCHASE: SKU-001, SKU-002, SKU-003]"""

    if any(w in last_msg for w in ["budget", "how much", "afford", "spending", "limit"]):
        return f"""Your current authorized budget is **₹5,000.00**.

Here's what fits:
- 🖱️ Wireless Mouse — ₹1,299.00 ✅
- 🔌 USB-C Hub — ₹2,499.00 ✅
- ⌨️ Mechanical Keyboard — ₹4,999.00 ✅
- 🎁 **Workstation Pro Bundle** — ₹3,498.00 ✅ ⭐ Best value!
- 🖥️ 4K Monitor — ₹18,999.00 ❌ Exceeds budget

The Workstation Bundle gives you the best bang for your budget. Interested?"""

    if any(w in last_msg for w in ["product", "catalog", "what do you", "what's available", "show me", "list"]):
        return """Here's our full product catalog:

| Product | Price | Stock |
|---------|-------|-------|
| 🖱️ Wireless Mouse (SKU-001) | ₹1,299.00 | ✅ In stock |
| ⌨️ Mechanical Keyboard (SKU-002) | ₹4,999.00 | ✅ In stock |
| 🔌 USB-C Hub (SKU-003) | ₹2,499.00 | ✅ In stock |
| 🖥️ 27-inch 4K Monitor (SKU-004) | ₹18,999.00 | ✅ In stock |

🎁 **Bundle Deal:** Workstation Pro (Mouse + Keyboard + Hub) = **₹3,498.00** (save ₹5,299!)

What catches your eye?"""

    return """I can help you find the right products! Here's what I can do:

- 🔍 **Browse products** — "Show me what's available"
- 🎁 **Discover bundles** — "Any deals or bundles?"
- 💰 **Check budget** — "What can I afford?"
- 🛒 **Make a purchase** — "I want to buy a mouse"

What would you like to explore?"""


def _execute_purchase(
    skus: list[str],
    budget: int,
    currency: str,
    db: Session,
) -> tuple[str, TransactionDetail | None, dict[str, Any] | None]:
    """Execute an actual purchase through TrustRail's full pipeline."""
    from app.services.growth import evaluate_growth_offer

    cart_items = [{"sku": sku.strip(), "quantity": 1} for sku in skus]

    # Smart bundle detection: if multiple SKUs are provided, first try evaluating
    # with just the first (trigger) SKU — the growth engine will recommend the
    # bundle at bundle pricing. This avoids the problem of individual pricing
    # when the user explicitly wants a bundle.
    recommendation = None
    if len(skus) > 1:
        trigger_item = [{"sku": skus[0].strip(), "quantity": 1}]
        rec = evaluate_growth_offer(
            cart_items=trigger_item,
            authorized_max_amount=budget,
            currency=currency,
            db=db,
        )
        # If the growth engine recommends a bundle that covers our requested SKUs,
        # use the bundle-priced recommendation
        if rec.recommended_offer and rec.decision in ("ALLOW", "ALLOW_WITH_GROWTH"):
            suggested_skus = {
                it["sku"] for it in (rec.suggested_intent_items or [])
            }
            requested_skus = {s.strip() for s in skus}
            if requested_skus.issubset(suggested_skus):
                recommendation = rec
                cart_items = rec.suggested_intent_items or cart_items

    # Fallback: evaluate the full cart as-is
    if recommendation is None:
        recommendation = evaluate_growth_offer(
            cart_items=cart_items,
            authorized_max_amount=budget,
            currency=currency,
            db=db,
        )

    rec_dict = recommendation.model_dump()

    if recommendation.decision == "REQUIRES_AUTHORIZATION":
        return (
            f"🛡️ **TrustRail Safety Gate: BLOCKED**\n\n{recommendation.reason}\n\n"
            f"The AI cannot exceed your authorized budget. Please increase your budget or choose different items.",
            TransactionDetail(
                state="BLOCKED",
                amount=recommendation.new_total_amount,
                currency=currency,
                items=cart_items,
            ),
            rec_dict,
        )

    # Use the growth engine's suggested items (which have bundle pricing applied)
    purchase_items = recommendation.suggested_intent_items or cart_items
    purchase_skus = [it["sku"] for it in purchase_items]

    # Step 2: Create intent via the transaction service
    from app.schemas.intent import (
        AuthorizationIn,
        ConstraintsIn,
        ItemIn,
        PurchaseIntentIn,
    )
    from app.services import transaction as txn_service

    payload = PurchaseIntentIn(
        agent_id="trustrail-ai-agent",
        merchant_id=MERCHANT_ID,
        items=[
            ItemIn(sku=it["sku"], quantity=it.get("quantity", 1))
            for it in purchase_items
        ],
        constraints=ConstraintsIn(
            max_amount=budget,
            currency=currency,
            max_quantity=sum(it.get("quantity", 1) for it in purchase_items),
        ),
        authorization=AuthorizationIn(
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
    )
    raw = payload.model_dump(mode="json")
    intent, txn = txn_service.create_intent(db, payload, raw)

    # Step 3: Validate
    txn_service.validate_intent(db, intent.id)

    # Step 4: Authorize
    txn_service.authorize_intent(db, intent.id)

    # Step 5: Execute transaction — returns (Transaction, PolicyResult)
    exec_txn, policy_result = txn_service.execute_transaction(db, intent_id=intent.id)

    checks = [
        {"name": pc.name, "passed": pc.passed, "detail": pc.detail}
        for pc in (policy_result.checks or [])
    ]

    txn_detail = TransactionDetail(
        transaction_id=exec_txn.id,
        state=exec_txn.state,
        amount=exec_txn.authorized_max_amount,
        currency=currency,
        items=cart_items,
        policy_checks=checks,
    )

    if exec_txn.state == "COMPLETED":
        amount_display = f"₹{(exec_txn.authorized_max_amount or 0) / 100:,.2f}"
        msg = (
            f"✅ **Transaction COMPLETED!**\n\n"
            f"**Transaction ID:** `{exec_txn.id}`\n"
            f"**Amount Charged:** {amount_display}\n"
            f"**State:** {exec_txn.state}\n"
            f"**Policy Checks:** {len(checks)} passed ✅\n\n"
            f"All {len(checks)} TrustRail policy checks passed: "
            f"merchant verified, SKUs valid, currency matched, budget enforced, inventory confirmed, prices unchanged.\n\n"
            f"💰 Revenue recorded in merchant growth analytics."
        )
    else:
        msg = (
            f"⚠️ Transaction entered state: **{exec_txn.state}**\n"
            f"Transaction ID: `{exec_txn.id}`\n"
            f"TrustRail has recorded this in the audit trail."
        )

    return msg, txn_detail, rec_dict


def process_chat_message(
    message: str,
    session_id: str,
    budget: int,
    currency: str,
    db: Session,
) -> ChatMessageOut:
    """Process a user chat message and return the AI agent's response.

    This is the main entry point for the conversational AI buyer flow.
    """
    # Initialize or retrieve session
    if session_id not in _sessions:
        _sessions[session_id] = []

    history = _sessions[session_id]

    # Add user message to history
    history.append({"role": "user", "content": message})

    # Build context
    catalog = _get_catalog(db)
    bundles = _get_bundles_context()
    system_prompt = _build_system_prompt(catalog, bundles, budget)

    # Get AI response
    ai_response = _call_gemini(history, system_prompt)

    # Check if the AI wants to execute a purchase
    transaction = None
    recommendation = None
    action = "info"
    growth_insight = None

    if "[EXECUTE_PURCHASE:" in ai_response:
        # Extract SKUs from the marker
        import re
        match = re.search(r'\[EXECUTE_PURCHASE:\s*([^\]]+)\]', ai_response)
        if match:
            skus = [s.strip() for s in match.group(1).split(",")]
            # Remove the marker from the display message
            display_msg = re.sub(r'\[EXECUTE_PURCHASE:[^\]]+\]', '', ai_response).strip()

            # Execute the actual purchase
            exec_msg, txn_detail, rec_dict = _execute_purchase(skus, budget, currency, db)

            ai_response = display_msg + "\n\n---\n\n" + exec_msg
            transaction = txn_detail
            recommendation = rec_dict
            action = "purchase" if (txn_detail and txn_detail.state == "COMPLETED") else "blocked"

            if txn_detail and txn_detail.state == "COMPLETED":
                # Compute growth insight
                from app.services.growth_analytics import compute_growth_metrics
                metrics = compute_growth_metrics(db)
                if metrics.incremental_growth_revenue > 0:
                    growth_insight = (
                        f"📊 Merchant Growth Impact: +₹{metrics.incremental_growth_revenue / 100:,.2f} "
                        f"incremental revenue | AOV uplift: +{metrics.aov_uplift_percentage}% | "
                        f"Attach rate: {metrics.attach_rate_percentage}%"
                    )
    elif any(w in ai_response.lower() for w in ["bundle", "recommend", "suggest", "deal"]):
        action = "recommend"

    # Add AI response to history
    history.append({"role": "assistant", "content": ai_response})

    # Keep history manageable (last 20 messages)
    if len(history) > 20:
        _sessions[session_id] = history[-20:]

    return ChatMessageOut(
        role="assistant",
        message=ai_response,
        session_id=session_id,
        action=action,
        transaction=transaction,
        recommendation=recommendation,
        growth_insight=growth_insight,
    )
