"""Step 5 — segmentation.

A pure component: timestamps and embeddings in, boundary indices out, no
database. That is what makes it testable against a frozen set at all.

Verification target: precision/recall against a hand-labeled set of ~20
sessions. The labels in `eval/segmentation_labels.json` were committed before
this pass was written — see that file's header for what that does and does not
establish.

The pass also writes `sessions.gap_since_prev`, which is the only thing in the
store that represents elapsed time. She has timestamps and no sense of
duration; nothing else distinguishes a continuous month from a month with one
conversation in it.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from lyra_memory.config import SESSION_GAP_SECONDS
from lyra_memory.store import Store
from lyra_memory.store.passes.segmentation import (
    SegmentationPass,
    find_boundaries,
)

_LABELS = json.loads(
    (Path(__file__).resolve().parent.parent / "eval" / "segmentation_labels.json").read_text()
)


@pytest.fixture
async def store(tmp_path: Path):
    s = await Store.open(tmp_path / "store.db")
    yield s
    await s.close()


# ── the pure component ───────────────────────────────────────────────────────

def test_first_atom_always_starts_a_session():
    assert find_boundaries([100.0]) == [0]


def test_no_atoms_no_boundaries():
    assert find_boundaries([]) == []


def test_a_large_gap_is_a_boundary():
    ts = [0.0, 10.0, 20.0, 20.0 + SESSION_GAP_SECONDS + 1]
    assert find_boundaries(ts) == [0, 3]


def test_a_gap_just_under_threshold_is_not_a_boundary():
    ts = [0.0, 10.0, 10.0 + SESSION_GAP_SECONDS - 1]
    assert find_boundaries(ts) == [0]


def test_boundaries_are_sorted_and_unique():
    ts = [0.0, 5.0, 100000.0, 100005.0, 200000.0]
    b = find_boundaries(ts)
    assert b == sorted(set(b))


def test_semantic_shift_alone_does_not_split():
    """A topic change is not a session change.

    Two consecutive turns 70 seconds apart about completely different things
    are one conversation, and treating them otherwise shreds every real
    session into topical fragments.
    """
    ts = [0.0, 70.0]
    orthogonal = [[1.0] + [0.0] * 383, [0.0, 1.0] + [0.0] * 382]
    assert find_boundaries(ts, orthogonal) == [0]


def test_semantic_shift_plus_a_soft_gap_does_split():
    """Both signals together are evidence; neither alone is enough."""
    from lyra_memory.config import SESSION_SOFT_GAP_SECONDS

    ts = [0.0, SESSION_SOFT_GAP_SECONDS + 60]
    orthogonal = [[1.0] + [0.0] * 383, [0.0, 1.0] + [0.0] * 382]
    assert find_boundaries(ts, orthogonal) == [0, 1]


def test_a_soft_gap_with_no_shift_does_not_split():
    from lyra_memory.config import SESSION_SOFT_GAP_SECONDS

    ts = [0.0, SESSION_SOFT_GAP_SECONDS + 60]
    same = [[1.0] + [0.0] * 383, [1.0] + [0.0] * 383]
    assert find_boundaries(ts, same) == [0]


# ── the verification check: precision/recall vs. the labeled set ─────────────

def _score(predicted: set[int], labeled: set[int]) -> tuple[float, float]:
    # Index 0 is a boundary by definition and would inflate both scores.
    predicted = predicted - {0}
    labeled = labeled - {0}
    if not predicted:
        return 0.0, 0.0
    tp = len(predicted & labeled)
    precision = tp / len(predicted)
    recall = tp / len(labeled) if labeled else 1.0
    return precision, recall


def test_boundaries_match_the_hand_labeled_set():
    timestamps = [float(offset) for offset, _text in _LABELS["atoms"]]
    predicted = set(find_boundaries(timestamps))
    labeled = set(_LABELS["boundaries"])

    precision, recall = _score(predicted, labeled)
    missed = sorted(labeled - predicted - {0})
    spurious = sorted(predicted - labeled - {0})

    assert precision == 1.0, f"split where the labels say not to: {spurious}"
    assert recall == 1.0, f"missed labeled boundaries: {missed}"


def test_the_mid_session_pause_does_not_split():
    """The case the whole threshold choice turns on: a 22-minute pause inside
    a conversation is someone making lunch, not a new session."""
    timestamps = [float(offset) for offset, _t in _LABELS["atoms"]]
    predicted = set(find_boundaries(timestamps))
    assert 15 not in predicted, "the 22-minute lunch pause was treated as a boundary"


def test_the_topic_change_does_not_split():
    timestamps = [float(offset) for offset, _t in _LABELS["atoms"]]
    predicted = set(find_boundaries(timestamps))
    assert 18 not in predicted, "an abrupt topic change was treated as a boundary"


# ── the pass ─────────────────────────────────────────────────────────────────

async def _seed_from_labels(store: Store) -> None:
    origin = time.time() - 2_000_000
    for offset, text in _LABELS["atoms"]:
        await store.append_atom(
            speaker="wilson", source="cli", text=text, ts=origin + offset)


async def test_pass_writes_one_session_row_per_boundary(store: Store):
    await _seed_from_labels(store)
    await SegmentationPass(store).run()

    async with store.db.execute("SELECT COUNT(*) FROM sessions") as cur:
        (n,) = await cur.fetchone()
    assert n == len(_LABELS["boundaries"])


async def test_pass_stamps_every_atom_with_a_session(store: Store):
    await _seed_from_labels(store)
    await SegmentationPass(store).run()

    async with store.db.execute(
        "SELECT COUNT(*) FROM atoms WHERE session_id IS NULL"
    ) as cur:
        assert (await cur.fetchone())[0] == 0


async def test_session_atom_counts_add_up(store: Store):
    await _seed_from_labels(store)
    await SegmentationPass(store).run()

    async with store.db.execute("SELECT SUM(atom_count) FROM sessions") as cur:
        (total,) = await cur.fetchone()
    async with store.db.execute("SELECT COUNT(*) FROM atoms") as cur:
        (atoms,) = await cur.fetchone()
    assert total == atoms


async def test_gap_since_prev_is_written(store: Store):
    """The one thing in the store that represents elapsed time."""
    await _seed_from_labels(store)
    await SegmentationPass(store).run()

    async with store.db.execute(
        "SELECT id, started_ts, ended_ts, gap_since_prev FROM sessions ORDER BY started_ts"
    ) as cur:
        rows = await cur.fetchall()

    assert rows[0][3] is None, "the first session has nothing to be a gap from"
    for (_id, started, _ended, gap), (_pid, _ps, prev_ended, _pg) in zip(rows[1:], rows):
        assert gap == pytest.approx(started - prev_ended)
        assert gap > 0


async def test_gap_distinguishes_a_sparse_month_from_a_dense_one(store: Store):
    """Atom count alone cannot tell these apart, which is the point."""
    await _seed_from_labels(store)
    await SegmentationPass(store).run()

    async with store.db.execute(
        "SELECT gap_since_prev FROM sessions WHERE gap_since_prev IS NOT NULL"
    ) as cur:
        gaps = [r[0] for r in await cur.fetchall()]
    assert max(gaps) > SESSION_GAP_SECONDS
    assert len(set(gaps)) > 1, "every gap identical means nothing was measured"


async def test_pass_is_idempotent(store: Store):
    """Re-derivable: running it twice must not double the sessions."""
    await _seed_from_labels(store)
    await SegmentationPass(store).run()
    async with store.db.execute("SELECT COUNT(*) FROM sessions") as cur:
        (first,) = await cur.fetchone()

    await SegmentationPass(store).run()
    async with store.db.execute("SELECT COUNT(*) FROM sessions") as cur:
        (second,) = await cur.fetchone()
    assert first == second


async def test_pass_on_an_empty_store_does_nothing(store: Store):
    await SegmentationPass(store).run()
    async with store.db.execute("SELECT COUNT(*) FROM sessions") as cur:
        assert (await cur.fetchone())[0] == 0


async def test_atoms_are_untouched_apart_from_session_id(store: Store):
    await _seed_from_labels(store)
    async with store.db.execute("SELECT id, ts, text, speaker FROM atoms ORDER BY id") as cur:
        before = await cur.fetchall()

    await SegmentationPass(store).run()

    async with store.db.execute("SELECT id, ts, text, speaker FROM atoms ORDER BY id") as cur:
        assert await cur.fetchall() == before


async def test_the_embedding_signal_is_off_and_this_is_why(store: Store):
    """A recorded measurement, not a preference.

    The sheet lists embeddings as a segmentation input, so the signal is
    wired. It is disabled because turning it on splits the 22-minute lunch
    pause the labels exist to protect: "hold on, food" and "ok back" are
    semantically unlike everything around them, including each other, so the
    shift test fires hardest on exactly the pattern that marks a pause WITHIN
    a session. This test exists so the finding survives someone deciding the
    flag looks like an oversight.
    """
    await _seed_from_labels(store)
    labeled = set(_LABELS["boundaries"])

    await SegmentationPass(store, use_embeddings=False).run()
    async with store.db.execute("SELECT started_ts FROM sessions") as cur:
        time_only = len(await cur.fetchall())

    await SegmentationPass(store, use_embeddings=True).run()
    async with store.db.execute("SELECT started_ts FROM sessions") as cur:
        with_embeddings = len(await cur.fetchall())

    assert time_only == len(labeled)
    assert with_embeddings > time_only, (
        "if embeddings no longer over-split, re-measure and reconsider the "
        "default rather than deleting this test"
    )
