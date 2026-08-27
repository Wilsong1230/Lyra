"""lyra_memory.dreaming_loop — dream as a LAYER, not as the memory.

The dream used to BE the memory: one ~3,000-character essay per cycle, and the
turns it was built from were discarded. Now the turn is the memory and the
dream is a short, pointer-heavy layer over it — `dreams` plus `dream_atoms` for
provenance, which is what makes a dream re-derivable from a frozen `atoms`
table.

The pass runs when idle, on a slow/cheap model, and writes:

    dreams + dream_atoms          the reflection and what it was built from
    facts                         durable claims, pointing at their source atom
    commitments                   stated intentions, and closure of open ones
    candidates -> traits          via IdentityEngine, which records the change

CONTAINMENT. Two source classes never reach this pass:
  * `sandbox_read` atoms are EXCLUDED FROM DREAM INPUT ENTIRELY. A file on disk
    must not be able to write to her identity. Discovery can produce facts; it
    cannot produce traits.
  * document-sourced facts are dispositionally inert — they feed the facts
    table only, never the candidate pool, trait promotion, or persona.

PROMPT DISCIPLINE for motor reflection: descriptive, not causal. She can observe
outcomes, conditions, trajectory; she cannot observe weights or gradients. Any
lesson phrased as HOW she does it is confabulation. Ask what happened, never
why.
"""
from __future__ import annotations
import asyncio
import json
import os
import time
import aiosqlite
from datetime import datetime

from lyra_memory.config import (
    DREAM_MODEL, OPENROUTER_BASE, DREAM_TRIGGER_ITEMS, DREAM_POLL_SECONDS,
    DREAM_TARGET_CHARS, SANDBOX_READ_SOURCE, DB_PATH,
)
from lyra_memory.db import backup_before_cold_pass
from lyra_memory.embeddings import embed
from lyra_memory.facts import FactStore, may_shape_disposition

_REFLECT_PROMPT = (
    "You are Lyra. These are turns from one session of your own experience.\n"
    "Write a SHORT reflection — at most {max_chars} characters, a few sentences.\n"
    "Say what happened and what it meant to you. Do not summarise every turn;\n"
    "the turns are kept and you can look at them again. Do not use headings.\n\n"
    "{gap_note}Session:\n{body}"
)

_EXTRACT_PROMPT = (
    "Read this session and extract, as JSON only:\n"
    '  "facts": durable claims about the world worth remembering later. Each:\n'
    '     {{"subject": short normalized key, "text": the claim, "atom_id": which turn stated it}}\n'
    '  "commitments": statements that someone will do something later. Each:\n'
    '     {{"text": as stated, "owner": "lyra" or "wilson", "atom_id": which turn stated it}}\n'
    '  "closed": previously-open commitments this session shows completed. Each:\n'
    '     {{"commitment_id": id, "atom_id": which turn shows it done}}\n'
    '  "traits": behavioural patterns about Lyra. Each:\n'
    '     {{"trait_name": short key, "trait_value": the observation,\n'
    '      "category": behavioral|emotional|relational|cognitive}}\n'
    "Omit anything you are not confident about. A commitment needs an explicit\n"
    "statement of intent, not an inferred one, and a due date only if one was\n"
    "actually named. Return only JSON.\n\n"
    "{open_commitments}Session:\n{body}"
)


class DreamingLoop:
    def __init__(
        self,
        conn: aiosqlite.Connection,
        working_memory,
        candidate_pool,
        identity_engine,
        *,
        segmenter=None,
        commitments=None,
        entities=None,
        salience=None,
        db_path=None,
    ) -> None:
        self._conn = conn
        self._wm = working_memory
        self._pool = candidate_pool
        self._identity = identity_engine
        self._segmenter = segmenter
        self._commitments = commitments
        self._entities = entities
        self._salience = salience
        self._facts = FactStore(conn)
        self._db_path = db_path or DB_PATH
        self._task: asyncio.Task | None = None
        self._idle_seconds: int = 300
        self._poll_seconds: int = DREAM_POLL_SECONDS

    # ── loop control ─────────────────────────────────────────────────────────

    def start(self, idle_seconds: int = 300, poll_seconds: int = DREAM_POLL_SECONDS) -> None:
        if self._task and not self._task.done():
            return
        self._idle_seconds = idle_seconds
        self._poll_seconds = poll_seconds
        self._task = asyncio.create_task(self._loop())
        print(f"\r\033[K[{datetime.now().isoformat()}] [DreamingLoop] started (idle={idle_seconds}s poll={poll_seconds}s)")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        print(f"\r\033[K[{datetime.now().isoformat()}] [DreamingLoop] stopped")

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._poll_seconds)
            try:
                await self.maybe_dream()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # A cold pass is allowed to fail: nothing it writes is
                # load-bearing and the substrate is untouched. The HOT path is
                # what fails loud.
                print(f"\r\033[K[{datetime.now().isoformat()}] [DreamingLoop] dream error: {e}")

    async def maybe_dream(self) -> int | None:
        count = self._wm.count_since_last_dream()
        if count == 0:
            return None
        last_turn = self._wm.last_turn_ts()
        idle = (time.time() - last_turn) if last_turn else 0
        if count >= DREAM_TRIGGER_ITEMS or idle >= self._idle_seconds:
            return await self.dream()
        return None

    # ── the pass ─────────────────────────────────────────────────────────────

    async def dream(self) -> int | None:
        """Run the cold pass over the most recent session. Returns the dream id.

        Backs the store up first: this pass stamps columns across `atoms` and
        `facts`, and a bad one has no undo.
        """
        backup_before_cold_pass(self._db_path, "dream")

        session_id = None
        if self._segmenter is not None:
            await self._segmenter.run()
            latest = await self._segmenter.latest()
            session_id = latest["id"] if latest else None
        if self._entities is not None:
            await self._entities.run()

        atoms = await self._dream_input(session_id)
        if not atoms:
            self._wm.mark_dreamed()
            return None

        body = "\n".join(f"[{a['id']}|{a['speaker']}]: {a['text']}" for a in atoms)
        gap_note = await self._gap_note(session_id)

        text = await self._call_llm(
            _REFLECT_PROMPT.format(max_chars=DREAM_TARGET_CHARS, gap_note=gap_note, body=body)
        )
        text = (text or "").strip()[: DREAM_TARGET_CHARS * 2]
        if not text:
            self._wm.mark_dreamed()
            return None

        dream_id = await self._write_dream(text, session_id, [a["id"] for a in atoms])
        await self._extract(body, atoms, session_id, dream_id)

        if self._salience is not None:
            # Revisable: re-scored now that this session's outcomes exist.
            await self._salience.run()

        self._wm.mark_dreamed()
        print(f"\r\033[K[{datetime.now().isoformat()}] [DreamingLoop] dream #{dream_id} ({len(text)} chars, {len(atoms)} atoms)")
        return dream_id

    async def _dream_input(self, session_id: int | None) -> list[dict]:
        """Atoms eligible as dream material.

        `sandbox_read` is excluded ENTIRELY — not down-weighted, not summarised.
        It is the same channel as the injection risk: a document can assert
        anything, and this pass writes to her identity.
        """
        if session_id is None:
            async with self._conn.execute(
                "SELECT id, speaker, source, text FROM atoms WHERE source != ?"
                " ORDER BY id DESC LIMIT ?",
                (SANDBOX_READ_SOURCE, DREAM_TRIGGER_ITEMS * 3),
            ) as cur:
                rows = list(reversed(await cur.fetchall()))
        else:
            async with self._conn.execute(
                "SELECT id, speaker, source, text FROM atoms WHERE session_id = ?"
                " AND source != ? ORDER BY ts, id",
                (session_id, SANDBOX_READ_SOURCE),
            ) as cur:
                rows = await cur.fetchall()
        return [{"id": r[0], "speaker": r[1], "source": r[2], "text": r[3]} for r in rows]

    async def _gap_note(self, session_id: int | None) -> str:
        """A long absence is a legitimate thing to reflect on. This is the one
        place `gap_since_prev` is read — it is dream input, not injected
        context."""
        if session_id is None or self._segmenter is None:
            return ""
        session = await self._segmenter.get(session_id)
        gap = session.get("gap_since_prev") if session else None
        if not gap or gap < 86400:
            return ""
        return f"(This session began after {gap / 86400:.1f} days without contact.)\n\n"

    async def _write_dream(self, text: str, session_id: int | None, atom_ids: list[int]) -> int:
        cur = await self._conn.execute(
            "INSERT INTO dreams (ts, text, session_id) VALUES (?, ?, ?)",
            (time.time(), text, session_id),
        )
        dream_id = cur.lastrowid
        await self._conn.execute(
            "INSERT INTO vec_dreams(rowid, embedding) VALUES (?, ?)",
            (dream_id, await embed(text)),
        )
        # Provenance, from the atoms actually read — not a timestamp sweep.
        await self._conn.executemany(
            "INSERT OR IGNORE INTO dream_atoms (dream_id, atom_id) VALUES (?, ?)",
            [(dream_id, a) for a in atom_ids],
        )
        await self._conn.commit()
        return dream_id

    async def _extract(
        self, body: str, atoms: list[dict], session_id: int | None, dream_id: int
    ) -> None:
        """Facts, commitments, closure, and trait evidence. One call, four outputs."""
        open_note = ""
        if self._commitments is not None:
            open_rows = await self._commitments.open_commitments()
            if open_rows:
                open_note = (
                    "Currently open commitments:\n"
                    + "\n".join(f"  {c['id']}: [{c['owner']}] {c['text']}" for c in open_rows)
                    + "\n\n"
                )

        raw = await self._call_llm(
            _EXTRACT_PROMPT.format(open_commitments=open_note, body=body)
        )
        try:
            parsed = json.loads(_strip_fence(raw))
        except (json.JSONDecodeError, TypeError) as e:
            print(f"\r\033[K[{datetime.now().isoformat()}] [DreamingLoop] extraction parse error: {e}")
            return

        valid_ids = {a["id"] for a in atoms}
        await self._write_facts(parsed.get("facts") or [], valid_ids)
        await self._write_commitments(parsed.get("commitments") or [], valid_ids)
        await self._close_commitments(parsed.get("closed") or [], valid_ids)
        await self._write_trait_evidence(parsed.get("traits") or [], dream_id)

    async def _write_facts(self, rows: list, valid_ids: set[int]) -> None:
        for row in rows[:10]:
            try:
                subject, text = row["subject"], row["text"]
            except (KeyError, TypeError):
                continue
            atom_id = row.get("atom_id")
            atom_id = atom_id if atom_id in valid_ids else None
            existing = await self._facts.get(subject)
            fact_id = await self._facts.add(
                subject, text, source_kind="stated", source_atom_id=atom_id, confidence=0.8,
            )
            # Detect, do not resolve. Compare only WITHIN a subject — a set of
            # tens, not thousands — and on a suspected contradiction write
            # conflict_with and stop. Both rows stay live.
            conflict = await self._detect_conflict(text, existing)
            if conflict is not None:
                await self._facts.flag_conflict(fact_id, conflict)

    async def _detect_conflict(self, text: str, existing: list[dict]) -> int | None:
        """Ask, once, whether the new claim contradicts an existing one.

        Tuned CONSERVATIVE on purpose. Under-flagging is clutter: visible and
        recoverable. Over-flagging pushes toward erasing true things. And the
        common case is REFINEMENT, not contradiction — "building Lyra" ->
        "building Lyra's memory layer" is neither duplicate nor contradiction,
        and must not be flagged as one.
        """
        if not existing:
            return None
        listing = "\n".join(f"{f['id']}: {f['text']}" for f in existing[:20])
        answer = await self._call_llm(
            "Does the NEW claim CONTRADICT any OLD claim below — meaning both cannot "
            "be true of the same moment? A more specific version of an old claim is a "
            "REFINEMENT, not a contradiction, and is not an answer here. Reply with the "
            "id of the single contradicted claim, or the word NONE.\n\n"
            f"NEW: {text}\n\nOLD:\n{listing}"
        )
        token = (answer or "").strip().split()[0] if (answer or "").strip() else "NONE"
        if not token.isdigit():
            return None
        candidate = int(token)
        return candidate if any(f["id"] == candidate for f in existing) else None

    async def _write_commitments(self, rows: list, valid_ids: set[int]) -> None:
        if self._commitments is None:
            return
        for row in rows[:10]:
            try:
                text, owner = row["text"], row["owner"]
            except (KeyError, TypeError):
                continue
            atom_id = row.get("atom_id")
            if atom_id not in valid_ids or owner not in ("lyra", "wilson"):
                continue
            # due_ts only if explicit; there is no inferred deadline.
            await self._commitments.add(text, owner, atom_id, due_ts=row.get("due_ts"))

    async def _close_commitments(self, rows: list, valid_ids: set[int]) -> None:
        if self._commitments is None:
            return
        for row in rows[:10]:
            try:
                commitment_id, atom_id = int(row["commitment_id"]), row["atom_id"]
            except (KeyError, TypeError, ValueError):
                continue
            if atom_id in valid_ids:
                await self._commitments.close(commitment_id, atom_id)

    async def _write_trait_evidence(self, rows: list, dream_id: int) -> None:
        """Trait evidence. Documents never reach here — see module docstring."""
        for row in rows[:3]:
            try:
                if not may_shape_disposition("stated"):
                    continue
                await self._pool.add_observation(
                    row["trait_name"], row["trait_value"], row["category"]
                )
            except (KeyError, TypeError) as e:
                print(f"\r\033[K[{datetime.now().isoformat()}] [DreamingLoop] trait row skipped: {e}")
        try:
            await self._identity.consolidate(dream_id=dream_id)
        except Exception as e:
            print(f"\r\033[K[{datetime.now().isoformat()}] [DreamingLoop] consolidate error: {e}")

    # ── model ────────────────────────────────────────────────────────────────

    async def _call_llm(self, prompt: str) -> str:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._call_llm_sync, prompt)

    def _call_llm_sync(self, prompt: str) -> str:
        import http.client
        import urllib.parse
        key = os.environ.get("OPENROUTER_API_KEY", "")
        parsed = urllib.parse.urlparse(OPENROUTER_BASE)
        conn = http.client.HTTPSConnection(parsed.netloc)
        body = json.dumps({"model": DREAM_MODEL, "messages": [{"role": "user", "content": prompt}]})
        conn.request("POST", "/api/v1/chat/completions", body=body,
                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        resp = conn.getresponse()
        data = json.loads(resp.read())
        if "choices" not in data:
            error = data.get("error", data)
            raise RuntimeError(f"OpenRouter API error: {error}")
        return data["choices"][0]["message"]["content"]


def _strip_fence(text: str) -> str:
    """Models wrap JSON in ```json fences whatever the prompt says."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()
