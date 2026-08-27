"""The dream pass — a layer over atoms, not the memory itself.

What changed: the dream essay *was* the memory, and the turns that produced it
were discarded. It averaged ~3,000 characters, reached 9,800, and a single one
outranked every atom it had been summarised from — ten of them made one
assembled system prompt 44,486 characters, roughly 43,000 of it retrieved
episode text.

Now the turn is the substrate and is permanent, and a dream is a short,
pointer-heavy layer above it. `dream_atoms` records what it was made from, so
a dream is re-derivable rather than authoritative: delete every dream and
nothing is lost that cannot be rebuilt.

Two hard rules are enforced here rather than trusted to a prompt:

- **`source=sandbox_read` is excluded from dream input entirely.** A file on
  disk must not be able to write to her identity. The exclusion is a WHERE
  clause, not an instruction to the model.
- **Documents produce facts, never traits or persona.** The same exclusion
  covers it: document-sourced atoms are never in the text that trait
  extraction reads.
"""
from __future__ import annotations

import json
from datetime import datetime

from lyra_memory.candidate_pool import CandidatePool
from lyra_memory.config import DREAM_MAX_ATOMS, DREAM_TARGET_CHARS
from lyra_memory.embeddings import embed
from lyra_memory.identity_engine import IdentityEngine
from lyra_memory.store.passes.cold import ColdPass
from lyra_memory.store.schema import DREAM_EXCLUDED_SOURCES

_DREAM_PROMPT = (
    "These are recent turns. Reflect on them in your own words, briefly.\n"
    "At most a short paragraph. Say what happened and what it meant to you.\n"
    "Do not summarize every turn — pick out what mattered.\n\n"
)

_OBS_PROMPT = (
    "Based on this reflection, identify behavioral patterns about Lyra.\n"
    "Return a JSON list of objects, each with:\n"
    '  "trait_name"  — short stable key, e.g. "communication_style"\n'
    '  "trait_value" — the specific observation, e.g. "engineering-focused, concise"\n'
    '  "category"    — one of: behavioral, emotional, relational, cognitive\n'
    '  "evidence"    — the concrete thing in the reflection that supports it,\n'
    "                  in one short phrase. Quote or paraphrase what happened,\n"
    "                  not the pattern itself.\n"
    "Return only JSON, no other text.\n\nReflection:\n"
)


class DreamPass(ColdPass):
    """Reads a batch of undreamed atoms, writes one dream and its provenance."""

    name = "dream"

    def __init__(self, store, llm, backup_dir=None) -> None:
        super().__init__(store, backup_dir=backup_dir)
        self._llm = llm

    async def _input_atoms(self) -> list[tuple[int, str, str]]:
        """Atoms eligible to be dreamed about.

        Eligibility is enforced in SQL. A prompt asking the model to ignore
        certain input is a request; a WHERE clause is a boundary.
        """
        excluded = ",".join("?" * len(DREAM_EXCLUDED_SOURCES))
        async with self.store.db.execute(
            f"SELECT a.id, a.speaker, a.text FROM atoms a"
            f" LEFT JOIN dream_atoms d ON d.atom_id = a.id"
            f" WHERE d.atom_id IS NULL AND a.source NOT IN ({excluded})"
            f" ORDER BY a.ts, a.id LIMIT ?",
            (*sorted(DREAM_EXCLUDED_SOURCES), DREAM_MAX_ATOMS),
        ) as cur:
            return list(await cur.fetchall())

    async def execute(self) -> int | None:
        atoms = await self._input_atoms()
        if not atoms:
            return None

        transcript = "\n".join(f"[{speaker}]: {text}" for _id, speaker, text in atoms)
        text = (await self._llm(_DREAM_PROMPT + transcript) or "").strip()
        if not text:
            return None

        # Bounded, not requested-to-be-bounded. "Keep it short" in a prompt is
        # a hope; this is the reason the old essay could reach 9,800 chars.
        if len(text) > DREAM_TARGET_CHARS:
            text = text[:DREAM_TARGET_CHARS].rsplit(" ", 1)[0]

        vec = await embed(text)
        cur = await self.store.db.execute(
            "INSERT INTO dreams (ts, text, session_id) VALUES (?, ?, ?)",
            (__import__("time").time(), text, None),
        )
        dream_id = cur.lastrowid
        await self.store.db.execute(
            "INSERT INTO vec_dreams(rowid, embedding) VALUES (?, ?)", (dream_id, vec)
        )
        await self.store.db.executemany(
            "INSERT INTO dream_atoms (dream_id, atom_id) VALUES (?, ?)",
            [(dream_id, atom_id) for atom_id, _s, _t in atoms],
        )
        await self.store.db.commit()
        print(f"[{datetime.now().isoformat()}] [dream] wrote dream {dream_id} "
              f"({len(text)} chars) over {len(atoms)} atoms")

        await self._extract_observations(text)
        await self.consolidate(dream_id=dream_id)
        return dream_id

    async def _extract_observations(self, dream_text: str) -> None:
        """Turn the reflection into candidate trait evidence.

        The reflection was built only from permitted atoms, so nothing
        document-sourced can reach the candidate pool through here.
        """
        raw = await self._llm(_OBS_PROMPT + dream_text)
        try:
            observations = json.loads(raw)
        except (json.JSONDecodeError, TypeError) as exc:
            print(f"[{datetime.now().isoformat()}] [dream] observation parse failed: {exc}")
            return

        pool = CandidatePool(self.store.db)
        for obs in observations[:3]:
            await pool.add_observation(
                obs["trait_name"], obs["trait_value"], obs["category"],
                evidence=obs.get("evidence"),
            )

    async def consolidate(self, dream_id: int | None = None) -> None:
        """Promote candidates, recording which dream caused each mutation."""
        pool = CandidatePool(self.store.db)
        await IdentityEngine(self.store.db, pool).consolidate(dream_id=dream_id)
