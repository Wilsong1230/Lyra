from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from collections.abc import Iterator
from http import client as httpclient
from urllib import parse as urlparse


def _iter_sse_openai(resp) -> Iterator[str]:
    """Yield text chunks from an OpenAI-compatible SSE stream."""
    while True:
        line = resp.readline()
        if not line:
            break
        text = line.decode("utf-8", errors="replace").rstrip("\r\n")
        if not text.startswith("data: "):
            continue
        payload = text[6:]
        if payload.strip() == "[DONE]":
            break
        try:
            obj = json.loads(payload)
            chunk = obj["choices"][0]["delta"].get("content") or ""
            if chunk:
                yield chunk
        except (json.JSONDecodeError, KeyError, IndexError):
            continue


def _iter_sse_anthropic(resp) -> Iterator[str]:
    """Yield text chunks from an Anthropic SSE stream."""
    while True:
        line = resp.readline()
        if not line:
            break
        text = line.decode("utf-8", errors="replace").rstrip("\r\n")
        if not text.startswith("data: "):
            continue
        try:
            obj = json.loads(text[6:])
            if obj.get("type") == "content_block_delta":
                chunk = obj.get("delta", {}).get("text", "")
                if chunk:
                    yield chunk
        except (json.JSONDecodeError, KeyError):
            continue


class Backend(ABC):
    name: str
    default_model: str

    @abstractmethod
    def chat(self, messages: list[dict], model: str | None = None, system: str | None = None) -> str: ...

    def stream_chat(self, messages: list[dict], model: str | None = None, system: str | None = None) -> Iterator[str]:
        yield self.chat(messages, model=model, system=system)

    def list_models(self) -> list[str]:
        return [self.default_model]


class AnthropicBackend(Backend):
    name = "anthropic"
    default_model = "claude-sonnet-4-6"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY not set")

    def _headers(self) -> dict:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    def _build_payload(self, messages, model, system, stream=False) -> dict:
        payload: dict = {
            "model": model or self.default_model,
            "max_tokens": 4096,
            "messages": messages,
            "stream": stream,
        }
        if system:
            payload["system"] = system
        return payload

    def chat(self, messages: list[dict], model: str | None = None, system: str | None = None) -> str:
        conn = httpclient.HTTPSConnection("api.anthropic.com")
        conn.request("POST", "/v1/messages", body=json.dumps(self._build_payload(messages, model, system)), headers=self._headers())
        resp = conn.getresponse()
        data = json.loads(resp.read())
        if resp.status != 200:
            err = data.get("error", data)
            raise RuntimeError(f"anthropic error {resp.status}: {err.get('message', err) if isinstance(err, dict) else err}")
        return data["content"][0]["text"]

    def stream_chat(self, messages: list[dict], model: str | None = None, system: str | None = None) -> Iterator[str]:
        conn = httpclient.HTTPSConnection("api.anthropic.com")
        conn.request("POST", "/v1/messages", body=json.dumps(self._build_payload(messages, model, system, stream=True)), headers=self._headers())
        resp = conn.getresponse()
        if resp.status != 200:
            data = json.loads(resp.read())
            err = data.get("error", data)
            raise RuntimeError(f"anthropic error {resp.status}: {err.get('message', err) if isinstance(err, dict) else err}")
        yield from _iter_sse_anthropic(resp)

    def list_models(self) -> list[str]:
        return ["claude-opus-4-7", "claude-sonnet-4-6", "claude-haiku-4-5-20251001"]


class _OpenAICompatBackend(Backend):
    def __init__(self, base_url: str, api_key: str = ""):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

    def _conn(self):
        parsed = urlparse.urlparse(self._base_url)
        return (httpclient.HTTPSConnection if parsed.scheme == "https" else httpclient.HTTPConnection)(parsed.netloc)

    def _path(self, endpoint: str) -> str:
        return urlparse.urlparse(self._base_url).path + endpoint

    def _headers(self) -> dict:
        h = {"content-type": "application/json"}
        if self._api_key:
            h["Authorization"] = f"Bearer {self._api_key}"
        return h

    def _build_messages(self, messages, system) -> list[dict]:
        if system:
            return [{"role": "system", "content": system}, *messages]
        return messages

    def chat(self, messages: list[dict], model: str | None = None, system: str | None = None) -> str:
        payload = {"model": model or self.default_model, "messages": self._build_messages(messages, system)}
        conn = self._conn()
        conn.request("POST", self._path("/chat/completions"), body=json.dumps(payload), headers=self._headers())
        resp = conn.getresponse()
        raw = resp.read()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise RuntimeError(f"{self.name} error {resp.status}: {raw.decode('utf-8', errors='replace')[:300]}")
        if resp.status != 200:
            err = data.get("error", data)
            raise RuntimeError(f"{self.name} error {resp.status}: {err.get('message', err) if isinstance(err, dict) else err}")
        return data["choices"][0]["message"]["content"]

    def stream_chat(self, messages: list[dict], model: str | None = None, system: str | None = None) -> Iterator[str]:
        payload = {"model": model or self.default_model, "messages": self._build_messages(messages, system), "stream": True}
        conn = self._conn()
        conn.request("POST", self._path("/chat/completions"), body=json.dumps(payload), headers=self._headers())
        resp = conn.getresponse()
        if resp.status != 200:
            data = json.loads(resp.read())
            err = data.get("error", data)
            raise RuntimeError(f"{self.name} error {resp.status}: {err.get('message', err) if isinstance(err, dict) else err}")
        yield from _iter_sse_openai(resp)


class OpenRouterBackend(_OpenAICompatBackend):
    name = "openrouter"
    default_model = "openai/gpt-4o-mini"

    def __init__(self, api_key: str | None = None):
        key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        if not key:
            raise ValueError("OPENROUTER_API_KEY not set")
        super().__init__("https://openrouter.ai/api/v1", api_key=key)

    def list_models(self) -> list[str]:
        conn = httpclient.HTTPSConnection("openrouter.ai")
        conn.request("GET", "/api/v1/models", headers={"Authorization": f"Bearer {self._api_key}"})
        data = json.loads(conn.getresponse().read())
        return sorted(m["id"] for m in data.get("data", []))


class CerebrasBackend(_OpenAICompatBackend):
    name = "cerebras"
    default_model = "gpt-oss-120b"

    def __init__(self, api_key: str | None = None):
        key = api_key or os.environ.get("CEREBRAS_API_KEY", "")
        if not key:
            raise ValueError("CEREBRAS_API_KEY not set")
        super().__init__("https://api.cerebras.ai/v1", api_key=key)

    def list_models(self) -> list[str]:
        conn = httpclient.HTTPSConnection("api.cerebras.ai")
        conn.request("GET", "/v1/models", headers={"Authorization": f"Bearer {self._api_key}"})
        data = json.loads(conn.getresponse().read())
        return [m["id"] for m in data.get("data", [])]


class OllamaBackend(Backend):
    name = "ollama"
    default_model = "llama3.2"

    def __init__(self, base_url: str | None = None):
        self._base_url = (base_url or os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")).rstrip("/")

    def _conn(self):
        parsed = urlparse.urlparse(self._base_url)
        return httpclient.HTTPConnection(parsed.netloc)

    def _path(self, endpoint: str) -> str:
        return urlparse.urlparse(self._base_url + endpoint).path

    def chat(self, messages: list[dict], model: str | None = None, system: str | None = None) -> str:
        all_messages = ([{"role": "system", "content": system}] if system else []) + list(messages)
        conn = self._conn()
        conn.request("POST", self._path("/api/chat"),
                     body=json.dumps({"model": model or self.default_model, "messages": all_messages, "stream": False}),
                     headers={"content-type": "application/json"})
        resp = conn.getresponse()
        data = json.loads(resp.read())
        if resp.status != 200:
            raise RuntimeError(f"ollama error {resp.status}: {data}")
        return data["message"]["content"]

    def stream_chat(self, messages: list[dict], model: str | None = None, system: str | None = None) -> Iterator[str]:
        all_messages = ([{"role": "system", "content": system}] if system else []) + list(messages)
        conn = self._conn()
        conn.request("POST", self._path("/api/chat"),
                     body=json.dumps({"model": model or self.default_model, "messages": all_messages, "stream": True}),
                     headers={"content-type": "application/json"})
        resp = conn.getresponse()
        if resp.status != 200:
            raise RuntimeError(f"ollama error {resp.status}: {json.loads(resp.read())}")
        while True:
            line = resp.readline()
            if not line:
                break
            try:
                obj = json.loads(line)
                chunk = obj.get("message", {}).get("content", "")
                if chunk:
                    yield chunk
                if obj.get("done"):
                    break
            except json.JSONDecodeError:
                continue

    def list_models(self) -> list[str]:
        conn = self._conn()
        conn.request("GET", self._path("/api/tags"))
        data = json.loads(conn.getresponse().read())
        return [m["name"] for m in data.get("models", [])]


BACKENDS: dict[str, type[Backend]] = {
    "anthropic": AnthropicBackend,
    "openrouter": OpenRouterBackend,
    "cerebras": CerebrasBackend,
    "ollama": OllamaBackend,
}


def create_backend(name: str, **kwargs) -> Backend:
    cls = BACKENDS.get(name)
    if not cls:
        raise ValueError(f"Unknown backend: {name!r}. Available: {list(BACKENDS)}")
    return cls(**kwargs)


def available_backends() -> list[str]:
    found = []
    if os.environ.get("ANTHROPIC_API_KEY"):
        found.append("anthropic")
    if os.environ.get("OPENROUTER_API_KEY"):
        found.append("openrouter")
    if os.environ.get("CEREBRAS_API_KEY"):
        found.append("cerebras")
    try:
        conn = httpclient.HTTPConnection("127.0.0.1:11434", timeout=2)
        conn.request("GET", "/api/tags")
        conn.getresponse()
        found.append("ollama")
    except Exception:
        pass
    return found


def auto_select_backend() -> Backend:
    for name in available_backends():
        try:
            return create_backend(name)
        except Exception:
            continue
    raise RuntimeError(
        "No backend available. Set ANTHROPIC_API_KEY, CEREBRAS_API_KEY, "
        "OPENROUTER_API_KEY, or run Ollama locally."
    )
