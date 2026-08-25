"""Phase 3 — the deterministic transaction state machine.

State is data in PostgreSQL; movement between states is governed *only* by the
explicit adjacency map below. The AI buyer can never set a transaction's state:
it can only trigger operations (validate / authorize / execute), and those
operations ask this module whether the resulting transition is legal.

Design rules:
* Every legal transition is enumerated explicitly. If it isn't in the map, it
  cannot happen.
* States with no outgoing transitions are terminal.
* ``INTENT_CREATED -> COMPLETED`` is deliberately absent, so shortcutting the
  lifecycle is impossible.
"""

from __future__ import annotations

from app.enums import TransactionState as S
from app.errors import InvalidStateTransition

# Explicit, exhaustive adjacency map. Read it top-to-bottom as the happy path,
# with failure/recovery branches hanging off each stage.
ALLOWED_TRANSITIONS: dict[S, set[S]] = {
    # --- happy path with early rejections ---
    S.INTENT_CREATED: {S.VALIDATED, S.INVALID, S.POLICY_BLOCKED, S.AUTH_EXPIRED},
    S.VALIDATED: {
        S.AUTHORIZED,
        S.POLICY_BLOCKED,
        S.AUTH_EXPIRED,
        S.INVENTORY_CHANGED,
        S.PRICE_CHANGED,
        S.INVALID,
    },
    S.AUTHORIZED: {
        S.PAYMENT_PENDING,
        S.AUTH_EXPIRED,
        S.INVENTORY_CHANGED,
        S.PRICE_CHANGED,
        S.POLICY_BLOCKED,
    },
    # --- payment ---
    S.PAYMENT_PENDING: {S.PAYMENT_CONFIRMED, S.PAYMENT_FAILED, S.PAYMENT_UNKNOWN},
    S.PAYMENT_UNKNOWN: {
        S.PAYMENT_CONFIRMED,
        S.PAYMENT_FAILED,
        S.RECOVERY_PENDING,
    },
    # --- fulfilment ---
    S.PAYMENT_CONFIRMED: {S.ORDER_CONFIRMED, S.ORDER_FAILED},
    S.ORDER_CONFIRMED: {S.COMPLETED},
    S.ORDER_FAILED: {S.REFUND_REQUIRED, S.RECOVERY_PENDING},
    # --- recovery ---
    S.RECOVERY_PENDING: {
        S.REFUND_REQUIRED,
        S.COMPLETED,
        S.PAYMENT_CONFIRMED,
        S.PAYMENT_FAILED,
    },
    S.REFUND_REQUIRED: {S.COMPLETED},
    # --- terminal states (no outgoing transitions) ---
    S.COMPLETED: set(),
    S.INVALID: set(),
    S.POLICY_BLOCKED: set(),
    S.AUTH_EXPIRED: set(),
    S.INVENTORY_CHANGED: set(),
    S.PRICE_CHANGED: set(),
    S.PAYMENT_FAILED: set(),
}

# Defensive: guarantee the map covers every state exactly once. A missing state
# would let can_transition() raise KeyError instead of returning a clean answer.
assert set(ALLOWED_TRANSITIONS.keys()) == set(S), "state machine map is incomplete"

TERMINAL_STATES: frozenset[S] = frozenset(
    state for state, outgoing in ALLOWED_TRANSITIONS.items() if not outgoing
)


def _coerce(state: S | str) -> S:
    return state if isinstance(state, S) else S(state)


def can_transition(from_state: S | str, to_state: S | str) -> bool:
    """Return True iff ``from_state -> to_state`` is an allowed transition."""
    return _coerce(to_state) in ALLOWED_TRANSITIONS[_coerce(from_state)]


def assert_transition(from_state: S | str, to_state: S | str) -> None:
    """Raise :class:`InvalidStateTransition` unless the transition is allowed."""
    frm, to = _coerce(from_state), _coerce(to_state)
    if to not in ALLOWED_TRANSITIONS[frm]:
        raise InvalidStateTransition(frm.value, to.value)


def is_terminal(state: S | str) -> bool:
    return _coerce(state) in TERMINAL_STATES
