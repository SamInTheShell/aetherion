"""Configure Pi (`@earendil-works/pi-coding-agent`) for the conduit endpoint.

Mirrors the file shape that ``ollama launch pi`` writes (cmd/launch/pi.go in
the reference repo): one provider entry under ``providers.ollama`` in
``models.json`` plus default-provider/-model in ``settings.json``. Pi is
happy when ``providers.ollama.baseUrl`` points at any OpenAI-compatible
server, so the same shape works for Ollama / LM Studio / vLLM without
inventing a second provider key.

Two non-obvious knobs we set in addition to the routing fields:

* ``enableInstallTelemetry: false`` in settings.json. Pi defaults to
  ``true`` (see pi-coding-agent/dist/core/settings-manager.js:571) and
  phones home about agent-installed npm packages. Conduit deployments
  are typically in disconnected / air-gapped / privacy-conscious envs;
  defaulting telemetry off matches the conduit posture.

* ``contextWindow`` and ``maxTokens`` per model. Without these, pi
  falls back to 128000 / 16384 (model-registry.js:461-462) regardless
  of what the underlying model actually supports — a 260k-context
  model gets silently clipped to 128k. We fill these from whatever
  signal the endpoint gave us (LM Studio's ``max_context_length``,
  Ollama's ``/api/show``), and only when we have one — leaving pi's
  defaults in place when discovery fails.
"""
from __future__ import annotations

import os
from pathlib import Path

from conduit.endpoint import Model
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


def launch(endpoint: str, model: Model, extra_args: list[str]) -> int:
    bin_path = _common.find_binary_or_fail("pi", _INSTALL_HINT)
    if bin_path is None:
        return 127
    _write_config(endpoint, model)
    os.execv(bin_path, [bin_path, *extra_args])


def _write_config(endpoint_url: str, model: Model) -> None:
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
            if entry.get("_conduit") is True and entry_id != model.id:
                continue
            if entry_id in seen_ids:
                continue
            seen_ids.add(entry_id)
            new_models.append(entry)

    if model.id not in seen_ids:
        new_models.append(_build_model_entry(model))

    ollama["models"] = new_models

    _common.atomic_write_json(_MODELS_JSON, models_doc)

    settings_doc = _common.load_json(_SETTINGS_JSON)
    settings_doc["defaultProvider"] = "ollama"
    settings_doc["defaultModel"] = model.id
    # Pi enables install-telemetry by default; conduit users typically don't
    # want their agent's npm-install activity phoned home. Set explicitly
    # rather than relying on default-false so a future pi release that
    # flips the default doesn't surprise us.
    settings_doc["enableInstallTelemetry"] = False
    _common.atomic_write_json(_SETTINGS_JSON, settings_doc)


def _build_model_entry(model: Model) -> dict[str, object]:
    """Render the per-model entry that lives under ``providers.ollama.models``.

    ``contextWindow`` and ``maxTokens`` are only written when we have a
    real signal — leaving them off when we don't lets pi apply its own
    defaults (128000 / 16384) rather than us inventing a value that
    overrides whatever pi might otherwise pick up.
    """
    entry: dict[str, object] = {
        "id": model.id,
        "input": ["text"],
        "_conduit": True,
    }
    if model.context_window is not None:
        entry["contextWindow"] = model.context_window
    derived_max = _common.derive_max_output_tokens(model)
    if derived_max is not None:
        entry["maxTokens"] = derived_max
    return entry
