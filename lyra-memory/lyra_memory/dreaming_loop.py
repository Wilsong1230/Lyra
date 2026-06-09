from __future__ import annotations
import asyncio
import json
import os
import time
import aiosqlite
from datetime import datetime
from lyra_memory.config import DREAM_MODEL, OPENROUTER_BASE, DREAM_TRIGGER_ITEMS, DREAM_POLL_SECONDS
from lyra_memory.working_memory import WorkingMemory
from lyra_memory.candidate_pool import CandidatePool
from lyra_memory.identity_engine import IdentityEngine
from lyra_memory.embeddings import embed

_OBS_PROMPT = (
    "Based on this reflection, identify behavioral patterns about Lyra.\n"
    "Return a JSON list of objects, each with:\n"
    '  "trait_name"  — short stable key, e.g. "communication_style"\n'
    '  "trait_value" — the specific observation, e.g. "engineering-focused, concise"\n'
    '  "category"    — one of: behavioral, emotional, relational, cognitive\n'
    "Return only JSON, no other text.\n\nReflection:\n"
)


class DreamingLoop:
    def __init__(self, conn: aiosqlite.Connection, working_memory: WorkingMemory, candidate_pool: CandidatePool, identity_engine: IdentityEngine) -> None:
        self._conn = conn
        self._wm = working_memory
        self._pool = candidate_pool
        self._identity = identity_engine
        self._task: asyncio.Task | None = None
        self._idle_seconds: int = 300
        self._poll_seconds: int = DREAM_POLL_SECONDS

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
                await self._maybe_dream()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"\r\033[K[{datetime.now().isoformat()}] [DreamingLoop] dream error: {e}")

    async def _maybe_dream(self) -> None:
        count = self._wm.count_since_last_dream()
        if count == 0:
            return
        last_turn = self._wm.last_turn_ts()
        idle = (time.time() - last_turn) if last_turn else 0
        if count >= DREAM_TRIGGER_ITEMS or idle >= self._idle_seconds:
            await self._dream()

    async def _dream(self) -> None:
        items = self._wm.get_undreamed()
        if not items:
            return
        snapshot = json.dumps([i.model_dump() for i in items])
        prompt = (
            "You are Lyra, a continuous AI entity. Reflect on these recent experiences in your own words:\n\n"
            + "\n".join(f"[{i.role or i.type}]: {i.content}" for i in items)
        )
        content = await self._call_llm(prompt)
        ts = time.time()
        salience = max(i.score for i in items)
        cur = await self._conn.execute(
            "INSERT INTO episodes (content, ts, source_items_json, salience) VALUES (?, ?, ?, ?)",
            (content, ts, snapshot, salience),
        )
        episode_rowid = cur.lastrowid

        vec_bytes = await embed(content)
        await self._conn.execute(
            "INSERT INTO vec_episodes(rowid, embedding) VALUES (?, ?)",
            (episode_rowid, vec_bytes),
        )
        await self._conn.commit()
        print(f"\r\033[K[{datetime.now().isoformat()}] [DreamingLoop] episode written ({len(content)} chars)")

        obs_json = await self._call_llm(_OBS_PROMPT + content)
        try:
            observations = json.loads(obs_json)
            for obs in observations[:3]:
                await self._pool.add_observation(obs["trait_name"], obs["trait_value"], obs["category"])
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            print(f"\r\033[K[{datetime.now().isoformat()}] [DreamingLoop] structured obs parse error: {e}")

        try:
            await self._identity.consolidate()
        except Exception as e:
            print(f"\r\033[K[{datetime.now().isoformat()}] [DreamingLoop] consolidate error: {e}")

        self._wm.mark_dreamed()

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
