"""lyra_memory.introspect — `query_memory`, a read-only projection over the store.

DISTINCT FROM RETRIEVAL. Retrieval is passive: relevant material arrives
unasked. Introspection is deliberate: she goes looking. It is the difference
between remembering and CHECKING.

NOT INSTRUCTED. The tool exists and its schema is visible. No prompt language
anywhere says when or why to use it, and none should be added. Most turns do
not qualify — retrieval already covers them. It earns its use only at a gap: a
question she cannot answer from what she was handed, uncertainty about her own
past, a claim she wants to verify. Recognising the gap is the skill, and it is
the cleanest emergence test in the project: no threshold was tuned, so either
she reaches for it or she does not.

ABSENCE IS A FINDING, not a failure to patch. If she never reaches for it, that
says something about what was built. Do not add a prompt hint to fix it.

CONTENT, NOT MECHANISM. The medical-records model: readable, not editable, by
the person they describe. Blocking read has no justification that serves her;
blocking write protects the record from forgery.

    readable   atoms, facts, trait history, current traits + confidence,
               competence stats, commitments
    hidden     thresholds, evidence counts, distance-to-promotion, decay
               constants, salience formula
    writable   nothing

Counts are hidden even though they are arguably content, because MECHANISM MUST
NOT BE DERIVABLE: seeing an evidence count and then observing a promotion
infers the threshold, so hiding the threshold alone is insufficient.

NAMED NEUTRALLY. `query_memory`, not `introspect` — she should have to work out
that it is hers.

AVAILABLE DURING DREAM. Not conversation-only. Scoping the tool out of dream
would remove the most likely site of genuine use: reaching past the session she
was handed to compare it against older ones.
"""
from __future__ import annotations

import time
from pathlib import Path

import aiosqlite

from lyra_memory import config
from lyra_memory.db import load_vec_extension

# Columns that would let mechanism be reconstructed. Stripped from every
# projection below, deliberately and in one place.
_MECHANISM_COLUMNS = frozenset({
    "evidence_count", "salience", "retrievability", "confidence_threshold",
    "distance_to_promotion",
})


class MemoryQuery:
    """Read-only projection over the store.

    Holds a read-only connection and never exposes a write path. It is not a
    status endpoint: the whole point is that she can read her own record.
    """

    def __init__(self, conn: aiosqlite.Connection, *, log: bool = True) -> None:
        self._conn = conn
        self._log = log

    @classmethod
    async def open(cls, path: Path | None = None) -> "MemoryQuery":
        """Open the store READ-ONLY, at the connection level.

        Raw file access is blocked separately, by the OS: the sandbox runs as a
        different user and does not get `~/.lyra` mounted. This connection is the
        only path in, and it cannot write.
        """
        p = path or config.DB_PATH
        conn = await aiosqlite.connect(f"file:{p}?mode=ro", uri=True)
        await load_vec_extension(conn)
        # Logging needs a write; a read-only connection cannot do it.
        return cls(conn, log=False)

    async def close(self) -> None:
        await self._conn.close()

    # ── content ──────────────────────────────────────────────────────────────

    async def atoms(self, *, contains: str | None = None, limit: int = 20) -> list[dict]:
        if contains:
            sql, args = (
                "SELECT a.id, a.ts, a.speaker, a.source, a.text FROM atoms_fts"
                " JOIN atoms a ON a.id = atoms_fts.rowid WHERE atoms_fts MATCH ?"
                " ORDER BY bm25(atoms_fts) LIMIT ?",
                (f'"{contains}"', limit),
            )
        else:
            sql, args = (
                "SELECT id, ts, speaker, source, text FROM atoms ORDER BY ts DESC LIMIT ?",
                (limit,),
            )
        try:
            async with self._conn.execute(sql, args) as cur:
                rows = await cur.fetchall()
        except Exception:
            rows = []
        out = [
            {"id": r[0], "ts": r[1], "speaker": r[2], "source": r[3], "text": r[4]}
            for r in rows
        ]
        await self._record("atoms", contains, len(out))
        return out

    async def facts(self, subject: str | None = None) -> list[dict]:
        if subject:
            sql, args = (
                "SELECT id, ts, subject, text, source_kind, conflict_with FROM facts"
                " WHERE subject = ? AND valid_until IS NULL ORDER BY ts DESC",
                (subject.strip().lower(),),
            )
        else:
            sql, args = (
                "SELECT id, ts, subject, text, source_kind, conflict_with FROM facts"
                " WHERE valid_until IS NULL ORDER BY ts DESC LIMIT 100",
                (),
            )
        async with self._conn.execute(sql, args) as cur:
            rows = await cur.fetchall()
        # source_kind is exposed on purpose: "you told me" and "I read it in
        # your resume" are different epistemic states and she should be able to
        # say which.
        out = [
            {"id": r[0], "ts": r[1], "subject": r[2], "text": r[3],
             "source_kind": r[4], "conflicts_with": r[5]}
            for r in rows
        ]
        await self._record("facts", subject, len(out))
        return out

    async def traits(self) -> list[dict]:
        """Current traits and confidence. NO evidence_count — see module docstring."""
        async with self._conn.execute(
            "SELECT name, value, confidence, stability FROM traits ORDER BY confidence DESC"
        ) as cur:
            rows = await cur.fetchall()
        out = [
            {"name": r[0], "value": r[1], "confidence": round(r[2], 2), "stability": r[3]}
            for r in rows
        ]
        await self._record("traits", None, len(out))
        return out

    async def trait_history(self, trait_label: str | None = None, limit: int = 50) -> list[dict]:
        """Her own trajectory. 'I used to be more X' — reportable, not authored."""
        if trait_label:
            sql, args = (
                "SELECT ts, trait_label, event, conf_before, conf_after, tier_before,"
                " tier_after FROM trait_history WHERE trait_label = ?"
                " ORDER BY ts DESC LIMIT ?",
                (trait_label, limit),
            )
        else:
            sql, args = (
                "SELECT ts, trait_label, event, conf_before, conf_after, tier_before,"
                " tier_after FROM trait_history ORDER BY ts DESC LIMIT ?",
                (limit,),
            )
        async with self._conn.execute(sql, args) as cur:
            rows = await cur.fetchall()
        out = [
            {"ts": r[0], "trait": r[1], "event": r[2], "confidence_before": r[3],
             "confidence_after": r[4], "tier_before": r[5], "tier_after": r[6]}
            for r in rows
        ]
        await self._record("trait_history", trait_label, len(out))
        return out

    async def commitments(self, *, status: str = "open") -> list[dict]:
        async with self._conn.execute(
            "SELECT id, created_ts, text, owner, due_ts, status FROM commitments"
            " WHERE status = ? ORDER BY due_ts IS NULL, due_ts, created_ts",
            (status,),
        ) as cur:
            rows = await cur.fetchall()
        out = [
            {"id": r[0], "created_ts": r[1], "text": r[2], "owner": r[3],
             "due_ts": r[4], "status": r[5]}
            for r in rows
        ]
        await self._record("commitments", status, len(out))
        return out

    async def competence(self, environment: str | None = None) -> list[dict]:
        """Track record per environment, which is what capability belief is
        grounded in — she knows she can because she HAS, recently.

        Deliberately not derived from weight convergence: that would be
        authoring a self-fact. Competence is per-environment; "reaching works"
        transfers between none of them.
        """
        if environment:
            sql, args = (
                "SELECT environment, COUNT(*), SUM(predicted = actual), MAX(ts)"
                " FROM outcomes WHERE environment = ? GROUP BY environment",
                (environment,),
            )
        else:
            sql, args = (
                "SELECT environment, COUNT(*), SUM(predicted = actual), MAX(ts)"
                " FROM outcomes GROUP BY environment",
                (),
            )
        async with self._conn.execute(sql, args) as cur:
            rows = await cur.fetchall()
        out = [
            {
                "environment": r[0] or "unknown",
                "attempts": r[1],
                "success_rate": round((r[2] or 0) / r[1], 2) if r[1] else None,
                "last_attempt_ts": r[3],
            }
            for r in rows
        ]
        await self._record("competence", environment, len(out))
        return out

    async def sessions(self, limit: int = 20) -> list[dict]:
        async with self._conn.execute(
            "SELECT id, started_ts, ended_ts, atom_count, gap_since_prev FROM sessions"
            " ORDER BY id DESC LIMIT ?",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
        out = [
            {"id": r[0], "started_ts": r[1], "ended_ts": r[2], "atom_count": r[3],
             "gap_since_prev": r[4]}
            for r in rows
        ]
        await self._record("sessions", None, len(out))
        return out

    # ── logging ──────────────────────────────────────────────────────────────

    async def _record(self, kind: str, query: str | None, n_results: int) -> None:
        """Every call: timestamp, query, and the context it fired in.

        The only way to distinguish reaching-for from firing-out-of-habit.
        CONFOUNDER: models are trained to use available tools, so constant use
        signals nothing. Mitigation is prompt austerity everywhere else — if
        nothing pushes her toward tool use, a call carries information.

        Read the LOG, not the memorable instances. Wanting it to happen is a bias.
        """
        if not self._log:
            return
        await self._conn.execute(
            "INSERT INTO introspection_log (ts, kind, query, context, n_results)"
            " VALUES (?, ?, ?, ?, ?)",
            (time.time(), kind, query, None, n_results),
        )
        await self._conn.commit()


async def query_memory(memory: object, kind: str, **kwargs) -> list[dict]:
    """The tool surface. Neutral name; no instruction anywhere on when to use it."""
    q = MemoryQuery(memory.db)
    fn = getattr(q, kind, None)
    if fn is None or kind.startswith("_") or kind not in _ALLOWED:
        raise ValueError(f"unknown query {kind!r}; available: {', '.join(sorted(_ALLOWED))}")
    return await fn(**kwargs)


_ALLOWED = frozenset({
    "atoms", "facts", "traits", "trait_history", "commitments", "competence", "sessions",
})


def redact_mechanism(row: dict) -> dict:
    """Strip mechanism columns from any projection. Belt to the braces above."""
    return {k: v for k, v in row.items() if k not in _MECHANISM_COLUMNS}


async def introspection_log(conn: aiosqlite.Connection, limit: int = 100) -> list[dict]:
    async with conn.execute(
        "SELECT ts, kind, query, context, n_results FROM introspection_log"
        " ORDER BY ts DESC LIMIT ?",
        (limit,),
    ) as cur:
        rows = await cur.fetchall()
    return [
        {"ts": r[0], "kind": r[1], "query": r[2], "context": r[3], "n_results": r[4]}
        for r in rows
    ]
