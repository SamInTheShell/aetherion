"""Configure Pi (`@earendil-works/pi-coding-agent`) for the conduit endpoint.

Mirrors the file shape that ``ollama launch pi`` writes (cmd/launch/pi.go in
the reference repo): one provider entry under ``providers.ollama`` in
``models.json`` plus default-provider/-model in ``settings.json``. Pi is
happy when ``providers.ollama.baseUrl`` points at any OpenAI-compatible
server, so the same shape works for Ollama / LM Studio / vLLM without
inventing a second provider key.
"""
from __future__ import annotations

import os
from pathlib import Path

from conduit.integrations import _common

NAME = "pi"
DISPLAY_NAME = "Pi"

_INSTALL_HINT = (
    "Inside the aetherion container, pi is installed at Dockerfile stage 3g "
    "via npm. On a host machine: `npm install -g @earendil-works/pi-coding-agent`."
)

_PI_HOME = Path.home() / ".pi" / "agent"
_MODELS_JSON = _PI_HOME / "models.json"
_SETTINGS_JSON = _PI_HOME / "settings.json"


def launch(endpoint: str, model: str, extra_args: list[str]) -> int:
    bin_path = _common.find_binary_or_fail("pi", _INSTALL_HINT)
    if bin_path is None:
        return 127
    _write_config(endpoint, model)
    os.execv(bin_path, [bin_path, *extra_args])


def _write_config(endpoint_url: str, model: str) -> None:
    _PI_HOME.mkdir(parents=True, exist_ok=True)

    models_doc = _common.load_json(_MODELS_JSON)
    providers = models_doc.setdefault("providers", {})
    if not isinstance(providers, dict):
        providers = {}
        models_doc["providers"] = providers

    ollama = providers.setdefault("ollama", {})
    if not isinstance(ollama, dict):
        ollama = {}
        providers["ollama"] = ollama

    # Always overwrite the connection knobs — the user just asked us to
    # point Pi at a specific endpoint, so any prior baseUrl is stale.
    ollama["baseUrl"] = endpoint_url.rstrip("/") + "/v1"
    ollama["api"] = "openai-completions"
    # Pi requires a non-empty apiKey even when the upstream server (ollama,
    # lmstudio) ignores it. "ollama" matches what `ollama launch` writes.
    ollama["apiKey"] = "ollama"

    existing = ollama.get("models")
    new_models: list[dict[str, object]] = []
    seen_ids: set[str] = set()
    if isinstance(existing, list):
        for entry in existing:
            if not isinstance(entry, dict):
                continue
            entry_id = entry.get("id")
            if not isinstance(entry_id, str):
                continue
            # Preserve user-managed entries (anything without our _conduit
            # marker) so hand-edits survive. Drop our own stale entries that
            # don't match the model we're about to select.
            if entry.get("_conduit") is True and entry_id != model:
                continue
            if entry_id in seen_ids:
                continue
            seen_ids.add(entry_id)
            new_models.append(entry)

    if model not in seen_ids:
        new_models.append({
            "id": model,
            "input": ["text"],
            "_conduit": True,
        })

    ollama["models"] = new_models

    _common.atomic_write_json(_MODELS_JSON, models_doc)

    settings_doc = _common.load_json(_SETTINGS_JSON)
    settings_doc["defaultProvider"] = "ollama"
    settings_doc["defaultModel"] = model
    _common.atomic_write_json(_SETTINGS_JSON, settings_doc)
