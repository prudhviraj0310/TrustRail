"""Identifier generation.

Two very different kinds of identifier live here:

* Envelope IDs (intent/transaction/event/order/payment) — random and unique per
  record. They identify *rows*.
* The transaction *identity* — a deterministic SHA-256 over the canonical,
  financially-relevant form of an intent. It identifies the *semantic purchase*
  and is the idempotency key. It is NEVER derived from raw LLM text.
"""

from __future__ import annotations

import hashlib
import uuid


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def new_intent_id() -> str:
    return _uid("int")


def new_transaction_id() -> str:
    return _uid("txn")


def new_event_id() -> str:
    return _uid("evt")


def new_order_id() -> str:
    return _uid("ord")


def new_payment_ref() -> str:
    return _uid("pay")


def identity_from_canonical(canonical_json: str) -> str:
    """Deterministic transaction identity from the canonical JSON string.

    Same canonical bytes -> same identity. Different bytes -> different identity.
    """
    digest = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
    return f"txid_{digest}"
