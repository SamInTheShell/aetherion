"""Configure OpenClaw to use the conduit endpoint.

OpenClaw stores provider config in ``~/.openclaw/openclaw.json``. We
register a single launcher-owned ``ollama`` provider entry under
``models.providers`` pointing at the conduit endpoint, then set the
primary agent model. Other providers and agent settings are left as-is.
Mirrors the layout ``ollama launch openclaw`` writes in
cmd/launch/openclaw.go (the Edit function).
"""
from __future__ import annotations

import os
from pathlib import Path

from conduit.endpoint import Model
from conduit.integrations import _common

NAME = "openclaw"
DISPLAY_NAME = "OpenClaw"

_INSTALL_HINT = (
    "Inside the aetherion container, openclaw is installed via npm. On a "
    "host machine: `npm install -g openclaw`."
)

_CONFIG_PATH = Path.home() / ".openclaw" / "openclaw.json"


def launch(endpoint: str, model: Model, extra_args: list[str]) -> int:
    bin_path = _common.find_binary_or_fail("openclaw", _INSTALL_HINT)
    if bin_path is None:
        return 127
    _write_config(endpoint, model.id)
    os.execv(bin_path, [bin_path, *extra_args])


def _write_config(endpoint: str, model: str) -> None:
    config = _common.load_json(_CONFIG_PATH)

    models_section = config.setdefault("models", {})
    if not isinstance(models_section, dict):
        models_section = {}
        config["models"] = models_section

    providers = models_section.setdefault("providers", {})
    if not isinstance(providers, dict):
        providers = {}
        models_section["providers"] = providers

    ollama = providers.setdefault("ollama", {})
    if not isinstance(ollama, dict):
        ollama = {}
        providers["ollama"] = ollama

    # baseUrl is the raw endpoint (NOT /v1) — openclaw's `ollama` provider
    # speaks ollama's native API on /, with /v1 used internally as needed.
    # apiKey just has to be non-empty; the upstream server ignores it.
    ollama["baseUrl"] = endpoint.rstrip("/")
    ollama["apiKey"] = "conduit"
    ollama["api"] = "ollama"

    # Replace the conduit-managed model entries (anything we previously
    # added) but keep user-managed entries. Stale conduit entries (a
    # different model from a prior launch) get evicted.
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
            if entry.get("_conduit") is True and entry_id != model:
                continue
            if entry_id in seen_ids:
                continue
            seen_ids.add(entry_id)
            new_models.append(entry)
    if model not in seen_ids:
        new_models.append({"id": model, "_conduit": True})
    ollama["models"] = new_models

    # agents.defaults.model.primary tells openclaw which model to use when
    # no per-agent override is set. We preserve other agents.* keys.
    agents = config.setdefault("agents", {})
    if not isinstance(agents, dict):
        agents = {}
        config["agents"] = agents
    defaults = agents.setdefault("defaults", {})
    if not isinstance(defaults, dict):
        defaults = {}
        agents["defaults"] = defaults
    model_section = defaults.setdefault("model", {})
    if not isinstance(model_section, dict):
        model_section = {}
        defaults["model"] = model_section
    model_section["primary"] = model

    _common.atomic_write_json(_CONFIG_PATH, config)
