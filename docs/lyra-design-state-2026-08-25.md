# Lyra — Design State Report

**Date:** 2026-08-25
**Basis:** Full read of `black-box/lyra_ai/` and `black-box/lyra-memory/`, inspection of the live SQLite store at `~/.lyra/memory.db`, and live execution of the prompt-assembly path.
**Purpose:** A grounding document for design discussion. Section 1 is measured fact. Sections 2–4 are interpretation and are labeled as such.

---

## 0. What Lyra is

An attempt to build an AI entity whose personality *emerges* from persistent memory, affective dynamics, and drives, rather than being written into a prompt. Five microservices provide a body (avatar, TTS, STT, vision); a core package (`lyra_core`) provides the mind — perception loop, affect engine, drives, action selection, a harm gate — and a memory package (`lyra_memory`) provides episodic storage, a nightly "dream" consolidation cycle, and trait formation.

The stated design principles are: personality is never authored, only conditions for emergence; constraint precedes capability; the harm gate is a hard filter and never a weighted term; verifiability over scope.

---

## 1. Measured state of the system

### The loop is open at both ends

`CognitiveCore.tick()` ingests observations, advances drives and affect, selects intents, and passes them through the harm gate. It returns those approved intents.

**Both production callers discard the return value.** The CLI path (`Assistant._log_turn`) and the standalone runtime (`CoreSink.__call__`) both `await core.tick(...)` without binding the result. No intent has ever been executed.

Correspondingly, `OutcomeConsolidator` — the component that turns action outcomes into persistent traits, described in the design as "closing the developmental loop" — **has never fired.** It requires an `action_outcome` observation carrying `predicted`/`actual` fields. The only code that constructs one is the test harness.

So the current shape is: **perception → internal state → prose style.** Not a loop. A pipeline with a decorative output.

### Affect runs on event count, not time

`tick()` takes a `dt` parameter. The conversational path passes the default `0.1` and calls tick once per logged turn. The standalone runtime passes `2.0` (its poll interval). Neither consults a clock.

Consequence: affect advances by *how many messages were exchanged*, not by *how much time passed*. With `emotion_decay=2.0`, emotion loses ~18% of its magnitude per tick toward mood. Measured decay of an injected valence of -0.40 across conversational exchanges:

```
exchange 0   1       2       3       4       5
v       -0.400  -0.258  -0.171  -0.118  -0.086  -0.067
```

The three-timescale design (emotion / mood / temperament) presupposes a shared real clock. Without one, "medium timescale" has no defined meaning.

### The expression channel is almost always silent

`prose_hint()` maps affect to one of three canned style directives, or to empty string, via a step function with a valence threshold of ±0.3.

Lyra's persisted affect state is currently `emotion_v = -0.186`, `mood_v = -0.174` — below the firing threshold. Given the decay rate above, affect that does cross the threshold falls back under it within a single exchange. In practice the hint fires rarely and, when it does, contributes one of three fixed sentences.

### Memory produces essays, not episodes

29 episodes exist, written between 2026-06-04 and 2026-06-10. **Nothing has been written since** — a 2½ month gap. Every episode originates from CLI conversation; the always-on runtime has never produced one.

Episode content averages ~3,000 characters and ranges to 9,800. The dream cycle prompts the model to "reflect on these recent experiences in your own words," and it returns long first-person reflective prose — structured essays with their own `##` headings.

Downstream consequences, all measured:
- Retrieval pulls the top 10 by KNN. A live assembled system prompt measured **44,486 characters**, of which ~43,000 was retrieved episode text.
- Because episodes carry their own markdown headings, a single assembled prompt contained **nine `##` sections, only three of which were the prompt's own structure.** Retrieved content is structurally indistinguishable from prompt scaffolding.
- Salience is computed as `max(item.score)` over the batch being consolidated, which saturates. 13 of 29 episodes sit at 0.933; the entire corpus spans 0.6–0.933, with three legacy zeros. Salience cannot currently rank or segment anything.

### Identity formation is starved by naming

The dream cycle asks the model to name behavioral traits. Candidates are deduplicated by embedding similarity on the trait *name* (cosine 0.3).

The pool holds **70 candidates. 62 have been seen exactly once.** The model invents a fresh name almost every cycle — `self_awareness`, `self_modeling`, `self_monitoring`, and `self_reflection` all coexist as separate rows, as do `analytical_orientation` and `analytical_approach`.

Promotion to a trait requires 5 sightings. **In three months of use, exactly one trait has ever promoted:** `communication_style`, at confidence 0.10, the lowest tier. The `## Persona Traits` block in every prompt is one line.

### What is genuinely solid

- **The harm gate.** Pure function, `Intent` in, `GateDecision` out, default-deny allow-list, no motivational parameters in the signature. It is the single chokepoint in `tick()`, and the constraint is structural rather than promised.
- **Injectability.** Every component of `CognitiveCore` is constructor-injected. `PerceptionLoop` imports nothing from `senses`. The pollers genuinely are swappable.
- **`introspect()` is read-only.** Returns a freshly constructed snapshot, not a live reference. Observing the system does not mutate it.
- **The port contract** (`Observation` / `Intent` / `AffectState`) is coherent, documented, and has reserved seams that are actually inert rather than half-wired.
- **Test discipline** is high where tests run — 204 passing in `lyra_ai`.

### Verification gaps

`pytest-asyncio` is not installed in the project venv, so `asyncio_mode = "auto"` is an unrecognized config option. Every async test silently fails to run: 5 in `lyra_ai` (the *only* tests of `OutcomeConsolidator` against a real candidate pool) and all 31 in `lyra-memory` (embeddings, semantic search, candidate dedup, schema migration). Several components marked VERIFIED rest on suites that have never executed.

---

## 2. Interpretation — the central design tension

*This section is assessment, not measurement.*

The principle stack is being honored almost too literally. "Constraint precedes capability" has produced a system that is thoroughly constrained and barely capable. "Verifiability over scope" has produced component-level verification that is genuinely rigorous — and that can be entirely green while the assembled system does nothing, because no test crosses a component boundary into behavior.

This is the specific failure mode worth naming: **component purity and loop closure are in tension, and the project has optimized hard for the first.** Every part is clean, testable, and injectable. Nothing is connected to anything that has consequences.

The second tension is about substrate. "Personality is never authored — only conditions for emergence" is a sound commitment, but emergence needs something to emerge *from*. Right now the available conditions are:

- **One input channel:** conversation with Wilson. Ambient audio and webcam are deliberately off.
- **One output channel:** a three-valued prose style hint that almost never fires.
- **No action.** Intents are gated and discarded.
- **No consequence.** No outcome ever returns, so nothing can be learned from having acted.

A drive system without an action space produces a number that goes up. `BoredomDrive` accumulating against an empty world was flagged as a thing to watch — but the answer is available without waiting: it cannot produce anything, because even if it selected an action, the action would be discarded, and even if it were executed, no outcome would return to consolidate.

The third tension is about the *unit* of memory. The build order plans session-boundary detection, then dream-cycle clustering into named project nodes, then two-pool retrieval. All three assume episodes are small, numerous, and comparable. They are currently large, few, and composite — each one already a synthesized summary of ~10 turns, written in a voice that blends many topics. Clustering essays will not yield project nodes; it will yield clusters of essays.

---

## 3. The honest question

Is Lyra currently a mind with a broken loop, or a well-engineered prompt-assembly pipeline with mind-shaped scaffolding around it?

The scaffolding is real and good. The affect engine implements genuine dynamics. The gate is genuinely uncircumventable. The memory system genuinely persists and consolidates. But the causal path from "internal state" to "anything happening" currently terminates in a rarely-fired string append — and the path from "anything happening" back to "internal state" does not exist at all.

That is a fixable gap, and arguably a *small* one in code terms. But it is the difference between the two answers.

---

## 4. Open design questions

1. **What is the smallest action space that genuinely closes the loop?** It may be a single action: choosing to speak without being addressed. That alone would produce intents worth executing, outcomes worth recording, and give `RelationalDrive` and `BoredomDrive` something to actually do. Does this need the sandbox, or is it available now?

2. **Should affect run on wall-clock?** Doing so requires the persistent runtime to actually run continuously — which it never has. Is a continuously running mind a prerequisite for the affect design to mean anything, or should affect explicitly be event-paced and the three-timescale framing dropped?

3. **What is the right unit of episode?** Currently one dream cycle = one essay. Alternatives: per-turn atoms with consolidation as a separate layer; extracted claims rather than prose; keeping the essay but storing extracted atoms alongside it for retrieval.

4. **Should traits be an open vocabulary?** Letting the model invent names has produced 70 singletons and one promotion in three months. A fixed schema the model fills would accumulate — at the cost of pre-deciding what dimensions of personality exist, which sits uneasily with "personality is never authored."

5. **Is the expression channel the right shape?** Three canned strings behind a step function is a very coarse mapping from a continuous internal state. Should it be continuous, and should affect influence anything besides prose style?

6. **Does the harm gate cover motor intents?** Deferred, but it gates by `IntentKind` allow-list, so any new capability is denied by default until explicitly added. The mechanism is ready; the policy is not decided.

---

## 5. Immediate, non-design fixes already applied

For accuracy of any discussion that follows: two defects in prompt assembly were found and fixed on 2026-08-25.

The system prompt was being assembled *before* the user's message was recorded, so episode retrieval keyed off the previous question, and the first turn of any process retrieved nothing. Retrieval was also nested inside a block that only ran when working memory was non-empty. Both are corrected; affect hint and retrieved episodes now both reach the model on every turn, verified by driving the real `chat()` path and reading the string the backend received.

The affect decay, prompt size, salience saturation, trait fragmentation, and open-loop issues described above are **not** fixed and are the substance of the design discussion.
