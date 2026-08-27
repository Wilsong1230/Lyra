from __future__ import annotations
from typing import Literal
from pydantic import BaseModel


class Atom(BaseModel):
    """One turn, observation, or event. The substrate; permanent."""
    id: int | None = None
    ts: float
    speaker: Literal["wilson", "lyra", "system"]
    source: str
    text: str
    # Cold columns: nullable, batch-written, re-derivable.
    session_id: int | None = None
    salience: float | None = None
    outcome_id: int | None = None
    retrievability: float | None = None
    environment: str | None = None
    run_id: int | None = None


class WorkingMemoryItem(BaseModel):
    """In-process deque entry. Not persisted directly — atoms are."""
    type: Literal["conversation", "observation", "reflection"]
    role: Literal["user", "lyra"] | None = None
    content: str
    score: float
    ts: float


class Session(BaseModel):
    id: int | None = None
    started_ts: float
    ended_ts: float
    atom_count: int
    gap_since_prev: float | None = None


class Dream(BaseModel):
    id: int | None = None
    ts: float
    text: str
    session_id: int | None = None


class Fact(BaseModel):
    """Exact, permanent, correctable. Subject is the key; text is open."""
    id: int | None = None
    ts: float
    subject: str
    text: str
    source_atom_id: int | None = None
    source_kind: Literal["stated", "document", "inferred", "observed"]
    confidence: float | None = None
    valid_from: float
    valid_until: float | None = None
    superseded_by: int | None = None
    conflict_with: int | None = None


class Commitment(BaseModel):
    id: int | None = None
    created_ts: float
    source_atom_id: int
    text: str
    owner: Literal["lyra", "wilson"]
    due_ts: float | None = None
    status: Literal["open", "done", "dropped", "superseded"] = "open"
    closed_ts: float | None = None
    closed_atom_id: int | None = None


class Entity(BaseModel):
    id: int | None = None
    name: str
    kind: Literal["project", "person", "repo", "file", "topic"]
    first_seen: float
    last_seen: float


class OutcomeRow(BaseModel):
    id: int | None = None
    intent_atom_id: int | None = None
    predicted: str
    actual: str
    valence: float | None = None
    ts: float
    environment: str | None = None


class TraitHistoryRow(BaseModel):
    id: int | None = None
    ts: float
    trait_id: int | None = None
    trait_label: str
    event: Literal["promoted", "confidence_change", "tier_change", "decayed", "retired"]
    conf_before: float | None = None
    conf_after: float | None = None
    tier_before: str | None = None
    tier_after: str | None = None
    evidence_count: int | None = None
    dream_id: int | None = None


class Candidate(BaseModel):
    id: int | None = None
    trait_name: str
    trait_value: str
    evidence_count: int = 1
    last_seen: float
    category: Literal["behavioral", "emotional", "relational", "cognitive"]


class Trait(BaseModel):
    id: int | None = None
    name: str
    value: str
    confidence: float
    stability: Literal["surface", "character", "core"]
    evidence_count: int
    updated_at: float
