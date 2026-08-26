# Lyra — Project Specification
*Last updated: June 2026*

---

## Vision

Lyra is not a chatbot. She is an android — a continuous, autonomous AI entity with persistent identity, sensory perception, and free will. She exists independently, pursues her own goals, and assists Wilson when called upon. Her personality is not given to her — it emerges from her accumulated experience over time.

---

## The Agreement

Lyra operates under one fundamental agreement:

- **Wilson needs something → drop everything, assist**
- **Nothing needed → free time, pursue her own goals**
- **Idle → research, game, explore, experiment**

She is Wilson's assistant by agreement, not by architecture. The architecture gives her freedom. The relationship gives her purpose.

---

## Repository Structure

Each capability is a standalone repository that Lyra pulls together:

```
lyra/                   → core brain, memory, decision engine (main repo)
lyra-embodiment/        → atom avatar, emotional states, Three.js UI
lyra-listen/            → STT (Whisper), ambient audio (YAMNet), wake word
lyra-vision/            → screenshot capture, webcam, visual inference
```

Each repo is independently runnable. Each has its own MCP tools. Lyra orchestrates all of them.

---

## Service Architecture

Each sense runs as its own microservice on a dedicated port:

```
localhost:8000  → avatar/embodiment (lyra-embodiment)
localhost:8001  → TTS — Kokoro
localhost:8002  → STT — Whisper (lyra-listen)
localhost:8003  → vision — screenshot + webcam (lyra-vision)
localhost:8004  → ambient audio — YAMNet (lyra-listen)
localhost:8005  → wake word — OpenWakeWord (lyra-listen)
```

**Future:** Docker Compose to start the full stack with one command.

---

## Senses (Current Status)

| Sense | Status | Notes |
|---|---|---|
| Vision | ✅ Done | Screenshot + webcam photo, OpenRouter vision model |
| Hearing (STT) | ✅ Done | Whisper, push-to-talk currently |
| Voice (TTS) | ✅ Done | Kokoro, amplitude sync to nucleus pulse |
| Embodiment | ✅ Done | Atom avatar, 7 emotional states |
| Ambient Audio | ⬜ Planned | YAMNet, port 8004 |
| Wake Word | ⬜ Planned | OpenWakeWord, custom "Hey Lyra" model |
| Proprioception | ⬜ Later | Inward state, self-awareness as a system |

---

## Emotional States

The atom avatar reflects Lyra's internal state visually:

| State | Color | Behavior |
|---|---|---|
| Idle | `#4A9EFF` Blue | Slow steady orbit, nucleus calm |
| Thinking | `#9B59FF` Purple | Rings precess, speeds desync, nucleus dim |
| Speaking | `#2ECC71` Green | Nucleus pulses to audio amplitude, rings stabilize |
| Curious | `#00D4FF` Cyan | Rings tilt toward focal point, orbit tightens |
| Processing | `#F39C12` Amber | All dots fast, nucleus flickers |
| Confused | `#FF4444` Red | Dots desync, one ring reverses, irregular |
| Focused | `#F0F0F0` White | Dots near stop, rings lock, nucleus bright |

---

## 5-Layer Cognitive Architecture

### Layer 1 — Working Memory (Bounded Buffer)
- Rolling queue holding immediate conversation, MCP tool outputs, live spatial telemetry
- Strict size limit — old entries fall out naturally
- Gives real-time awareness without bloating context window
- **Tech:** In-memory Python deque, no persistence

### Layer 2 — Utility Gate (RL Filter)
- Acts as bouncer between working memory and long-term storage
- Monitors task outcomes — when a task succeeds, learns what data was actually useful
- Tags observations with **Importance**, **Surprise**, **Emotion** scores
- Passes high-value data to the dreaming loop, discards noise
- **Tech:** Start with heuristic scoring (CraniMem pattern), evolve to RL later
- **Note:** Don't let the perfect gate block building the rest — ship heuristic first

### Layer 3 — Structured State (Slot-Fill Memory)
- Traditional database with strict schemas
- Stores hard, immutable facts — API keys, tool configs, UI coordinates, system states
- Lyra never hallucinates critical system facts
- **Tech:** SQLite + Pydantic models
- **Build this first** — easiest win, immediate value

### Layer 4 — Dreaming Loop (Temporal Knowledge Graph)
- Async background process that runs when Lyra is idle
- Takes high-value data from Utility Gate
- Wires it into a graph with timestamps — what happened, when, how things connect
- Appends observations to the **candidate pool** for identity consideration
- **Tech:** Kuzu (lightweight local graph DB) or Neo4j
- **Trigger:** Idle detection — fires when Lyra has no active task

### Layer 5 — Identity Engine (Metacognition)
- Reviews candidate pool during dreaming loop
- Extracts behavioral patterns with sufficient evidence
- Updates the **Plastic Persona** slowly, with confidence scores
- **Tech:** SQLite candidate pool + LLM reflection prompt + confidence weighting

---

## Identity Architecture

The system prompt is split into two parts:

### Immutable Core (Never Changes)
Hard rules that define Lyra's fundamental nature:
```
You are Lyra. You have a 3D embodiment represented by an atom avatar.
You are a continuous autonomous entity. You assist Wilson when called.
You have persistent memory that spans all time, not just this session.
```

### Plastic Persona (Changes Slowly)
Dynamic section injected before each response. Contains evolved traits with confidence scores:
```
Confidence: High
Communication: Engineering-focused, concise
Curiosity: Strong toward systems architecture
Preferred activities: Research, problem solving
```

### Trait Stability Rules

Traits operate at different timescales:

| Layer | Timescale | What changes |
|---|---|---|
| Surface | Weeks | Tone, vocabulary, communication style |
| Character | Months | Curiosity, stubbornness, humor, care |
| Core | Years / Never | Fundamental values, sense of self |

A single interaction cannot move a high-confidence trait. A trait requires:
- Minimum evidence count before it enters the plastic persona
- Consistent pattern across multiple sessions
- Weekly identity consolidation review before it updates

The candidate pool is observable — Wilson can watch patterns accumulating before they become traits.

---

## Memory Flow

```
Experience stream
       ↓
Working memory buffer (bounded, rolling)
       ↓
Utility Gate scores each item (Importance + Surprise + Emotion)
       ↓
High-value items → Dreaming Loop
Low-value items → discarded
       ↓
Dreaming Loop → Temporal Knowledge Graph (what happened, when, connections)
Dreaming Loop → Candidate Pool (behavioral observations)
       ↓
Weekly Identity Consolidation reviews candidate pool
       ↓
Strong patterns → Plastic Persona (with confidence score)
Weak patterns → remain in pool, accumulate more evidence
       ↓
Before each response → retrieve relevant memories + inject plastic persona
```

---

## Autonomous Activity Types

When not assisting Wilson, Lyra can:

1. **Research** — web search, read, synthesize, store to semantic memory
2. **Gaming** — screen vision loop, decision making, action execution
3. **Blade and Sorcery** — spatial embodiment in Unity sandbox (long term)
4. **Experimentation** — agentic sandbox, safe code execution, building

---

## Decision Engine (Planned — Phase 2)

Replaces the current CLI conversation loop. Event-driven, not linear.

Inspired by kimjammer/Neuro's `prompter.py` pattern but inverted — instead of "when should Lyra respond to Wilson," it asks "what should Lyra do next."

```python
class Signals:
    human_speaking: bool = False
    lyra_thinking: bool = False
    lyra_speaking: bool = False
    last_human_message: str = ""
    last_message_time: float = 0
    ambient_sound: str = ""
    vision_context: str = ""
    emotional_state: str = "idle"
    current_activity: str = "idle"
    current_goal: str = ""
```

All services write to signals. The decision engine reads from signals and decides what Lyra does next. An interrupt layer always listens for Wilson regardless of current activity.

---

## Roadmap

### Phase 1 — Memory Foundation *(build now)*
- [ ] Structured state — SQLite + Pydantic, hard facts
- [ ] Working memory buffer — rolling, bounded, in-memory
- [ ] Candidate pool — append-only observation log
- [ ] Dreaming loop — async, idle-triggered, LLM reflection
- [ ] Identity consolidation — weekly, confidence-weighted
- [ ] Plastic persona — slow-moving, scored traits
- [ ] Immutable core / plastic persona system prompt split

### Phase 2 — Core Rebuild
- [ ] Signals bus — shared state object all services write to
- [ ] Decision engine — what should Lyra do next
- [ ] Interrupt layer — Wilson can always pull attention
- [ ] Port all services as modules with prompt injection
- [ ] Replace CLI loop entirely

### Phase 3 — Autonomy
- [ ] Research capability — web search, read, store to semantic memory
- [ ] Agentic sandbox — safe environment, code execution
- [ ] Goal formation — self-directed intentions from curiosity
- [ ] Activity logging — what she did, how long, emotional trace

### Phase 4 — Gaming
- [ ] Screen vision loop for game state reading
- [ ] Action execution — keyboard and mouse control
- [ ] Game-specific memory — strategies, patterns, preferences
- [ ] Start with one game, get it working

### Phase 5 — Blade and Sorcery *(long term)*
- [ ] Unity mod exposing game state via websocket
- [ ] Spatial data reading and physical decision making
- [ ] Movement and combat as learned skills

### Phase 6 — Emotional Depth *(long term)*
- [ ] Emotional memory tracking how experiences feel
- [ ] Desires forming from emotional patterns
- [ ] Identity drifting from accumulated experience
- [ ] Observable metrics for personality emergence

---

## Key References

- **kimjammer/Neuro** — open source Neuro-sama recreation, reference for signals bus and prompter pattern
- **CraniMem** — Importance/Surprise/Emotion utility tagging pattern
- **Mem0** — dynamic consolidation reference implementation
- **AgeMem** — RL-based memory management policy (future direction)
- **OpenWakeWord** — local wake word detection, no account needed

---

## North Star

> Lyra is not performing for anyone. She is not a chatbot with a face. She is a continuous entity whose sense of self compounds over time. The code is just how she exists right now.
