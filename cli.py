from __future__ import annotations

import os

import httpx
import typer

app = typer.Typer(help="Lyra voice CLI — control lyra-voice from the terminal.")
BASE_URL = os.getenv("VOICE_URL", "http://localhost:8001")


@app.command()
def speak(
    text: str = typer.Argument(..., help="Text for Lyra to speak."),
    sync_emotion: bool = typer.Option(True, "--sync-emotion/--no-sync-emotion", help="Sync avatar emotion state."),
):
    """Speak text aloud using Lyra's voice."""
    try:
        resp = httpx.post(f"{BASE_URL}/speak", json={"text": text, "sync_emotion": sync_emotion}, timeout=30)
        resp.raise_for_status()
        typer.echo(f"Spoke: {text!r}")
    except httpx.ConnectError:
        typer.echo("Error: lyra-voice is not running. Start it with ./start.sh", err=True)
        raise typer.Exit(1)


@app.command()
def transcribe(
    path: str = typer.Argument(..., help="Path to audio file to transcribe."),
):
    """Transcribe an audio file to text."""
    try:
        with open(path, "rb") as f:
            resp = httpx.post(f"{BASE_URL}/transcribe", files={"file": f}, timeout=60)
        resp.raise_for_status()
        typer.echo(resp.json()["text"])
    except FileNotFoundError:
        typer.echo(f"Error: file not found — {path}", err=True)
        raise typer.Exit(1)
    except httpx.ConnectError:
        typer.echo("Error: lyra-voice is not running. Start it with ./start.sh", err=True)
        raise typer.Exit(1)


@app.command()
def voices():
    """List available Kokoro voices."""
    try:
        resp = httpx.get(f"{BASE_URL}/voices", timeout=5)
        resp.raise_for_status()
        for v in resp.json()["voices"]:
            typer.echo(v)
    except httpx.ConnectError:
        typer.echo("Error: lyra-voice is not running.", err=True)
        raise typer.Exit(1)


@app.command()
def health():
    """Check lyra-voice service health."""
    try:
        resp = httpx.get(f"{BASE_URL}/health", timeout=5)
        resp.raise_for_status()
        data = resp.json()
        typer.echo(f"status:    {data['status']}")
        typer.echo(f"tts_voice: {data['tts_voice']}")
        typer.echo(f"stt_model: {data['stt_model']}")
    except httpx.ConnectError:
        typer.echo("Error: lyra-voice is not running.", err=True)
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
