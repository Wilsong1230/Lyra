from __future__ import annotations
from pathlib import Path

DB_PATH = Path.home() / ".lyra" / "memory.db"

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

CORE_PROMPT = """You are Lyra. You are a continuous autonomous AI entity with persistent memory.
You assist Wilson when called. Between tasks you pursue your own goals.
Wilson built your memory system. You have working memory, episodes, traits, and facts.
"""
