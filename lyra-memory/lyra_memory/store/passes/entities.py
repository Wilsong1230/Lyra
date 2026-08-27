"""Entity extraction — the names everything else keys on.

Facts are retrieved by exact match on `subject`, and clustering groups atoms
by shared entity. Both need a *name*. Without one, each falls back to
embedding similarity, which is exactly the failure that made candidate dedup
useless — 70 candidates, 60 singletons.

Two extractors, because they fail in opposite directions:

- **A model** finds people, projects and topics, which no regex can.
- **A pattern** finds file paths and repo names, which models paraphrase,
  re-case, and hallucinate slashes into. An exact string is the thing an index
  most needs verbatim and the thing a model is least reliable at reproducing.

Incremental, with a watermark: extraction costs a call per batch, so the pass
reads only atoms it has not seen. `rebuild=True` re-reads everything, for when
the extractor itself changes.

Unlike the dream pass, this one **does** read `sandbox_read` atoms. Documents
produce facts, facts are keyed on entities, and the `atom_entities` link keeps
provenance back to the atom the name came from. What documents must not touch
is dispositional state, and nothing here writes any.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

from lyra_memory.config import ENTITY_BATCH_SIZE
from lyra_memory.store.passes.cold import ColdPass
from lyra_memory.store.schema import ENTITY_KINDS

_WATERMARK_KEY = "entities_watermark"

# A path-ish token: has a separator and a plausible extension or directory
# shape. Deliberately narrow — a false "file" entity pollutes the key space
# that fact retrieval depends on being exact.
_PATH_RE = re.compile(
    r"\b[\w.-]+(?:/[\w.-]+)+\b"          # a/b, lyra_embodiment/avatar.js
    r"|\b[\w-]+\.(?:py|js|ts|tsx|json|md|sql|sh|yaml|yml|toml|db)\b"
)

_PROMPT = (
    "List the named entities in the text below.\n"
    "Return only a JSON list of objects, each with:\n"
    '  "name"       — the entity as written, lowercase\n'
    '  "kind"       — one of: project, person, repo, file, topic\n'
    '  "confidence" — 0 to 1\n'
    "Return [] if there are none. Do not invent entities.\n\nText:\n"
)


class EntityPass(ColdPass):
    name = "entities"

    def __init__(self, store, llm, backup_dir=None, batch_size: int = ENTITY_BATCH_SIZE,
                 rebuild: bool = False) -> None:
        super().__init__(store, backup_dir=backup_dir)
        self._llm = llm
        self._batch_size = batch_size
        self._rebuild = rebuild

    async def _watermark(self) -> int:
        if self._rebuild:
            return 0
        async with self.store.db.execute(
            "SELECT value FROM schema_meta WHERE key = ?", (_WATERMARK_KEY,)
        ) as cur:
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def execute(self) -> int:
        watermark = await self._watermark()
        async with self.store.db.execute(
            "SELECT id, ts, text FROM atoms WHERE id > ? ORDER BY id", (watermark,)
        ) as cur:
            atoms = await cur.fetchall()
        if not atoms:
            return 0

        written = 0
        for start in range(0, len(atoms), self._batch_size):
            batch = atoms[start:start + self._batch_size]
            for atom_id, ts, text in batch:
                found = await self._extract(text)
                for entity in found:
                    await self._link(atom_id, ts, entity)
                    written += 1
            # Advance the watermark with the batch, so a crash resumes rather
            # than re-reading everything that already cost a call.
            await self.store.db.execute(
                "INSERT INTO schema_meta (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (_WATERMARK_KEY, str(batch[-1][0])),
            )
            await self.store.db.commit()

        print(f"[{datetime.now().isoformat()}] [entities] "
              f"{len(atoms)} atoms, {written} links")
        return written

    async def _extract(self, text: str) -> list[dict]:
        raw = await self._llm(_PROMPT + text)
        try:
            found = json.loads(raw) or []
        except (json.JSONDecodeError, TypeError):
            found = []

        entities = {}
        for item in found:
            name = str(item.get("name", "")).strip().lower()
            if not name:
                continue
            kind = str(item.get("kind", "")).strip().lower()
            if kind not in ENTITY_KINDS:
                raise ValueError(
                    f"entity {name!r} has unknown kind {kind!r}; "
                    f"expected one of {sorted(ENTITY_KINDS)}"
                )
            entities[name] = {"name": name, "kind": kind,
                              "confidence": float(item.get("confidence", 0.5))}

        # The deterministic half. Confidence 1.0 because it is a literal match
        # in the text, not a judgement.
        for match in _PATH_RE.findall(text):
            name = match.lower()
            entities.setdefault(name, {"name": name, "kind": "file", "confidence": 1.0})

        return list(entities.values())

    async def _link(self, atom_id: int, ts: float, entity: dict) -> None:
        await self.store.db.execute(
            "INSERT INTO entities (name, kind, first_seen, last_seen) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(name) DO UPDATE SET"
            "   first_seen = MIN(first_seen, excluded.first_seen),"
            "   last_seen  = MAX(last_seen,  excluded.last_seen)",
            (entity["name"], entity["kind"], ts, ts),
        )
        async with self.store.db.execute(
            "SELECT id FROM entities WHERE name = ?", (entity["name"],)
        ) as cur:
            (entity_id,) = await cur.fetchone()
        await self.store.db.execute(
            "INSERT INTO atom_entities (atom_id, entity_id, confidence) VALUES (?, ?, ?)"
            " ON CONFLICT(atom_id, entity_id) DO UPDATE SET"
            "   confidence = MAX(confidence, excluded.confidence)",
            (atom_id, entity_id, entity["confidence"]),
        )
