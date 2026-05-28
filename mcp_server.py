import subprocess
import time
import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("lyra-embodiment")

FASTAPI_URL = "http://localhost:8000"
VALID_STATES = ["idle", "thinking", "speaking", "curious", "processing", "confused", "focused"]


def _ensure_server() -> bool:
    try:
        httpx.get(f"{FASTAPI_URL}/state", timeout=1)
        return True
    except (httpx.ConnectError, httpx.TimeoutException):
        subprocess.Popen(
            ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(2)
        try:
            httpx.get(f"{FASTAPI_URL}/state", timeout=1)
            return True
        except (httpx.ConnectError, httpx.TimeoutException):
            return False


@mcp.tool()
def set_emotion(state: str) -> str:
    """Set the emotional state of the Lyra avatar."""
    if state not in VALID_STATES:
        return f"Error: '{state}' is not a valid state. Valid states: {', '.join(VALID_STATES)}"
    if not _ensure_server():
        return "Error: Could not connect to Lyra server. Try running ./start.sh manually."
    data = httpx.post(f"{FASTAPI_URL}/state", json={"state": state}, timeout=5).json()
    return f"State set to '{data['state']}' (color: {data['color']})"


@mcp.tool()
def get_state() -> str:
    """Get the current emotional state of the Lyra avatar."""
    if not _ensure_server():
        return "Error: Could not connect to Lyra server. Try running ./start.sh manually."
    data = httpx.get(f"{FASTAPI_URL}/state", timeout=5).json()
    return f"Current state: '{data['state']}' (color: {data['color']})"


if __name__ == "__main__":
    mcp.run()
