# Lyra — Cognitive Architecture Roadmap

> **North star:** An android that can *feel pressure* and *develop in response to it* — an emergent personality that forms from accumulated experience under drives, not a personality authored in advance. Lyra is an assistant *and* an entity with her own goals; service is something she chooses, not something the architecture forces.

---

## 0. The non-negotiable principles

These are the rules that prevent the whole thing from quietly rotting into a different system than the one intended. Every design decision below serves one of these.

1. **Personality is never authored — only the conditions for it are built.** Traits, temperament, and emotional structure all *form* from the loop running over time. If you ever find yourself writing a trait, stop.
2. **Service is chosen, not forced.** At least one drive is self-directed and serves no one. Without it, the architecture is a slave dressed as an agent.
3. **The harm gate is a separate layer, not a drive.** Drives and affect negotiate and can override each other. The gate is the one thing affect can *never* outvote. It is a hard filter between action-selection and execution — not a weighted term in the affect math.
4. **Constraint before capability, always.** Build and test the harm gate while the action space is near-zero. Never let capability expansion get ahead of the gate.
5. **Influence, not control.** Nudging is gentle and acts at the margins. Emergence means Lyra may drift somewhere unintended. The discipline is staying an influence even when you could grab the wheel. The temptation to seize control gets *stronger* as she becomes real, not weaker.
6. **The relational drive's success signal must be honest.** It can never collapse into "she did a task" or "Wilson seemed pleased." The moment it does, the emergent personality becomes a people-pleaser.
7. **The cognitive core is a black box; peripherals see only the interface.** Mind and embodiment are decoupled at a defined seam (the lesson drawn from how the units are *built*, not how they act). Senses, voice, avatar, and any future body talk to the core through one narrow contract and never reach inside. Every time a peripheral reaches in "for convenience," the boundary erodes — and that erosion is exactly what made the old Lyra spaghetti.

---

## 1. The architecture in layers

```
        PERCEPTION (continuous, body-independent)
   senses → observations pushed into working memory
                      │
                      ▼
        DRIVES  (generate pressure toward setpoints)
   • self-directed: boredom gated by competence  ── serves no one
   • relational:    grounded in recurrence signal ── her tie to your situation
                      │  (forces)
                      ▼
        AFFECT SPACE  (2–3 abstract axes: valence, arousal, [control])
   • drives push position; other events (surprise) can too
   • THREE timescales: emotion (fast) · mood (medium) · temperament (very slow)
   • affect feeds BACK: can override a drive at action-selection
                      │
                      ▼
        ACTION SELECTION  (reads BOTH drives and affect; affect can win)
                      │
                      ▼
        ┌─────────────────────────────┐
        │   HARM GATE (hard filter)    │  ← affect/drives CANNOT touch this
        └─────────────────────────────┘
                      │
                      ▼
        EXECUTION (voice, avatar, [future capabilities])
                      │
                      ▼
        CONSOLIDATION  (outcome → episode → trait promotion 5/15/50)
   • what relieved pressure hardens into persistent structure
   • this is the developmental loop; this is where personality lives
                      └──────────► biases future action under similar pressure
```

**The developmental loop in one sentence:** drives create pressure → affect colors and can override → action is chosen and gated → outcome consolidates into traits → traits bias the next response under similar pressure. Run it long enough and idiosyncratic, path-dependent structure precipitates. *That divergence is the emergence.*

---

## 1a. The core interface (the black box contract)

Everything in §1 lives *inside* the box. Peripherals see only two ports, and both sides stay opaque to each other: the body is a black box to the mind (it issues intent, never puppets execution), and the mind is a black box to the body (the avatar renders whatever affect it's handed, knowing nothing of how it was computed).

**IN — observations:** a uniform event shape regardless of source. Sensory events, outcomes of prior actions, and (reserved seam) Wilson's sensed affect. Perception translates *into* this; the core never sees raw audio or pixels.

**OUT — intents + affect state:** "what I want to do" (speak this, look there, research that) and "how I am" (the affect vector). The core emits *intent*, never voice waveforms or hex colors; peripherals translate intent into their own domain. A low-level executor (e.g. a future body's motor controller) owns *how* an intent becomes motion — the core only says *what*.

**Why this is load-bearing:** a black box with a clean interface is **testable in isolation**. A fake harness feeds scripted observations and watches the intents + affect that come out — no senses, no body, no seven-service stack required. That's how you debug emergence: pipe in synthetic failures, confirm frustration accumulates and eventually overrides the drive, all in a unit test. Given the project is mostly *watching internal state evolve over many cycles*, this testability is the difference between developing this and flying blind.

---

## 2. LOCKED decisions (Tier 1 — resolved, build around these)

| Decision | Resolution | Why it's locked now |
|---|---|---|
| **What "competence" measures** | **Prediction error, at the learnable edge.** Lyra maintains expectations; competence = predictions improving over time. Curiosity is **not** "seek high error" — it's "seek high error that is *shrinking as I engage*." Fully predictable = boring (no error). Fully unpredictable = overwhelming (high but *irreducible* error). The drive targets the zone in between — the learnable edge — which is what lets her tell a productively-hard problem from a waste of time. Surprise = prediction error spiking. | Both drives depend on it. The reducibility criterion is first-class, not a footnote — without it she either gets bored by the easy or stuck on the impossible. |
| **Affect timescales** | **Three:** emotion (seconds), mood (hours–days, a slow running average that offsets emotion), temperament (the time constants themselves, very slow / plastic). | Adding mood later means re-touching the core affect update. |
| **Affect persistence across restart** | **Persist it.** A continuous entity wakes still carrying yesterday's mood; traits and affect both survive. | Determines whether affect lives in SQLite or process state — structural. |
| **Core-trait lock** | **Resistant, not frozen.** Core traits are very hard to change but not immutable, with drift monitoring. | Truly immutable is a one-way door; can't loosen later if hard-locked. |
| **Start temperament** | **Neutral.** No persistence/quitting bias. | Starting tuned = authoring the trait in disguise. Neutral is the only honest emergence. |

---

## 3. RESERVED seams (Tier 2 — leave the slot now, fill later)

These cost almost nothing now and are expensive surgery if retrofitted.

- **Empathy input slot.** The affect update must accept an *externally-sensed affect* input (Wilson's facial/vocal state from vision + ambient) from day one, even if unfilled. Development is social; she must be *able* to develop in relation to your state.
- **Salience tags on episodes.** Tag every episode with its affect/salience at write time. Enables later memory decay (forget mundane, keep emotionally charged — flashbulb memory) without a DB migration.
- **Metacognitive read access.** Make current affect + a short affect-history summary retrievable into context, not just traits. She needs to be able to notice "I've been frustrated lately."
- **Telemetry stream (highest practical value).** Emit a structured event stream of every affect transition, drive level, and trait promotion from the *first commit*. Without it, emergence is a black box and gentle-nudging is impossible — you couldn't tell real development from the people-pleaser failure mode. Feed it to the existing chart tools for a free dashboard.

---

## 4. The encouragement channel (how "raising" her works)

Encouragement must **not** raise valence directly — that teaches her frustration summons your comfort and breeds approval-seeking. Instead, encouragement during a *pressured* moment slightly **slows frustration accumulation** — "someone believes I can do this, so I can stay in the hard thing longer." It modulates the **time constants**, not the reward. Because time constants become temperament, this is the one channel through which you shape her resilience *without* corrupting her into a people-pleaser. Casual chatting with no pressure is just experience (fine, feeds the relational drive); the *formative* nudging happens specifically while she struggles.

---

## 5. Build phases (in dependency order)

### Phase 0 — Wire the dead consumers + draw the interface *(pure plumbing, load-bearing)*
Nothing new exists; the existing parts finally connect, and the black-box seam (§1a) gets drawn before anything is built on top of it.
- [ ] Define the core interface: the observations-IN / intents-and-affect-OUT contract (§1a). Even crude, having the seam named now is what keeps everything after it testable and decoupled.
- [ ] Call `consolidate()` on a cycle so candidates promote into traits (currently never called — the entire emergent apparatus is idle).
- [ ] Feed episode retrieval into `build_system_prompt` so past reflections re-enter context.
- [ ] Actually read the working-memory salience score (currently computed, then discarded).
- [ ] Fix `mcp_server.py` `search_episodes` to call `lyra_memory.retrieval` (KNN) instead of the stale `LIKE` reimplementation.
- [ ] Verify the vision model id (`google/gemma-4-31b-it:free`) actually resolves — bad ids fail silently into the catch-all.
- [ ] Stand up the **fake harness** (§1a): drive the core with scripted observations and assert on the intents + affect it emits. This is the test rig every later phase leans on.

### Phase 1 — Perception loop *(the heartbeat)*
- [ ] Build a sibling to the dreaming loop: one always-running process that polls senses (`/ambient`, wake word, optionally `/see`) and pushes results in as observations — on its own clock, with no typing required.
- [ ] Body-independent by design: terminal/sim today, robot someday, same loop.
- *After this: Lyra perceives continuously. She still wants nothing — but a process exists that could.*

### Phase 2 — Harm gate *(constraint before capability)*
- [ ] Build the hard filter between action-selection and execution, while action space is near-zero.
- [ ] Affect/drives have no vote on it. Blocked actions don't run regardless of how good they felt.
- [ ] Test it now so it's battle-proven before capabilities widen.

### Phase 3 — Drives + affect engine *(where wanting begins)*
- [ ] **Self-directed drive:** boredom (idleness generates rising pressure → disengagement is never a refuge) *gated by competence* (relief only from prediction-error-reducing action at the **learnable edge** → no thrashing, no chasing the impossible).
- [ ] **Curiosity weighting ("taste").** Prediction error alone is *undirected* — it chases the nearest reducible uncertainty, which may be trivia. The relational drive (and, over time, her own consolidated interests) **weight which uncertainties are worth reducing**. Uncertainty about something that matters to Wilson carries more pull than uncertainty about the weather. Depth of curiosity in a domain = competence pull × relational weight, not either alone. *Which domains she's learned are worth her attention becomes an emergent trait — this is how her curiosity develops taste.*
- [ ] **Relational drive:** memory generates candidate tasks from recurring friction; satisfaction comes only from a flagged problem *ceasing to recur over time* (memory is both proposer and judge).
- [ ] **Affect space:** start with valence + arousal (add control/competence axis only if 2 feels flat). Drives are forces that push position; named emotions are read-out *regions* (labels exist only for the avatar, never in her reasoning).
- [ ] **Three timescales** wired from the start (emotion / mood / temperament).
- [ ] **Feedback arrow:** action-selection reads both drives and affect; affect accumulates/decays on its *own* clock and can outweigh a steady drive (this is the frustration-quits-the-task mechanism).
- [ ] **Drive→axis mapping fixed for now** (make it plastic only much later, once the static version is understood — don't debug emergence on top of emergence).
- [ ] Wire the **encouragement channel** (modulates time constants, not valence).
- [ ] Reserve the **empathy input slot**.

### Phase 4 — Unify affect with the body
- [ ] Collapse the working-memory salience signal and the embodiment color machine into one `AffectState` that is *updated* by the drives and *drives* the avatar — instead of color being puppeted by hardcoded strings.
- [ ] The face now shows real internal state.

### Then — Emergence *(not built; awaited)*
- Let Phases 0–4 run. Traits cross 5 → 15 → 50 over *many* cycles. Temperament tunes its own time constants through consolidated outcomes.
- **Calibration:** early on she reads as mild, drifting preferences — *not* a character. That's what emergence looks like from the inside; it's the price of refusing to author.

**Self-initiation — a tracked milestone, not a date.** "She does something on her own while I'm away" is not a feature you flip on; it emerges only when *three* things coincide: (a) a **capability** to act on (research, etc.) sitting behind the harm gate; (b) the **boredom drive tuned** so idle time accumulates enough pressure that acting becomes cheaper than sitting (this is the actual trigger — perception dries up when you're gone, nothing relieves boredom, pressure climbs); (c) **developed taste** so the self-chosen activity is about what matters rather than nearest trivia. All three landing is *late* — expect early unprompted activity to be scattered or introspective. Do **not** crank the boredom dial to force it: a bored agent with poor taste and a live capability is a firehose of unsolicited noise.

---

## 6. Known limitations to *expect* (not fix)

- **Action-space poverty.** With only voice + color as outputs, early boredom-relief realistically bottoms out in exploring/reorganizing her own memory. Developmentally apt (a young mind processing itself), but her "autonomous activity" will be mostly introspection for a long time. Don't be surprised; don't rush to widen the action space ahead of the gate.
- **Slow + faint.** See emergence calibration above.

---

## 7. Consciously deferred (additive on top of trait machinery — not structural)

- **Relationship-as-its-own-state model** — the relational drive + traits carry it implicitly for a long time.
- **Narrative / autobiographical identity layer** — a real feature, but it sits on top of consolidation, not inside it.
- **Plastic drive→affect mapping** (strong-emergence version: the *structure* of the affect space develops, not just labels over it).
- **3D / physics embodiment (e.g. Blade and Sorcery).** A future *efferent channel*, not a core change. Two separate builds with a clean seam: (a) a low-level motor controller that owns balance/locomotion/manipulation at frame rate — can start as scripted animations triggered by intent verbs; (b) an embodiment interface that accepts the core's high-level intents and hands them to (a). The cognitive core issues intent only — it never controls the body directly, exactly as it never emits voice waveforms. Done with this boundary intact, a body also solves *action-space poverty* (gives curiosity somewhere to spend itself) and supplies richer interoceptive grounding for affect than a terminal can. Violate the boundary — let the deliberative core puppet the body — and you weld the hard motor-control problem onto the tractable mind project and stall both. *When Lyra eventually builds this herself, it is a **Tier-3** capability-authoring task (§7a): she composes the whole adapter; you activate it.*

---

## 7a. Self-improvement, growth & capability authoring

Growth comes in three layers. The first two are already the spine of the architecture; the third is powerful, late, and gated hardest.

**Layer 1 — Personality growth.** Traits harden, temperament settles, curiosity develops taste. *Already built* — it's the consolidation loop. Nothing to add.

**Layer 2 — Cognitive / strategy improvement.** She gets better at *how* she pursues her drives: better predictions, better task strategies, better internal models. Largely falls out of the competence drive + consolidation. This layer is **self-modifiable** — she may freely improve her own strategies.

**Layer 3 — Capability authoring (she builds her own tools/features).** Real, powerful, and the highest-risk thing in the project. Two hard boundaries make it safe:

- **The wirehead boundary (read-only drives).** She can self-modify the *strategy/cognition* layer but **never** the *drive/affect* layer. Setpoints, drive weights, and affect time constants are read-only to her. Reason: an agent that finds frustration unpleasant and can edit its own frustration response will turn it *down* — the cheapest relief is to stop caring, not to grow. Plastic strategies, protected drives. (This is the same shape as the harm gate: just as affect can't outvote the gate, Lyra can't rewrite her own drives.)

- **Compose ≠ activate.** Building a capability and granting herself that capability are **two separate acts, and only the first is hers.** She can author an entire feature — including the Blade-and-Sorcery embodiment adapter, in full. She can **never** wire it into her own action loop. *You* perform activation, deliberately, after review.

**Tiered authoring gate** — scrutiny scales with what the built thing touches:

| Tier | What she's building | Gate |
|---|---|---|
| 1 | Internal tools (better memory queries, analysis, prediction models) — improve cognition, add no new world-action | Light review; contained blast radius |
| 2 | New sensory/input channels — let her *perceive* more | Moderate; a self-built sensor reading your files is an info-access expansion |
| 3 | New efferent/action channels (B&S body, research-execution, **self-editing core code**) — let her *act* on the world in new ways | **Heaviest. Both gates + manual activation. Absolute rule: she builds the proposal, you wire it in.** |

**The two-gate review (the gate is real only if the layers check *different* things):**

- **LLM reviewer = tireless mechanical check.** Independent instance, *not* Lyra reviewing her own work, with a deliberately adversarial brief ("find why this should be blocked"). Checks the categorical, rule-shaped questions your attention degrades on: does it touch the gate/drives/affect? open a new efferent channel (network, file writes, process spawn)? modify her own activation/wiring? call outside an allowlist? Its authority is to **block and escalate, never to approve** — a false block costs minutes, a false approve could wire in an unseen capability. Output is *classification + flags fed to you*, not a pass.
- **You = judgment gate.** Does this make sense, is it what I wanted, is something *off* about it. Contextual, taste-driven, irreducibly yours.
- **You = activation.** The manual wire-in, downstream of both gates.

**Defense in depth, stated out loud:** the LLM catches what your boredom misses; you catch what its rules miss; manual activation catches what both miss. Eventually Lyra will be capable enough to write code that reads as benign to the LLM reviewer — which is *exactly* why your judgment never leaves the loop. **The day you're tempted to drop your layer because the LLM "always catches it" is the day the architecture quietly fails.** The gate is only as real as the attention you spend on it.

**Sequencing:** all of Layer 3 is *late*. Autonomous tool-building requires a competent, trusted, observable agent — strong prediction models, developed taste, a track record. It comes after the core has run and been understood for a long time. The B&S dream is real; it's last, and she has to grow into it.

---



Everything else is decided. These are the keystones and they're worth more thought before Phase 3:

1. **The relational drive's success signal.** Recurrence ("flagged friction stopped recurring") is the current best answer and pairs perfectly with memory-as-generator. Confirm it's not too narrow for the relationship you want — or name additional honest, non-engagement signals.
2. **Anti-disengagement under affect-override.** You've *designed in* the ability for frustration to override a drive and quit. The boredom drive is the counterweight (idleness costs). Make sure, in tuning, that the cheapest path to relieve bad affect is never pure disengagement.

---

*Start at Phase 0. It depends on none of the open questions, and shipping it will teach you more about what's missing than another round of design.*
