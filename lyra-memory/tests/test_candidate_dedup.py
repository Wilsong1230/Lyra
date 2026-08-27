"""Step 0.5 — candidate dedup fix.

The bug: candidates were matched on the model-invented *label*, which carries
almost no signal, so near-duplicate observations never merged. 70 candidates,
60 of them singletons, 1 trait promoted in 3 months — promotion effectively
never fired, and with nothing promoted, step 0 would record nothing.

The fix per the build sheet: embed **description + evidence**, not the label.

Verification target: the four known-duplicate `self_*` labels collapse to 1.
That is a claim about meaning, so it runs under `real_embeddings` and skips
where MiniLM is unavailable (see tests/conftest.py). The mechanism — that
evidence is part of the embedded text, that the stored vector tracks the
cluster rather than its first member, and that four labels can collapse to
one through the merge path — is verified here unconditionally.
"""
from __future__ import annotations

import struct
from pathlib import Path

import pytest

from lyra_memory.candidate_pool import CandidatePool, _embedding_text
from lyra_memory.config import EMBED_DIM
from lyra_memory.db import init_db


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


def _unpack(raw: bytes) -> list[float]:
    return list(struct.unpack(f"{EMBED_DIM}f", raw))


async def _stored_vector(conn, candidate_id: int) -> list[float]:
    async with conn.execute(
        "SELECT embedding FROM vec_candidates WHERE rowid = ?", (candidate_id,)
    ) as cur:
        row = await cur.fetchone()
    return _unpack(row[0])


# ── the embedded text ────────────────────────────────────────────────────────

def test_embedding_text_includes_description_and_evidence():
    text = _embedding_text("prefers terse deliverables", "cut my summary to one line")
    assert "prefers terse deliverables" in text
    assert "cut my summary to one line" in text


def test_embedding_text_excludes_the_label():
    """The label is the thing that was wrong to match on."""
    text = _embedding_text("prefers terse deliverables", "cut my summary to one line")
    assert "communication_style" not in text
    assert "self_reflection" not in text


def test_embedding_text_without_evidence_is_the_description():
    """No evidence available → exactly the previous behaviour, nothing invented."""
    assert _embedding_text("prefers terse deliverables", None) == "prefers terse deliverables"


async def test_evidence_changes_the_embedded_vector():
    """Evidence must actually reach the vector, not just the row.

    Asserted on the vectors rather than on a candidate count, so it holds
    under either embedding backend and does not silently become a test of
    where CANDIDATE_DEDUP_THRESHOLD happens to sit.
    """
    from lyra_memory.embeddings import embed

    description = "reflects on her own state"
    without = await embed(_embedding_text(description, None))
    with_a = await embed(_embedding_text(description, "wondered whether she had been consistent"))
    with_b = await embed(_embedding_text(description, "counted the deploy script failures"))

    assert with_a != without, "evidence must change the embedded text"
    assert with_a != with_b, "different evidence must produce different vectors"


async def test_evidence_text_is_persisted(tmp_db_path: Path):
    conn = await init_db(tmp_db_path)
    pool = CandidatePool(conn)
    await pool.add_observation(
        "curiosity", "asks follow-up questions", "behavioral",
        evidence="asked three times why the threshold was 0.37",
    )
    async with conn.execute("SELECT evidence_text FROM candidates") as cur:
        (stored,) = await cur.fetchone()
    assert stored == "asked three times why the threshold was 0.37"
    await conn.close()


# ── the stored vector tracks the cluster ─────────────────────────────────────

async def test_merge_updates_stored_vector_to_running_centroid(tmp_db_path: Path):
    """A cluster is represented by its members, not by whichever arrived first.

    Keeping the first member's vector makes matching depend on insertion
    order: the same three observations in a different order give a different
    pool. The centroid removes that.
    """
    import numpy as np

    from lyra_memory.embeddings import embed

    conn = await init_db(tmp_db_path)
    pool = CandidatePool(conn)

    first = "notices when a plan has drifted from its goal"
    second = "notices when a plan has drifted from the goal"

    await pool.add_observation("drift_a", first, "cognitive")
    await pool.add_observation("drift_b", second, "cognitive")

    candidates = await pool.get_candidates()
    assert len(candidates) == 1, "near-identical descriptions must merge"
    assert candidates[0].evidence_count == 2

    v1 = np.array(_unpack(await embed(first)))
    v2 = np.array(_unpack(await embed(second)))
    expected = (v1 + v2) / 2
    expected = expected / np.linalg.norm(expected)

    stored = np.array(await _stored_vector(conn, candidates[0].id))
    assert stored == pytest.approx(expected, abs=1e-5)
    await conn.close()


async def test_stored_vector_stays_normalized_across_many_merges(tmp_db_path: Path):
    import numpy as np

    conn = await init_db(tmp_db_path)
    pool = CandidatePool(conn)
    for i in range(6):
        await pool.add_observation(
            f"label_{i}", "decomposes problems into numbered sub-steps", "cognitive"
        )
    candidates = await pool.get_candidates()
    assert len(candidates) == 1
    assert candidates[0].evidence_count == 6
    stored = np.array(await _stored_vector(conn, candidates[0].id))
    assert float(np.linalg.norm(stored)) == pytest.approx(1.0, abs=1e-5)
    await conn.close()


# ── the verification target ──────────────────────────────────────────────────

# Four labels the pool actually produced for one behaviour. Distinct
# model-invented names, near-identical descriptions — exactly the case that
# label matching could never merge.
_SELF_STAR = [
    ("self_reflection", "reflects on her own behaviour and reasoning"),
    ("self_awareness", "reflects on her own behavior and reasoning"),
    ("self_examination", "reflects on her own reasoning and behaviour"),
    ("self_inquiry", "reflects on her own behaviour and her reasoning"),
]


async def test_four_self_star_labels_collapse_to_one_mechanically(tmp_db_path: Path):
    """The 4 → 1 collapse through the merge path, on descriptions that are
    near-duplicates by surface as well as by meaning, so it holds under the
    offline stand-in too."""
    conn = await init_db(tmp_db_path)
    pool = CandidatePool(conn)
    for label, description in _SELF_STAR:
        await pool.add_observation(label, description, "cognitive")

    candidates = await pool.get_candidates()
    assert len(candidates) == 1, (
        "the four self_* labels must collapse to one candidate; got "
        + ", ".join(c.trait_name for c in candidates)
    )
    assert candidates[0].evidence_count == 4
    await conn.close()


async def test_four_self_star_labels_collapse_to_one(tmp_db_path: Path, real_embeddings):
    """The step 0.5 verification check, on real MiniLM distances."""
    conn = await init_db(tmp_db_path)
    pool = CandidatePool(conn)
    for label, description in _SELF_STAR:
        await pool.add_observation(label, description, "cognitive")

    candidates = await pool.get_candidates()
    assert len(candidates) == 1
    assert candidates[0].evidence_count == 4
    await conn.close()


async def test_genuinely_distinct_candidates_still_do_not_merge(tmp_db_path: Path):
    """The failure mode on the other side: a threshold loose enough to merge
    the self_* set must not collapse unrelated behaviours."""
    conn = await init_db(tmp_db_path)
    pool = CandidatePool(conn)
    await pool.add_observation(
        "warmth", "responds warmly to distress in others", "emotional")
    await pool.add_observation(
        "rigor", "decomposes problems into numbered sub-steps", "cognitive")
    await pool.add_observation(
        "brevity", "cuts her own drafts down before sending", "behavioral")

    assert len(await pool.get_candidates()) == 3
    await conn.close()


async def test_closed_vocabulary_still_bypasses_semantic_matching(tmp_db_path: Path):
    """Opposites in the developmental vocabulary must stay separate.

    'persists under frustration' and 'abandons under frustration' sit inside
    any threshold loose enough to merge real duplicates, so that vocabulary
    dedups by exact name and keeps out of the vec index entirely.
    """
    conn = await init_db(tmp_db_path)
    pool = CandidatePool(conn)
    await pool.add_observation(
        "persists_under_frustration", "persists under frustration", "behavioral",
        closed_vocabulary=True,
    )
    await pool.add_observation(
        "abandons_under_frustration", "abandons under frustration", "behavioral",
        closed_vocabulary=True,
    )
    assert len(await pool.get_candidates()) == 2

    async with conn.execute("SELECT COUNT(*) FROM vec_candidates") as cur:
        (n,) = await cur.fetchone()
    assert n == 0, "closed-vocabulary candidates must not enter the semantic index"
    await conn.close()


async def test_closed_vocabulary_ignores_evidence_for_matching(tmp_db_path: Path):
    """Exact-name dedup must stay exact-name even when evidence is supplied."""
    conn = await init_db(tmp_db_path)
    pool = CandidatePool(conn)
    for evidence in ("first attempt failed", "third attempt succeeded"):
        await pool.add_observation(
            "persists_under_frustration", "persists under frustration", "behavioral",
            evidence=evidence, closed_vocabulary=True,
        )
    candidates = await pool.get_candidates()
    assert len(candidates) == 1
    assert candidates[0].evidence_count == 2
    await conn.close()
