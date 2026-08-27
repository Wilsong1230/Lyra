"""Fact extraction — exact, permanent, correctable.

Facts and atoms are two stores with different physics, and the split is what
makes forgetting safe. Atoms decay; facts never do. She forgets what a Tuesday
felt like. She does not forget where you go to school.

Shape: `subject` + free-form `text`, **no predicate column**. Most facts are
not triples — "prefers terse deliverables, corrects scope creep mid-session"
has no predicate — and a fixed predicate vocabulary is an authored schema, the
same trap as pre-authored traits. Subject is the key so matching is exact;
text is open so it can say anything.

**v1 detects contradictions and does not resolve them.** No `superseded_by` is
written and no `valid_until` is stamped. A suspected contradiction sets
`conflict_with` and stops; both rows stay live and both inject, newest first.

The asymmetry is the reason. Under-supersession is clutter — visible and
recoverable. Over-supersession silently erases true things — invisible and
unrecoverable. And the hard case is not contradiction but *refinement*:
"building Lyra" → "building Lyra's memory layer" is neither duplicate nor
contradiction, and that rule cannot be written before seeing real examples.
Write it after ~50 real flagged conflicts have accumulated, by reading them.
"""
from __future__ import annotations

import json
from datetime import datetime

from lyra_memory.config import (
    FACT_CONFIDENCE,
    FACT_CROWDED_SUBJECT,
    FACT_MAX_COMPARISONS,
)
from lyra_memory.store.passes.cold import ColdPass

_WATERMARK_KEY = "facts_watermark"

_EXTRACT_PROMPT = (
    "Read the exchange below and list any durable factual claims it contains "
    "about the world — things that would still be worth knowing next month.\n"
    "Return only a JSON list of objects, each with:\n"
    '  "subject" — the entity the claim is about, one or two words, lowercase\n'
    '  "text"    — the claim, in a short phrase\n'
    "Skip pleasantries, opinions about the current task, and anything that is "
    "only true right now. Return [] if there are none.\n\nExchange:\n"
)

_CONFLICT_PROMPT = (
    "Here is a new claim and a numbered list of existing claims about the same "
    "subject.\n"
    "Does the new claim CONTRADICT any of them — that is, can they not both be "
    "true of the same subject at the same time?\n"
    "A claim that merely adds detail, narrows, or updates without contradicting "
    "is NOT a contradiction.\n"
    "Answer with the single number of the contradicted claim, or the word none.\n\n"
)


def source_kind_for(speaker: str, source: str) -> str:
    """Which epistemic channel a claim arrived through.

    Decided here, from the atom, and never by the extracting model. "You told
    me" and "I read it in your resume" are different epistemic states, and a
    document that could nominate its own `source_kind` would be able to
    launder its authority into something she treats as stated.

    The document case is checked first for that reason: it does not matter who
    was speaking when a file was read, only that it came from a file.
    """
    if source == "sandbox_read":
        return "document"
    if speaker == "lyra":
        return "observed" if source in {"vision"} else "inferred"
    if speaker == "system":
        return "observed"
    return "stated"


class FactPass(ColdPass):
    name = "facts"

    def __init__(self, store, llm, backup_dir=None) -> None:
        super().__init__(store, backup_dir=backup_dir)
        self._llm = llm

    async def _watermark(self) -> int:
        async with self.store.db.execute(
            "SELECT value FROM schema_meta WHERE key = ?", (_WATERMARK_KEY,)
        ) as cur:
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def execute(self) -> int:
        watermark = await self._watermark()
        async with self.store.db.execute(
            "SELECT id, ts, speaker, source, text FROM atoms WHERE id > ? ORDER BY id",
            (watermark,),
        ) as cur:
            atoms = await cur.fetchall()
        if not atoms:
            return 0

        written = 0
        for atom_id, ts, speaker, source, text in atoms:
            raw = await self._llm(_EXTRACT_PROMPT + text)
            try:
                claims = json.loads(raw) or []
            except (json.JSONDecodeError, TypeError):
                claims = []

            for claim in claims:
                subject = str(claim.get("subject", "")).strip().lower()
                claim_text = str(claim.get("text", "")).strip()
                if not subject or not claim_text:
                    continue

                kind = source_kind_for(speaker, source)
                conflict_with = await self._find_conflict(subject, claim_text)
                await self.store.db.execute(
                    "INSERT INTO facts (ts, subject, text, source_atom_id, source_kind,"
                    " confidence, valid_from, valid_until, superseded_by, conflict_with)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)",
                    (ts, subject, claim_text, atom_id, kind,
                     FACT_CONFIDENCE[kind], ts, conflict_with),
                )
                written += 1

        await self.store.db.execute(
            "INSERT INTO schema_meta (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_WATERMARK_KEY, str(atoms[-1][0])),
        )
        await self.store.db.commit()

        stats = await self.instrumentation()
        print(f"[{datetime.now().isoformat()}] [facts] {written} written; "
              f"{stats['unresolved_conflicts']} unresolved conflicts")
        for subject in stats["crowded_subjects"]:
            print(f"[{datetime.now().isoformat()}] [facts] subject {subject!r} has "
                  f"{stats['facts_per_subject'][subject]} facts — consolidation "
                  f"may be under-firing")
        return written

    async def _find_conflict(self, subject: str, claim_text: str) -> int | None:
        """Compare only within the subject — tens of rows, not thousands."""
        async with self.store.db.execute(
            "SELECT id, text FROM facts WHERE subject = ? AND valid_until IS NULL"
            " ORDER BY ts DESC LIMIT ?",
            (subject, FACT_MAX_COMPARISONS),
        ) as cur:
            existing = await cur.fetchall()
        if not existing:
            return None

        listing = "\n".join(f"{n + 1}. {text}" for n, (_id, text) in enumerate(existing))
        answer = (await self._llm(
            f"{_CONFLICT_PROMPT}New claim: {claim_text}\n\nExisting claims:\n{listing}\n"
        ) or "").strip().lower()

        for token in answer.replace(".", " ").split():
            if token.isdigit() and 1 <= int(token) <= len(existing):
                return existing[int(token) - 1][0]
        return None

    async def instrumentation(self) -> dict:
        """Logged, not enforced.

        A subject past ~20 facts, or conflicts accumulating without
        resolution, means consolidation is under-firing. Neither blocks a
        write: redundancy about a frequently-discussed subject is not a defect
        — she has the most experience of Wilson, so she should have the most
        facts about him. What needs guarding is contradictory and unresolvable,
        not numerous.
        """
        async with self.store.db.execute(
            "SELECT subject, COUNT(*) FROM facts WHERE valid_until IS NULL"
            " GROUP BY subject"
        ) as cur:
            per_subject = {row[0]: row[1] for row in await cur.fetchall()}
        async with self.store.db.execute(
            "SELECT COUNT(*) FROM facts WHERE conflict_with IS NOT NULL"
        ) as cur:
            (conflicts,) = await cur.fetchone()

        return {
            "facts_per_subject": per_subject,
            "crowded_subjects": sorted(
                s for s, n in per_subject.items() if n >= FACT_CROWDED_SUBJECT),
            "unresolved_conflicts": conflicts,
        }
