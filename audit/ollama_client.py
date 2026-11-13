"""Minimal Ollama HTTP client (standard library only)."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request


class OllamaError(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, host: str = "http://localhost:11434", timeout: float = 300, retries: int = 2):
        self.host = host.rstrip("/")
        self.timeout = timeout
        self.retries = retries

    def _request(self, method: str, path: str, payload: dict | None = None, timeout: float | None = None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(self.host + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:500]
            raise OllamaError(f"HTTP {e.code} from {path}: {body}") from e
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            raise OllamaError(f"cannot reach Ollama at {self.host}: {e}") from e

    def version(self) -> str:
        return self._request("GET", "/api/version", timeout=10).get("version", "unknown")

    def list_models(self) -> dict[str, dict]:
        """name -> {digest, size, details}"""
        data = self._request("GET", "/api/tags", timeout=15)
        return {m["name"]: m for m in data.get("models", [])}

    def show(self, model: str) -> dict:
        info = self._request("POST", "/api/show", {"model": model}, timeout=30)
        # The modelfile/license/template fields are large; keep the useful parts.
        return {"details": info.get("details"), "capabilities": info.get("capabilities"),
                "modified_at": info.get("modified_at"), "parameters": info.get("parameters"),
                "model_info": _slim_model_info(info.get("model_info") or {})}

    def chat(self, model: str, messages: list[dict], options: dict, think=None,
             keep_alive: str | None = None, tools: list[dict] | None = None) -> dict:
        """Non-streaming /api/chat. Retries transport errors only."""
        payload: dict = {"model": model, "messages": messages, "stream": False, "options": options}
        if think is not None:
            payload["think"] = think
        if keep_alive:
            payload["keep_alive"] = keep_alive
        if tools:
            payload["tools"] = tools
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                return self._request("POST", "/api/chat", payload)
            except OllamaError as e:
                last = e
                # A 4xx (e.g. model does not support `think`) will not fix itself.
                if "HTTP 4" in str(e):
                    break
                time.sleep(2 * (attempt + 1))
        raise OllamaError(str(last))


def _slim_model_info(mi: dict) -> dict:
    keep = ("general.architecture", "general.parameter_count", "general.quantization_version",
            "general.basename", "general.size_label")
    return {k: mi[k] for k in keep if k in mi}
