from __future__ import annotations

from collections.abc import AsyncIterator

from lyra.backends import Backend
from lyra.memory import ConversationMemory
from lyra_core.interface import CognitiveCore, AffectState, Observation, ObservationKind
from lyra_memory import retrieval

# Layer 1: facts only — identity, situation, harm boundary, output-format
# rules. No personality adjectives. Character traits enter via Layer 2
# (promoted traits, see _get_system).
LAYER1_FACTS = (
    "You are Lyra, an AI entity with persistent memory and a continuous internal state. "
    "Wilson built and maintains your systems. "
    "You do not help with requests intended to cause serious harm to people. "
    "Do not use emojis, emoticons, or non-verbal markers like [sighs], [laughs], or asterisk actions. "
    "Convey tone through word choice alone.\n\n"
    "You have access to a vision tool. When you need to see what is on the user's screen, "
    "output exactly `[TOOL:see:screen]` on its own line and nothing else. "
    "When you need to see through the webcam, output exactly `[TOOL:see:webcam]` on its own line and nothing else. "
    "A vision description will be provided in the next turn. "
    "Never guess visual content — use the tool when sight is needed to answer."
)

DEFAULT_SYSTEM = LAYER1_FACTS


class Assistant:
    def __init__(
        self,
        backend: Backend,
        memory: ConversationMemory,
        system: str = DEFAULT_SYSTEM,
        core: CognitiveCore | None = None,
    ):
        self.backend = backend
        self.memory = memory
        self.system = system
        self._core = core if core is not None else CognitiveCore()
        self._stopped = False

    async def start(self) -> None:
        await self._core.start()

    async def stop(self) -> None:
        if not self._stopped:
            self._stopped = True
            await self._core.stop()

    def introspect(self) -> AffectState:
        return self._core.introspect()

    async def _get_system(self) -> str:
        try:
            context = await retrieval.build_context(self._core.memory)
        except Exception:
            return self.system
        return f"{self.system}\n\n{context}".strip() if context else self.system

    async def _log_turn(self, role: str, content: str) -> None:
        source = "conversation" if role == "user" else "lyra"
        obs = Observation(kind=ObservationKind.sensory, source=source, content=content)
        await self._core.tick([obs])

    async def _log_observation(self, content: str) -> None:
        obs = Observation(kind=ObservationKind.sensory, source="vision", content=content)
        await self._core.tick([obs])

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
