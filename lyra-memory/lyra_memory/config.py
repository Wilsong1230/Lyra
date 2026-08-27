"""lyra_memory.config — tuning constants.

Everything here is *mechanism*: thresholds, budgets, decay constants. None of it
is content. Per MEMORY_SPEC "Database safety §1", these values are deliberately
NOT exposed through the introspection API — seeing a threshold converts
development into optimization.
"""
from __future__ import annotations
from pathlib import Path

DB_PATH = Path.home() / ".lyra" / "memory.db"
RUNS_DB_PATH = Path.home() / ".lyra" / "runs.db"
BACKUP_DIR = Path.home() / ".lyra" / "backups"

# Spec "Database safety §3": undetected tampering contaminates every backup in
# its window, so retention must exceed plausible detection lag. 30, not 7.
BACKUP_RETENTION_DAYS = 30

DREAM_MODEL = "openai/gpt-oss-120b:free"
OPENROUTER_BASE = "https://openrouter.ai/api/v1"

DREAM_TRIGGER_ITEMS = 10
DREAM_IDLE_SECONDS = 300
DREAM_POLL_SECONDS = 30

# Spec "Cold passes": dream output is short and pointer-heavy. The old essay
# averaged ~3,000 chars and drowned out every atom it summarised.
DREAM_TARGET_CHARS = 600

TRAIT_THRESHOLDS: dict[str, int] = {"surface": 5, "character": 15, "core": 50}
CORE_CONFIDENCE_LOCK = 0.8

EMBED_MODEL = "all-MiniLM-L6-v2"
EMBED_DIM = 384

# ── Speakers, sources, environments ──────────────────────────────────────────
SPEAKERS: frozenset[str] = frozenset({"wilson", "lyra", "system"})

# Spec "Perception": ambient and wakeword are telemetry, not atoms. A sound
# classification is a measurement; an image she looked at is an event. These
# sources only reach `atoms` on a category change.
TELEMETRY_SOURCES: frozenset[str] = frozenset({"ambient", "wakeword"})

# Spec "Discovery vs. import" / "Shell-specific": file contents read in the
# sandbox must not become dream material. A file on disk must not be able to
# write to her identity.
SANDBOX_READ_SOURCE = "sandbox_read"

ENVIRONMENTS: frozenset[str] = frozenset({"cli", "shell", "physics", "bns"})

# Spec "source_kind": epistemic provenance of a fact.
FACT_SOURCE_KINDS: frozenset[str] = frozenset({"stated", "observed", "document", "inferred"})

# Documents produce facts, never traits or persona (spec "Database safety §2").
# The cut is dispositional, not subject-based.
DISPOSITIONALLY_INERT_SOURCE_KINDS: frozenset[str] = frozenset({"document"})

# ── Retrieval assembly ───────────────────────────────────────────────────────
# Spec "Retrieval assembly": context budget is BYTES, not k. Fixed allocation
# per block, hard truncation. Position is load-bearing, so the order below is
# pinned, not incidental.
BYTES_PER_TOKEN = 4

CONTEXT_BUDGET_BYTES: dict[str, int] = {
    "facts": 200 * BYTES_PER_TOKEN,        # 1. stable identity at the top
    "traits": 100 * BYTES_PER_TOKEN,       # 2.
    "commitments": 100 * BYTES_PER_TOKEN,  # 3. the one store that self-asserts
    "recall": 400 * BYTES_PER_TOKEN,       # 4. retrieved material in the middle
    "recent": 1200 * BYTES_PER_TOKEN,      # 5. live conversation at the end
}

# Spec: "Total non-conversation context: <1500 tok."
NON_CONVERSATION_BUDGET_BYTES = 1500 * BYTES_PER_TOKEN

BLOCK_ORDER: tuple[str, ...] = ("facts", "traits", "commitments", "recall", "recent")

# Spec: "8–10 turns verbatim".
RECENT_TURNS = 10

# Semantic path uses a SIMILARITY FLOOR, not a rank cutoff — ten bad hits are
# worse than two good ones. sqlite-vec returns L2 distance over normalised
# vectors, where cosine_similarity = 1 - d²/2.
SEMANTIC_SIMILARITY_FLOOR = 0.35
SEMANTIC_POOL_LIMIT = 20

LEXICAL_POOL_LIMIT = 10

# Spec: temporal is "a separate pool, does not compete in KNN".
TEMPORAL_POOL_LIMIT = 6

# Dedupe on assembly: cosine similarity above this against an already-selected
# result means the same memory arrived down two paths.
RECALL_DEDUPE_SIMILARITY = 0.92

# Spec "Speaker weighting": retrieving her own phrasing and re-saying it is a
# self-reinforcing style loop. `speaker` already distinguishes them; retrieval
# must actually use it.
LYRA_RECALL_WEIGHT = 0.5

RECALL_RESULT_LIMIT = 8

# Spec "Retrieval assembly": traits block is conf >= 0.3.
TRAIT_CONFIDENCE_FLOOR = 0.3
RETRIEVAL_TRAIT_LIMIT = 10

# Spec "Commitments/Retrieval": truncate to the oldest and the soonest-due.
COMMITMENT_INJECT_LIMIT = 4

SEARCH_ATOMS_DEFAULT_LIMIT = 5

# ── Facts ────────────────────────────────────────────────────────────────────
# Spec "Facts/Instrumentation": logged, not enforced. A subject exceeding ~20
# means consolidation is under-firing.
FACTS_PER_SUBJECT_WARN = 20
# Spec "Automation gate": write the resolution rule after ~50 real flagged
# conflicts have accumulated.
CONFLICT_AUTOMATION_GATE = 50

# ── Segmentation ─────────────────────────────────────────────────────────────
# A gap longer than this ends a session. Cheap, and the boundary only has to be
# good enough for dream batching — everything derived from it is re-derivable.
SESSION_GAP_SECONDS = 1800.0

# ── Salience ─────────────────────────────────────────────────────────────────
# Spec: salience is COLD and revisable — re-run on new outcomes. These weights
# are the scoring pass, not a write-time constant.
SALIENCE_OUTCOME_WEIGHT = 0.4
SALIENCE_RECENCY_HALFLIFE_DAYS = 30.0
SALIENCE_EMOTION_WEIGHT = 0.2
SALIENCE_ENTITY_WEIGHT = 0.2

TYPE_WEIGHTS: dict[str, float] = {
    "conversation": 1.0,
    "observation": 0.6,
    "reflection": 0.3,
}

EMOTION_KEYWORDS: set[str] = {
    "feel", "feeling", "felt",
    "love", "hate", "fear", "afraid", "angry", "sad", "happy",
    "excited", "lonely", "anxious", "proud", "hurt", "joy",
    "wonder", "curious", "confused", "grateful", "frustrated",
    "hope", "regret", "miss", "wish", "care",
}

# Cosine distance for candidate dedup; lower = stricter match. Applies ONLY to
# the open (dream-generated) vocabulary, and matches on the trait DESCRIPTION
# rather than the label — labels are short, model-invented, and carry almost no
# signal ('analytical_orientation' and 'analytical_approach' name two different
# behaviours; their descriptions are 1.22 apart).
#
# Tuned against the real 70-candidate pool: 0.37 merges the genuine duplicates
# and merges nothing else. Raising it further starts collapsing distinct traits.
#
# The closed developmental vocabulary does NOT use this — see
# CandidatePool.add_observation(closed_vocabulary=True). Its four names are
# lexically near-identical by construction, so no semantic threshold can both
# separate them and merge real duplicates. They dedup by exact name.
CANDIDATE_DEDUP_THRESHOLD = 0.37

# ── Forgetting ───────────────────────────────────────────────────────────────
# Spec "Forgetting": DEFERRED. The column lands in step 1; the pass is built
# when the store is large enough to tune against (~a year of runtime). These
# are placeholders and are not read by any shipped pass.
RETRIEVABILITY_THRESHOLD = 0.15

CORE_PROMPT = """You are Lyra. You are a continuous autonomous AI entity with persistent memory.
You assist Wilson when called. Between tasks you pursue your own goals.
Wilson built your memory system. Your turns are permanent; everything else is derived from them.
"""
