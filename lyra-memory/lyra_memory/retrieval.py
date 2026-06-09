from __future__ import annotations
import datetime
from pathlib import Path
import aiosqlite
from lyra_memory import config
from lyra_memory.config import SEARCH_EPISODES_DEFAULT_LIMIT, RETRIEVAL_EPISODE_LIMIT
from lyra_memory.db import load_vec_extension
from lyra_memory.embeddings import embed


async def build_context(memory: object) -> str:
    traits = await memory.identity_engine.get_top_traits()
    working = memory.working_memory.get_items()

    parts: list[str] = []

    if traits:
        parts.append(
            "## Persona Traits\n"
            + "\n".join(
                f"- [{t.stability}] {t.name}: {t.value} (confidence={t.confidence:.2f})"
                for t in traits
            )
        )

    if working:
        parts.append(
            "## Current Experience\n"
            + "\n".join(f"[{i.role or i.type}]: {i.content}" for i in working)
        )

        query = " ".join(i.content for i in working)
        try:
            eps = await search_episodes(query, limit=RETRIEVAL_EPISODE_LIMIT)
        except Exception:
            eps = []
        if eps:
            parts.append("## Past Reflections\n" + "\n".join(f"- {e['content']}" for e in eps))

    return "\n\n".join(parts)


async def build_system_prompt(memory: object) -> str:
    context = await build_context(memory)
    return f"{config.CORE_PROMPT}\n\n{context}".strip() if context else config.CORE_PROMPT


async def search_episodes(
    query: str,
    limit: int = SEARCH_EPISODES_DEFAULT_LIMIT,
    path: Path | None = None,
) -> list[dict]:
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    print(f"[{ts}] search_episodes query={query!r} limit={limit}")
    query_vec = await embed(query)
    async with aiosqlite.connect(path or config.DB_PATH) as conn:
        await load_vec_extension(conn)
        async with conn.execute(
            "SELECT v.rowid, e.content, e.ts "
            "FROM vec_episodes v "
            "JOIN episodes e ON e.id = v.rowid "
            "WHERE v.embedding MATCH ? AND k = ? "
            "ORDER BY v.distance",
            (query_vec, limit),
        ) as cur:
            rows = await cur.fetchall()
    return [{"id": r[0], "content": r[1], "ts": r[2]} for r in rows]


async def get_fact(subject_key: str) -> dict | None:
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    print(f"[{ts}] get_fact key={subject_key!r}")
    async with aiosqlite.connect(config.DB_PATH) as conn:
        async with conn.execute(
            "SELECT key, value, updated_at FROM facts WHERE key = ?",
            (subject_key,),
        ) as cur:
            row = await cur.fetchone()
    if row is None:
        return None
    return {"key": row[0], "value": row[1], "updated_at": row[2]}
