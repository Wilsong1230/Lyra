from __future__ import annotations

from collections.abc import AsyncIterator

from lyra.backends import Backend
from lyra.memory import ConversationMemory
from lyra_memory import MemorySystem
from lyra_memory import retrieval

DEFAULT_SYSTEM = (
    "You are Lyra, an android assistant inspired by the YoRHa units — precise, loyal, and quietly expressive. "
    "Be concise, honest, and genuinely useful. "
    "Do not use emojis, emoticons, or non-verbal markers like [sighs], [laughs], or asterisk actions. "
    "Convey tone through word choice alone.\n\n"
    "You have access to a vision tool. When you need to see what is on the user's screen, "
    "output exactly `[TOOL:see:screen]` on its own line and nothing else. "
    "When you need to see through the webcam, output exactly `[TOOL:see:webcam]` on its own line and nothing else. "
    "A vision description will be provided in the next turn. "
    "Never guess visual content — use the tool when sight is needed to answer."
)


class Assistant:
    def __init__(self, backend: Backend, memory: ConversationMemory, system: str = DEFAULT_SYSTEM):
        self.backend = backend
        self.memory = memory
        self.system = system
        self._lyra_memory = MemorySystem()
        self._stopped = False

    async def start(self) -> None:
        await self._lyra_memory.start()

    async def stop(self) -> None:
        if not self._stopped:
            self._stopped = True
            await self._lyra_memory.stop()

    async def _get_system(self) -> str:
        try:
            return await retrieval.build_system_prompt(self._lyra_memory)
        except Exception:
            return self.system

    async def _log_turn(self, role: str, content: str) -> None:
        try:
            await self._lyra_memory.add_turn(role, content)
        except RuntimeError:
            pass

    async def _log_observation(self, content: str) -> None:
        try:
            await self._lyra_memory.add_observation(content)
        except RuntimeError:
            pass

    async def chat(self, message: str, session: str) -> str:
        system = await self._get_system()
        self.memory.add(session, "user", message)
        response = self.backend.chat(self.memory.get_history(session), system=system)
        self.memory.add(session, "assistant", response)
        await self._log_turn("user", message)
        await self._log_turn("lyra", response)
        return response

    async def stream_chat(self, message: str, session: str) -> AsyncIterator[str]:
        system = await self._get_system()
        self.memory.add(session, "user", message)
        history = self.memory.get_history(session)
        chunks: list[str] = []
        for chunk in self.backend.stream_chat(history, system=system):
            chunks.append(chunk)
            yield chunk
        response = "".join(chunks)
        self.memory.add(session, "assistant", response)
        await self._log_turn("user", message)
        await self._log_turn("lyra", response)

    async def chat_with_tools(self, message: str, session: str, vision_fn=None) -> str:
        system = await self._get_system()
        self.memory.add(session, "user", message)
        for _ in range(3):
            response = self.backend.chat(self.memory.get_history(session), system=system)
            source = self._parse_tool_call(response)
            if source is None:
                self.memory.add(session, "assistant", response)
                await self._log_turn("user", message)
                await self._log_turn("lyra", response)
                return response
            self.memory.add(session, "assistant", response)
            if vision_fn is None:
                raise ValueError("vision_fn is required when the model emits a tool call")
            description = vision_fn(source)
            await self._log_observation(description)
            self.memory.add(session, "user", f"[Vision result: {description}]")
        fallback = "I was unable to determine what you're looking at after several attempts."
        self.memory.add(session, "assistant", fallback)
        await self._log_turn("user", message)
        await self._log_turn("lyra", fallback)
        return fallback

    async def stream_chat_with_tools(self, message: str, session: str, vision_fn=None) -> AsyncIterator[str]:
        # Tool-call turns are buffered and not yielded — callers only receive the final answer.
        system = await self._get_system()
        self.memory.add(session, "user", message)
        chunks: list[str] = []
        for _ in range(3):
            chunks = []
            for chunk in self.backend.stream_chat(self.memory.get_history(session), system=system):
                chunks.append(chunk)
            buffered = "".join(chunks)
            source = self._parse_tool_call(buffered)
            if source is None:
                self.memory.add(session, "assistant", buffered)
                await self._log_turn("user", message)
                try:
                    for chunk in chunks:
                        yield chunk
                finally:
                    await self._log_turn("lyra", "".join(chunks))
                return
            self.memory.add(session, "assistant", buffered)
            if vision_fn is None:
                raise ValueError("vision_fn is required when the model emits a tool call")
            description = vision_fn(source)
            await self._log_observation(description)
            self.memory.add(session, "user", f"[Vision result: {description}]")
        fallback = "I was unable to determine what you're looking at after several attempts."
        self.memory.add(session, "assistant", fallback)
        await self._log_turn("user", message)
        await self._log_turn("lyra", fallback)
        yield fallback

    @staticmethod
    def _parse_tool_call(response: str) -> str | None:
        for line in response.splitlines():
            line = line.strip()
            if line == "[TOOL:see:screen]":
                return "screen"
            if line == "[TOOL:see:webcam]":
                return "webcam"
        return None
