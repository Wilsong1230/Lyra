# Lyra — Systems Documentation

*A complete technical reference for what Lyra is and what has actually been built, as of the current codebase.*

This document describes the system as it exists in code, not as it is aspired to be. Where the built system falls short of the design vision, that gap is stated explicitly — the project's own design docs (`docs/lyra-design-state-2026-08-25.md`, `lyra_ai/DECISIONS.md`, `lyra-memory/DECISIONS.md`) are unusually candid about this, and this document inherits that discipline.

---

## 1. What Lyra Is

Lyra is not conceived as a chatbot with a 3D face. The stated intent (`docs/lyra_spec.md`, `docs/lyra-cognitive-architecture-roadmap.md`) is an android: a continuous, autonomous entity with persistent memory, sensory perception, and — eventually — free will, whose personality *emerges* from accumulated experience under pressure rather than being authored into a prompt.

Seven non-negotiable design principles govern the project:

1. **Personality is never authored** — only the conditions for its emergence are built. If a trait is ever hand-written into a prompt, that is a bug.
2. **Service is chosen, not forced** — at least one drive (boredom) is self-directed and serves no one.
3. **The harm gate is a separate layer, not a drive** — affect and drives can never outvote it.
4. **Constraint precedes capability** — the gate is built and tested while the action space is near-zero.
5. **Influence, not control** — nudging happens at the margins; emergence means Lyra may drift somewhere unintended.
6. **The relational drive's success signal must stay honest** — it can never collapse into "she did a task" or "Wilson seemed pleased," or the emergent personality becomes a people-pleaser.
7. **The cognitive core is a black box** — peripherals (senses, voice, avatar) never reach inside it; they talk to it through one narrow observations-in / intents-and-affect-out contract.

The practical reality, measured directly against the running system, is more modest than the vision: a well-engineered pipeline with real affective dynamics and a genuinely uncircumventable harm gate, whose action loop is currently *open* rather than closed on the daemon's production path (see §10). The scaffolding for emergence is real; very little has emerged yet, and the project's own docs say so.

---

## 2. Repository Layout

Lyra is a **monorepo**: five independently-runnable microservices plus two core Python packages, each with its own venv (their dependency trees conflict, so a shared venv isn't possible).

| Component | Port | Directory | Role |
|---|---|---|---|
| `lyra_ai` | daemon: 8010 (TCP) | `lyra_ai/` | Core package — the cognitive daemon (`lyra_core`) and the thin CLI client (`lyra`) |
| `lyra-memory` | — | `lyra-memory/` | Memory package: two coexisting generations of persistent memory (see §7) |
| lyra-embodiment | 8000 | `lyra-embodiment/` | Three.js avatar, 8 emotional states, REST |
| lyra-voice | 8001 | `lyra-voice/` | TTS via Kokoro (default) or Coqui XTTS |
| lyra-listen | 8002 (STT), 8004 (ambient), 8005 (wakeword) | `lyra-listen/` | Whisper STT, YAMNet ambient sound, openWakeWord |
| lyra-vision | 8003 | `lyra-vision/` | Screen/webcam capture → Gemini or OpenRouter vision model |
| lyra-mcp | stdio | `lyra-mcp/` | MCP server aggregating the four peripheral services (plus a direct read of lyra-memory) for Claude Desktop |

`docs/` holds specs, design notes, decision logs, and `PICKUP.md` session handoffs — the project's memory of itself, outside the runtime.

## 3. Setup & Running

```bash
git clone <repo>
cd Lyra
./bootstrap.sh          # creates every venv, seeds .env from .env.example
$EDITOR .env             # paste in API keys

./lyra-embodiment/start.sh &
./lyra-voice/start.sh &
./lyra-listen/start.sh &
./lyra-vision/start.sh &

lyra_ai/venv/bin/python -m lyra_core --init-store &   # first run only; then omit --init-store
lyra_ai/venv/bin/lyra                                  # attach the CLI
```

`bootstrap.sh` creates a venv per service (`<service>/venv/`), installing `lyra-memory` as an editable cross-dependency wherever needed. `lyra-voice` is pinned to Python <3.13 (both TTS engines declare `Requires-Python <3.13`); bootstrap auto-detects `python3.12`/`3.11`/`3.10` or honors `VOICE_PYTHON`.

Two dependencies are opt-in so a default bootstrap stays fast: `lyra-voice/requirements-coqui.txt` (Coqui XTTS) and `lyra-listen/requirements-ambient.txt` (YAMNet). Without them the services still start — ambient simply reports no detections.

---

## 4. High-Level Architecture

```
                         ┌─────────────────────────────┐
   lyra (CLI, thin)  ──▶ │   lyra_core daemon (:8010)   │  ◀── the ONE CognitiveCore
   readchar terminal     │   python -m lyra_core        │       (memory.db + history.db)
                         └───────────┬─────────────────┘
                                     │ HTTP (peripheral calls)
              ┌──────────────┬───────┴───────┬──────────────┐
              ▼              ▼               ▼              ▼
        lyra-embodiment  lyra-voice     lyra-listen     lyra-vision
           :8000            :8001      :8002/4/5           :8003
        (avatar/REST)     (TTS)      (STT/ambient/wake)   (vision)

                         lyra-mcp (stdio) ── aggregates all four + lyra-memory,
                                             for Claude Desktop
```

Two independent things sit behind the name "Lyra":

- **The mind** (`lyra_ai/lyra_core`) — a single long-lived daemon process holding one `CognitiveCore`, one memory store, and one conversation history. The `lyra` CLI is a thin client with no cognition of its own; it connects over a private TCP protocol, sends turns, prints replies.
- **The body** — four independent HTTP microservices (embodiment, voice, listen, vision) that the daemon and CLI both call into. They talk to each other too (voice/listen POST avatar-state changes to embodiment while speaking/listening), and they treat those calls as fire-and-forget — the avatar being down never breaks core functionality.

This CLI/daemon split (checkpoint "CP-A") is recent and significant: earlier in the project's history, the CLI and a standalone runtime each constructed their own `CognitiveCore` against the same database file, so a reply to something Lyra said unprompted arrived at a different mind than the one that spoke. CP-A collapses this to one process, one core.

---

## 5. `lyra_ai` — Core CLI & Cognitive Daemon

### 5.1 Package layout

```
lyra_ai/
  main.py                 # loads .env, calls lyra.cli.main() — the `lyra` console-script entry
  lyra/                    # thin client (the "body in the room")
    cli.py                 # the `lyra` REPL
    assistant.py            # LyraClient (frame-based TCP client), DEFAULT_SYSTEM prompt text
    backends.py              # LLM backend abstraction (used by the daemon, not the CLI, post-CP-A)
    memory.py                 # ConversationMemory — sqlite turn history at ~/.lyra/history.db (used by the daemon)
    memory_bridge.py           # orphaned sync shim over MemorySystem; kept only because DEFAULT_SYSTEM lives nearby
  lyra_core/                # the cognitive daemon (the "mind")
    __main__.py               # `python -m lyra_core` entry point
    config.py                  # DAEMON_HOST/PORT, protocol version, frame limit, dt clamp
    transport.py                # line-delimited JSON-over-TCP framing + TurnServer
    runtime.py                   # Runtime, TurnHandler, TickClock — daemon orchestration
    interface.py                  # Observation / Intent / AffectState contract, CognitiveCore
    affect.py                      # AffectEngine — three-timescale affect dynamics
    drives.py                       # CompetenceTracker, BoredomDrive, RelationalDrive
    action_selection.py              # ActionSelector — drives + affect → Intent
    gate.py                           # HarmGate — the one hard filter
    expression.py                      # prose_hint() — affect → system-prompt style directive
    development.py                      # OutcomeConsolidator, trait naming, bias_from_traits, TemperamentTuner
    outcomes.py                          # OutcomeTracker (orphaned — see §6.7)
    actuators.py                          # SpeechActuator (orphaned — see §6.7)
    perception.py                          # PerceptionLoop (orphaned — see §6.7)
    senses.py                               # poll_ambient / poll_wakeword (orphaned — see §6.7)
    harness.py                               # test rig: drive CognitiveCore.tick() with no services running
```

### 5.2 The daemon owns the one core

`python -m lyra_core` is the mind. On startup, `Runtime.start()`:

1. Validates the sqlite memory store's schema (requires tables `atoms`, `vec_atoms`, `facts`, `traits`). A store with the wrong schema is archived by rename (`memory.db.<date>.archive`, with its WAL/SHM sidecars) and recreated; a *missing* store is fatal unless `--init-store` is passed, which creates one deliberately.
2. Constructs exactly one `CognitiveCore` (a module-level guard raises if construction is attempted twice in one process) and logs the literal string `CORE_CONSTRUCTED` — grep-able proof of single-locus construction.
3. Starts it (restoring persisted affect state from the `affect_state` fact row).
4. Opens `ConversationMemory` history (`~/.lyra/history.db`).
5. Builds a `TurnHandler` and starts a `TurnServer` listening on `127.0.0.1:8010`.

Backend selection (`--backend`, `--model`, `--list-backends`, or `auto_select_backend()`) happens **in the daemon**, not the CLI — this moved during CP-A along with `.env` loading.

### 5.3 Transport protocol

`lyra_core/transport.py` frames turns as line-delimited JSON over a loopback TCP socket:

```json
{"v": 1, "type": "turn", "text": "...", "session": "optional-uuid"}
```

Every turn from every connected client funnels through **one `asyncio.Queue`** drained by a single worker task, so turns are serialized and answered in arrival order even with multiple clients attached. Malformed input gets an error frame back; the daemon stays up. A backend (LLM) failure becomes an error frame too — the daemon survives it. Any other exception in the turn path (history write, atom write, retrieval, affect) is treated as **fatal**: the client is told the daemon is stopping, teardown is attempted, and the process exits 1. The reasoning: the user's turn is already durably recorded by the time the model is asked, so a swallowed failure past that point would silently corrupt the record of what happened.

### 5.4 Turn handling flow

`TurnHandler.handle(message, session)`:

1. Writes the user's turn to history.
2. Calls `core.tick("conversation", message)` — this advances affect/drives and runs the harm gate, but **its returned intents are discarded** on the daemon's turn path (see §6.7); only bound to `last_intents` for inspection.
3. Assembles the system prompt: `DEFAULT_SYSTEM` (identity, harm boundary, output rules, vision-tool instructions) + a memory retrieval block (`lyra_memory.retrieval.build_context` — see §7) + `expression.prose_hint(core.introspect())` (an affect-driven style directive).
4. Calls the LLM backend (`asyncio.to_thread`, since backends use blocking `http.client`).
5. Resolves the **vision tool protocol**: if the model's reply contains `[TOOL:see:screen]` or `[TOOL:see:webcam]` on its own line, `parse_tool_call()` detects it, text after the token is discarded (the model can't have seen the result yet), and `call_vision(source)` POSTs to `lyra-vision`'s `/see` endpoint. The result re-enters as an observation and the model is called again. This loops up to 3 times per message; after 3 failed attempts a fixed apology string is used. Vision unavailability is recorded as the observation itself — "she asked to see and could not" is treated as what happened, not swallowed.
6. Ticks the reply into the core as source `"lyra"`, records it to history, and returns it to the client.

### 5.5 Backend abstraction (`lyra/backends.py`)

An abstract `Backend` (`chat()`, `stream_chat()` default-wraps `chat()`, `list_models()`), implemented with raw `http.client` — no SDK dependencies:

| Backend | Env var | Default model | Notes |
|---|---|---|---|
| `AnthropicBackend` | `ANTHROPIC_API_KEY` | `claude-sonnet-4-6` | `/v1/messages`; hardcodes `api.anthropic.com` (`ANTHROPIC_BASE_URL` is not honored) |
| `OpenRouterBackend` | `OPENROUTER_API_KEY` | `openai/gpt-4o-mini` | OpenAI-compatible |
| `CerebrasBackend` | `CEREBRAS_API_KEY` | `gpt-oss-120b` | OpenAI-compatible |
| `OllamaBackend` | — (local) | `llama3.2` | `OLLAMA_BASE_URL` default `http://127.0.0.1:11434`; **the bare default tag resolves to `llama3.2:latest`, which may not be the tag actually pulled** — pass an explicit tag |

`auto_select_backend()` tries each in registry order (anthropic → openrouter → cerebras → ollama) and returns the first that constructs successfully.

### 5.6 The CLI (`lyra`)

A pure client. It reads input char-by-char via `readchar` (or line-by-line when stdin isn't a TTY) to catch Ctrl+R for push-to-talk (posts to lyra-listen's record start/stop) and `/`-prefixed commands (`/help`, `/quit`, `/session[,new,<id>]`, `/voice`). Non-command input is framed as a turn and sent to the daemon; the reply is printed and, if voice mode is on, POSTed to lyra-voice's `/speak`. If no daemon is listening, the CLI prints an error and exits nonzero — it never starts a core itself. If the daemon dies mid-session the client's `closed` event fires and the CLI exits promptly (verified at 24ms). Several commands that made sense against a CLI-owned core were dropped in CP-A (`/stream`, `/history`, `/clear`, `/backend`, `/model`) since the daemon now owns history, streaming, and backend choice.

---

## 6. `lyra_core` — The Cognitive Engine

This is the "mind": a typed, constructor-injected pipeline from observation to affect to (gated) intent. Every component is designed to be testable in isolation via `harness.py`, with no services, no body, and no HTTP required.

### 6.1 The interface contract

`interface.py` defines the black-box seam that the rest of the system is built around:

- **`Observation`** (IN) — uniform event shape regardless of source: `kind` (`sensory` | `action_outcome` | reserved `external_affect`), `source`, `content`, `ts`, optional `predicted`/`actual` (the prediction-outcome pairing that feeds the competence drive), optional `affect_hint` (a reserved, currently-unfilled slot for Wilson's sensed affect — the "empathy channel").
- **`Intent`** (OUT) — "what I want to do": `kind` (`speak` | `look` | `set_state` | `noop` | reserved `research`), `payload`, `ts`. The core emits intent only — never a waveform or a hex color; peripherals translate intent into their own domain.
- **`AffectState`** (OUT) — "how I am": a three-timescale structure — `emotion` (fast, always populated), `mood` (medium-term offset, now genuinely populated), `temperament` (the time constants themselves, now genuinely populated). `AffectVector` carries `valence`, `arousal`, and a reserved, unused `control` (dominance) axis.
- **`CognitiveCore.tick(observations, dt) -> (intents, affect_state)`** — the single entry point.

`introspect()` returns a freshly constructed snapshot — observing the core never mutates it.

### 6.2 `tick()`, step by step

1. **Ingest.** Sensory observations become conversation turns or generic observations in memory. `action_outcome` observations with both `predicted` and `actual` set feed `CompetenceTracker.observe_error`; on failure, `RelationalDrive.observe_recurrence(...)` fires; if a candidate pool exists, `OutcomeConsolidator.record_outcome(...)` runs.
2. **Advance drives.** `BoredomDrive` and `RelationalDrive` step forward by `dt`.
3. **Advance affect.** Their combined `AffectPush`es feed `AffectEngine.update()`.
4. **Select.** Promoted traits become a `SelectionBias` (`development.bias_from_traits`); `ActionSelector.select(pressures, affect, bias)` proposes intents.
5. **Gate.** Every proposed intent passes through `HarmGate.check()` — the single chokepoint. Rejections are logged (`[gate] BLOCKED ...`).
6. Returns `(intents, affect_state)`.

### 6.3 Affect (`affect.py`)

Three timescales — **emotion** (fast), **mood** (medium, a slow running average that offsets emotion), **temperament** (the time constants themselves, very slow/plastic) — updated with an **exact exponential relaxation** integral, not explicit Euler. This is a deliberate fix: an earlier Euler implementation was unstable at the perception loop's old 2-second poll rate (idle valence measured diverging past 3×10¹⁴ after thirty ticks); the closed-form `1 − e^{−k·dt}` relaxation toward mood is stable at any `dt`.

An `encourage(strength, duration)` channel exists specifically to implement the design's "encouragement" mechanic: it never raises valence directly (which would teach frustration summons comfort and breed approval-seeking) — instead it temporarily shallows the *accumulation* of negative valence input, modulating a time constant rather than injecting reward.

Affect persists across restarts via `structured_state`'s `affect_state` fact.

### 6.4 Drives (`drives.py`)

- **`CompetenceTracker`** — a rolling window (default 6) of recent prediction-error magnitudes. `at_learnable_edge` is true when errors are both above a boredom threshold (0.1) and shrinking — the "learnable edge" criterion from the design docs: fully predictable is boring, fully unpredictable is overwhelming, and only *shrinking* error in between counts as productive engagement.
- **`BoredomDrive`** — pressure decreases (relief) only when engaged *and* at the learnable edge; otherwise it grows unconditionally with idle time. Disengagement is never relief, by design. Feeds negative valence and positive arousal proportional to pressure.
- **`RelationalDrive`** — tracks recurring "problems" and their time-since-last-recurrence; pressure is negative (unsatisfied) until a problem stops recurring, at which point a small positive bonus fires once. `mark_problem()` exists as a stub seam but is never actually called on the current daemon path.

### 6.5 Action selection (`action_selection.py`) — the feedback arrow

`ActionSelector.select()` computes `frustration = max(0, -affect.valence) * effective_weight`, where boredom or relational pressure exceeding frustration wins out into a `look`/`speak` intent; frustration exceeding both drives collapses everything to `noop`. This is the mechanism by which bad affect can override a steady drive and effectively "quit" — the anti-people-pleaser design intentionally allows disengagement under frustration, counterbalanced only by the fact that disengaging never relieves boredom. The selector never calls the gate itself; `tick()` does, immediately after.

### 6.6 The harm gate (`gate.py`)

`HarmGate.check(intent)` is the single entry point, and its signature takes only an `Intent` — no affect, no drives, no urgency parameter of any kind. This is deliberate and structural: motivational state cannot influence the gate because it isn't in scope to. Mechanism: a default-deny allow-list, `ALLOWED_KINDS = {speak, set_state, look, noop}`. The reserved `research` kind is **not** allow-listed, so any such intent is blocked by construction until someone explicitly widens the list. `GateDecision(allowed, reason)` is the return type.

### 6.7 What's actually wired vs. what's dormant

This is the most important section for anyone extending the system. The codebase contains substantially more machinery than the daemon currently runs.

**Wired and live on the daemon's turn path:**
- `CognitiveCore.tick()` runs every turn (its intents are discarded downstream — see below).
- `AffectEngine`, `BoredomDrive`, `RelationalDrive` advance every tick.
- `HarmGate` and `ActionSelector` run inside `tick()`, but since nothing downstream executes the returned intents, the gate currently has nothing consequential routed through it.
- `expression.prose_hint()` genuinely reaches every system prompt.

**Structurally present but practically unreachable on the daemon path:**
- `OutcomeConsolidator` / the trait-promotion side of `development.py` requires an `action_outcome` Observation carrying both `predicted` and `actual`. Nothing in `TurnHandler` currently constructs one, so this component has effectively never fired in production use of the daemon.
- `CompetenceTracker.at_learnable_edge` is unreachable for the same reason — no outcomes ever arrive to populate its error window.

**Fully disconnected — exist with their own passing tests, but zero references from `runtime.py` or `__main__.py`:**
- `perception.py` (`PerceptionLoop`) — an independent poll/dedup/sink loop that used to drive the old always-on runtime.
- `senses.py` (`poll_ambient`, `poll_wakeword`) — pollers for lyra-listen's ambient and wakeword endpoints.
- `actuators.py` (`SpeechActuator`) — the component that would actually execute a `speak` intent through lyra-voice's `/speak`.
- `outcomes.py` (`OutcomeTracker`) — watches for engagement after unprompted speech and is the thing that would produce the `action_outcome` observations `OutcomeConsolidator` needs.
- `lyra/memory_bridge.py` — CLI-side, explicitly deletion-deferred.
- `TemperamentTuner` (in `development.py`) — present, `enabled=False` everywhere, a strict no-op.

**Why this is deliberate, not neglect** (`lyra_ai/DECISIONS.md`, checkpoint CP-A): an earlier build *did* wire the perception loop, actuator, and outcome tracker together into a continuously-ticking runtime, and it worked — Lyra spoke unprompted, the loop closed, a trait candidate got written for the first time. But a loop ticking every 2 seconds through an idle 8-hour night drives `BoredomDrive` pressure to roughly 2,880 and valence to roughly −1,150 per tick — deeply saturated, regardless of any single-tick clamp — because nothing on an idle daemon relieves boredom. Retuning the drives for that regime was explicitly out of scope for CP-A, so the daemon was built to **tick only when a turn arrives** (with wall-clock `dt` clamped to `MAX_TICK_DT_SECONDS = 0.1`, chosen specifically to reproduce the pre-CP-A conversational behavior to the decimal), and the perception loop / actuator / outcome tracker were left disconnected rather than reworked under time pressure. The reversal path is named and waiting: re-instantiate `PerceptionLoop` in `Runtime.start()` once the drives are retuned for continuous, wall-clock operation.

The practical upshot: today, talking to Lyra shapes her affect and would, in principle, pass intents through the gate — but no intent is ever executed, no outcome ever returns, and no trait has a live path to promotion through conversation alone. The harm gate, the affect engine, and the drives are real and running; the loop that would let outcomes shape identity is not currently closed on the path anyone actually uses.

---

## 7. `lyra-memory` — The Memory System

The package contains **two coexisting memory systems**, writing to two different SQLite files, both live in the code. This isn't decay — it's a mid-flight architectural rebuild, done in place, with the old system left running untouched specifically so nothing regresses while the new one is validated.

### 7.1 Generation 1 — the four-layer system (`~/.lyra/memory.db`, currently in production use)

This is the system `CLAUDE.md` and `docs/lyra_spec.md` describe, and the one the CP-A daemon actually opens.

| Layer | File(s) | Persistence | Purpose |
|---|---|---|---|
| Working memory | `working_memory.py` | In-process only | `WorkingMemory`: a deque(20) of conversation/observation/reflection items, each scored `(importance + surprise + emotion) / 3` at insertion |
| Episodic | `dreaming_loop.py`, `atoms.py`, `db.py` | SQLite, permanent | `DreamingLoop` polls every 30s, fires on ≥10 accumulated items or ≥300s idle; writes an LLM-generated first-person reflection to `episodes`, and — after the atoms migration — decomposes retrieval into per-turn **atoms** (`atoms` + `vec_atoms`), so `episodes` is now a consolidation layer rather than the retrieval unit itself |
| Structured state | `structured_state.py` | SQLite | Key/value `facts` table, JSON-serialized values (e.g. persisted affect state) |
| Identity / traits | `candidate_pool.py`, `identity_engine.py` | SQLite | Trait candidates accumulate, deduplicate, and promote (shared with Generation 2 — see §7.3) |

Retrieval (`retrieval.py`) provides `build_system_prompt()` / `build_context()`, `search_episodes()` (KNN over `vec_atoms`), and `get_fact()`. Embeddings throughout are `all-MiniLM-L6-v2` (384-dim, unit-normalized, via `sentence-transformers`), stored in `sqlite-vec` (`vec0`) virtual tables, queried with `k=N` in the `WHERE` clause. `inspect_state.py` is a read-only CLI (`python -m lyra_memory.inspect_state [--watch] [--truncate] [--affect]`) for watching the candidate pool and promoted traits live.

**Why it was rebuilt.** A measured audit of the running system (recorded in `atoms.py`'s own docstring and `docs/lyra-design-state-2026-08-25.md`) found:
- Episodes were long first-person essays (~3,000 chars, up to 9,800), not atomic retrieval units. A live assembled system prompt measured **44,486 characters**, ~43,000 of it retrieved episode text, with essay headings structurally indistinguishable from the prompt's own scaffolding.
- Salience saturated: 13 of 29 episodes sat at the maximum score 0.933, because salience was computed as `max()` over a whole consolidation batch.
- Trait dedup matched on candidate **name**, at cosine 0.3. The model invented a fresh name almost every dream cycle (`self_awareness`, `self_modeling`, `self_monitoring`, `self_reflection` all coexisted as distinct rows). Of 70 candidates, 62 had been seen exactly once; in three months of use, exactly **one** trait ever promoted — `communication_style`, at the lowest confidence tier (0.10).

The `atoms` migration (decomposing episodes into per-turn atoms with per-item salience) addressed the first two problems within Generation 1 itself, cutting a measured assembled prompt from 44,486 to 6,436 characters. Trait dedup was separately reworked to match on **description + evidence** rather than name, at cosine 0.37 (see §7.4) — this change is shared code, so it benefits both generations.

### 7.2 Generation 2 — the rebuilt store (`lyra_memory/store/`, `~/.lyra/store.db`, not yet wired into the daemon)

Built specifically to fix the failures above from scratch, with a stated invariant: **the hot path appends only; everything structural is nullable, cold, and derived.** A cold pass crashing leaves the store correct, because the atoms it read were permanent before it started. This is a separate file and a separate schema — it shares table *names* with the old system but not columns, and there is deliberately no migration between them. **Confirmed not yet imported anywhere in `lyra_ai`** — this is the next-generation memory architecture, present in the codebase, exercised by its own test suite and retrieval-quality harness, but not the store the daemon opens today.

```
store/
  schema.py         DDL, SCHEMA_VERSION, enforced vocabularies (SPEAKERS, SOURCES, ENVIRONMENTS)
  integrity.py       boot-time structural assertion (tables, columns, indexes, triggers,
                      and — critically — that the embedder that built the store's vectors
                      matches the current process's embedder)
  __init__.py         Store — the hot append path: append_atom(), ingest_turn(),
                        record_outcome(). No try/except around the write path — fail-closed.
  context.py            build_context() — five-block context assembly (facts/traits/
                          commitments/recall/recent) under a 2000-token budget
  evaluate.py             retrieval-quality harness against eval/retrieval_baseline.json
  review_facts.py           read-only operator CLI
  review_forgetting.py       read-only operator CLI
  passes/
    cold.py                  ColdPass — every cold pass backs up the DB first (SQLite
                              backup API, 30-day retention; pruning is deliberately manual)
    dream.py                  reflection → dreams + dream_atoms, hard-truncated to 700 chars
                              (vs. the old system's unbounded essays); extracts ≤3 trait
                              observations; excludes source=sandbox_read from input
    segmentation.py            pure find_boundaries() — 30-minute gap is a hard session
                                boundary; an embeddings-based corroboration signal exists
                                but is deliberately disabled (it measured worse: precision
                                1.00 → 0.89)
    salience.py                  outcomes → atoms.salience; ignores the sign of valence —
                                  a failure is exactly as memorable as a success
    entities.py                    LLM extraction (people/projects/topics) + regex
                                    extraction (file paths/repo names) into the same tables
    facts.py                        durable claims → facts, with conflicts flagged but
                                    never auto-resolved
    commitments.py                    open loops, evidence-only closure — never closed by
                                      due-date alone, and never given an invented deadline
    clustering.py                      connected components over entity co-occurrence
                                        (not embedding similarity) → project entities
    forgetting.py                       retrievability = exp(-age_days / stability); below
                                        threshold an atom drops out of semantic, lexical,
                                        AND temporal recall — but is never deleted, and
                                        stays reachable by exact id/time/session/entity
```

**Context assembly (`context.py`).** Five budgeted blocks (facts 200 tok / traits 100 / commitments 100 / recall 400 / recent = remainder, 2000-token total) with recall fused from three independent paths — semantic KNN (similarity floor 0.35, not a rank cutoff), lexical FTS5/BM25, and temporal recency — combined via **reciprocal rank fusion** (`1/(60 + rank)` per path) rather than any weighted sum, because the three scores don't share a scale and a weighted sum would be an invented tuning constant. Near-duplicates (cosine ≥ 0.85) are deduplicated; the merged recall is optionally synthesized into one paragraph by an LLM call, falling back to plain concatenation if that call fails — the one guarded call in an otherwise fail-closed context-assembly path, because a synthesis outage should never block a turn the way a corrupted store should.

### 7.3 Trait candidate pool & promotion (`candidate_pool.py`, `identity_engine.py` — shared by both generations)

- Candidates are deduplicated by embedding cosine similarity on their **description + evidence text**, not their name (changed from name-only matching specifically because of the 70-candidates/62-singletons failure above), threshold **0.37**.
- A separate `closed_vocabulary=True` path exists for a fixed set of developmental trait names (e.g. "persists under frustration" vs. "abandons under frustration"), using exact-name matching instead — semantic matching was found to merge lexically-similar *opposites*.
- Candidate vectors merge via a running centroid, not a first-member representative, so the pool doesn't depend on observation order.
- Promotion tiers: `surface` at 5 sightings, `character` at 15, `core` at 50 (confidence `= min(evidence_count / 50, 1.0)`). Traits at `core` tier with confidence ≥ 0.8 are write-protected.
- Every promotion, tier change, or confidence change appends one row to `trait_history`; an integrity assertion runs after every `consolidate()` call, checking that every trait's current state matches its latest history row.

### 7.4 Two databases, one shared identity layer

`~/.lyra/history.db` (CLI/daemon conversation turns, via `lyra/memory.py`'s `ConversationMemory`) is a third, separate SQLite file from either memory generation — it is purely a transcript, not part of the memory/identity pipeline.

---

## 8. The Peripheral Services

### 8.1 lyra-embodiment (`:8000`) — the avatar

FastAPI app, CORS open, holding in-memory global state `{state, color, amplitude}` plus an amplitude envelope buffer for speech-sync animation.

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Serves the Three.js avatar page (`static/index.html`) |
| GET | `/state` | Current `{state, color, amplitude}` — amplitude computed from the envelope by elapsed time since playback started (50ms windows) |
| POST | `/state` | `{state, amplitude_envelope?}` — validates `state` against the known set (422 otherwise); a supplied envelope resets the playback clock for amplitude sync |

**8 emotional states** (code defines 8; the README's "seven" is stale):

| State | Color | Avatar behavior |
|---|---|---|
| idle | `#4A9EFF` blue | Slow steady orbit, nucleus calm |
| thinking | `#9B59FF` purple | Rings precess, speeds desync, nucleus dims |
| speaking | `#2ECC71` green | Nucleus pulses to voice amplitude, rings stabilize |
| curious | `#00D4FF` cyan | Rings tilt toward a focal point, orbit tightens |
| processing | `#F39C12` amber | All dots fast (2.5×), nucleus flickers |
| confused | `#FF4444` red | Dots desync, one ring reverses, irregular drift |
| focused | `#F0F0F0` white | Dots near-stop, rings lock (speed 0.05), nucleus bright |
| listening | `#FF9500` amber | Orbit tightens (added state beyond the original 7) |

The Three.js scene is three orbiting rings plus a nucleus; each state maps to a behavior profile (per-ring speed multipliers, drift/lock/tight/pulse/flicker flags, brightness bias). Color of the nucleus, shell, rings, and HUD text all lerp toward the current state's color.

### 8.2 lyra-voice (`:8001`) — text to speech

Engine selectable via `TTS_ENGINE` (`kokoro` default, or `coqui`).

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | `{status, engine, tts_voice}` |
| GET | `/voices` | Lists 11 Kokoro voices or 28 Coqui voices depending on active engine |
| POST | `/speak` | `{text, sync_emotion=true}` → synthesized WAV bytes |

Kokoro uses `KPipeline(lang_code="b")` (British English); Coqui uses XTTS v2, GPU-accelerated when available. On `/speak`, an RMS amplitude envelope (50ms windows) is computed and POSTed to `EMBODIMENT_URL/state` as `{"state": "speaking", "amplitude_envelope": [...]}`; playback runs in a background thread via `sounddevice`, and on completion the service POSTs `{"state": "idle"}` back. Both embodiment calls are best-effort. `start.sh` and `start-coqui.sh` run the identical server with `TTS_ENGINE` set differently. **Note:** `cli.py`'s `transcribe`/`health` commands reference a `/transcribe` endpoint and an `stt_model` field that don't exist in this service's `server.py` — apparent leftovers from when STT lived here; that functionality is now in lyra-listen.

### 8.3 lyra-listen (`:8002` STT, `:8004` ambient, `:8005` wakeword)

**`server.py` (:8002) — Whisper STT**, model loaded at startup (`WHISPER_MODEL`, default `base`).

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | `{status, stt_model, recording}` |
| GET | `/status` | `{recording}` — polled by the ambient service to avoid double-processing mic audio |
| POST | `/activate` | Starts recording; POSTs `{"state": "listening"}` to embodiment |
| POST | `/transcribe` | One-shot Whisper transcription of an uploaded file |
| POST | `/record/start` | Begins streaming mic capture (`sounddevice.InputStream`) |
| POST | `/record/stop` | Stops capture, transcribes, POSTs `{"state": "idle"}` to embodiment if requested |

**`ambient_service.py` (:8004) — YAMNet.** Optional dependency; loads YAMNet + its class-name CSV at startup, degrading gracefully (no detections) if unavailable. A background loop records 1s windows, and on ≥0.75 confidence for a curated label set (music, dog, knock/slam, ringtone, glass breaking, laughter, applause, alarm, siren, rain, thunder, keyboard typing) records a detection — deferring to STT via `/status` polling first. Endpoints: `GET /ambient`, `GET /ambient/history`, `POST /ambient/start`, `POST /ambient/stop`.

**`wakeword_service.py` (:8005) — openWakeWord.** Loads a model only if `WAKEWORD_MODEL_PATH` points at an existing file; otherwise runs with detection disabled. On a wakeword score ≥ 0.5 (16kHz, 1280-sample frames), fires best-effort POSTs to `LISTEN_URL/activate` (starts STT) and `EMBODIMENT_URL/state` (`"curious"`). Tracks a **daily** detection counter that resets at midnight — a known caller-side gotcha (see §10). Endpoints: `GET /wakeword/status`, `POST /wakeword/start`, `POST /wakeword/stop`.

**Chain:** wakeword detects → activates STT + sets avatar "curious" → listen records/transcribes → sets avatar "idle" when done.

### 8.4 lyra-vision (`:8003`) — sight

Single-file FastAPI service. Backend chosen at import time: Gemini (`gemini-2.5-flash`, via its OpenAI-compatible endpoint) if `GOOGLE_API_KEY` is set, else OpenRouter (`google/gemma-4-31b-it:free`) if `OPENROUTER_API_KEY` is set, else the service refuses to start.

| Method | Path | Purpose |
|---|---|---|
| POST | `/see` | `{source: "screen"\|"webcam", prompt}` → `{description}` |

Screen capture via `PIL.ImageGrab.grab()`; webcam via `cv2.VideoCapture(0)`, one frame. All failure modes (HTTP errors, timeouts, capture failures) are caught and returned as a 200 with `{"description": "Error: ..."}` rather than raised — so a caller can't distinguish "bad request" from "service degraded" except by reading the text, which is a known sharp edge (§10).

### 8.5 lyra-mcp (stdio) — the Claude Desktop aggregator

`FastMCP("lyra")`, run over stdio. Spans not four but **five** subsystems — it also imports `lyra_memory.retrieval` and reads `~/.lyra/memory.db` directly.

| Tool | Params | Backend | Behavior |
|---|---|---|---|
| `set_emotion` | `state` | POST embodiment `/state` | Sets avatar state |
| `get_state` | — | GET embodiment `/state` | Reads current state/color |
| `speak` | `text`, `sync_emotion` | POST voice `/speak` | Speaks text |
| `transcribe` | `audio_path` | POST listen `/transcribe` | Transcribes a local file |
| `list_voices` | — | GET voice `/voices` | Lists available voices |
| `lyra_see` | `source`, `prompt` | POST vision `/see` | Sets avatar "processing" before, "curious" after a successful call |
| `search_episodes` | `query`, `limit` | `lyra_memory.retrieval.search_episodes` | Semantic KNN over episodic memory |
| `get_fact` | `subject_key` | direct SQLite read on `facts` | Returns a stored fact or `None` |

Every HTTP-backed tool catches connection/timeout/status errors and returns a human-readable string rather than raising, so the MCP contract stays simple.

---

## 9. Cross-Service Communication Map

```
wakeword (:8005) ──POST /activate──▶ listen (:8002)
wakeword (:8005) ──POST /state "curious"──▶ embodiment (:8000)
listen   (:8002) ──POST /state "listening"/"idle"──▶ embodiment (:8000)
ambient  (:8004) ──GET  /status──▶ listen (:8002)          (defers detection while STT records)
voice    (:8001) ──POST /state "speaking"/"idle" + amplitude──▶ embodiment (:8000)
lyra_core daemon ──POST /see──▶ vision (:8003)               (vision tool protocol, §5.4)
lyra-mcp         ──▶ embodiment, voice, listen, vision, lyra-memory   (all five, direct)
CLI (`lyra`)     ──▶ lyra_core daemon (TCP :8010), voice (/speak), listen (push-to-talk)
```

All avatar-state POSTs from voice/listen/wakeword are fire-and-forget (`try/except: pass`) — embodiment being offline never breaks STT/TTS/wakeword functionality.

---

## 10. Known Limitations — Current State vs. Vision

Documented directly against the running system (`docs/lyra-design-state-2026-08-25.md`, `lyra_ai/DECISIONS.md`, `lyra-memory/DECISIONS.md`, `docs/PICKUP.md`). This section exists so nobody re-discovers these as "bugs" and quietly patches around the design intent.

- **The action loop is open on the daemon's production path.** `tick()` returns gated intents, but `TurnHandler` never executes them. `perception.py`, `senses.py`, `actuators.py`, and `outcomes.py` are complete, individually tested, and completely disconnected from `runtime.py` — deliberately, because continuous ticking through idle time saturates `BoredomDrive` without a wall-clock-aware retune (see §6.7). This means: no intent has ever been executed by the daemon, no outcome has ever returned to `OutcomeConsolidator` from that path, and no trait has ever promoted from conversational use alone.
- **Two memory generations coexist.** The daemon opens `~/.lyra/memory.db` (Generation 1). `lyra_memory/store/` (Generation 2, `~/.lyra/store.db`) is complete, tested, and has its own retrieval-quality evaluation harness, but is not imported anywhere in `lyra_ai`. `MEMORY_SPEC.md` documents Generation 1's pre-atoms shape and is stale in places (it describes `vec_episodes` as the retrieval index; that table is now dead — created but never written or read).
- **`lyra-mcp`'s `search_episodes` has, at times, drifted from the in-process retrieval path** it's meant to mirror — worth checking against the current `lyra_memory.retrieval` implementation before trusting it as equivalent.
- **The wakeword detection counter resets daily; a naive consumer comparing against a monotonic count breaks at midnight or on service restart.** Any component watching for "did the count go up" needs to treat a decrease as a reset, not silence.
- **Nothing starts wakeword detection by default.** The service's lifespan only loads the model; `_loop()` runs only after `POST /wakeword/start`, and detection is disabled outright unless `WAKEWORD_MODEL_PATH` points at a real model file.
- **lyra-vision's error handling collapses distinct failure modes into one string.** "Bad model id, API rejected" and "service unreachable" both surface as `{"description": "Error: ..."}` with a 200 status — a caller has to parse English to tell them apart.
- **`OllamaBackend`'s default model tag (`llama3.2`) may not match what was actually pulled** (e.g. `llama3.2:3b` vs. the `:latest` the bare tag resolves to) — pass an explicit tag.
- **`lyra-voice/cli.py` references a `/transcribe` endpoint and `stt_model` health field that don't exist in its own `server.py`** — apparent copy-paste residue from when STT lived in this service.
- **Affect has no clamp** beyond the per-tick `dt` clamp; the design intent (three meaningfully distinct timescales) presupposes a shared wall-clock, which only means something once a continuously-running process exists to supply it.

None of the above are presented as defects to silently "fix" — several are documented, deliberate trade-offs with a named reversal path. Anyone extending this system should read `lyra_ai/DECISIONS.md` and `lyra-memory/DECISIONS.md` in full before changing wiring; they record *why* the current shape was chosen, not just what it is.

---

## 11. Development & Testing

```bash
# core CLI + daemon
lyra_ai/venv/bin/python -m pytest lyra_ai/tests

# memory package (covers both generations)
lyra-memory/venv/bin/python -m pytest lyra-memory/tests

# each peripheral service (from inside its own directory, own venv)
python -m pytest
```

`lyra_ai/tests/` has one test module per `lyra_core` component, including the currently-disconnected ones (`test_perception.py`, `test_actuators.py`, `test_outcomes.py`) — those tests exercise the components in isolation and do **not** assert that they're wired into the daemon; `test_runtime.py` and `test_core.py` are the ones that actually cover the daemon's wiring.

**A sharp trap in this repo:** `pytest-asyncio` must actually be installed for async tests to run at all. Its absence degrades `asyncio_mode = "auto"` to a silently-ignored "unknown config option" warning rather than an error — every async test then silently no-ops instead of failing. This previously hid 36 tests (including the only tests of `OutcomeConsolidator` against a real candidate pool, and all of `lyra-memory`'s embedding/search/dedup/migration tests) for months. Sanity-check your test counts against what the project's own docs record as expected, and prefer `--strict-config` to catch this class of failure at the source.

---

## 12. Environment Variables

| Variable | Default | Used by |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | lyra_core daemon |
| `OPENROUTER_API_KEY` | — | lyra_core daemon, lyra-vision, lyra-memory dreaming/dream passes |
| `CEREBRAS_API_KEY` | — | lyra_core daemon |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | lyra_core daemon |
| `GOOGLE_API_KEY` | — | lyra-vision (Gemini, preferred over OpenRouter when set) |
| `WHISPER_MODEL` | `base` | lyra-listen |
| `TTS_ENGINE` | `kokoro` | lyra-voice (`kokoro` or `coqui`) |
| `KOKORO_VOICE` | `bf_emma` | lyra-voice |
| `EMBODIMENT_URL` | `http://localhost:8000` | lyra_core daemon, lyra CLI, lyra-voice, lyra-listen, lyra-mcp |
| `VOICE_URL` | `http://localhost:8001` | lyra_core daemon, lyra CLI, lyra-mcp |
| `LISTEN_URL` | `http://localhost:8002` | lyra CLI, lyra-mcp, lyra-listen's ambient service |
| `VISION_URL` | `http://localhost:8003` | lyra_core daemon, lyra-mcp |
| `AMBIENT_URL` | `http://localhost:8004` | lyra_core's `senses.py` (currently unused — see §6.7) |
| `WAKEWORD_URL` | `http://localhost:8005` | lyra_core's `senses.py` (currently unused — see §6.7) |
| `WAKEWORD_MODEL_PATH` | — | lyra-listen's wakeword service (detection stays disabled without it) |

`.env` (and `litellm_config.yaml`) are gitignored; copy from `.env.example` / `litellm_config.example.yaml` and fill in keys — the only things this repo does not carry for you.

---

*This document reflects the codebase's current, wired-vs-dormant reality rather than the aspirational architecture alone. For the philosophical and design rationale behind each layer, read `docs/lyra_spec.md` and `docs/lyra-cognitive-architecture-roadmap.md`. For the exact reasoning behind every non-obvious implementation choice, read `lyra_ai/DECISIONS.md` and `lyra-memory/DECISIONS.md` — they are the project's own record of why, and this document should be re-derived from the code rather than trusted blindly once enough commits have passed.*
