"""lyra_memory.atoms — per-turn episodic atoms, the retrieval unit.

WHY ATOMS
─────────
Episodes used to be one dream-cycle essay: ~3,000 characters (max 9,800),
composite over ~10 turns, written in a voice that blends every topic in the
batch. Three measured consequences:

  * a live assembled system prompt reached 44,486 characters, ~43,000 of it
    retrieved episode text;
  * retrieved text carried its own `##` headings and was structurally
    indistinguishable from the prompt's own scaffolding;
  * salience was max(item.score) over the batch, which saturates — 13 of 29
    episodes sat at 0.933.

Everything planned downstream (session-boundary detection, clustering into
project nodes, two-pool retrieval) assumes episodes are small, numerous and
comparable. Clustering essays yields clusters of essays.

An atom is one turn, observation, or reflection, stored with the salience
WorkingMemory already computed for it at write time. The dream essay is kept —
it is genuine consolidation — but it lives in `episodes` as a layer above the
atoms it summarises (atoms.episode_id) and never competes in KNN.
"""
from __future__ import annotations

import aiosqlite

from lyra_memory.embeddings import embed
from lyra_memory.models import WorkingMemoryItem


class AtomStore:
    """Writes and searches episodic atoms."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def write(self, item: WorkingMemoryItem) -> int:
        """Persist one working-memory item as an atom, with its own salience."""
        cur = await self._conn.execute(
            "INSERT INTO atoms (content, ts, type, role, salience) VALUES (?, ?, ?, ?, ?)",
            (item.content, item.ts, item.type, item.role, item.score),
        )
        rowid = cur.lastrowid
        await self._conn.execute(
            "INSERT INTO vec_atoms(rowid, embedding) VALUES (?, ?)",
            (rowid, await embed(item.content)),
        )
        await self._conn.commit()
        return rowid

    async def link_to_episode(self, atom_ids: list[int], episode_id: int) -> None:
        """Attach atoms to the dream episode that consolidated them."""
        if not atom_ids:
            return
        await self._conn.executemany(
            "UPDATE atoms SET episode_id = ? WHERE id = ?",
            [(episode_id, a) for a in atom_ids],
        )
        await self._conn.commit()

    async def count(self) -> int:
        async with self._conn.execute("SELECT COUNT(*) FROM atoms") as cur:
            return (await cur.fetchone())[0]
