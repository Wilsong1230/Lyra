"""lyra_memory.retrieval — context assembly.

Three paths, one merge, a pinned order, and byte budgets.

POSITION IS LOAD-BEARING. Models weight the start and end of context most, so
the block order below is pinned rather than incidental: stable identity at the
top, live conversation at the end, retrieved material in the middle where it
informs without dominating.

    1. facts        subject-matched, exact          ~200 tok
    2. traits       conf >= 0.3                     ~100 tok
    3. commitments  open, if any                    ~100 tok
    4. recall       synthesized                     ~400 tok
    5. recent       working-memory deque, verbatim  remainder

Budget is BYTES, not k. Hard truncation. Total non-conversation: <1500 tok.

FAILURE POLICY: build_context does NOT swallow exceptions. Fail-open makes a
broken store and an empty store behaviourally identical, which for an emergence
thesis is undetectable from transcripts. The single exception is the synthesis
call, which falls back to concatenation by design and must never block a turn.
"""
from __future__ import annotations

import logging
import math
import struct
import time
from pathlib import Path

import aiosqlite

from lyra_memory import config
from lyra_memory.config import (
    BLOCK_ORDER,
    BYTES_PER_TOKEN,
    CONTEXT_BUDGET_BYTES,
    LEXICAL_POOL_LIMIT,
    LYRA_RECALL_WEIGHT,
    NON_CONVERSATION_BUDGET_BYTES,
    RECALL_DEDUPE_SIMILARITY,
    RECALL_RESULT_LIMIT,
    RECENT_TURNS,
    SEARCH_ATOMS_DEFAULT_LIMIT,
    SEMANTIC_POOL_LIMIT,
    SEMANTIC_SIMILARITY_FLOOR,
    TEMPORAL_POOL_LIMIT,
)
from lyra_memory.db import load_vec_extension
from lyra_memory.embeddings import embed

_log = logging.getLogger(__name__)


# ── Byte budgeting ───────────────────────────────────────────────────────────

def truncate_to_bytes(text: str, budget: int) -> str:
    """Hard truncation at a byte budget, never splitting a UTF-8 sequence."""
    raw = text.encode("utf-8")
    if len(raw) <= budget:
        return text
    return raw[:budget].decode("utf-8", errors="ignore")


def _lines_within_budget(lines: list[str], budget: int) -> list[str]:
    """Take whole lines while they fit. Partial lines read as corruption."""
    out: list[str] = []
    used = 0
    for line in lines:
        cost = len(line.encode("utf-8")) + 1
        if used + cost > budget:
            break
        out.append(line)
        used += cost
    return out


# ── The three paths ──────────────────────────────────────────────────────────

def _similarity_from_l2(distance: float) -> float:
    """sqlite-vec returns L2 over normalised vectors: cos = 1 - d^2 / 2."""
    return 1.0 - (distance * distance) / 2.0


_ATOM_COLS = (
    "a.id, a.ts, a.speaker, a.source, a.text, a.session_id, a.salience"
)


def _hit(row, score: float, path: str) -> dict:
    return {
        "id": row[0], "ts": row[1], "speaker": row[2], "source": row[3],
        "text": row[4], "session_id": row[5], "salience": row[6],
        "score": score, "path": path,
    }


async def semantic_path(
    conn: aiosqlite.Connection, query_vec: bytes, limit: int = SEMANTIC_POOL_LIMIT
) -> list[dict]:
    """vec KNN with a SIMILARITY FLOOR, not a rank cutoff.

    A rank cutoff always returns k results, however bad; ten weak hits crowd out
    two good ones and read as free association. The floor lets the pool come
    back empty, which is a MISS and gets logged as one.
    """
    try:
        async with conn.execute(
            f"SELECT {_ATOM_COLS}, v.distance FROM vec_atoms v "
            "JOIN atoms a ON a.id = v.rowid "
            "WHERE v.embedding MATCH ? AND k = ? ORDER BY v.distance",
            (query_vec, limit),
        ) as cur:
            rows = await cur.fetchall()
    except Exception as exc:
        # sqlite-vec raises when the index has no segment yet (empty store).
        import sqlite3
        underlying = exc.__cause__ if exc.__cause__ is not None else exc
        if not isinstance(underlying, (sqlite3.OperationalError, sqlite3.DatabaseError)):
            raise
        return []

    hits = []
    for row in rows:
        sim = _similarity_from_l2(row[-1])
        if sim < SEMANTIC_SIMILARITY_FLOOR:
            continue
        hits.append(_hit(row, sim, "semantic"))
    return hits


_FTS_SPECIAL = str.maketrans({c: " " for c in '"*():^-'})


async def lexical_path(
    conn: aiosqlite.Connection, query: str, limit: int = LEXICAL_POOL_LIMIT
) -> list[dict]:
    """FTS5/BM25. Catches repo names, filenames, proper nouns — the tokens an
    embedding blurs into their neighbourhood and KNN therefore misses."""
    terms = [t for t in query.translate(_FTS_SPECIAL).split() if t]
    if not terms:
        return []
    # Quote each term: a bare token can be FTS5 syntax (NEAR, OR, AND).
    match = " OR ".join(f'"{t}"' for t in terms)
    try:
        async with conn.execute(
            f"SELECT {_ATOM_COLS}, bm25(atoms_fts) FROM atoms_fts "
            "JOIN atoms a ON a.id = atoms_fts.rowid "
            "WHERE atoms_fts MATCH ? ORDER BY bm25(atoms_fts) LIMIT ?",
            (match, limit),
        ) as cur:
            rows = await cur.fetchall()
    except Exception:
        return []

    # bm25() is negative, more-negative = better. Map to (0, 1] without
    # pretending it is comparable to a cosine — it only orders within this path.
    hits = []
    for row in rows:
        bm = row[-1]
        hits.append(_hit(row, 1.0 / (1.0 + math.exp(bm)), "lexical"))
    return hits


async def temporal_path(
    conn: aiosqlite.Connection, limit: int = TEMPORAL_POOL_LIMIT
) -> list[dict]:
    """Plain SQL recency. A SEPARATE POOL: recency does not compete in KNN,
    where it would either dominate every query or never surface at all."""
    async with conn.execute(
        f"SELECT {_ATOM_COLS} FROM atoms a ORDER BY a.ts DESC LIMIT ?", (limit,)
    ) as cur:
        rows = await cur.fetchall()
    return [_hit(r, 0.0, "temporal") for r in rows]


# ── Merge ────────────────────────────────────────────────────────────────────

def _unpack(vec: bytes) -> tuple[float, ...]:
    return struct.unpack(f"{len(vec) // 4}f", vec)


def _cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    return sum(x * y for x, y in zip(a, b))


def apply_speaker_weight(hits: list[dict], *, about_lyra: bool) -> list[dict]:
    """Down-weight her own past turns in recall.

    Retrieving her own phrasing and re-saying it is a self-reinforcing style
    loop — the model reads its own output as evidence of how it talks. `speaker`
    already distinguishes them; retrieval has to actually use it.

    When the question IS about what she said, the weight is lifted rather than
    inverted: her turns become eligible again, they do not get a bonus.
    """
    if about_lyra:
        return hits
    out = []
    for h in hits:
        h = dict(h)
        if h["speaker"] == "lyra":
            h["score"] *= LYRA_RECALL_WEIGHT
        out.append(h)
    return out


_SELF_REFERENCE_MARKERS = (
    "you said", "you told me", "your words", "did you say", "you mentioned",
    "you wrote", "what did you", "you promised",
)


def is_about_lyra(query: str) -> bool:
    q = query.lower()
    return any(m in q for m in _SELF_REFERENCE_MARKERS)


async def merge_paths(
    conn: aiosqlite.Connection,
    pools: list[list[dict]],
    *,
    limit: int = RECALL_RESULT_LIMIT,
) -> list[dict]:
    """Score-order the union, then drop near-duplicates on assembly.

    The same memory arriving down two paths is one memory. Dedupe is cosine
    against everything already selected (MMR's diversity term with a hard
    threshold), not a distinct-id check — two different atoms can carry the
    same content.
    """
    by_id: dict[int, dict] = {}
    for pool in pools:
        for h in pool:
            existing = by_id.get(h["id"])
            if existing is None or h["score"] > existing["score"]:
                by_id[h["id"]] = h

    ranked = sorted(by_id.values(), key=lambda h: (-h["score"], -h["ts"]))
    if not ranked:
        return []

    vectors = await _load_vectors(conn, [h["id"] for h in ranked])

    selected: list[dict] = []
    selected_vecs: list[tuple[float, ...]] = []
    for h in ranked:
        if len(selected) >= limit:
            break
        v = vectors.get(h["id"])
        if v is not None and any(
            _cosine(v, sv) >= RECALL_DEDUPE_SIMILARITY for sv in selected_vecs
        ):
            continue
        selected.append(h)
        if v is not None:
            selected_vecs.append(v)
    return selected


async def _load_vectors(
    conn: aiosqlite.Connection, atom_ids: list[int]
) -> dict[int, tuple[float, ...]]:
    if not atom_ids:
        return {}
    placeholders = ",".join("?" * len(atom_ids))
    try:
        async with conn.execute(
            f"SELECT rowid, embedding FROM vec_atoms WHERE rowid IN ({placeholders})",
            atom_ids,
        ) as cur:
            rows = await cur.fetchall()
    except Exception:
        return {}
    return {r[0]: _unpack(r[1]) for r in rows}


# ── Synthesis ────────────────────────────────────────────────────────────────

_SYNTHESIS_PROMPT = (
    "These are fragments of your own memory, retrieved because they may bear on "
    "the current question. Write ONE short paragraph in your own voice recalling "
    "what they amount to. Do not list them, do not add anything that is not "
    "there, and do not speculate. If they do not amount to anything, say so in "
    "one line.\n\nQuestion: {query}\n\nFragments:\n{fragments}"
)


def concatenate_recall(hits: list[dict]) -> str:
    return "\n".join(f"- {h['text']}" for h in hits)


async def synthesize_recall(hits: list[dict], query: str, llm=None) -> str:
    """One short synthesis pass over the merged results, not the raw hits.

    Five retrieved fragments pasted in read as disjointed notes; one synthesized
    paragraph reads as memory. Costs one extra call on the hot path — acceptable,
    and it changes how she reads more than any schema decision in the store.

    FALLS BACK TO CONCATENATION if the call fails. Never blocks a turn. This is
    the one place in retrieval that swallows an exception, and it does so
    because the degraded output is still correct, just flatter.
    """
    if not hits:
        return ""
    if llm is None:
        return concatenate_recall(hits)
    try:
        text = await llm(
            _SYNTHESIS_PROMPT.format(query=query, fragments=concatenate_recall(hits))
        )
    except Exception as exc:
        _log.warning("recall synthesis failed, falling back to concatenation: %s", exc)
        return concatenate_recall(hits)
    text = (text or "").strip()
    return text or concatenate_recall(hits)


# ── Assembly ─────────────────────────────────────────────────────────────────

_BLOCK_HEADINGS = {
    "facts": "## What you know",
    "traits": "## How you are",
    "commitments": "## Open loops",
    "recall": "## Recall",
    "recent": "## Now",
}


async def build_context(
    memory: object,
    query: str | None = None,
    *,
    llm=None,
    log: bool = True,
) -> str:
    """Assemble the non-conversation context plus the recent turns.

    Raises on a broken store. See module docstring — this is deliberate.
    """
    conn = memory.db
    if conn is None or memory.working_memory is None:
        # Loud, and legible. An unstarted store must not assemble an empty
        # context that reads exactly like a store with nothing in it.
        raise RuntimeError(
            "build_context called on a MemorySystem that was never started — "
            "call await memory.start() first"
        )
    working = memory.working_memory.get_items()

    if query is None:
        last_user = next((i for i in reversed(working) if i.role == "user"), None)
        query = last_user.content if last_user else None

    misses: list[str] = []
    blocks: dict[str, str] = {}
    injected_atom_ids: list[int] = []
    injected_fact_ids: list[int] = []
    injected_dream_ids: list[int] = []

    # 1. facts — exact match on subject, not KNN. Deterministic, cheap, and it
    #    does not consume recall budget.
    fact_rows = await memory.facts.for_query(query) if query else []
    if fact_rows:
        injected_fact_ids = [f["id"] for f in fact_rows]
        blocks["facts"] = "\n".join(
            _lines_within_budget(
                [f"- {f['subject']}: {f['text']}" for f in fact_rows],
                CONTEXT_BUDGET_BYTES["facts"],
            )
        )
    elif query:
        misses.append(f"facts:{query}")

    # 2. traits
    traits = await memory.identity_engine.get_context_traits()
    if traits:
        blocks["traits"] = "\n".join(
            _lines_within_budget(
                [f"- {t.name}: {t.value}" for t in traits],
                CONTEXT_BUDGET_BYTES["traits"],
            )
        )

    # 3. commitments — the one store that surfaces UNPROMPTED. Everything else
    #    is retrieved on relevance; open loops assert themselves.
    open_commitments = await memory.commitments.for_injection()
    if open_commitments:
        blocks["commitments"] = "\n".join(
            _lines_within_budget(
                [_format_commitment(c) for c in open_commitments],
                CONTEXT_BUDGET_BYTES["commitments"],
            )
        )

    # 4. recall — three paths, one merge, one synthesis.
    if query:
        query_vec = await embed(query)
        semantic = await semantic_path(conn, query_vec)
        lexical = await lexical_path(conn, query)
        temporal = await temporal_path(conn)

        if not semantic:
            misses.append(f"semantic:{query}")
        if not lexical:
            misses.append(f"lexical:{query}")

        about_lyra = is_about_lyra(query)
        pools = [
            apply_speaker_weight(semantic, about_lyra=about_lyra),
            apply_speaker_weight(lexical, about_lyra=about_lyra),
            temporal,
        ]
        hits = await merge_paths(conn, pools)
        # Whatever is already verbatim in the `recent` block must not also be
        # paid for out of recall budget. Compare against the DEQUE's contents,
        # not the last N atoms by time: on a small or long-idle store those are
        # the same rows as the whole history, and filtering by id would empty
        # recall entirely.
        verbatim = {i.content for i in working[-RECENT_TURNS:]}
        hits = [h for h in hits if h["text"] not in verbatim]
        if hits:
            injected_atom_ids = [h["id"] for h in hits]
            blocks["recall"] = truncate_to_bytes(
                await synthesize_recall(hits, query, llm=llm),
                CONTEXT_BUDGET_BYTES["recall"],
            )

    # 5. recent — verbatim, at the end, where the model weights it most.
    if working:
        recent = working[-RECENT_TURNS:]
        blocks["recent"] = "\n".join(
            _lines_within_budget(
                [f"[{i.role or i.type}]: {i.content}" for i in recent],
                CONTEXT_BUDGET_BYTES["recent"],
            )
        )

    rendered = _render(blocks)

    if log:
        await memory.context_log.write(
            atom_ids=injected_atom_ids,
            fact_ids=injected_fact_ids,
            dream_ids=injected_dream_ids,
            budget_used=len(rendered.encode("utf-8")) // BYTES_PER_TOKEN,
            misses=misses,
        )
    return rendered


def _render(blocks: dict[str, str]) -> str:
    """Emit the blocks in the pinned order, enforcing the non-conversation cap."""
    parts: list[str] = []
    non_conversation_used = 0
    for name in BLOCK_ORDER:
        body = blocks.get(name)
        if not body:
            continue
        if name != "recent":
            cost = len(body.encode("utf-8"))
            if non_conversation_used + cost > NON_CONVERSATION_BUDGET_BYTES:
                body = truncate_to_bytes(
                    body, max(0, NON_CONVERSATION_BUDGET_BYTES - non_conversation_used)
                )
                if not body.strip():
                    continue
            non_conversation_used += len(body.encode("utf-8"))
        parts.append(f"{_BLOCK_HEADINGS[name]}\n{body}")
    return "\n\n".join(parts)


def _format_commitment(c: dict) -> str:
    who = "you" if c["owner"] == "wilson" else "I"
    if c["due_ts"]:
        when = time.strftime("%Y-%m-%d", time.localtime(c["due_ts"]))
        overdue = " (overdue)" if c["due_ts"] < time.time() else ""
        return f"- {who} said: {c['text']} — due {when}{overdue}"
    return f"- {who} said: {c['text']}"


async def build_system_prompt(memory: object, *, llm=None) -> str:
    context = await build_context(memory, llm=llm)
    return f"{config.CORE_PROMPT}\n\n{context}".strip() if context else config.CORE_PROMPT


# ── Standalone search (MCP surface) ──────────────────────────────────────────

async def search_atoms(
    query: str,
    limit: int = SEARCH_ATOMS_DEFAULT_LIMIT,
    path: Path | None = None,
) -> list[dict]:
    """Semantic + lexical search over atoms, opening its own connection.

    Dream text lives in `dreams` and is deliberately excluded from this index: a
    single essay outranks and drowns out every atom it was summarised from.
    """
    _log.debug("search_atoms query=%r limit=%d", query, limit)
    query_vec = await embed(query)
    async with aiosqlite.connect(path or config.DB_PATH) as conn:
        await load_vec_extension(conn)
        pools = [
            await semantic_path(conn, query_vec),
            await lexical_path(conn, query),
        ]
        return await merge_paths(conn, pools, limit=limit)


async def get_facts(subject: str, path: Path | None = None) -> list[dict]:
    """Current facts for a subject. `valid_until IS NULL`, newest first."""
    async with aiosqlite.connect(path or config.DB_PATH) as conn:
        from lyra_memory.facts import FactStore
        return await FactStore(conn).get(subject)
