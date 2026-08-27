"""Boot-time schema assertion. A crash, not a migration prompt.

Fail-open makes a broken store and an empty store behaviourally identical.
For an emergence thesis that is undetectable from transcripts, and it has
already happened here once: 653 turns, 0 episodes, no symptom.

So the store asserts its own shape on every open, and a mismatch raises.
There is deliberately no repair path — a schema that has drifted is a
question about *when* it drifted and which backup predates it, and silently
patching it forward destroys that question.

**The assertion compares structure, not a version stamp.** A store whose
`atoms_fts` insert trigger has been dropped still reports schema v1 while
silently indexing nothing; a version check would pass it.

The expected structure is derived by building the schema fresh in an
in-memory database from the same DDL constants the real store is built from,
so there is no second copy of the schema here to drift out of sync with the
first.
"""
from __future__ import annotations

import re
import sqlite3

import aiosqlite

from lyra_memory.store.schema import (
    COLD_SQL,
    FTS_SQL,
    HOT_SQL,
    SCHEMA_VERSION,
    VEC_SQL,
)


class SchemaMismatch(RuntimeError):
    """The store on disk is not the schema this code was built against."""


_COMMENT_RE = re.compile(r"--[^\n]*")
_WS_RE = re.compile(r"\s+")


def _normalize(sql: str | None) -> str:
    """Structure only.

    SQLite stores CREATE statements verbatim, comments and all. Comparing raw
    text would mean editing a comment invalidates every existing store, which
    is an assertion nobody would keep for long — and an assertion people
    disable is worse than none.
    """
    if sql is None:
        return ""
    return _WS_RE.sub(" ", _COMMENT_RE.sub("", sql)).strip()


async def _structure(conn: aiosqlite.Connection) -> dict:
    """The shape of a live database: tables, their columns, indexes, triggers."""
    async with conn.execute(
        "SELECT type, name, sql FROM sqlite_master"
        " WHERE name NOT LIKE 'sqlite_%' ORDER BY name"
    ) as cur:
        objects = await cur.fetchall()

    tables: dict[str, list[tuple]] = {}
    triggers: dict[str, str] = {}
    indexes: dict[str, str] = {}

    for obj_type, name, sql in objects:
        if obj_type == "table":
            async with conn.execute(f"PRAGMA table_info({name})") as cur:
                # (name, type, notnull, pk) — position deliberately excluded so
                # the comparison is about shape, not column ordering.
                tables[name] = sorted(
                    (row[1], (row[2] or "").upper(), row[3], row[5])
                    for row in await cur.fetchall()
                )
        elif obj_type == "trigger":
            triggers[name] = _normalize(sql)
        elif obj_type == "index":
            # Auto-indexes have no SQL; keep the name so a dropped explicit
            # index is still caught.
            indexes[name] = _normalize(sql)

    return {"tables": tables, "triggers": triggers, "indexes": indexes}


_reference_cache: dict | None = None


async def reference_structure() -> dict:
    """What the schema *should* look like, built from the DDL constants.

    Constructed in an in-memory database rather than written out by hand, so
    this file cannot drift away from schema.py.
    """
    global _reference_cache
    if _reference_cache is not None:
        return _reference_cache

    conn = await aiosqlite.connect(":memory:")
    try:
        await conn._execute(_load_vec_into, conn._conn)
        await conn.executescript(HOT_SQL)
        await conn.executescript(VEC_SQL)
        await conn.executescript(COLD_SQL)
        await conn.executescript(FTS_SQL)
        await conn.commit()
        _reference_cache = await _structure(conn)
    finally:
        await conn.close()
    return _reference_cache


def _load_vec_into(raw_conn: sqlite3.Connection) -> None:
    import sqlite_vec

    raw_conn.enable_load_extension(True)
    sqlite_vec.load(raw_conn)
    raw_conn.enable_load_extension(False)


def _diff(expected: dict, actual: dict) -> list[str]:
    problems: list[str] = []

    for kind in ("tables", "triggers", "indexes"):
        missing = sorted(set(expected[kind]) - set(actual[kind]))
        extra = sorted(set(actual[kind]) - set(expected[kind]))
        for name in missing:
            problems.append(f"missing {kind[:-1]}: {name}")
        for name in extra:
            problems.append(f"unexpected {kind[:-1]}: {name}")

    for name in sorted(set(expected["tables"]) & set(actual["tables"])):
        want, have = expected["tables"][name], actual["tables"][name]
        if want == have:
            continue
        want_cols = {c[0] for c in want}
        have_cols = {c[0] for c in have}
        for col in sorted(want_cols - have_cols):
            problems.append(f"table {name}: missing column {col}")
        for col in sorted(have_cols - want_cols):
            problems.append(f"table {name}: unexpected column {col}")
        for col in sorted(want_cols & have_cols):
            w = next(c for c in want if c[0] == col)
            h = next(c for c in have if c[0] == col)
            if w != h:
                problems.append(
                    f"table {name}: column {col} is {h[1:]}, expected {w[1:]} "
                    "(type, notnull, pk)"
                )

    for name in sorted(set(expected["triggers"]) & set(actual["triggers"])):
        if expected["triggers"][name] != actual["triggers"][name]:
            problems.append(f"trigger {name}: definition differs")

    return problems


async def assert_schema(conn: aiosqlite.Connection) -> None:
    """Raise unless the store matches the schema this code was built against."""
    async with conn.execute(
        "SELECT value FROM schema_meta WHERE key = 'schema_version'"
    ) as cur:
        row = await cur.fetchone()

    if row is None:
        raise SchemaMismatch(
            "store has no schema_version row. This is not a store this code "
            "wrote. There is no migration path; restore a backup or rebuild."
        )
    if int(row[0]) != SCHEMA_VERSION:
        raise SchemaMismatch(
            f"store is schema v{row[0]}, this code expects v{SCHEMA_VERSION}. "
            "There is no migration path; restore a backup or rebuild the store."
        )

    from lyra_memory.embeddings import backend_id

    async with conn.execute(
        "SELECT value FROM schema_meta WHERE key = 'embedder'"
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        raise SchemaMismatch(
            "store does not record which embedder built its vectors. Its "
            "embedding space is unknown, so no distance in it can be trusted."
        )
    if row[0] != backend_id():
        raise SchemaMismatch(
            f"store's vectors were built by {row[0]!r}, this process embeds "
            f"with {backend_id()!r}. Distances across two embedding spaces are "
            "noise. Either set LYRA_EMBED_BACKEND to match, or rebuild the "
            "store's vectors with the embedder you want."
        )

    problems = _diff(await reference_structure(), await _structure(conn))
    if problems:
        raise SchemaMismatch(
            f"store schema does not match schema v{SCHEMA_VERSION}:\n  "
            + "\n  ".join(problems)
            + "\n\nThe version stamp matched but the structure did not, which "
            "means something wrote to this store outside this code. Do not "
            "patch it forward — work out when it drifted and restore the "
            "backup that predates it."
        )
