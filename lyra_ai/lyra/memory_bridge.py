# lyra_ai/lyra/memory_bridge.py
from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path

from lyra_memory import config as mem_config
from lyra_memory import MemorySystem
from lyra_memory import retrieval

from lyra.assistant import DEFAULT_SYSTEM

logger = logging.getLogger(__name__)


class MemoryBridge:
    """Synchronous shim over the async MemorySystem for use in the blocking CLI.

    MemoryBridge is a CLI-only adapter. When transitioning to a continuous async
    service, use MemorySystem directly and drop this class — the public .memory
    and .loop attributes exist precisely to make that handoff easy.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path
        self._core_prompt = DEFAULT_SYSTEM
        self.memory: MemorySystem | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._started = False

    @property
    def is_started(self) -> bool:
        return self._started

    def start(self) -> None:
        """Start background event loop thread and initialize MemorySystem."""
        if self._started:
            return
        mem_config.CORE_PROMPT = self._core_prompt
        self._ready.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="lyra-memory")
        self._thread.start()
        if not self._ready.wait(timeout=10):
            logger.warning("MemoryBridge: timed out waiting for MemorySystem to start — memory disabled")
            return
        if self.memory is None:
            logger.warning("MemoryBridge: MemorySystem failed to initialize — memory disabled")
            return
        self._started = True

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self.loop = loop
        try:
            loop.run_until_complete(self._init_memory())
            self._ready.set()
            loop.run_forever()
        except Exception as exc:
            logger.warning("MemoryBridge: failed to start MemorySystem: %s", exc)
            self._ready.set()  # unblock start() even on failure
        finally:
            pending = asyncio.all_tasks(loop)
            if pending:
                for task in pending:
                    task.cancel()
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()

    async def _init_memory(self) -> None:
        self.memory = MemorySystem(db_path=self._db_path)
        await self.memory.start()

    def stop(self) -> None:
        """Gracefully stop the dreaming loop and close the database."""
        if not self._started or self.loop is None or self.memory is None:
            return
        future = asyncio.run_coroutine_threadsafe(self.memory.stop(), self.loop)
        try:
            future.result(timeout=3)
        except Exception as exc:
            logger.warning("MemoryBridge: error during stop: %s", exc)
        self.loop.call_soon_threadsafe(self.loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=3)
        self._started = False
        self.memory = None
        self.loop = None

    def add_turn(self, role: str, content: str) -> None:
        """Observe a conversation turn. Fire-and-forget; never blocks or raises."""
        if not self._started or self.loop is None or self.memory is None:
            return
        asyncio.run_coroutine_threadsafe(
            self.memory.add_turn(role, content), self.loop
        )

    def get_system_prompt(self) -> str:
        """Return the current system prompt with live persona context.

        Falls back to DEFAULT_SYSTEM on timeout or if memory is not started.
        """
        if not self._started or self.loop is None or self.memory is None:
            return DEFAULT_SYSTEM
        future = asyncio.run_coroutine_threadsafe(
            retrieval.build_system_prompt(self.memory), self.loop
        )
        try:
            return future.result(timeout=1)
        except Exception as exc:
            logger.debug("MemoryBridge: get_system_prompt fallback (%s)", exc)
            return DEFAULT_SYSTEM
