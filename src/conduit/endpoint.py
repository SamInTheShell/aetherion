"""Talk to OpenAI-compatible model servers (Ollama, LM Studio, vLLM, ...).

Every supported backend exposes ``GET /v1/models`` with a JSON envelope of
``{"data": [{"id": "..."}, ...]}``, so a single call covers both the
``ollama`` and ``lmstudio`` aliases plus any custom URL the user sets.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

# Generous enough to ride out a cold-started LM Studio that's still loading
# its catalog, tight enough that a misconfigured endpoint surfaces quickly
# rather than feeling like the CLI is hung.
_TIMEOUT_SECONDS = 10.0


class EndpointError(RuntimeError):
    """Raised when we can't reach or interpret the configured endpoint."""


def list_models(endpoint: str) -> list[str]:
    url = endpoint.rstrip("/") + "/v1/models"
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT_SECONDS) as resp:
            body = resp.read()
    except urllib.error.URLError as e:
        reason = e.reason if hasattr(e, "reason") else e
        raise EndpointError(_format_connect_error(url, reason)) from e
    except (TimeoutError, OSError) as e:
        raise EndpointError(_format_connect_error(url, e)) from e

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as e:
        raise EndpointError(f"{url} did not return JSON: {e}") from e

    data = payload.get("data")
    if not isinstance(data, list):
        raise EndpointError(
            f"{url} returned an unexpected payload (no 'data' array): "
            f"{payload!r}"
        )

    names: list[str] = []
    for entry in data:
        if isinstance(entry, dict) and isinstance(entry.get("id"), str):
            names.append(entry["id"])
    if not names:
        raise EndpointError(
            f"{url} reported no models — pull one first (e.g. "
            "`ollama pull llama3`) or load one in LM Studio"
        )
    return names


def _format_connect_error(url: str, reason: object) -> str:
    return f"could not reach {url}: {reason}"
