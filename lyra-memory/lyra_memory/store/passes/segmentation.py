"""Segmentation — where one conversation ends and the next begins.

`find_boundaries` is a pure component: timestamps and optional vectors in,
boundary indices out, no database, no clock, no I/O. Everything about it is
testable against a frozen set, which is the only reason a precision/recall
number for it means anything.

The pass around it writes `sessions`, stamps `atoms.session_id`, and records
`gap_since_prev` — the only representation of elapsed time in the store. She
has timestamps and no sense of duration; nothing else distinguishes a
continuous month from a month with one conversation in it.
"""
from __future__ import annotations

import struct
from datetime import datetime

from lyra_memory.config import (
    EMBED_DIM,
    SEGMENTATION_USE_EMBEDDINGS,
    SESSION_GAP_SECONDS,
    SESSION_SHIFT_COSINE,
    SESSION_SOFT_GAP_SECONDS,
)
from lyra_memory.store.passes.cold import ColdPass


def _cosine(a, b) -> float:
    return sum(x * y for x, y in zip(a, b))


def find_boundaries(
    timestamps: list[float],
    vectors: list[list[float]] | None = None,
) -> list[int]:
    """Indices of the atoms that begin a session.

    Time is the primary signal and is decisive on its own: a gap past
    `SESSION_GAP_SECONDS` is a boundary regardless of what was being talked
    about.

    Meaning is only ever corroborating. A topic change is *not* a session
    change — people change the subject mid-conversation constantly — so a
    semantic shift can only split when there is already a smaller gap to
    corroborate. Letting embeddings split on their own shreds a real session
    into topical fragments, and the labeled set includes an abrupt topic
    change 70 seconds after the previous turn precisely to hold that line.
    """
    if not timestamps:
        return []

    boundaries = [0]
    for i in range(1, len(timestamps)):
        gap = timestamps[i] - timestamps[i - 1]

        if gap >= SESSION_GAP_SECONDS:
            boundaries.append(i)
            continue

        if gap >= SESSION_SOFT_GAP_SECONDS and vectors is not None:
            similarity = _cosine(vectors[i], vectors[i - 1])
            if similarity < SESSION_SHIFT_COSINE:
                boundaries.append(i)

    return boundaries


class SegmentationPass(ColdPass):
    """Derives `sessions` from `atoms`. Idempotent: rebuilds from scratch.

    The sheet lists embeddings as an input, so the semantic signal is wired
    and available — but it is **off by default, because it was measured and it
    made segmentation worse**:

        time only          precision 1.00  recall 1.00
        time + embeddings  precision 0.89  recall 1.00   spurious: [14, 15]

    Indices 14 and 15 are "hold on, food" and "ok back" — the two halves of
    the 22-minute mid-session pause the labeled set exists to protect. The
    failure is structural rather than a weakness of any particular embedder:
    short interstitial utterances are semantically unlike everything around
    them, including each other, so a shift test fires hardest on exactly the
    pattern that marks a pause *within* a session rather than a break between
    two. A better embedder makes this worse, not better.

    Left in and disabled rather than deleted: the measurement is worth being
    able to repeat, and it should be repeated on real MiniLM (see MANUAL.md).
    """

    name = "segmentation"

    def __init__(self, store, backup_dir=None, use_embeddings: bool = SEGMENTATION_USE_EMBEDDINGS) -> None:
        super().__init__(store, backup_dir=backup_dir)
        self._use_embeddings = use_embeddings

    async def execute(self) -> int:
        async with self.store.db.execute(
            "SELECT id, ts FROM atoms ORDER BY ts, id"
        ) as cur:
            rows = await cur.fetchall()
        if not rows:
            return 0

        atom_ids = [r[0] for r in rows]
        timestamps = [r[1] for r in rows]

        vectors = None
        if self._use_embeddings:
            async with self.store.db.execute(
                "SELECT v.embedding FROM atoms a JOIN vec_atoms v ON v.rowid = a.id"
                " ORDER BY a.ts, a.id"
            ) as cur:
                vectors = [list(struct.unpack(f"{EMBED_DIM}f", r[0]))
                           for r in await cur.fetchall()]

        boundaries = find_boundaries(timestamps, vectors)

        # Sessions are derived, so they are rebuilt rather than appended to.
        # Nothing is lost: every session row is a function of atoms.ts, and
        # the atoms are permanent.
        await self.store.db.execute("DELETE FROM sessions")
        await self.store.db.execute("UPDATE atoms SET session_id = NULL")

        previous_ended: float | None = None
        for n, start in enumerate(boundaries):
            end = boundaries[n + 1] if n + 1 < len(boundaries) else len(atom_ids)
            started_ts = timestamps[start]
            ended_ts = timestamps[end - 1]
            gap = (started_ts - previous_ended) if previous_ended is not None else None

            cur = await self.store.db.execute(
                "INSERT INTO sessions (started_ts, ended_ts, atom_count, gap_since_prev)"
                " VALUES (?, ?, ?, ?)",
                (started_ts, ended_ts, end - start, gap),
            )
            session_id = cur.lastrowid
            await self.store.db.executemany(
                "UPDATE atoms SET session_id = ? WHERE id = ?",
                [(session_id, atom_ids[i]) for i in range(start, end)],
            )
            previous_ended = ended_ts

        await self.store.db.commit()
        print(f"[{datetime.now().isoformat()}] [segmentation] "
              f"{len(atom_ids)} atoms into {len(boundaries)} sessions")
        return len(boundaries)
