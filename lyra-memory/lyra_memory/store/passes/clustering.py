"""Clustering — entities that keep appearing together become a project.

Reads `atom_entities` and `dreams`, writes `entities(kind='project')` and the
links from atoms to those projects.

The grouping signal is **co-occurrence, not embedding similarity**. Two
entities belong together because they keep showing up in the same turns, which
is a fact about her history; embedding similarity would say `lyra-voice` and
`lyra-listen` are alike because their names are alike, which is a fact about
their spelling. Co-occurrence is also what makes the cluster explainable — the
edges are countable, and any project it produces can be traced back to the
atoms that built it.

Nothing here deletes. Project entities are upserted by name and their atom
links rebuilt; a cluster that stops appearing leaves its project entity in
place, because `facts.subject` may already key on that name and there is no
deletion path in this store.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime

from lyra_memory.config import (
    CLUSTER_MIN_COOCCURRENCE,
    CLUSTER_MIN_SIZE,
)
from lyra_memory.store.passes.cold import ColdPass

_NAME_PROMPT = (
    "These names keep appearing together in one person's work.\n"
    "Give the project they belong to a short lowercase name, two or three "
    "words at most. Reply with the name and nothing else.\n\n"
)


def _components(edges: dict[tuple[int, int], int], min_weight: int) -> list[set[int]]:
    """Connected components over edges that clear the weight threshold.

    Deliberately the simplest grouping that works. A community-detection
    algorithm would need a resolution parameter, which is a tuning constant
    with no data yet to fit it to — and an over-merged cluster is visible and
    fixable, while a scheme nobody can explain is neither.
    """
    adjacency: dict[int, set[int]] = defaultdict(set)
    for (a, b), weight in edges.items():
        if weight >= min_weight:
            adjacency[a].add(b)
            adjacency[b].add(a)

    seen: set[int] = set()
    components = []
    for node in adjacency:
        if node in seen:
            continue
        stack, group = [node], set()
        while stack:
            current = stack.pop()
            if current in group:
                continue
            group.add(current)
            stack.extend(adjacency[current] - group)
        seen |= group
        components.append(group)
    return components


class ClusteringPass(ColdPass):
    name = "clustering"

    def __init__(self, store, llm=None, backup_dir=None) -> None:
        super().__init__(store, backup_dir=backup_dir)
        self._llm = llm

    async def execute(self) -> int:
        async with self.store.db.execute(
            "SELECT ae.atom_id, ae.entity_id FROM atom_entities ae"
            " JOIN entities e ON e.id = ae.entity_id"
            " WHERE e.kind != 'project'"
            " ORDER BY ae.atom_id"
        ) as cur:
            rows = await cur.fetchall()
        if not rows:
            return 0

        by_atom: dict[int, list[int]] = defaultdict(list)
        for atom_id, entity_id in rows:
            by_atom[atom_id].append(entity_id)

        edges: dict[tuple[int, int], int] = defaultdict(int)
        for entity_ids in by_atom.values():
            unique = sorted(set(entity_ids))
            for i, a in enumerate(unique):
                for b in unique[i + 1:]:
                    edges[(a, b)] += 1

        clusters = [c for c in _components(edges, CLUSTER_MIN_COOCCURRENCE)
                    if len(c) >= CLUSTER_MIN_SIZE]
        if not clusters:
            return 0

        async with self.store.db.execute("SELECT id, name FROM entities") as cur:
            names = {row[0]: row[1] for row in await cur.fetchall()}

        written = 0
        for cluster in clusters:
            members = {i: names[i] for i in sorted(cluster) if i in names}
            if not members:
                continue
            project = await self._name(members, edges)
            await self._write(project, cluster, by_atom)
            written += 1

        await self.store.db.commit()
        print(f"[{datetime.now().isoformat()}] [clustering] {written} projects "
              f"from {len(names)} entities")
        return written

    async def _name(self, members: dict[int, str],
                    edges: dict[tuple[int, int], int]) -> str:
        """Name a cluster, falling back to its most connected member.

        The fallback is not a placeholder — the entity with the most weight
        inside the cluster is genuinely the thing the others orbit, and a name
        drawn from the store beats one invented by a model that is
        unavailable.
        """
        degree: dict[int, int] = {i: 0 for i in members}
        for (a, b), weight in edges.items():
            if a in members and b in members:
                degree[a] += weight
                degree[b] += weight
        # Tie-break on name so the result is deterministic across runs.
        fallback = min(members, key=lambda i: (-degree[i], members[i]))
        fallback = members[fallback]

        if self._llm is None:
            return fallback

        context = ", ".join(members[i] for i in sorted(members, key=members.get))
        async with self.store.db.execute(
            "SELECT text FROM dreams ORDER BY ts DESC LIMIT 3"
        ) as cur:
            dreams = [row[0] for row in await cur.fetchall()]
        prompt = _NAME_PROMPT + context
        if dreams:
            prompt += "\n\nRecent reflections that mention them:\n" + "\n".join(dreams)

        try:
            answer = (await self._llm(prompt) or "").strip().lower()
        except Exception as exc:  # noqa: BLE001
            print(f"[{datetime.now().isoformat()}] [clustering] naming failed "
                  f"({exc}); using {fallback!r}")
            return fallback

        answer = answer.splitlines()[0].strip(' "\'.') if answer else ""
        return answer if 0 < len(answer) <= 60 else fallback

    async def _write(self, project: str, cluster: set[int],
                     by_atom: dict[int, list[int]]) -> None:
        async with self.store.db.execute(
            "SELECT MIN(first_seen), MAX(last_seen) FROM entities WHERE id IN "
            f"({','.join('?' * len(cluster))})",
            tuple(cluster),
        ) as cur:
            first_seen, last_seen = await cur.fetchone()

        await self.store.db.execute(
            "INSERT INTO entities (name, kind, first_seen, last_seen)"
            " VALUES (?, 'project', ?, ?)"
            " ON CONFLICT(name) DO UPDATE SET"
            "   kind = 'project',"
            "   first_seen = MIN(first_seen, excluded.first_seen),"
            "   last_seen  = MAX(last_seen,  excluded.last_seen)",
            (project, first_seen, last_seen),
        )
        async with self.store.db.execute(
            "SELECT id FROM entities WHERE name = ?", (project,)
        ) as cur:
            (project_id,) = await cur.fetchone()

        # Rebuild this project's atom links only. Other entities' links, and
        # every other project, are untouched.
        await self.store.db.execute(
            "DELETE FROM atom_entities WHERE entity_id = ?", (project_id,))
        for atom_id, entity_ids in by_atom.items():
            if cluster & set(entity_ids):
                await self.store.db.execute(
                    "INSERT INTO atom_entities (atom_id, entity_id, confidence)"
                    " VALUES (?, ?, ?)"
                    " ON CONFLICT(atom_id, entity_id) DO NOTHING",
                    (atom_id, project_id, 1.0),
                )
