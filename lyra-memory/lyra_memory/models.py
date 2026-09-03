from __future__ import annotations
from typing import Literal
from pydantic import BaseModel


class WorkingMemoryItem(BaseModel):
    type: Literal["conversation", "observation", "reflection"]
    role: Literal["user", "lyra"] | None = None
    content: str
    score: float
    ts: float


class Episode(BaseModel):
    id: int | None = None
    content: str
    ts: float
    source_items_json: str


class Candidate(BaseModel):
    id: int | None = None
    trait_name: str
    trait_value: str
    evidence_count: int = 1
    last_seen: float
    # CP-F: "retrieval" added — CP-E's consolidate_retrieval_outcome() (in
    # lyra_ai/lyra_core/interface.py) has written candidates rows with
    # category="retrieval" since CP-D, but nothing ever read them back
    # through this model until IdentityEngine.consolidate() was wired to
    # the live daemon here — CandidatePool.get_candidates() validates every
    # `candidates` row into a Candidate, so a category this Literal doesn't
    # know about is not a retrieval-path bug, it is every candidate in the
    # store failing to load, dream-derived ones included. See DECISIONS.md
    # (CP-F, change 1).
    # CP-H: "repo_citation" added — the same finding CP-F made for
    # "retrieval", recurring: CognitiveCore.consolidate_repo_citation_
    # outcome() (interface.py) writes candidates rows with category=
    # "repo_citation", and CandidatePool.get_candidates() validates every
    # `candidates` row into a Candidate regardless of category, so an
    # unlisted category is not scoped to repo-citation candidates failing
    # to load — it is every candidate in the store failing to load the
    # moment one repo_citation row exists. See DECISIONS.md (CP-H, change 1).
    category: Literal["behavioral", "emotional", "relational", "cognitive", "retrieval", "repo_citation"]
    evidence_text: str | None = None


class Trait(BaseModel):
    id: int | None = None
    name: str
    value: str
    confidence: float
    stability: Literal["surface", "character", "core"]
    evidence_count: int
    updated_at: float


class Fact(BaseModel):
    key: str
    value: str
    updated_at: float
