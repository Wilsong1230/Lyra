"""Context assembly: what she is looking at when she answers.

Five blocks in a pinned order, each with a token budget and hard truncation.
Position is load-bearing — models weight the start and the end of context
most — so stable identity sits at the top, the live conversation at the end,
and retrieved material in the middle where it informs without dominating.

    1 facts        200   subject-matched, exact, never KNN
    2 traits       100   confidence >= 0.3
    3 commitments  100   open only; the one store that surfaces unprompted
    4 recall       400   three paths merged, deduped, synthesized
    5 recent       rest  8-10 turns verbatim

Nothing here is guarded. A broken store must not read as an empty one. The
single exception is the synthesis call, which the spec explicitly requires to
fall back to concatenation rather than block a turn — see `_synthesize_recall`.
"""
from __future__ import annotations

import math
import re
import struct
import time
from dataclasses import dataclass, field

from lyra_memory.config import (
    COMMITMENT_INJECT_LIMIT,
    CONTEXT_BUDGETS,
    CONTEXT_TOTAL_BUDGET,
    EMBED_DIM,
    LEXICAL_LIMIT,
    RECALL_DEDUP_COSINE,
    RECENT_TURNS,
    RRF_K,
    SEMANTIC_FETCH_K,
    SEMANTIC_SIMILARITY_FLOOR,
    SPEAKER_RECALL_WEIGHT,
    TEMPORAL_LIMIT,
    TRAIT_CONFIDENCE_FLOOR,
)
from lyra_memory.embeddings import embed

BLOCK_ORDER = ["facts", "traits", "commitments", "recall", "recent"]

_HEADINGS = {
    "facts": "## Facts",
    "traits": "## Traits",
    "commitments": "## Open commitments",
    "recall": "## Recall",
    "recent": "## Recent",
}

_WORD_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./-]*")

# Stopwords for the LEXICAL path only.
#
# BM25 exists here to catch selective tokens — repo names, filenames, proper
# nouns. OR-ing every token of a natural question instead matches the whole
# store on "the / did / we / about", and BM25 then ranks essentially at
# random. Measured: "what did we decide about the harm gate in the combat
# sandbox", a question this corpus cannot answer, pulled nine unrelated atoms
# into recall purely on stopword matches.
#
# This is a list about term selectivity, not about meaning — it never touches
# the semantic path, and it decides nothing about what is relevant. The better
# version is a document-frequency cutoff measured from the index itself; that
# needs a store large enough to measure against, so it is deferred rather than
# guessed at. See DECISIONS.md.
_STOPWORDS = frozenset("""
a about above after again against all am an and any are as at be because been
before being below between both but by can cannot could did do does doing
down during each few for from further had has have having he her here hers
him his how i if in into is it its itself just me more most my no nor not of
off on once only or other our out over own same she should so some such than
that the their them then there these they this those through to too under
until up very was we were what when where which while who whom why will with
would you your
""".split())


def estimate_tokens(text: str) -> int:
    """Rough token count: ~4 characters per token.

    The sheet budgets in bytes precisely so that this does not need a
    tokenizer on the hot path. The approximation only has to be stable and
    slightly conservative — it bounds a budget, it does not price a call.
    """
    if not text:
        return 0
    return math.ceil(len(text) / 4)


def _truncate_to_budget(lines: list[str], budget: int) -> str:
    """Fill a block line by line and stop. Hard truncation, whole lines only —
    half a fact is worse than no fact."""
    kept: list[str] = []
    used = 0
    for line in lines:
        cost = estimate_tokens(line + "\n")
        if used + cost > budget:
            break
        kept.append(line)
        used += cost
    return "\n".join(kept)


def _unpack(raw: bytes) -> list[float]:
    return list(struct.unpack(f"{EMBED_DIM}f", raw))


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


@dataclass
class Hit:
    atom_id: int
    text: str
    ts: float
    speaker: str
    score: float = 0.0
    paths: set[str] = field(default_factory=set)


@dataclass
class ContextResult:
    text: str
    blocks: dict[str, str]
    atom_ids: list[int]
    fact_ids: list[int]
    dream_ids: list[int]
    budget_used: int
    misses: list[str]

    def as_log_row(self) -> dict:
        return {
            "atom_ids": self.atom_ids,
            "fact_ids": self.fact_ids,
            "dream_ids": self.dream_ids,
            "budget_used": self.budget_used,
            "misses": self.misses,
        }


# ── block 1: facts ───────────────────────────────────────────────────────────

async def _facts_block(store, query: str, misses: list[str]) -> tuple[str, list[int]]:
    """Exact match on subject. Deterministic, cheap, and it does not consume
    recall budget.

    `WHERE valid_until IS NULL`, newest first. Conflicted pairs both inject —
    v1 detects contradictions and does not resolve them, and an LLM handles
    "was at FGCU / more recently graduated" without help.
    """
    subjects = {w.lower() for w in _WORD_RE.findall(query or "")
                if w.lower() not in _STOPWORDS}
    if not subjects:
        return "", []

    placeholders = ",".join("?" * len(subjects))
    async with store.db.execute(
        f"SELECT id, subject, text, source_kind FROM facts"
        f" WHERE valid_until IS NULL AND lower(subject) IN ({placeholders})"
        f" ORDER BY ts DESC, id DESC",
        tuple(subjects),
    ) as cur:
        rows = await cur.fetchall()

    if not rows:
        misses.append(f"facts: no subject in {sorted(subjects)[:8]} has facts")
        return "", []

    lines = [f"- {subject}: {text} [{kind}]" for _id, subject, text, kind in rows]
    block = _truncate_to_budget(lines, CONTEXT_BUDGETS["facts"])
    kept = [r[0] for r, line in zip(rows, lines) if line in block]
    return block, kept


# ── block 2: traits ──────────────────────────────────────────────────────────

async def _traits_block(store) -> str:
    async with store.db.execute(
        "SELECT name, value, confidence, stability FROM traits"
        " WHERE confidence >= ? ORDER BY confidence DESC",
        (TRAIT_CONFIDENCE_FLOOR,),
    ) as cur:
        rows = await cur.fetchall()
    lines = [f"- {name}: {value}" for name, value, _c, _s in rows]
    return _truncate_to_budget(lines, CONTEXT_BUDGETS["traits"])


# ── block 3: commitments ─────────────────────────────────────────────────────

async def _commitments_block(store) -> str:
    """Not KNN. A direct query, injected whenever the count is nonzero.

    Everything else is retrieved on relevance; open loops assert themselves.
    Truncated to the soonest-due and the oldest when there are many, so a
    backlog cannot crowd out the block.
    """
    async with store.db.execute(
        "SELECT id, text, owner, due_ts, created_ts FROM commitments"
        " WHERE status = 'open'"
        " ORDER BY due_ts IS NULL, due_ts ASC, created_ts ASC"
    ) as cur:
        rows = await cur.fetchall()
    if not rows:
        return ""

    if len(rows) > COMMITMENT_INJECT_LIMIT:
        # Soonest-due first (the query's own order), plus the oldest by age —
        # the loop most at risk of being quietly forgotten.
        oldest = min(rows, key=lambda r: r[4])
        selected = rows[: COMMITMENT_INJECT_LIMIT - 1]
        if oldest not in selected:
            selected = selected + [oldest]
    else:
        selected = rows

    lines = []
    for _id, text, owner, due_ts, _created in selected:
        due = f" (due {time.strftime('%Y-%m-%d', time.localtime(due_ts))})" if due_ts else ""
        lines.append(f"- [{owner}] {text}{due}")
    return _truncate_to_budget(lines, CONTEXT_BUDGETS["commitments"])


# ── block 4: recall — three paths, one merge ─────────────────────────────────

async def _semantic_hits(store, query_vec: bytes, misses: list[str]) -> list[Hit]:
    """vec KNN, filtered by a similarity FLOOR rather than a rank cutoff.

    k here is a fetch bound, not a relevance judgement: with a rank cutoff
    alone, a near-empty store confidently returns its nearest garbage.
    """
    async with store.db.execute(
        "SELECT v.rowid, v.distance, a.text, a.ts, a.speaker"
        " FROM vec_atoms v JOIN atoms a ON a.id = v.rowid"
        " WHERE v.embedding MATCH ? AND k = ? ORDER BY v.distance",
        (query_vec, SEMANTIC_FETCH_K),
    ) as cur:
        rows = await cur.fetchall()

    hits = []
    for atom_id, distance, text, ts, speaker in rows:
        # normalized vectors: cos = 1 - L2²/2
        similarity = 1.0 - (distance * distance) / 2.0
        if similarity < SEMANTIC_SIMILARITY_FLOOR:
            continue
        hits.append(Hit(atom_id, text, ts, speaker, paths={"semantic"}))

    if not hits:
        misses.append(
            f"semantic: nothing above similarity floor {SEMANTIC_SIMILARITY_FLOOR}")
    return hits


async def _lexical_hits(store, query: str, misses: list[str]) -> list[Hit]:
    """FTS5/BM25 — catches repo names, filenames, and proper nouns that
    embeddings blur into their neighbourhood."""
    tokens = [t for t in _WORD_RE.findall(query or "")
              if len(t) > 1 and t.lower() not in _STOPWORDS]
    if not tokens:
        misses.append("lexical: query had no selective tokens")
        return []

    # Quote each token so FTS5 treats '/' and '.' as literal rather than
    # syntax, and OR them: BM25 does the ranking.
    match = " OR ".join(f'"{t}"' for t in tokens)
    async with store.db.execute(
        "SELECT f.rowid, a.text, a.ts, a.speaker FROM atoms_fts f"
        " JOIN atoms a ON a.id = f.rowid"
        " WHERE atoms_fts MATCH ? ORDER BY bm25(atoms_fts) LIMIT ?",
        (match, LEXICAL_LIMIT),
    ) as cur:
        rows = await cur.fetchall()

    if not rows:
        misses.append("lexical: no BM25 match")
    return [Hit(r[0], r[1], r[2], r[3], paths={"lexical"}) for r in rows]


async def _temporal_hits(store, exclude: set[int]) -> list[Hit]:
    """Plain SQL recency, as a separate pool.

    Deliberately not part of the KNN contest: recency is its own reason to
    surface, and making it compete on distance means it never wins.
    """
    async with store.db.execute(
        "SELECT id, text, ts, speaker FROM atoms ORDER BY ts DESC LIMIT ?",
        (TEMPORAL_LIMIT + len(exclude),),
    ) as cur:
        rows = await cur.fetchall()
    return [Hit(r[0], r[1], r[2], r[3], paths={"temporal"})
            for r in rows if r[0] not in exclude][:TEMPORAL_LIMIT]


def _fuse(pools: list[list[Hit]]) -> list[Hit]:
    """Reciprocal rank fusion across the three paths.

    RRF because the three scores do not share a scale — a cosine similarity,
    a BM25 score, and a timestamp cannot be added together, and inventing a
    calibration between them would be a tuning constant pretending to be a
    fact. Rank is the only thing they have in common.

    Her own turns are down-weighted here, where the merge happens.
    """
    merged: dict[int, Hit] = {}
    for pool in pools:
        for rank, hit in enumerate(pool):
            existing = merged.get(hit.atom_id)
            if existing is None:
                existing = Hit(hit.atom_id, hit.text, hit.ts, hit.speaker)
                merged[hit.atom_id] = existing
            existing.paths |= hit.paths
            existing.score += 1.0 / (RRF_K + rank + 1)

    for hit in merged.values():
        hit.score *= SPEAKER_RECALL_WEIGHT.get(hit.speaker, 1.0)

    return sorted(merged.values(), key=lambda h: (-h.score, -h.ts))


async def _dedupe(store, hits: list[Hit]) -> list[Hit]:
    """Drop a hit that is a near-duplicate of one already selected.

    Five retrieved fragments that say the same thing read as one thing said
    five times, and they cost five times the budget.
    """
    if not hits:
        return []

    ids = [h.atom_id for h in hits]
    placeholders = ",".join("?" * len(ids))
    async with store.db.execute(
        f"SELECT rowid, embedding FROM vec_atoms WHERE rowid IN ({placeholders})",
        tuple(ids),
    ) as cur:
        vectors = {row[0]: _unpack(row[1]) for row in await cur.fetchall()}

    selected: list[Hit] = []
    selected_vecs: list[list[float]] = []
    for hit in hits:
        vec = vectors.get(hit.atom_id)
        if vec is not None and any(
            _cosine(vec, chosen) >= RECALL_DEDUP_COSINE for chosen in selected_vecs
        ):
            continue
        selected.append(hit)
        if vec is not None:
            selected_vecs.append(vec)
    return selected


async def _synthesize_recall(hits: list[Hit], synthesize, misses: list[str]) -> str:
    """One synthesized paragraph, not five pasted fragments.

    Five retrieved fragments read as disjointed notes; one paragraph reads as
    memory. It costs an extra call on the hot path and changes how she reads
    more than any schema decision.

    This is the one guarded call in context assembly. The spec is explicit
    that synthesis "falls back to concatenation if the synthesis call fails.
    Never blocks a turn" — and unlike a store failure, a synthesis failure is
    an unreachable external model, not a memory that is silently wrong. The
    guard is as narrow as it can be, wraps nothing else, and records a miss so
    the fallback is visible in the log rather than inferred from tone.
    """
    concatenated = "\n".join(f"- {h.text}" for h in hits)
    if synthesize is None or not hits:
        return concatenated

    prompt = (
        "Summarize these recalled fragments as a single short paragraph in "
        "plain past tense. Do not add anything that is not in them.\n\n"
        + concatenated
    )
    try:
        result = await synthesize(prompt)
    except Exception as exc:  # noqa: BLE001 - explicitly specified fallback
        misses.append(f"synthesis: failed ({type(exc).__name__}: {exc}); concatenated")
        return concatenated
    if not result or not result.strip():
        misses.append("synthesis: returned nothing; concatenated")
        return concatenated
    return result.strip()


async def _recall_block(
    store, query: str, query_vec: bytes, synthesize, exclude: set[int],
    misses: list[str],
) -> tuple[str, list[int]]:
    semantic = await _semantic_hits(store, query_vec, misses)
    lexical = await _lexical_hits(store, query, misses)

    # Temporal is a companion pool, not a source of recall on its own. It
    # exists so that a recent atom can surface alongside relevant ones without
    # having to win a nearest-neighbour contest — not so that a query the
    # store cannot answer still comes back with something.
    #
    # Measured: with temporal ungated, both "answerable: no" turns in the
    # baseline set had material injected anyway (spurious injection 1.00).
    # Presenting the last ten unrelated turns under a "Recall" heading is
    # worse than presenting nothing: it reads as remembering.
    if semantic or lexical:
        temporal = await _temporal_hits(store, exclude)
    else:
        temporal = []
        misses.append("recall: no relevant hit on any path; temporal pool withheld")

    fused = _fuse([semantic, lexical, temporal])
    fused = [h for h in fused if h.atom_id not in exclude]
    deduped = await _dedupe(store, fused)

    # Take only what the budget can hold before paying for a synthesis call
    # over material that would have been truncated away anyway.
    budget = CONTEXT_BUDGETS["recall"]
    selected: list[Hit] = []
    used = 0
    for hit in deduped:
        cost = estimate_tokens(f"- {hit.text}\n")
        if used + cost > budget:
            break
        selected.append(hit)
        used += cost

    if not selected:
        return "", []

    text = await _synthesize_recall(selected, synthesize, misses)
    # Synthesis output is budgeted too: a model that ignores "short" must not
    # be able to spend the whole context.
    text = _truncate_to_budget(text.split("\n"), budget)
    return text, [h.atom_id for h in selected]


# ── block 5: recent ──────────────────────────────────────────────────────────

async def _recent_block(store, turns: int, budget: int) -> tuple[str, list[int]]:
    if turns <= 0 or budget <= 0:
        return "", []
    async with store.db.execute(
        "SELECT id, speaker, text FROM atoms ORDER BY ts DESC, id DESC LIMIT ?",
        (turns * 2,),
    ) as cur:
        rows = list(reversed(await cur.fetchall()))

    lines = [f"{speaker}: {text}" for _id, speaker, text in rows]
    block = _truncate_to_budget(lines, budget)
    kept = [r[0] for r, line in zip(rows, lines) if line in block]
    return block, kept


# ── assembly ─────────────────────────────────────────────────────────────────

async def build_context(
    store,
    query: str,
    query_vec: bytes | None = None,
    synthesize=None,
    recent_turns: int = RECENT_TURNS,
    total_budget: int = CONTEXT_TOTAL_BUDGET,
) -> ContextResult:
    """Assemble one turn's context.

    `query_vec` lets the caller pass the embedding it already computed — the
    hot path embeds the incoming text once and uses it for retrieval here and
    for the atom it appends afterwards.
    """
    misses: list[str] = []
    if query_vec is None:
        query_vec = await embed(query or "")

    facts, fact_ids = await _facts_block(store, query, misses)
    traits = await _traits_block(store)
    commitments = await _commitments_block(store)

    # The recent block is verbatim; recall must not spend its budget repeating
    # what is about to appear in full a few lines later.
    recent_budget = total_budget - sum(
        estimate_tokens(b) for b in (facts, traits, commitments))
    recent, recent_ids = await _recent_block(store, recent_turns, recent_budget)

    recall, recall_ids = await _recall_block(
        store, query, query_vec, synthesize, set(recent_ids), misses)

    blocks = {
        "facts": facts, "traits": traits, "commitments": commitments,
        "recall": recall, "recent": recent,
    }
    rendered = [
        f"{_HEADINGS[name]}\n{blocks[name]}" for name in BLOCK_ORDER if blocks[name]
    ]
    text = "\n\n".join(rendered)

    return ContextResult(
        text=text,
        blocks=blocks,
        atom_ids=recall_ids + recent_ids,
        fact_ids=fact_ids,
        dream_ids=[],
        budget_used=estimate_tokens(text),
        misses=misses,
    )
