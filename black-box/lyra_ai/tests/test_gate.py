"""Tests for the pure harm gate — intent-in, allow/block decision out.

Structural assertions (signature, purity, allow-list shape) are the
boundary written as executable proofs: they fail the moment a motivational
parameter is added or the allow-list silently widens.
"""
from __future__ import annotations

import inspect

import pytest

from lyra_core.gate import ALLOWED_KINDS, GateDecision, HarmGate
from lyra_core.interface import Intent, IntentKind


# ── Helpers ───────────────────────────────────────────────────────────────────

def _intent(kind: IntentKind) -> Intent:
    return Intent(kind=kind, payload={})


# ── Allowed kinds — speak, set_state, look, noop ──────────────────────────────

@pytest.mark.parametrize("kind", [
    IntentKind.speak,
    IntentKind.set_state,
    IntentKind.look,
    IntentKind.noop,
])
def test_allowed_kind_is_permitted(kind):
    gate = HarmGate()
    decision = gate.check(_intent(kind))
    assert decision.allowed is True


@pytest.mark.parametrize("kind", [
    IntentKind.speak,
    IntentKind.set_state,
    IntentKind.look,
    IntentKind.noop,
])
def test_allowed_kind_has_empty_reason(kind):
    gate = HarmGate()
    decision = gate.check(_intent(kind))
    assert decision.reason == ""


# ── Blocked kinds — research and anything not allow-listed ────────────────────

def test_research_is_blocked():
    gate = HarmGate()
    decision = gate.check(_intent(IntentKind.research))
    assert decision.allowed is False


def test_research_reason_names_the_kind():
    gate = HarmGate()
    decision = gate.check(_intent(IntentKind.research))
    assert "research" in decision.reason


def test_every_non_allowed_kind_is_blocked():
    """Covers all IntentKinds not in ALLOWED_KINDS (research now; others later)."""
    gate = HarmGate()
    for kind in IntentKind:
        if kind in ALLOWED_KINDS:
            continue
        d = gate.check(_intent(kind))
        assert d.allowed is False, f"{kind!r} should be blocked but was allowed"
        assert kind.value in d.reason, f"reason for {kind!r} must name the kind"


# ── Allow-list shape ──────────────────────────────────────────────────────────

def test_allow_list_is_exactly_the_four_safe_kinds():
    """Adding a new IntentKind without updating ALLOWED_KINDS must fail here.

    This is the tripwire: new capability + no deliberate allow-list edit = red.
    """
    expected = frozenset({
        IntentKind.speak,
        IntentKind.set_state,
        IntentKind.look,
        IntentKind.noop,
    })
    assert ALLOWED_KINDS == expected


# ── Determinism ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("kind", list(IntentKind))
def test_same_intent_produces_identical_decisions(kind):
    gate = HarmGate()
    intent = _intent(kind)
    d1 = gate.check(intent)
    d2 = gate.check(intent)
    assert d1.allowed == d2.allowed
    assert d1.reason == d2.reason


# ── Signature boundary — no motivational parameters ───────────────────────────

def test_check_signature_is_self_plus_intent_only():
    """Executable proof that no motivational parameter exists on check().

    This test is the architectural boundary made into an assertion:
    affect, drives, and urgency must never appear in this signature.
    """
    sig = inspect.signature(HarmGate.check)
    assert list(sig.parameters.keys()) == ["self", "intent"]


# ── Purity ────────────────────────────────────────────────────────────────────

def test_check_does_not_mutate_the_allow_list():
    gate = HarmGate()
    snapshot = frozenset(ALLOWED_KINDS)
    for kind in IntentKind:
        gate.check(_intent(kind))
    assert frozenset(ALLOWED_KINDS) == snapshot


def test_two_gate_instances_produce_identical_decisions():
    g1, g2 = HarmGate(), HarmGate()
    for kind in IntentKind:
        intent = _intent(kind)
        assert g1.check(intent).allowed == g2.check(intent).allowed
        assert g1.check(intent).reason == g2.check(intent).reason
