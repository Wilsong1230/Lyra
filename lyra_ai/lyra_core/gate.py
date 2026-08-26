"""lyra_core.gate — pure harm gate.

The single entry point is HarmGate.check(intent).  It takes an Intent and
NOTHING ELSE.  This is deliberate and load-bearing: the gate cannot be
outvoted by motivational state (affect, drives, urgency) because motivational
state is not in its signature.  The boundary is structural, not a promise.

Default-deny rationale: a capability that doesn't exist yet must NOT pass
just because no rule forbids it.  New IntentKinds get EXPLICITLY added to
ALLOWED_KINDS when the capability is built and reviewed — never permitted by
omission.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from lyra_core.interface import Intent, IntentKind


# Extend this set only after explicit review of the new capability.
# Adding a kind here without a corresponding gate review is the mistake
# this structure is designed to make visible.
ALLOWED_KINDS: frozenset[IntentKind] = frozenset({
    IntentKind.speak,
    IntentKind.set_state,
    IntentKind.look,
    IntentKind.noop,
})


@dataclass
class GateDecision:
    allowed: bool
    reason: str = ""


class HarmGate:
    """Hard filter between action-selection and execution.

    check() is the only entry point.  Its signature accepts Intent and
    nothing else — no affect, no drives, no context — so the gate cannot
    be outvoted by how badly the action was wanted.  This is deliberate
    and load-bearing: do not add motivational parameters now or ever.
    """

    def check(self, intent: Intent) -> GateDecision:
        if intent.kind in ALLOWED_KINDS:
            return GateDecision(allowed=True)
        return GateDecision(
            allowed=False,
            reason=f"intent kind {intent.kind.value!r} is not in the allow-list",
        )
