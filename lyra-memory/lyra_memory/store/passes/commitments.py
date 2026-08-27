"""Commitments — the line between having memory and being useful.

Without this table, "I'll look at that tomorrow" is an atom, retrievable by
luck. With it, open loops assert themselves: this is the one store that
surfaces *unprompted*, because everything else is retrieved on relevance and
an unclosed loop is not a relevance question.

Both owners matter. She should be able to say "you said you'd migrate the DB",
not only track her own.

Closure is evidence-only, and that is the whole point of the table:

- **Never closed on time alone.** A due date passing means *overdue*, not
  done. Nothing in this module reads the clock to decide status.
- **`dropped` requires evidence of abandonment** — an explicit statement, or
  supersession by a conflicting commitment. Silent aging into `dropped` would
  let her quietly forget things she said she would do, which is precisely the
  failure this table exists to prevent.
"""
from __future__ import annotations

import json
from datetime import datetime

from lyra_memory.store.passes.cold import ColdPass
from lyra_memory.store.schema import COMMITMENT_STATUSES

_WATERMARK_KEY = "commitments_watermark"

_EXTRACT_PROMPT = (
    "Read the exchange below. Did anyone state an intention to do something "
    "LATER?\n"
    "Return only a JSON list of objects, each with:\n"
    '  "text"  — the commitment, as stated, in a short phrase\n'
    '  "owner" — who owes it: "lyra" or "wilson"\n'
    '  "line"  — the number of the line it was stated on\n'
    '  "due"   — the deadline ONLY if one was explicitly given, else null\n'
    "\n"
    "Do NOT include:\n"
    "  - questions about whether to do something\n"
    "  - hypotheticals or counterfactuals ('if we had more time...')\n"
    "  - things already done, reported in the past tense\n"
    "  - statements of uncertainty or of what is possible\n"
    "  - decisions NOT to do something\n"
    "Return [] if there are none.\n\nExchange:\n"
)

_CLOSURE_PROMPT = (
    "Here is a list of open commitments and a recent exchange.\n"
    "Which commitments does the exchange show were COMPLETED, or explicitly "
    "ABANDONED?\n"
    "Completion needs evidence that the thing was actually done — not that it "
    "was discussed, planned, or is still in progress.\n"
    "Abandonment needs an explicit statement that it will not happen.\n"
    "A deadline passing is NOT evidence of either.\n"
    'Return only a JSON list of objects: {"number": N, "status": "done" or '
    '"dropped"}. Return [] if none.\n\n'
)


class CommitmentPass(ColdPass):
    name = "commitments"

    def __init__(self, store, llm, backup_dir=None) -> None:
        super().__init__(store, backup_dir=backup_dir)
        self._llm = llm

    async def _watermark(self) -> int:
        async with self.store.db.execute(
            "SELECT value FROM schema_meta WHERE key = ?", (_WATERMARK_KEY,)
        ) as cur:
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def execute(self) -> int:
        watermark = await self._watermark()
        async with self.store.db.execute(
            "SELECT id, ts, speaker, text FROM atoms WHERE id > ? ORDER BY id",
            (watermark,),
        ) as cur:
            atoms = await cur.fetchall()
        if not atoms:
            return 0

        # Closure runs first, against the commitments that were already open
        # before this batch — so a commitment made and completed inside one
        # batch is recorded as made, then closed, rather than skipped.
        closed = await self._close(atoms)
        opened = await self._extract(atoms)

        await self.store.db.execute(
            "INSERT INTO schema_meta (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (_WATERMARK_KEY, str(atoms[-1][0])),
        )
        await self.store.db.commit()

        print(f"[{datetime.now().isoformat()}] [commitments] "
              f"{opened} opened, {closed} closed")
        return opened

    async def _extract(self, atoms: list[tuple]) -> int:
        numbered = "\n".join(
            f"{n}. [{speaker}] {text}" for n, (_id, _ts, speaker, text) in enumerate(atoms))
        raw = await self._llm(_EXTRACT_PROMPT + numbered)
        try:
            found = json.loads(raw) or []
        except (json.JSONDecodeError, TypeError):
            return 0

        written = 0
        for item in found:
            text = str(item.get("text", "")).strip()
            owner = str(item.get("owner", "")).strip().lower()
            if not text or owner not in {"lyra", "wilson"}:
                continue

            line = item.get("line")
            try:
                source_atom_id = atoms[int(line)][0]
                ts = atoms[int(line)][1]
            except (TypeError, ValueError, IndexError):
                source_atom_id, ts = atoms[0][0], atoms[0][1]

            # due_ts only when a real timestamp was resolved upstream. A
            # phrase like "friday" is kept in the text rather than guessed at
            # — an invented deadline is worse than none, because overdue is a
            # state this store reports on.
            due_ts = item.get("due_ts")
            due_ts = float(due_ts) if isinstance(due_ts, (int, float)) else None

            await self.store.db.execute(
                "INSERT INTO commitments (created_ts, source_atom_id, text, owner,"
                " due_ts, status, closed_ts, closed_atom_id)"
                " VALUES (?, ?, ?, ?, ?, 'open', NULL, NULL)",
                (ts, source_atom_id, text, owner, due_ts),
            )
            written += 1
        return written

    async def _close(self, atoms: list[tuple]) -> int:
        async with self.store.db.execute(
            "SELECT id, text, owner FROM commitments WHERE status = 'open'"
            " ORDER BY created_ts"
        ) as cur:
            open_commitments = await cur.fetchall()
        if not open_commitments:
            return 0

        listing = "\n".join(
            f"{n + 1}. [{owner}] {text}"
            for n, (_id, text, owner) in enumerate(open_commitments))
        exchange = "\n".join(f"[{speaker}] {text}" for _id, _ts, speaker, text in atoms)
        raw = await self._llm(
            f"{_CLOSURE_PROMPT}Open commitments:\n{listing}\n\nExchange:\n{exchange}\n")
        try:
            decisions = json.loads(raw) or []
        except (json.JSONDecodeError, TypeError):
            return 0

        closing_atom_id = atoms[-1][0]
        closing_ts = atoms[-1][1]
        closed = 0
        for decision in decisions:
            try:
                index = int(decision.get("number")) - 1
            except (TypeError, ValueError):
                continue
            status = str(decision.get("status", "")).strip().lower()
            if status not in COMMITMENT_STATUSES or status == "open":
                continue
            if not 0 <= index < len(open_commitments):
                continue

            await self.store.db.execute(
                "UPDATE commitments SET status = ?, closed_ts = ?, closed_atom_id = ?"
                " WHERE id = ?",
                (status, closing_ts, closing_atom_id, open_commitments[index][0]),
            )
            closed += 1
        return closed

    async def overdue(self, now: float) -> list[tuple]:
        """Open commitments past their due date.

        Reported, never acted on. A due date passing means overdue, not done,
        and this method deliberately has no power to change a status.

        Overdue count is also **not** a valence source. Making her feel bad
        about a backlog is a design choice, and it is the wrong one.
        """
        async with self.store.db.execute(
            "SELECT id, text, owner, due_ts FROM commitments"
            " WHERE status = 'open' AND due_ts IS NOT NULL AND due_ts < ?"
            " ORDER BY due_ts",
            (now,),
        ) as cur:
            return list(await cur.fetchall())
