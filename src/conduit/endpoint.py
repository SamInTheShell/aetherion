"""Talk to OpenAI-compatible model servers (Ollama, LM Studio, vLLM, ...).

Every supported backend exposes ``GET /v1/models`` with a JSON envelope of
``{"data": [{"id": "..."}, ...]}``, so a single call covers both the
``ollama`` and ``lmstudio`` aliases plus any custom URL the user sets.

We also pull per-model capability hints (context window, max output tokens)
out of the same response when the server extends the OpenAI schema:

  * LM Studio:  ``loaded_context_length`` / ``max_context_length``
  * vLLM:       ``max_model_len``

Ollama's ``/v1/models`` is bare — it returns only ids — so callers that
need its context window have to follow up with :func:`enrich_model`,
which POSTs to Ollama's native ``/api/show``.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

# Generous enough to ride out a cold-started LM Studio that's still loading
# its catalog, tight enough that a misconfigured endpoint surfaces quickly
# rather than feeling like the CLI is hung.
_TIMEOUT_SECONDS = 10.0


class EndpointError(RuntimeError):
    """Raised when we can't reach or interpret the configured endpoint."""


@dataclass(frozen=True)
class Model:
    """One model as the configured endpoint reports it.

    ``context_window`` and ``max_output_tokens`` are ``None`` when the
    endpoint's /v1/models payload doesn't surface them. For Ollama this is
    the common case — callers should pass the model through
    :func:`enrich_model` to fill the gaps before writing agent configs.
    """

    id: str
    context_window: int | None = None
    max_output_tokens: int | None = None


def list_models(endpoint: str) -> list[Model]:
    payload = _get_json(endpoint.rstrip("/") + "/v1/models")
    data = payload.get("data")
    if not isinstance(data, list):
        raise EndpointError(
            f"{endpoint} returned an unexpected payload (no 'data' array): "
            f"{payload!r}"
        )

    models: list[Model] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        model_id = entry.get("id")
        if not isinstance(model_id, str):
            continue
        models.append(Model(
            id=model_id,
            context_window=_extract_context_window(entry),
            max_output_tokens=_extract_max_output_tokens(entry),
        ))
    if not models:
        raise EndpointError(
            f"{endpoint} reported no models — pull one first (e.g. "
            "`ollama pull llama3`) or load one in LM Studio"
        )
    return models


def enrich_model(endpoint: str, model: Model) -> Model:
    """Best-effort fill of missing capability fields via endpoint-specific
    deep lookups. Safe to call on every model — when fields are already
    populated or the lookup isn't supported, the input ``Model`` is
    returned unchanged.

    Two probes, tried in order, both designed to 404 cleanly when we're
    not actually talking to the server that owns them:

    * **LM Studio** — ``/v1/models`` is strict OpenAI-compat (id /
      object / created / owned_by only), so the context-window fields
      live on its enhanced ``/api/v0/models/{id}`` REST endpoint
      instead. We probe that on every miss so an LM Studio user gets
      ``max_context_length`` even though the OpenAI list call returned
      nothing useful.

    * **Ollama** — same story: ``/v1/models`` is a bare list, the
      capability info lives behind ``POST /api/show``. ``model_info``
      carries a ``*.context_length`` key (prefix varies by architecture:
      ``llama.context_length``, ``qwen2.context_length``, etc.).

    Both probes ride the same ``_TIMEOUT_SECONDS`` so a wrong-server
    guess fails quickly without hanging the launch.
    """
    if model.context_window is not None and model.max_output_tokens is not None:
        return model

    ctx = model.context_window
    if ctx is None:
        ctx = _try_lmstudio_context(endpoint, model.id)
    if ctx is None:
        ctx = _try_ollama_context(endpoint, model.id)
    if ctx is None:
        return model

    return Model(
        id=model.id,
        context_window=ctx,
        max_output_tokens=model.max_output_tokens,
    )


def _try_lmstudio_context(endpoint: str, model_id: str) -> int | None:
    """GET ``/api/v0/models/{id}`` (LM Studio's enhanced REST API) and
    extract the context window. Returns ``None`` on any failure — the
    endpoint may not be LM Studio, the model id may not be loaded yet,
    or the LM Studio version may pre-date this endpoint. Caller falls
    back to the next probe.

    LM Studio also exposes the same fields on ``/api/v0/models`` (the
    list form), but going single-model means we transfer kilobytes,
    not the whole catalog, and we don't have to match ids client-side.
    """
    # URL-encoding the id matters: model ids commonly contain slashes
    # (``google/gemma-4-26b-a4b-qat``), and an unencoded slash turns
    # the lookup into ``/api/v0/models/google/gemma-…`` which LM Studio
    # routes as "list models in the 'google' namespace, item 'gemma-…'"
    # and 404s. ``quote(..., safe='')`` flattens the path segment.
    encoded = urllib.parse.quote(model_id, safe="")
    url = endpoint.rstrip("/") + "/api/v0/models/" + encoded
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT_SECONDS) as resp:
            payload = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return _extract_context_window(payload)


def _try_ollama_context(endpoint: str, model_id: str) -> int | None:
    """POST to ``/api/show`` (Ollama's native API) and pull the context
    length out of ``model_info``. Returns ``None`` on any failure — the
    endpoint may not be Ollama, the model may not be loaded, /api/show
    may have changed shape. Caller falls back to integration defaults.
    """
    url = endpoint.rstrip("/") + "/api/show"
    body = json.dumps({"model": model_id}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
            payload = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None

    info = payload.get("model_info")
    if isinstance(info, dict):
        for key, value in info.items():
            if key.endswith(".context_length") and isinstance(value, int) and value > 0:
                return value

    # Older Ollama dumps it under `parameters` as a text blob; the field
    # we want is `num_ctx <N>`. Best-effort parse so we don't refuse a
    # working signal just because the user is on an older daemon.
    params = payload.get("parameters")
    if isinstance(params, str):
        for line in params.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0] == "num_ctx" and parts[1].isdigit():
                return int(parts[1])

    return None


def _extract_context_window(entry: dict[str, object]) -> int | None:
    """Pull the model's context window out of an OpenAI-style /v1/models
    entry. Different servers surface this under different keys; prefer
    the most-conservative signal (what's actually loaded) when both
    "loaded" and "max" are present, so we never tell an agent the window
    is bigger than the server will currently serve.

    Priority order:
      1. ``loaded_context_length`` (LM Studio: what's in VRAM right now)
      2. ``max_context_length``    (LM Studio: model's full capability)
      3. ``max_model_len``         (vLLM)
      4. ``context_window``        (some OpenAI-compat shims)
    """
    for key in ("loaded_context_length", "max_context_length", "max_model_len", "context_window"):
        value = entry.get(key)
        if isinstance(value, int) and value > 0:
            return value
    return None


def _extract_max_output_tokens(entry: dict[str, object]) -> int | None:
    """Pull max output tokens from an OpenAI-style /v1/models entry. None
    of the common local servers surface this today — LM Studio, Ollama,
    and vLLM all rely on the caller asking for as much as they want and
    truncating to the context window. We probe a couple of plausible
    keys anyway so future server versions Just Work.
    """
    for key in ("max_output_tokens", "max_completion_tokens", "max_tokens"):
        value = entry.get(key)
        if isinstance(value, int) and value > 0:
            return value
    return None


def _get_json(url: str) -> dict[str, object]:
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

    if not isinstance(payload, dict):
        raise EndpointError(f"{url} did not return a JSON object: {payload!r}")
    return payload


def _format_connect_error(url: str, reason: object) -> str:
    return f"could not reach {url}: {reason}"
