"""lyra_memory.facts — exact, permanent, correctable.

Two stores, different physics. The split is what makes forgetting safe:

              facts                      atoms
  retrieval   exact match on subject     KNN / BM25 / recency
  decay       never                      yes
  correction  supersession               none needed
  volume      low                        high

World knowledge ("grass is green") is not stored at all — it is model weights
and is not at risk. What IS at risk is facts about Wilson's world: repo names,
school, which project is live. Routing those out of the decay pool is upstream
of the decay function; no exemption logic is needed because they are not in the
pool.

SHAPE: `subject` + free-form `text`. No predicate column. Strict triples were
rejected because most facts are not triples ("prefers terse deliverables,
corrects scope creep mid-session" has no predicate) and a fixed predicate
vocabulary is an authored schema — the same trap as pre-authored traits. Fully
loose was rejected too: with no key, matching falls back to embedding
similarity, which is the trait-dedup bug again.

SUPERSESSION — DETECT, DO NOT RESOLVE. v1 writes no `superseded_by` and stamps
no `valid_until`. On a suspected contradiction it writes `conflict_with` and
stops. Both rows stay live and retrieval injects both, newest first; an LLM
handles "was at FGCU / more recently graduated" without help.

The asymmetry is why. Under-supersession is clutter: visible, recoverable.
Over-supersession silently erases true things: invisible, unrecoverable. And
the hard case is not contradiction but REFINEMENT — "building Lyra" ->
"building Lyra's memory layer" is neither duplicate nor contradiction. That
rule cannot be written before seeing real examples.
"""
from __future__ import annotations

import re
import time

import aiosqlite

from lyra_memory.config import (
    CONFLICT_AUTOMATION_GATE,
    DISPOSITIONALLY_INERT_SOURCE_KINDS,
    FACT_SOURCE_KINDS,
    FACTS_PER_SUBJECT_WARN,
)

_COLS = (
    "id, ts, subject, text, source_atom_id, source_kind, confidence,"
    " valid_from, valid_until, superseded_by, conflict_with"
)


def normalize_subject(subject: str) -> str:
    """Subject is the key, so it has to be stable. Joins entities(name) where
    possible, which is why it is lowercased and whitespace-collapsed rather than
    slugged — entity names keep their internal punctuation."""
    return re.sub(r"\s+", " ", subject.strip().lower())


class FactStore:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def add(
        self,
        subject: str,
        text: str,
        *,
        source_kind: str,
        source_atom_id: int | None = None,
        confidence: float | None = None,
        ts: float | None = None,
        conflict_with: int | None = None,
    ) -> int:
        """Write one fact. Never updates or deletes an existing row.

        `source_kind` is load-bearing: "you told me" and "I read it in your
        resume" are different epistemic states and the store holds the
        difference. Discovery-by-reading is the same channel as the sandbox
        injection risk — a document can assert anything — so document-sourced
        facts are attributable and carry lower confidence by construction.
        """
        if source_kind not in FACT_SOURCE_KINDS:
            raise ValueError(
                f"unknown source_kind {source_kind!r}; expected one of {sorted(FACT_SOURCE_KINDS)}"
            )
        ts = time.time() if ts is None else ts
        cur = await self._conn.execute(
            "INSERT INTO facts (ts, subject, text, source_atom_id, source_kind,"
            " confidence, valid_from, valid_until, superseded_by, conflict_with)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)",
            (
                ts, normalize_subject(subject), text, source_atom_id, source_kind,
                confidence, ts, conflict_with,
            ),
        )
        await self._conn.commit()
        return cur.lastrowid

    async def get(self, subject: str) -> list[dict]:
        """Current facts for a subject. Exact match, not KNN.

        `valid_until IS NULL`, newest first. Conflicted pairs both inject —
        v1 does not choose between them.
        """
        async with self._conn.execute(
            f"SELECT {_COLS} FROM facts WHERE subject = ? AND valid_until IS NULL"
            " ORDER BY ts DESC",
            (normalize_subject(subject),),
        ) as cur:
            rows = await cur.fetchall()
        return [_row(r) for r in rows]

    async def for_query(self, query: str) -> list[dict]:
        """Facts whose subject is mentioned in the turn.

        Deterministic and cheap: it does not consume recall budget and does not
        compete with the semantic pool. Subjects are matched as whole tokens so
        "go" does not match "google".
        """
        if not query:
            return []
        async with self._conn.execute(
            "SELECT DISTINCT subject FROM facts WHERE valid_until IS NULL"
        ) as cur:
            subjects = [r[0] for r in await cur.fetchall()]
        if not subjects:
            return []

        haystack = normalize_subject(query)
        matched = [
            s for s in subjects
            if s and re.search(rf"(?<!\w){re.escape(s)}(?!\w)", haystack)
        ]
        out: list[dict] = []
        for s in matched:
            out.extend(await self.get(s))
        out.sort(key=lambda f: -f["ts"])
        return out

    async def flag_conflict(self, fact_id: int, conflicts_with_id: int) -> None:
        """Record a SUSPECTED contradiction. Both rows stay live.

        This is the only write supersession detection makes in v1. It is written
        by her dream pass, not by Wilson — everything downstream, including
        eventual auto-resolution, is her machinery operating on her own store.
        """
        await self._conn.execute(
            "UPDATE facts SET conflict_with = ? WHERE id = ?", (conflicts_with_id, fact_id)
        )
        await self._conn.commit()

    async def unresolved_conflicts(self) -> int:
        async with self._conn.execute(
            "SELECT COUNT(*) FROM facts WHERE conflict_with IS NOT NULL"
            " AND valid_until IS NULL"
        ) as cur:
            return (await cur.fetchone())[0]

    async def instrumentation(self) -> dict:
        """Logged, not enforced.

        A subject exceeding ~20, or conflicts accumulating without resolution,
        means consolidation is under-firing. Redundancy about frequently
        discussed subjects is NOT itself a defect — she has the most experience
        of Wilson, so she should have the most facts about him. The failure to
        guard is contradictory-and-unresolvable, not numerous.
        """
        async with self._conn.execute(
            "SELECT subject, COUNT(*) c FROM facts WHERE valid_until IS NULL"
            " GROUP BY subject ORDER BY c DESC"
        ) as cur:
            per_subject = [{"subject": r[0], "count": r[1]} for r in await cur.fetchall()]
        conflicts = await self.unresolved_conflicts()
        return {
            "per_subject": per_subject,
            "crowded_subjects": [s for s in per_subject if s["count"] > FACTS_PER_SUBJECT_WARN],
            "unresolved_conflicts": conflicts,
            # Spec "Automation gate": write the resolution rule after ~50 real
            # flagged conflicts have accumulated. Read them to learn what the
            # rules actually are, not to edit rows.
            "automation_gate_reached": conflicts >= CONFLICT_AUTOMATION_GATE,
        }


def may_shape_disposition(source_kind: str) -> bool:
    """Documents produce facts, never traits or persona.

    The cut is DISPOSITIONAL, not subject-based. A subject filter ("nothing
    about Lyra") is too blunt — "the harm gate is default-deny" is a fact about
    her that is checkable against code and fine to read, while "Wilson prefers
    terse deliverables" is about Wilson but shapes how she behaves. Subject does
    not separate them; kind does.

    A file must never be able to assert what she is.
    """
    return source_kind not in DISPOSITIONALLY_INERT_SOURCE_KINDS


def _row(r) -> dict:
    return {
        "id": r[0], "ts": r[1], "subject": r[2], "text": r[3],
        "source_atom_id": r[4], "source_kind": r[5], "confidence": r[6],
        "valid_from": r[7], "valid_until": r[8], "superseded_by": r[9],
        "conflict_with": r[10],
    }
