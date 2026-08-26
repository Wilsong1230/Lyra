# Lyra Memory Integration Design

**Date:** 2026-06-04  
**Scope:** Wire `MemorySystem` (lyra-memory) directly into `lyra_ai/lyra/assistant.py` and convert the CLI to async.

---

## Goal

Replace the `MemoryBridge` sync-shim pattern with a fully async `Assistant` that owns and drives `MemorySystem` directly. After every exchange the assistant logs both turns to the persistent memory layer and builds its system prompt from live persona context.

---

## Files Changed

| File | Change |
|---|---|
| `lyra_ai/lyra/assistant.py` | Owns `MemorySystem`; methods go async |
| `lyra_ai/lyra/cli.py` | `async def main()`; `MemoryBridge` removed; input via `asyncio.to_thread` |
| `lyra-memory/lyra_memory/config.py` | Already has correct `CORE_PROMPT` — no change |

---

## `assistant.py` Design

### Initialization

`Assistant.__init__` instantiates `MemorySystem()` internally and stores it as `self._lyra_memory`. No new constructor parameters are added.

```python
self._lyra_memory = MemorySystem()
```

### Lifecycle

```python
async def start(self) -> None:
    await self._lyra_memory.start()

async def stop(self) -> None:
    await self._lyra_memory.stop()
```

### System prompt

Every `chat*` method resolves the system prompt at call time:

```python
system = await retrieval.build_system_prompt(self._lyra_memory)
```

`retrieval.build_system_prompt` already falls back to `config.CORE_PROMPT` when no context exists, so `DEFAULT_SYSTEM` is no longer used as the live prompt (it remains in the module for reference).

### Turn logging

After the final response is assembled — at the same point `self.memory.add(session, "assistant", response)` fires — two additional awaits run:

```python
await self._lyra_memory.add_turn("user", message)
await self._lyra_memory.add_turn("lyra", response)
```

This applies to all four methods: `chat`, `stream_chat`, `chat_with_tools`, `stream_chat_with_tools`.

`ConversationMemory` (`self.memory`) is untouched — it continues to provide per-session turn history for the backend context window.

---

## `cli.py` Design

### Async main

```python
async def main() -> None:
    ...

if __name__ == "__main__":
    asyncio.run(main())
```

The `main()` entry point registered in `pyproject.toml` also calls `asyncio.run(main())`.

### Input

```python
user_text = await asyncio.to_thread(input, "you> ")
```

Replaces the `_read_input()` / `readchar` / push-to-talk system entirely. The following are removed:
- `_read_input()`, `_wait_for_enter()`
- `_PTT_KEY`, `_CTRL_C`, `_CTRL_D`, `_ESC`, `_BACKSPACE` constants
- `import readchar`
- The `if raw is None:` PTT recording block

### MemoryBridge removal

`MemoryBridge` is removed from the CLI. Its three roles are replaced:

| Old (`cli.py`) | New (inside `assistant.py`) |
|---|---|
| `bridge.start()` | `await assistant.start()` |
| `bridge.stop()` | `await assistant.stop()` (in `finally`) |
| `bridge.get_system_prompt()` → `assistant.system = ...` | `await build_system_prompt(...)` per call |
| `bridge.add_turn("user", ...)` | `await self._lyra_memory.add_turn("user", ...)` |
| `bridge.add_turn("assistant", ...)` | `await self._lyra_memory.add_turn("lyra", ...)` |

### Awaited assistant calls

```python
response = await assistant.chat_with_tools(user_input, session, vision_fn=_call_vision)
```

`stream_chat_with_tools` changes from `Iterator[str]` to `AsyncIterator[str]` (`async def` + `yield`). The streaming loop in `cli.py` becomes:

```python
async for chunk in assistant.stream_chat_with_tools(user_input, session, vision_fn=_call_vision):
    print(chunk, end="", flush=True)
    chunks.append(chunk)
```

---

## What Does Not Change

- `ConversationMemory` and its SQLite-backed session history
- Service URLs and HTTP helpers (`_post_json`, `set_state`, `speak_response`, `_call_vision`)
- All `/command` handling logic
- Streaming toggle, voice toggle, session management
- `lyra-memory/lyra_memory/config.py` (`CORE_PROMPT` already correct)
- All other services (lyra-embodiment, lyra-voice, lyra-listen, lyra-vision, lyra-mcp)

---

## Error Handling

`assistant.start()` propagates any `MemorySystem` startup failure to the caller. `cli.py` wraps it in a try/except, prints a warning, and exits gracefully if memory fails to start.

`build_system_prompt` falls back to `config.CORE_PROMPT` on any retrieval error (already implemented in `retrieval.py`).
