from __future__ import annotations

import asyncio
import datetime
import os
import httpx
from mcp.server.fastmcp import FastMCP
from lyra_memory.retrieval import search_atoms as _search_atoms_async
from lyra_memory.retrieval import get_facts as _get_facts_async



def _log(msg: str) -> None:
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    print(f"[{ts}] {msg}")

mcp = FastMCP("lyra")

EMBODIMENT_URL = os.getenv("EMBODIMENT_URL", "http://localhost:8000")
VOICE_URL = os.getenv("VOICE_URL", "http://localhost:8001")
LISTEN_URL = os.getenv("LISTEN_URL", "http://localhost:8002")
VISION_URL = os.getenv("VISION_URL", "http://localhost:8003")


@mcp.tool()
def set_emotion(state: str) -> str:
    """Set the emotional state of the Lyra avatar."""
    try:
        data = httpx.post(f"{EMBODIMENT_URL}/state", json={"state": state}, timeout=5).json()
        return f"State set to '{data['state']}' (color: {data['color']})"
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        return f"Error: could not reach lyra-embodiment — {e}"


@mcp.tool()
def get_state() -> str:
    """Get the current emotional state of the Lyra avatar."""
    try:
        data = httpx.get(f"{EMBODIMENT_URL}/state", timeout=5).json()
        return f"Current state: '{data['state']}' (color: {data['color']})"
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        return f"Error: could not reach lyra-embodiment — {e}"


@mcp.tool()
def speak(text: str, sync_emotion: bool = True) -> str:
    """Speak text aloud using Lyra's voice. Optionally syncs avatar emotion state."""
    try:
        resp = httpx.post(
            f"{VOICE_URL}/speak",
            json={"text": text, "sync_emotion": sync_emotion},
            timeout=30,
        )
        resp.raise_for_status()
        return f"Spoke: {text!r}"
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError) as e:
        return f"Error: could not reach lyra-voice — {e}"


@mcp.tool()
def transcribe(audio_path: str) -> str:
    """Transcribe a local audio file to text using Whisper."""
    try:
        with open(audio_path, "rb") as f:
            resp = httpx.post(f"{LISTEN_URL}/transcribe", files={"file": f}, timeout=60)
        resp.raise_for_status()
        return resp.json()["text"]
    except FileNotFoundError:
        return f"Error: file not found — {audio_path}"
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError) as e:
        return f"Error: could not reach lyra-listen — {e}"


@mcp.tool()
def list_voices() -> str:
    """List available Kokoro voices."""
    try:
        data = httpx.get(f"{VOICE_URL}/voices", timeout=5).json()
        return ", ".join(data["voices"])
    except (httpx.ConnectError, httpx.TimeoutException) as e:
        return f"Error: could not reach lyra-voice — {e}"


@mcp.tool()
def lyra_see(source: str = "screen", prompt: str = "Describe what you see.") -> str:
    """See the user's screen or webcam. source: 'screen' (default) or 'webcam'. Lyra picks based on context."""
    try:
        httpx.post(f"{EMBODIMENT_URL}/state", json={"state": "processing"}, timeout=5)
    except (httpx.ConnectError, httpx.TimeoutException):
        pass
    try:
        resp = httpx.post(
            f"{VISION_URL}/see",
            json={"source": source, "prompt": prompt},
            timeout=30,
        )
        resp.raise_for_status()
        description = resp.json()["description"]
        try:
            httpx.post(f"{EMBODIMENT_URL}/state", json={"state": "curious"}, timeout=5)
        except (httpx.ConnectError, httpx.TimeoutException):
            pass
        return description
    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError) as e:
        return f"Error: could not reach lyra-vision — {e}"


@mcp.tool()
def search_memory(query: str, limit: int = 5) -> list[dict]:
    """Search Lyra's long-term memory. Each result is one turn or observation, not a summary. Combines semantic similarity with keyword matching, so paraphrases and exact tokens (repo names, filenames) both find their target."""
    _log(f"search_memory query={query!r} limit={limit}")
    try:
        return asyncio.run(_search_atoms_async(query, limit=limit))
    except Exception as e:
        return [{"error": str(e)}]


@mcp.tool()
def get_facts(subject: str) -> list[dict]:
    """Look up what Lyra knows about a subject. Facts are exact, permanent and matched on the subject key rather than by similarity. Returns every current fact for that subject, newest first — a suspected contradiction leaves both rows live rather than picking one, so more than one answer is a legitimate result."""
    _log(f"get_facts subject={subject!r}")
    try:
        return asyncio.run(_get_facts_async(subject))
    except Exception as e:
        return [{"error": str(e)}]


if __name__ == "__main__":
    mcp.run()
