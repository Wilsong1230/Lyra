"""Cold passes: everything that enriches the substrate after the fact.

Each runs when idle, is independently testable against a frozen `atoms` table,
and is re-derivable — a pass crashing leaves the store correct, because the
atoms it reads were permanent before it started and everything it writes is
derived.
"""
