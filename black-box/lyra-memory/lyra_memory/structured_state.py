from __future__ import annotations
import json
import time
import aiosqlite
from datetime import datetime


class StructuredState:
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn

    async def set_fact(self, key: str, value: object) -> None:
        ts = time.time()
        serialized = json.dumps(value)
        await self._conn.execute(
            "INSERT INTO facts (key, value, updated_at) VALUES (?, ?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (key, serialized, ts),
        )
        await self._conn.commit()
        print(f"[{datetime.now().isoformat()}] [StructuredState] SET {key!r} = {value!r}")

    async def get_fact(self, key: str) -> object:
        async with self._conn.execute(
            "SELECT value FROM facts WHERE key = ?", (key,)
        ) as cur:
            row = await cur.fetchone()
        return json.loads(row[0]) if row else None

    async def get_all_facts(self) -> dict:
        async with self._conn.execute("SELECT key, value FROM facts") as cur:
            rows = await cur.fetchall()
        return {k: json.loads(v) for k, v in rows}
