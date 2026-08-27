"""lyra_memory.entities — proper nouns the store should be able to key on.

Cold pass. Reads `atoms.text`, writes `entities` and `atom_entities`. Feeds two
things: the `facts` subject key (facts join entities(name) where possible) and,
later, clustering into project nodes.

Extraction is deliberately conservative. An over-eager extractor produces
thousands of one-off "entities" that never join anything, and the cost of a
missed entity is one fact that has to be matched by hand later — recoverable —
while the cost of a wrong one is a subject key that silently splits a fact set.
"""
from __future__ import annotations

import re
import time

import aiosqlite

ENTITY_KINDS = frozenset({"project", "person", "repo", "file", "topic"})

# repo/path-shaped tokens: owner/name, a.b.c file names, dotted module paths.
_REPO_RE = re.compile(r"\b[\w.-]+/[\w.-]+\b")
_FILE_RE = re.compile(r"\b[\w-]+\.(?:py|js|ts|md|sql|json|yaml|yml|toml|sh|rs|go|c|h|cpp)\b")
# Capitalised runs that are not sentence-initial. Cheap proper-noun proxy.
_PROPER_RE = re.compile(r"(?<![.!?]\s)(?<!^)\b([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})*)\b")

_STOPWORDS = {
    "i", "the", "and", "but", "you", "monday", "tuesday", "wednesday", "thursday",
    "friday", "saturday", "sunday", "january", "february", "march", "april",
    "may", "june", "july", "august", "september", "october", "november", "december",
}


def extract_candidates(text: str) -> list[tuple[str, str]]:
    """Return (name, kind) pairs found in one atom's text. Pure."""
    found: dict[str, str] = {}
    for m in _REPO_RE.finditer(text):
        found.setdefault(m.group(0).lower(), "repo")
    for m in _FILE_RE.finditer(text):
        found.setdefault(m.group(0).lower(), "file")
    for line in text.split("\n"):
        for m in _PROPER_RE.finditer(line):
            name = m.group(1).lower()
            if name in _STOPWORDS or len(name) < 3:
                continue
            found.setdefault(name, "topic")
    return sorted(found.items())


class EntityStore:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def upsert(self, name: str, kind: str, seen_ts: float) -> int:
        if kind not in ENTITY_KINDS:
            raise ValueError(f"unknown entity kind {kind!r}")
        name = name.strip().lower()
        async with self._conn.execute(
            "SELECT id, first_seen, last_seen FROM entities WHERE name = ?", (name,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            cur2 = await self._conn.execute(
                "INSERT INTO entities (name, kind, first_seen, last_seen) VALUES (?, ?, ?, ?)",
                (name, kind, seen_ts, seen_ts),
            )
            return cur2.lastrowid
        entity_id, first_seen, last_seen = row
        await self._conn.execute(
            "UPDATE entities SET first_seen = ?, last_seen = ? WHERE id = ?",
            (min(first_seen, seen_ts), max(last_seen, seen_ts), entity_id),
        )
        return entity_id

    async def link(self, atom_id: int, entity_id: int, confidence: float = 1.0) -> None:
        await self._conn.execute(
            "INSERT OR IGNORE INTO atom_entities (atom_id, entity_id, confidence)"
            " VALUES (?, ?, ?)",
            (atom_id, entity_id, confidence),
        )

    async def run(self, *, since_atom_id: int = 0, limit: int = 500) -> int:
        """Cold pass over atoms that have no entity links yet."""
        async with self._conn.execute(
            "SELECT a.id, a.ts, a.text FROM atoms a"
            " LEFT JOIN atom_entities ae ON ae.atom_id = a.id"
            " WHERE a.id > ? AND ae.atom_id IS NULL ORDER BY a.id LIMIT ?",
            (since_atom_id, limit),
        ) as cur:
            rows = await cur.fetchall()

        linked = 0
        for atom_id, ts, text in rows:
            for name, kind in extract_candidates(text):
                entity_id = await self.upsert(name, kind, ts or time.time())
                await self.link(atom_id, entity_id)
                linked += 1
        await self._conn.commit()
        return linked

    async def for_atom(self, atom_id: int) -> list[dict]:
        async with self._conn.execute(
            "SELECT e.id, e.name, e.kind, ae.confidence FROM atom_entities ae"
            " JOIN entities e ON e.id = ae.entity_id WHERE ae.atom_id = ?",
            (atom_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [{"id": r[0], "name": r[1], "kind": r[2], "confidence": r[3]} for r in rows]

    async def names(self) -> list[str]:
        async with self._conn.execute("SELECT name FROM entities ORDER BY name") as cur:
            return [r[0] for r in await cur.fetchall()]
