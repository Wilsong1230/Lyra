"""Shared machinery for cold passes.

One hard rule lives here: **backup before every cold pass.** The hot path only
appends, so it cannot lose anything; cold passes mutate — salience,
`session_id`, supersession — and a bad pass could stamp the store with no undo.

Retention is 30 days, not 7, because undetected tampering contaminates every
backup inside its window, so the retention period has to exceed plausible
detection lag. Pruning is deliberately **not** automated here: deleting
backups is the one operation that could destroy the evidence of what went
wrong. See MANUAL.md.
"""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime
from pathlib import Path


class ColdPass:
    """Base for a pass that reads atoms and writes derived rows."""

    name = "cold"

    def __init__(self, store, backup_dir: Path | None = None) -> None:
        self.store = store
        self._backup_dir = backup_dir

    @property
    def backup_dir(self) -> Path:
        if self._backup_dir is not None:
            return Path(self._backup_dir)
        return self.store.path.parent / "backups"

    async def backup(self) -> Path:
        """Snapshot the store before touching it.

        Uses SQLite's own backup API rather than copying the file, so it is
        safe against a concurrent writer under WAL.
        """
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.fromtimestamp(time.time()).strftime("%Y%m%dT%H%M%S")
        target = self.backup_dir / f"store-{stamp}-{self.name}.db"

        def _copy(raw: sqlite3.Connection) -> None:
            dest = sqlite3.connect(target)
            try:
                raw.backup(dest)
            finally:
                dest.close()

        await self.store.db._execute(_copy, self.store.db._conn)
        return target

    async def run(self):
        await self.backup()
        return await self.execute()

    async def execute(self):  # pragma: no cover - overridden
        raise NotImplementedError
