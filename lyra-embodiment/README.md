# Lyra Embodiment

Lyra Embodiment is an open embodiment protocol for AI — a standalone MCP server that gives any AI model a visual presence through an atom avatar. Connect any MCP-compatible client and call `set_emotion` to drive the avatar's state in real time. The atom is the reference implementation: three orbiting rings, a pulsing nucleus, seven emotional states, all rendered in a browser with Three.js.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
chmod +x start.sh
./start.sh
```

Open [http://localhost:8000](http://localhost:8000) to see the avatar.

## Usage

**Manual:** Click any state button at the bottom of the browser window.

**MCP client:** Connect `mcp_server.py` as an MCP server (stdio transport). Call the tools below from any MCP-compatible client such as Claude Desktop.

## MCP Tools

### `set_emotion(state: str)`

Sets the avatar's emotional state. Valid states: `idle`, `thinking`, `speaking`, `curious`, `processing`, `confused`, `focused`.

**Example input:**
```json
{ "state": "thinking" }
```

**Example output:**
```
State set to 'thinking' (color: #9B59FF)
```

### `get_state()`

Returns the current emotional state and color.

**Example output:**
```
Current state: 'thinking' (color: #9B59FF)
```

## Roadmap

- **Game engine support** — Unity and Unreal plugins via WebSocket bridge
- **Spatial tools** — avatar position, orientation, and scale control
- **Animation control** — custom sequences, gesture triggers, expressions
- **Additional avatars** — extend beyond the atom reference implementation
