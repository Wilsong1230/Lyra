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
    category: Literal["behavioral", "emotional", "relational", "cognitive"]
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
