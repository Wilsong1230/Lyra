from __future__ import annotations
import datetime
import logging
from pathlib import Path
import aiosqlite
from lyra_memory import config
from lyra_memory.config import SEARCH_EPISODES_DEFAULT_LIMIT, RETRIEVAL_EPISODE_LIMIT
from lyra_memory.db import load_vec_extension
from lyra_memory.embeddings import embed

_log = logging.getLogger(__name__)


async def build_context(memory: object, query: str | None = None) -> str:
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

    # Episode retrieval deliberately sits OUTSIDE the `if working:` block. The
    # caller's query is authoritative; the most recent user turn is only a
    # fallback. Nesting this under `if working:` meant the first turn of a
    # process — when working memory is still empty — retrieved nothing at all.
    if query is None:
        last_user = next((i for i in reversed(working) if i.role == "user"), None)
        query = last_user.content if last_user else None

    if query:
        db_path = getattr(memory, "_db_path", None)
        try:
            eps = await search_episodes(query, limit=RETRIEVAL_EPISODE_LIMIT, path=db_path)
        except Exception as e:
            print(f"[{datetime.datetime.now().isoformat()}] [retrieval] episode retrieval failed: {e}")
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
    """KNN over ATOMS — one turn/observation/reflection each.

    Dream essays live in `episodes` and are deliberately excluded: a single
    3,000-character essay outranks and drowns out every atom it was
    summarised from, and ten of them made the assembled prompt 44KB.
    """
    _log.debug("search_atoms query=%r limit=%d", query, limit)
    query_vec = await embed(query)
    async with aiosqlite.connect(path or config.DB_PATH) as conn:
        await load_vec_extension(conn)
        async with conn.execute(
            "SELECT v.rowid, a.content, a.ts, a.salience, a.role, a.type "
            "FROM vec_atoms v "
            "JOIN atoms a ON a.id = v.rowid "
            "WHERE v.embedding MATCH ? AND k = ? "
            "ORDER BY v.distance",
            (query_vec, limit),
        ) as cur:
            rows = await cur.fetchall()
    return [
        {"id": r[0], "content": r[1], "ts": r[2], "salience": r[3],
         "role": r[4], "type": r[5]}
        for r in rows
    ]


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
