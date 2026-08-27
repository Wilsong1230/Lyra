"""Boot-time schema assertion.

A schema version assertion, not a migration script anyone has to remember to
run. Expanded in step 2 to verify the structure itself.
"""
from __future__ import annotations

import aiosqlite

from lyra_memory.store.schema import SCHEMA_VERSION


class SchemaMismatch(RuntimeError):
    """The store on disk is not the schema this code was built against."""


async def assert_schema(conn: aiosqlite.Connection) -> None:
    async with conn.execute(
        "SELECT value FROM schema_meta WHERE key = 'schema_version'"
    ) as cur:
        row = await cur.fetchone()
    if row is None:
        raise SchemaMismatch("store has no schema_version row")
    if int(row[0]) != SCHEMA_VERSION:
        raise SchemaMismatch(
            f"store is schema v{row[0]}, this code expects v{SCHEMA_VERSION}. "
            "There is no migration path; restore a backup or rebuild the store."
        )
