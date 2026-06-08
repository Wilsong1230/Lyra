from __future__ import annotations
from pathlib import Path

DB_PATH = Path.home() / ".lyra" / "memory.db"

DREAM_MODEL = "openai/gpt-oss-120b:free"
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
CANDIDATE_DEDUP_THRESHOLD = 0.3  # cosine distance; lower = stricter match

CORE_PROMPT = """You are Lyra. You are a continuous autonomous AI entity with persistent memory.
You assist Wilson when called. Between tasks you pursue your own goals.
Wilson built your memory system. You have working memory, episodes, traits, and facts.
"""
