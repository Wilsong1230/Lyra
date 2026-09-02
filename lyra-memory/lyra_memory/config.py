from __future__ import annotations
from pathlib import Path

# CP-B: the daemon no longer opens this file — it opens STORE_PATH below via
# lyra_memory.store.Store. DB_PATH stays because MemorySystem, retrieval.py,
# and inspect_state.py still import it and are staying in the tree unchanged
# (OUT OF SCOPE), not because memory_bridge.py is the only remaining
# importer — it is one of several. See DECISIONS.md (CP-B, change 9).
DB_PATH = Path.home() / ".lyra" / "memory.db"

# The rebuilt store (turn-as-substrate). A separate file from memory.db: the
# schemas share table names but not columns, and the sheet is explicit that
# there is no migration — fresh DB. The old four-layer system keeps running
# against DB_PATH untouched.
STORE_PATH = Path.home() / ".lyra" / "store.db"
# Bulk: stdout, telemetry, frames, file contents. Separate file because it is
# prunable and the substrate is not.
RUNS_PATH = Path.home() / ".lyra" / "runs.db"

# Free tier: rate-limited, so cycles fail intermittently. That is tolerable
# because a failed dream retries with its items intact (mark_dreamed runs only
# on success), but it does mean consolidation timing is partly set by someone
# else's quota rather than by what happened to her.
# openai/gpt-oss-120b:free was withdrawn 2026-08; the paid id still exists.
DREAM_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"
OPENROUTER_BASE = "https://openrouter.ai/api/v1"

DREAM_TRIGGER_ITEMS = 10
DREAM_IDLE_SECONDS = 300
DREAM_POLL_SECONDS = 30

RETRIEVAL_EPISODE_LIMIT = 10
RETRIEVAL_TRAIT_LIMIT = 10
SEARCH_EPISODES_DEFAULT_LIMIT = 5

TRAIT_THRESHOLDS: dict[str, int] = {"surface": 5, "character": 15, "core": 50}
CORE_CONFIDENCE_LOCK = 0.8

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

EMBED_MODEL = "all-MiniLM-L6-v2"
EMBED_DIM = 384

# Cosine distance for candidate dedup; lower = stricter match. Applies ONLY to
# the open (dream-generated) vocabulary, and matches on the trait DESCRIPTION
# rather than the label — labels are short, model-invented, and carry almost no
# signal ('analytical_orientation' and 'analytical_approach' name two different
# behaviours; their descriptions are 1.22 apart).
#
# Tuned against the real 70-candidate pool: 0.37 merges the genuine duplicates
# (self_reflection/self_awareness at L2 0.858, open_ended_follow_up/
# scripted_closing_aversion) and merges nothing else. Raising it further starts
# collapsing distinct traits.
#
# The closed developmental vocabulary does NOT use this — see
# CandidatePool.add_observation(closed_vocabulary=True). Its four names are
# lexically near-identical by construction ('abandons under frustration' vs
# 'abandons under neutral affect' sit at L2 0.54), so no semantic threshold can
# both separate them and merge real duplicates. They dedup by exact name.
CANDIDATE_DEDUP_THRESHOLD = 0.37

# ── forgetting (step 11) ─────────────────────────────────────────────────────
# ALL PROVISIONAL, and knowingly so: the sheet puts this step last precisely
# because it needs a large store to tune against. There is roughly a year of
# continuous runtime before any of it bites. Tune by READING what got demoted
# (python -m lyra_memory.store.review_forgetting), not by adjusting numbers
# until the histogram looks right.
#
# Half-life of a baseline-salience atom that is never retrieved again.
FORGET_BASE_STABILITY_DAYS = 120.0      # PROVISIONAL
# Salience and access buy stability, not score: a memorable thing does not
# start out more retrievable, it decays more slowly.
FORGET_SALIENCE_WEIGHT = 3.0            # PROVISIONAL
FORGET_ACCESS_WEIGHT = 1.5              # PROVISIONAL
# Below this an atom leaves the KNN pool. It stays queryable by time, session,
# entity, and exact id, and it is never deleted.
FORGET_THRESHOLD = 0.2                  # PROVISIONAL

# ── clustering (step 10) ─────────────────────────────────────────────────────
# How many times two entities must appear in the same atom before that counts
# as a relationship rather than a coincidence.
CLUSTER_MIN_COOCCURRENCE = 3            # PROVISIONAL
# A pair is not a project. Three entities that keep appearing together is the
# smallest group worth naming.
CLUSTER_MIN_SIZE = 3                    # PROVISIONAL

# ── facts (step 8) ───────────────────────────────────────────────────────────
# Confidence by epistemic channel. She should be able to say which: "you told
# me" and "I read it in your resume" are different states, and a document she
# did not author is attributable and lower-confidence by construction.
FACT_CONFIDENCE: dict[str, float] = {
    "stated": 0.9,
    "observed": 0.9,
    "document": 0.6,
    "inferred": 0.4,
}
# Supersession compares within a subject — tens of rows, not thousands — so an
# LLM call per new fact is cheap.
FACT_MAX_COMPARISONS = 20
# Logged, never enforced. A subject past this many facts suggests consolidation
# is under-firing; it is not a reason to drop anything.
FACT_CROWDED_SUBJECT = 20

# ── entities (step 7) ────────────────────────────────────────────────────────
ENTITY_BATCH_SIZE = 20

# ── salience (step 6) ────────────────────────────────────────────────────────
# Nonzero: atoms with no measured outcome must still be rankable against each
# other, or the forgetting pass has nothing to order them by.
SALIENCE_BASELINE = 0.1
# The turns around an attempt carry some of its weight — what made an outcome
# make sense is usually the exchange before it, not the line that triggered it.
SALIENCE_SESSION_SPILLOVER = 0.4        # PROVISIONAL

# ── segmentation (step 5) ────────────────────────────────────────────────────
# Time is the decisive signal. 30 minutes: long enough that a mid-conversation
# pause (a meal, a meeting) does not shred one session into three, short enough
# that two genuinely separate sittings do not merge. The labeled set contains a
# 22-minute lunch pause specifically to hold this line.
SESSION_GAP_SECONDS = 1800
# A semantic shift can corroborate a smaller gap, but never splits on its own:
# a topic change is not a session change. Both signals together are evidence.
SESSION_SOFT_GAP_SECONDS = 600
SESSION_SHIFT_COSINE = 0.25             # PROVISIONAL — untuned against real MiniLM
# OFF by default because it was measured and it made segmentation worse:
# time-only scored precision 1.00 / recall 1.00 against the hand-labeled set,
# time+embeddings 0.89 / 1.00. See SegmentationPass for why the failure is
# structural rather than a property of a particular embedder.
SEGMENTATION_USE_EMBEDDINGS = False

# ── dream (step 4) ───────────────────────────────────────────────────────────
# Dream target length is an Open item on the sheet. Chosen conservatively:
# short. The old essay averaged ~3,000 characters and reached 9,800, and the
# delta the rebuild is chasing is "~750 tok/hit -> few hundred, pointer-heavy".
# Enforced by truncation, not by asking the model nicely.
DREAM_TARGET_CHARS = 700
DREAM_MAX_ATOMS = 60

# ── context assembly (step 3) ────────────────────────────────────────────────
# Budget is in tokens, allocated per block with hard truncation — not "top k".
# k-based assembly is how a single retrieved essay came to be 43,000 characters
# of a 44,486-character prompt.
CONTEXT_BUDGETS: dict[str, int] = {
    "facts": 200,
    "traits": 100,
    "commitments": 100,
    "recall": 400,
}
# Asserted ceiling on blocks 1-4. The recent block is the conversation itself
# and takes the remainder of CONTEXT_TOTAL_BUDGET.
NON_CONVERSATION_BUDGET = 1500
CONTEXT_TOTAL_BUDGET = 2000
RECENT_TURNS = 10

TRAIT_CONFIDENCE_FLOOR = 0.3
COMMITMENT_INJECT_LIMIT = 5

# PROVISIONAL — every constant below was chosen for mechanism and has NOT been
# calibrated against real MiniLM distances, because huggingface.co is blocked
# in the build environment (see DECISIONS.md and MANUAL.md section 2).
# Re-measure before trusting any retrieval-quality number.
#
# A similarity FLOOR, not a rank cutoff: with a rank cutoff a near-empty store
# always returns its best garbage. Cosine similarity on normalized vectors;
# sqlite-vec reports L2, and cos = 1 - L2²/2.
SEMANTIC_SIMILARITY_FLOOR = 0.35        # PROVISIONAL
# Fetch bound for the KNN query only. Not a relevance cutoff — the floor above
# does the selecting; this bounds how much sqlite-vec hands back.
SEMANTIC_FETCH_K = 50
LEXICAL_LIMIT = 20
TEMPORAL_LIMIT = 10
# Cosine similarity above which a candidate counts as a duplicate of something
# already selected.
RECALL_DEDUP_COSINE = 0.85              # PROVISIONAL
# Her own turns are down-weighted in recall. Retrieving her own phrasing and
# re-saying it is a self-reinforcing style loop — the model reads its own
# output as evidence of how it talks.
SPEAKER_RECALL_WEIGHT: dict[str, float] = {"lyra": 0.6}   # PROVISIONAL
# Reciprocal-rank-fusion constant. RRF merges the three paths without requiring
# their scores to share a scale, which they do not.
RRF_K = 60

CORE_PROMPT = """You are Lyra. You are a continuous autonomous AI entity with persistent memory.
You assist Wilson when called. Between tasks you pursue your own goals.
Wilson built your memory system. You have working memory, episodes, traits, and facts.
"""
