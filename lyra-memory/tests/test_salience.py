"""Step 6 — salience from outcomes.

Verification target: salience correlates with outcome valence — *direction,
not magnitude*.

Which direction is the substantive question, and it is answered here as
**magnitude of valence, not its sign**. A failure that changed the approach is
exactly as worth keeping as a first success; ranking good outcomes above bad
ones would mean she preferentially forgets the things that went wrong, which
is the opposite of what a track record is for. The signed correlation is
therefore deliberately near zero, and that is asserted rather than left to be
discovered later as a bug.

Salience is revisable — the pass recomputes from scratch, so a new outcome
re-scores the atoms around it.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from lyra_memory.config import SALIENCE_BASELINE
from lyra_memory.store import Store
from lyra_memory.store.passes.salience import SaliencePass


@pytest.fixture
async def store(tmp_path: Path):
    s = await Store.open(tmp_path / "store.db")
    yield s
    await s.close()


async def _atom(store: Store, text: str, ts: float | None = None) -> int:
    return await store.append_atom(
        speaker="wilson", source="cli", text=text,
        ts=ts if ts is not None else time.time())


async def _salience(store: Store, atom_id: int) -> float:
    async with store.db.execute(
        "SELECT salience FROM atoms WHERE id = ?", (atom_id,)) as cur:
        return (await cur.fetchone())[0]


# ── the verification check ───────────────────────────────────────────────────

async def test_salience_increases_with_outcome_magnitude(store: Store):
    weak = await _atom(store, "a change with a mild result")
    strong = await _atom(store, "a change with a decisive result")
    await store.record_outcome(intent_atom_id=weak, valence=0.2, actual="mild")
    await store.record_outcome(intent_atom_id=strong, valence=0.95, actual="decisive")

    await SaliencePass(store).run()

    assert await _salience(store, strong) > await _salience(store, weak)


async def test_failure_is_as_memorable_as_success(store: Store):
    """The failure that changed the approach is a discontinuity worth keeping.

    Ranking successes above failures would make her preferentially forget what
    went wrong.
    """
    success = await _atom(store, "the approach that finally worked")
    failure = await _atom(store, "the approach that failed and changed the plan")
    await store.record_outcome(intent_atom_id=success, valence=0.9, actual="passed")
    await store.record_outcome(intent_atom_id=failure, valence=-0.9, actual="failed")

    await SaliencePass(store).run()

    assert await _salience(store, success) == pytest.approx(
        await _salience(store, failure))


async def test_signed_correlation_is_near_zero_on_purpose(store: Store):
    """Stated as an assertion so it is not later 'fixed'."""
    ids, valences = [], []
    for i, valence in enumerate([-0.9, -0.6, -0.3, 0.3, 0.6, 0.9]):
        atom_id = await _atom(store, f"an attempt number {i}", ts=time.time() + i)
        await store.record_outcome(intent_atom_id=atom_id, valence=valence, actual="x")
        ids.append(atom_id)
        valences.append(valence)

    await SaliencePass(store).run()
    saliences = [await _salience(store, i) for i in ids]

    n = len(valences)
    mean_v = sum(valences) / n
    mean_s = sum(saliences) / n
    covariance = sum((v - mean_v) * (s - mean_s) for v, s in zip(valences, saliences)) / n
    assert abs(covariance) < 1e-9, (
        "salience tracked the sign of valence; it must track magnitude")

    magnitudes = [abs(v) for v in valences]
    paired = sorted(zip(magnitudes, saliences))
    assert [s for _m, s in paired] == sorted(s for _m, s in paired), (
        "salience must be monotonic in |valence|")


# ── atoms with no outcome ────────────────────────────────────────────────────

async def test_atoms_with_no_outcome_get_the_baseline(store: Store):
    atom_id = await _atom(store, "a turn that led to nothing measurable")
    await SaliencePass(store).run()
    assert await _salience(store, atom_id) == pytest.approx(SALIENCE_BASELINE)


async def test_baseline_is_nonzero(store: Store):
    """Zero would make every unscored atom equally forgettable, which gives
    the forgetting pass nothing to rank."""
    assert SALIENCE_BASELINE > 0


async def test_salience_is_bounded(store: Store):
    atom_id = await _atom(store, "an attempt")
    await store.record_outcome(intent_atom_id=atom_id, valence=5.0, actual="out of range")
    await SaliencePass(store).run()
    assert 0.0 <= await _salience(store, atom_id) <= 1.0


# ── the outcome/atom link is preserved ───────────────────────────────────────

async def test_atoms_outcome_id_is_populated(store: Store):
    """'outcome -> trait, link dropped' becomes 'outcome <-> atom preserved'."""
    atom_id = await _atom(store, "an attempt")
    outcome_id = await store.record_outcome(
        intent_atom_id=atom_id, valence=0.8, actual="worked")

    await SaliencePass(store).run()

    async with store.db.execute(
        "SELECT outcome_id FROM atoms WHERE id = ?", (atom_id,)) as cur:
        assert (await cur.fetchone())[0] == outcome_id


# ── session spillover ────────────────────────────────────────────────────────

async def test_atoms_near_an_outcome_are_more_salient_than_unrelated_ones(store: Store):
    from lyra_memory.store.passes.segmentation import SegmentationPass

    now = time.time() - 10_000
    neighbour = await _atom(store, "the turn just before the attempt", ts=now)
    attempt = await _atom(store, "the attempt itself", ts=now + 30)
    await store.record_outcome(intent_atom_id=attempt, valence=0.9, actual="worked")
    far = await _atom(store, "an unrelated conversation days later", ts=now + 500_000)

    await SegmentationPass(store).run()
    await SaliencePass(store).run()

    assert await _salience(store, neighbour) > await _salience(store, far)
    assert await _salience(store, attempt) > await _salience(store, neighbour)


# ── revisable ────────────────────────────────────────────────────────────────

async def test_a_new_outcome_rescores_existing_atoms(store: Store):
    """Salience is revisable, not write-once. The old design computed it once
    at write time and 13 of 29 episodes saturated at 0.933."""
    atom_id = await _atom(store, "an attempt whose result is not known yet")
    await SaliencePass(store).run()
    before = await _salience(store, atom_id)

    await store.record_outcome(intent_atom_id=atom_id, valence=0.9, actual="worked")
    await SaliencePass(store).run()
    after = await _salience(store, atom_id)

    assert after > before


async def test_pass_is_idempotent(store: Store):
    atom_id = await _atom(store, "an attempt")
    await store.record_outcome(intent_atom_id=atom_id, valence=0.7, actual="worked")

    await SaliencePass(store).run()
    first = await _salience(store, atom_id)
    await SaliencePass(store).run()
    assert await _salience(store, atom_id) == pytest.approx(first)


async def test_pass_does_not_touch_atom_text_or_time(store: Store):
    await _atom(store, "an attempt")
    async with store.db.execute("SELECT id, ts, text, speaker FROM atoms") as cur:
        before = await cur.fetchall()
    await SaliencePass(store).run()
    async with store.db.execute("SELECT id, ts, text, speaker FROM atoms") as cur:
        assert await cur.fetchall() == before


# ── the outcome unit is the attempt ──────────────────────────────────────────

async def test_ten_failures_then_a_pass_is_one_success(store: Store):
    """Hard rule. Ten failed test runs then a pass is ONE success, not ten
    failures and a win — otherwise debugging floors her mood permanently and
    rebuilds the 'abandons under frustration' topology."""
    attempt = await _atom(store, "get the vec extension loading on this python")

    outcome_id = await store.record_outcome(
        intent_atom_id=attempt, valence=0.8,
        actual="loaded, after ten failed runs", predicted="should load")

    async with store.db.execute("SELECT COUNT(*) FROM outcomes") as cur:
        assert (await cur.fetchone())[0] == 1

    await SaliencePass(store).run()
    assert await _salience(store, attempt) > SALIENCE_BASELINE
    assert outcome_id is not None


async def test_abandoning_is_the_failure(store: Store):
    attempt = await _atom(store, "tried to make the sim converge, gave up")
    await store.record_outcome(
        intent_atom_id=attempt, valence=-0.8, actual="abandoned")
    await SaliencePass(store).run()
    assert await _salience(store, attempt) > SALIENCE_BASELINE
