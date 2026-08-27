from __future__ import annotations
import time
from collections import deque
from datetime import datetime
from lyra_memory.config import TYPE_WEIGHTS, EMOTION_KEYWORDS
from lyra_memory.models import WorkingMemoryItem

# Passive background sense sources receive a lower base importance than standard
# observations (0.6) so ambient audio doesn't dominate the dreaming queue.
# The sink (Phase 1.3) passes obs.source when calling add_observation().
_SENSE_SOURCES: frozenset[str] = frozenset({"ears", "wakeword"})
_SENSE_IMPORTANCE: float = 0.15


class WorkingMemory:
    """The hot deque. In-process, bounded, not the record.

    `atoms` is the record. This buffer exists so the reply path has the last
    few turns verbatim without a query, and so the dream trigger has something
    to count.
    """

    def __init__(self) -> None:
        self._deque: deque[WorkingMemoryItem] = deque(maxlen=20)
        self._undreamed: list[WorkingMemoryItem] = []
        self._items_since_dream: int = 0
        self._last_turn_ts: float = 0.0

    def _score(self, item_type: str, content: str) -> float:
        importance = TYPE_WEIGHTS[item_type]

        punct = 0.3 if ("?" in content or "!" in content) else 0.0
        known = {w for item in self._deque for w in item.content.lower().split()}
        new_words = set(content.lower().split())
        novelty = min((len(new_words - known) / max(len(new_words), 1)) * 1.5, 0.5)
        surprise = min(punct + novelty, 1.0)

        emotion = 1.0 if any(w in content.lower() for w in EMOTION_KEYWORDS) else 0.0
        return (importance + surprise + emotion) / 3

    def _append(self, item: WorkingMemoryItem) -> WorkingMemoryItem:
        self._deque.append(item)
        self._undreamed.append(item)
        self._items_since_dream += 1
        print(f"[{datetime.now().isoformat()}] [WorkingMemory] +{item.type} score={item.score:.2f} role={item.role!r}")
        return item

    # The add_* methods return the item they created; MemorySystem appends the
    # corresponding atom separately.
    #
    # The score below is NOT the atom's salience. Salience is a COLD, revisable
    # pass (lyra_memory.outcomes.SalienceScorer) because what mattered about a
    # moment usually is not knowable at that moment — it depends on how things
    # turned out. This score only orders the in-process deque and decides what
    # the dream trigger counts.

    def add_turn(self, role: str, content: str) -> WorkingMemoryItem:
        self._last_turn_ts = time.time()
        return self._append(WorkingMemoryItem(
            type="conversation", role=role, content=content,
            score=self._score("conversation", content), ts=self._last_turn_ts,
        ))

    def add_observation(self, content: str, source: str | None = None) -> WorkingMemoryItem:
        score = _SENSE_IMPORTANCE if source in _SENSE_SOURCES else self._score("observation", content)
        return self._append(WorkingMemoryItem(
            type="observation", role=None, content=content,
            score=score, ts=time.time(),
        ))

    def add_reflection(self, content: str) -> WorkingMemoryItem:
        return self._append(WorkingMemoryItem(
            type="reflection", role=None, content=content,
            score=self._score("reflection", content), ts=time.time(),
        ))

    def get_items(self) -> list[WorkingMemoryItem]:
        return list(self._deque)

    def get_undreamed(self) -> list[WorkingMemoryItem]:
        return list(self._undreamed)

    def count_since_last_dream(self) -> int:
        return self._items_since_dream

    def last_turn_ts(self) -> float:
        return self._last_turn_ts

    def mark_dreamed(self) -> None:
        self._undreamed.clear()
        self._items_since_dream = 0
        print(f"[{datetime.now().isoformat()}] [WorkingMemory] dream marker reset")
