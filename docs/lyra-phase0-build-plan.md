# Lyra — Phase 0 Build Plan

> **Scope:** Phase 0 only — establish the black-box seam, the test rig, and make the existing emergent machinery actually run. No drives, no affect dynamics, no perception loop. Each step below = one scoped Claude Code prompt = one commit = one green checkpoint. Do them in order; do not start a step until the previous one is verified green.
>
> **Repo:** the merged repo, with `lyra/`, `lyra_memory/`, and the new `lyra_core/` as sibling package folders. Dependencies point inward: `lyra/` (shell) → `lyra_core` / `lyra_memory` (box). The box never imports the shell.

---

## Pre-flight (do once, before Step 1)

- [ ] Confirm the merge is green: `lyra_memory`'s existing test suite passes in the new repo, `lyra`'s imports resolve, one clean commit on `main`.
- [ ] Create the empty package skeleton: `lyra_core/__init__.py`, `lyra_core/interface.py`, `lyra_core/harness.py`, and a `tests/test_core.py`. Empty files, no logic.
- [ ] Confirm `lyra_core` is importable in the same venv as `lyra_memory` (add to the package/pyproject so `from lyra_core import ...` works).
- [ ] Commit: `chore(core): add empty lyra_core package skeleton`

*Verifiable outcome: `python -c "import lyra_core"` succeeds; full existing test suite still green.*

---

## Step 1 — The core interface contract *(the load-bearing decision)*

**Goal:** define the two ports as plain, typed objects. This is the contract every later phase and every peripheral depends on. Get the *shape* right — including the reserved seams — because changing it later is expensive.

**Files:** `lyra_core/interface.py`, `tests/test_core.py`

**Build:**
- `Observation` (IN) — a uniform event shape regardless of source. Fields:
  - `kind`: enum/literal — `sensory` | `action_outcome` | `external_affect` (the last is the reserved empathy seam — accepted now, unused until later).
  - `source`: str — which peripheral/origin (`vision`, `ears`, `conversation`, `self`...).
  - `content`: str — the payload (description, transcript, outcome text).
  - `ts`: float.
  - `predicted` / `actual` (optional): the reserved **prediction-outcome pairing** for the competence drive — an `action_outcome` observation can carry what was expected vs. what happened. Empty until Phase 3, but the slot exists now.
  - `affect_hint` (optional): reserved slot on `external_affect` observations for sensed valence/arousal of Wilson. Empty until the empathy channel is filled.
- `Intent` (OUT) — "what I want to do." Fields: `kind` (`speak` | `look` | `set_state` | `noop` | reserved `research` | ...), `payload`: dict, `ts`. The core emits intent only — never waveforms or hex colors.
- `AffectState` (OUT) — "how I am." Fields: `valence: float`, `arousal: float`, optional `control: float` (third axis, reserved). Plus the **three-timescale** shape reserved now: `emotion` (the current vector), `mood` (medium-term offset), `temperament` (the time constants). In Phase 0 only `emotion` is populated; `mood`/`temperament` are present but inert.
- `CognitiveCore` — a class with one entry point: `tick(observations: list[Observation]) -> tuple[list[Intent], AffectState]`. **Stub only** — ingest nothing, return an empty intent list and a neutral `AffectState`. The point of this step is the *contract*, not behavior.

**Single verifiable outcome:** tests construct each type, call `core.tick([...])`, and assert the return is `(list[Intent], AffectState)` with neutral affect. No behavior asserted yet.

**Commit:** `feat(core): define observation/intent/affect interface contract + stub tick()`

> ⚠️ **Review the type fields before approving this step.** The reserved seams (`external_affect`, `predicted`/`actual`, the three affect timescales) are the things that are cheap now and surgery later. If a seam is missing here, it gets missed everywhere downstream.

---

## Step 2 — The fake harness *(the test rig everything leans on)*

**Goal:** drive the core in complete isolation — no senses, no body, no memory daemon, no HTTP. Scripted observations in, assert on intents + affect out. This rig is how every later phase gets debugged.

**Files:** `lyra_core/harness.py`, `tests/test_core.py`

**Build:**
- A `Harness` (or simple functions) that: constructs a `CognitiveCore`, feeds it a *scripted sequence* of `Observation`s across multiple `tick()` calls, and collects the `(intents, affect)` outputs into an inspectable trace.
- A couple of example scripts as fixtures: e.g. a sequence of `action_outcome` observations representing repeated failures (this is the script you'll later use to confirm frustration accumulates — inert now, but write the *scaffold* so Phase 3 just fills it in).
- Tests that run a script through the stub core and assert on the *trace shape* (N ticks in → N affect readings out), not on behavior.

**Single verifiable outcome:** `pytest tests/test_core.py` runs a multi-tick scripted sequence through the stub core and asserts on the collected trace, with zero services running.

**Commit:** `feat(core): add fake harness for driving the core in isolation`

---

## Step 3 — Dead wire: `consolidate()` runs on a cycle

**Goal:** the single most important dead-wire fix. `IdentityEngine.consolidate()` is never called, so candidates never promote into traits and the entire emergent-identity apparatus is idle. Wire it to run.

**Files:** `lyra_memory/` (the dreaming loop or the memory system's cycle), `tests/test_memory.py`

**Build:**
- Give the `DreamingLoop` (or `MemorySystem`) a reference to the `IdentityEngine` and call `consolidate()` after each dream cycle (or on its own cadence). Decide the cadence deliberately — after each episode write is the obvious first choice.
- Extend the existing dreaming-loop test: after enough observations to cross the surface threshold (5), assert a trait actually appears in the `traits` table.

**Single verifiable outcome:** a test drives enough candidate observations to cross the surface threshold and confirms a trait gets promoted — proving the consolidation path is live for the first time.

**Commit:** `fix(memory): call consolidate() on the dream cycle so candidates promote into traits`

---

## Step 4 — Dead wire: episode retrieval feeds the system prompt

**Goal:** `build_system_prompt` currently gives traits + working memory but never the dreamed episodes. Lyra's own past reflections never re-enter her context. Wire retrieval in.

**Files:** `lyra_memory/retrieval.py`, `tests/test_memory.py`

**Build:**
- In `build_context` / `build_system_prompt`, pull a small number of relevant recent episodes (use the existing KNN `search_episodes`, queried against current working-memory content or most-recent turn) and include them in the assembled context.
- Keep it bounded (the existing `RETRIEVAL_EPISODE_LIMIT`).
- Test: with episodes present in the DB, assert `build_system_prompt` output contains episode content.

**Single verifiable outcome:** a test with seeded episodes confirms `build_system_prompt` surfaces at least one relevant episode into the prompt.

**Commit:** `fix(memory): surface relevant episodes into build_system_prompt`

---

## Step 5 — Dead wire: the salience score is actually read

**Goal:** `working_memory._score()` computes an importance/surprise/emotion salience value on every item, and *nothing consumes it*. Give it one real consumer.

**Files:** `lyra_memory/working_memory.py` (+ wherever the consumer lands), `tests/test_memory.py`

**Build:**
- Pick the **smallest honest consumer** for now — do *not* build the affect engine here (that's Phase 3). The cleanest Phase 0 use: let salience influence what `get_undreamed()` prioritizes, or tag episodes with the salience of their source items at write time (this also pre-fills the **salience-tag seam** for later memory decay). Prefer the episode-tagging option — it's the reserved seam and it's inert-but-present.
- Test: assert episodes are written with a salience/affect tag derived from their source items.

**Single verifiable outcome:** episodes in the DB carry a salience tag at write time; a test confirms the value is non-default for high-salience input.

**Commit:** `fix(memory): tag episodes with source salience at write time (reads the working-memory score)`

> Note: this deliberately does *not* make salience drive behavior yet — it just stops the signal being discarded and pre-fills the decay seam. Behavioral use comes in Phase 3.

---

## Step 6 — Cleanup: MCP `search_episodes` uses the real KNN path

**Goal:** `mcp_server.py` has its own stale `LIKE` copy of `search_episodes` with a docstring telling the model *not* to use semantic phrasing — directly contradicting the sqlite-vec migration. Lyra's actual memory tool bypasses everything built. Route it through `lyra_memory.retrieval`.

**Files:** `mcp_server.py`

**Build:**
- Replace the hand-rolled `LIKE` query with a call into `lyra_memory.retrieval.search_episodes` (the KNN version).
- Fix the tool docstring to describe semantic search, not keyword matching.
- Note: `mcp_server.py` is sync and `retrieval.search_episodes` is async — bridge with `asyncio.run` or a small sync wrapper. Keep the bridge minimal.

**Single verifiable outcome:** the MCP `search_episodes` tool returns semantic KNN results (paraphrased query finds a non-substring episode), matching the behavior of the core retrieval path.

**Commit:** `fix(mcp): route search_episodes through lyra_memory KNN retrieval instead of stale LIKE copy`

---

## Step 7 — Verify the vision model id

**Goal:** `vision_service.py` uses `google/gemma-4-31b-it:free`. A bad model id fails *silently* into the `Error: vision request failed` catch-all, indistinguishable from the service being down. Confirm it resolves, or fix it.

**Files:** `lyra-vision` repo (`vision_service.py`) — *separate repo, separate commit.*

**Build:**
- Verify the model identifier resolves on OpenRouter (or whichever provider is active). If it doesn't, correct it to a valid current vision model.
- Optionally: distinguish "bad model id / API rejected" from "service unreachable" in the error handling so a future silent failure is visible.

**Single verifiable outcome:** a real `/see` call returns a description (or the error clearly names *why* — bad id vs. unreachable — rather than collapsing both into one string).

**Commit (in lyra-vision):** `fix(vision): verify/correct vision model id and disambiguate failure modes`

---

## Phase 0 exit criteria

Phase 0 is done when **all** of these hold:

- [ ] `lyra_core` exists with a typed interface contract and a working fake harness, both green.
- [ ] Traits actually promote: running the system long enough produces rows in the `traits` table.
- [ ] Lyra's own episodes re-enter her context via `build_system_prompt`.
- [ ] The salience score is consumed (episodes tagged), not discarded.
- [ ] The MCP memory tool uses the same KNN retrieval as the core.
- [ ] Vision either works or fails legibly.
- [ ] Full test suite green; one clean commit per step.

At that point the existing machinery is *alive* for the first time, the box has a tested seam, and you're positioned to start **Phase 1 (perception loop)** — and, more importantly, you'll have learned from running it which Phase 1/3 assumptions actually hold.

---

## How to use this plan

Each step is **one Claude Code prompt**. When you're ready for a step, we write that prompt together — scoped to exactly that step's files and single verifiable outcome, nothing built ahead of it. Don't batch steps into one prompt; the one-change-one-checkpoint discipline is what keeps you able to tell what broke.

**Recommended first prompt:** Step 1 (the interface contract), *after* you've reviewed and signed off on the type fields sketched above — since that's the decision the rest of the box is built on.
