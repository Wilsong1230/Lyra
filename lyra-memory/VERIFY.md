# Verification walk

Companion to `MEMORY_SPEC.md`. The spec's build order ends with **"One per
session. Verify before advancing."** The code for steps 0–9 landed in one
commit, so that verification has not happened. This is the walk.

Work top to bottom. Do not advance on a step you have not actually looked at.

**What "verified" means here:** not "the tests pass." The unit tests were written
alongside the code they test, on a deterministic fake embedder, so they check
plumbing and invariants — not whether retrieval returns the right thing or
whether a threshold is set anywhere near correctly. Every step below has a
*judgment* column for the calls that only real data can settle.

---

## Step −1 · Prerequisite: the real embedder

**Nothing below is meaningful until this passes.** All development ran on
`hash_embedder`, a deterministic offline substitute with no notion of
paraphrase. Its vectors are not comparable with MiniLM's.

```bash
cd lyra-memory && source venv/bin/activate
python -c "
from sentence_transformers import SentenceTransformer
m = SentenceTransformer('all-MiniLM-L6-v2')
print(len(m.encode('hello')))"
```

**Pass:** prints `384`.
**Fail:** anything else — you have no semantic retrieval, and steps 3 onward are
untestable. Fix this first.

> If a store was ever written under `hash_embedder`, delete it. Vectors from the
> two embedders are in different spaces and a mixed store retrieves nothing.

---

## Steps 0–3 · Verifiable in one sitting each

These need no accumulated history. Use a scratch store, not `~/.lyra/memory.db`.

### Step 0 · `trait_history`

```bash
python - <<'PY'
import asyncio, tempfile, time
from pathlib import Path
from lyra_memory import MemorySystem

async def main():
    m = MemorySystem(db_path=Path(tempfile.mkdtemp()) / "m.db")
    await m.start(run_dream_loop=False)
    for _ in range(5):
        await m.candidate_pool.add_observation("terse", "short answers", "behavioral", closed_vocabulary=True)
    await m.identity_engine.consolidate()
    for _ in range(10):
        await m.candidate_pool.add_observation("terse", "short answers", "behavioral", closed_vocabulary=True)
    await m.identity_engine.consolidate()

    for h in await m.identity_engine.history("terse"):
        print(h.event, h.tier_before, "->", h.tier_after, h.conf_before, "->", h.conf_after)
    print("audit (should be []):", await m.identity_engine.audit())
    print("replay now:", await m.identity_engine.replay(time.time()))
    await m.stop()
asyncio.run(main())
PY
```

**Pass:** two rows — `promoted None -> surface`, then `tier_change surface ->
character`. `audit()` empty. `replay()` matches the current `traits` row.
**Fail:** a mutation with no history row, or `replay()` disagreeing with `traits`
— history is supposed to be the source of truth.

**Judgment to check:** nothing tuned here. This step is mechanical.

---

### Step 1 · Turns land

Run the real CLI, say three things, quit, then look:

```bash
lyra                     # say something, get replies, exit
python -m lyra_memory.inspect_state
```

**Pass:** the `STORE` block shows a nonzero `atoms` count matching your turns
(user *and* Lyra), and no `!!` warnings.
**Fail:** zero atoms. That is the 653-turns-0-episodes failure, and it should now
be loud rather than silent — check the terminal for a raised exception.

**Judgment to check — the one measurement I never made:** the spec budgets the
hot path at **<50ms** and I never timed it. Time it:

```bash
python - <<'PY'
import asyncio, tempfile, time
from pathlib import Path
from lyra_memory import MemorySystem

async def main():
    m = MemorySystem(db_path=Path(tempfile.mkdtemp()) / "m.db")
    await m.start(run_dream_loop=False)
    times = []
    for i in range(20):
        t = time.perf_counter()
        await m.atom_store.append("wilson", "cli", f"a turn about the memory layer, number {i}")
        times.append((time.perf_counter() - t) * 1000)
    times.sort()
    print(f"median {times[10]:.1f}ms   p95 {times[18]:.1f}ms   max {times[-1]:.1f}ms")
    await m.stop()
asyncio.run(main())
PY
```

Measured here with the *fake* embedder, the three inserts alone run **~1.4ms
median / 1.9ms p95**. So the 50ms budget rests almost entirely on the embed
call, and that is where to look if the number above is bad — not at the
transaction. If the median exceeds 50ms with real MiniLM, that is a genuine
finding and belongs in the spec's Open list.

---

### Step 2 · Failure is loud

```bash
python - <<'PY'
import asyncio, sqlite3, tempfile
from pathlib import Path
from lyra_memory.db import init_db, SCHEMA_VERSION

async def main():
    p = Path(tempfile.mkdtemp()) / "m.db"
    c = await init_db(p); await c.close()
    sqlite3.connect(p).execute(
        "UPDATE schema_meta SET value=? WHERE key='schema_version'", (str(SCHEMA_VERSION + 1),)
    ).connection.commit()
    try:
        await init_db(p); print("FAIL — opened a mismatched store")
    except Exception as e:
        print("PASS —", type(e).__name__)
asyncio.run(main())
PY
```

Then the harder one, by hand: start `lyra` with `~/.lyra/memory.db` **chmod
000**. It should crash on start with something legible, not run and quietly
remember nothing.

**Judgment to check:** I made `MemoryBridge.get_system_prompt` block up to 20s
because recall synthesis now adds an LLM call. If that stall is unacceptable in
the CLI, the fix is to pass `llm=None` for interactive turns rather than to
restore the swallow.

---

### Step 3 · Retrieval assembly — *the step most likely to be wrong*

Have a handful of real conversations first (5–10 turns across two sittings), so
there is something to retrieve. Then:

```bash
python - <<'PY'
import asyncio
from lyra_memory import MemorySystem
from lyra_memory.retrieval import build_context

async def main():
    m = MemorySystem(); await m.start(run_dream_loop=False)
    ctx = await build_context(m, query="<ask about something you actually discussed>")
    print(ctx)
    print("\n---", len(ctx.encode()), "bytes")
    print("misses:", (await m.context_log.recent())[0]["misses"])
    print("miss rate:", await m.context_log.miss_rate())
    await m.stop()
asyncio.run(main())
PY
```

**Read the output as prose, not as a checklist.** The question is whether it
reads like memory or like search results.

**Pass:** blocks appear in order (facts, traits, commitments, recall, recent);
recall pulls things that are actually relevant; non-conversation total under
~6000 bytes.
**Fail:** recall full of near-misses, or empty on a query you know is covered.

**Judgment to check — every one of these is a guess I made with no real
vectors, and all live in `config.py`:**

| constant | I set | what falsifies it |
|---|---|---|
| `SEMANTIC_SIMILARITY_FLOOR` | 0.35 | recall full of junk → raise it. Recall empty on covered queries → lower it. This is the highest-value dial. |
| `RECALL_DEDUPE_SIMILARITY` | 0.92 | same memory appearing twice → lower it. Distinct memories collapsing → raise it. |
| `LYRA_RECALL_WEIGHT` | 0.5 | her own phrasing still dominating recall → lower it |
| `CONTEXT_BUDGET_BYTES` | spec's token counts × 4 | blocks truncating mid-thought → the 4 bytes/token estimate is wrong for your text |
| `RECENT_TURNS` | 10 | spec says 8–10; pick by reading |

Also worth checking: `miss_rate()` over a real day. A high rate early is
expected and informative — it shows where the store is thin, which is the point
of logging misses at all.

---

## Steps 4–9 · Need accumulated real usage

These cannot be verified tonight. Dream is a cold pass over a real session and
needs `OPENROUTER_API_KEY` set. Run Lyra normally for a few days first, then
walk these one per session.

| step | verify with | judgment to check |
|---|---|---|
| 4 · dream as layer | `inspect_state` → `dreams` count nonzero; `SELECT * FROM dream_atoms` points at real atoms of that session | `DREAM_TARGET_CHARS = 600` — read a few. Too terse to be worth keeping? Too long and it is an essay again. |
| 5 · segmentation | `query_memory(m, "sessions")` — do the boundaries match sittings you remember? `gap_since_prev` sane? | `SESSION_GAP_SECONDS = 1800`. A guess. If one evening splits into four sessions, raise it. |
| 6 · salience | `SELECT text, salience FROM atoms ORDER BY salience DESC LIMIT 20` | do the top 20 look like what mattered? The four `SALIENCE_*` weights are unvalidated. Check it does not saturate the way the old `max()` did — 13 of 29 at 0.933. |
| 7 · entities | `SELECT name, kind FROM entities` | the capitalised-word heuristic **will** produce junk topics. Count the false positives before trusting `facts.subject` joins. |
| 8 · facts | `query_memory(m, "facts")`; `await m.facts.instrumentation()` | the conflict-detection prompt has never run against a real model. Read every flagged conflict: the spec predicts most are *refinement*, not contradiction. If it flags refinements, the prompt is wrong. |
| 9 · commitments | say "I'll do X tomorrow", let a dream cycle run, then `query_memory(m, "commitments")` | does extraction fire at all? Does it fire on things that were not commitments? Closure detection is the harder half — check it does not close on mere mention. |

**On step 8, the standing instruction from the spec:** do not write the
supersession-resolution rule until ~50 real flagged conflicts have accumulated.
`instrumentation()["automation_gate_reached"]` tells you when.

---

## Steps 10–11 · Deliberately unbuilt

Clustering and the forgetting pass are not implemented, per the spec.
`atoms.retrievability` exists as a nullable and nothing reads it. Do not build
either until segmentation (step 5) is verified and the store is large enough to
tune a decay curve against — the spec estimates roughly a year of runtime.

---

## Known-unverified, carried from the build

- Hot-path latency never measured (step 1 above).
- No constant in `config.py` has been tuned against real MiniLM vectors.
- The dream, conflict-detection, and extraction prompts have never run against a
  real model — only against stubs returning fixed JSON.
- The §1 filesystem boundary is **not enforced by this code and cannot be.**
  `MemoryQuery.open()` is read-only and `audit()` detects out-of-path writes,
  but nothing stops a process that can already open `~/.lyra/memory.db` for
  writing. That needs the sandbox running as a different user with the store at
  0600, or a volume it is not mounted into. Standing question before shipping
  any new capability: *does this create a path to `~/.lyra`?*
