"""Unit tests for Phase 3 — the transaction state machine.

The state machine is the guardrail that makes "the AI jumped straight to
COMPLETED" impossible. These tests verify the adjacency map is complete, the
happy path is walkable, terminal states are dead-ends, and illegal jumps raise.
"""

from __future__ import annotations

import itertools

import pytest

from app.enums import TransactionState as S
from app.errors import InvalidStateTransition
from app.services.state_machine import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATES,
    assert_transition,
    can_transition,
    is_terminal,
)

HAPPY_PATH = [
    S.INTENT_CREATED,
    S.VALIDATED,
    S.AUTHORIZED,
    S.PAYMENT_PENDING,
    S.PAYMENT_CONFIRMED,
    S.ORDER_CONFIRMED,
    S.COMPLETED,
]

EXPECTED_TERMINALS = {
    S.COMPLETED,
    S.INVALID,
    S.POLICY_BLOCKED,
    S.AUTH_EXPIRED,
    S.INVENTORY_CHANGED,
    S.PRICE_CHANGED,
    S.PAYMENT_FAILED,
}


def test_map_covers_every_state_exactly_once():
    assert set(ALLOWED_TRANSITIONS.keys()) == set(S)


def test_happy_path_is_walkable_step_by_step():
    for frm, to in itertools.pairwise(HAPPY_PATH):
        assert can_transition(frm, to), f"{frm} -> {to} should be allowed"


def test_intent_created_cannot_shortcut_to_completed():
    assert can_transition(S.INTENT_CREATED, S.COMPLETED) is False
    with pytest.raises(InvalidStateTransition):
        assert_transition(S.INTENT_CREATED, S.COMPLETED)


def test_terminal_states_match_expected_and_have_no_exits():
    assert TERMINAL_STATES == frozenset(EXPECTED_TERMINALS)
    for state in EXPECTED_TERMINALS:
        assert is_terminal(state)
        assert ALLOWED_TRANSITIONS[state] == set()


def test_non_terminal_states_have_at_least_one_exit():
    for state, outgoing in ALLOWED_TRANSITIONS.items():
        if state not in EXPECTED_TERMINALS:
            assert outgoing, f"{state} should have outgoing transitions"


def test_assert_transition_is_silent_on_legal_move():
    # Should not raise.
    assert_transition(S.INTENT_CREATED, S.VALIDATED)
    assert_transition(S.AUTHORIZED, S.PAYMENT_PENDING)


def test_can_transition_accepts_string_states():
    assert can_transition("INTENT_CREATED", "VALIDATED") is True
    assert can_transition("INTENT_CREATED", "COMPLETED") is False


def test_recovery_branches_exist():
    # A paid-but-unfulfilled order can reach refund; recovery can complete.
    assert can_transition(S.ORDER_FAILED, S.REFUND_REQUIRED)
    assert can_transition(S.REFUND_REQUIRED, S.COMPLETED)
    assert can_transition(S.PAYMENT_UNKNOWN, S.RECOVERY_PENDING)


def test_no_transition_target_is_outside_the_enum():
    known = set(S)
    for outgoing in ALLOWED_TRANSITIONS.values():
        assert outgoing <= known
