"""Configure Hermes Agent (`hermes-agent`) to use the conduit endpoint.

Hermes reads ``~/.hermes/config.yaml``. We round-trip the file through
PyYAML so existing user-managed keys (toolsets they've customized, env
overrides, etc.) survive verbatim; only the launcher-owned slots get
rewritten. Mirrors the shape ``ollama launch hermes`` writes in
cmd/launch/hermes.go, just under a conduit-owned provider key.
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

from conduit.endpoint import Model
from conduit.integrations import _common

NAME = "hermes"
DISPLAY_NAME = "Hermes Agent"

_INSTALL_HINT = (
    "Inside the aetherion container, hermes is installed at Dockerfile stage 6 "
    "via `uv tool install hermes-agent`. On a host machine: "
    "`uv tool install hermes-agent`."
)

_CONFIG_PATH = Path.home() / ".hermes" / "config.yaml"

# Provider slot conduit owns inside the providers: map. Distinct from the
# `ollama` key `ollama launch` uses, so a config file passed through both
# launchers ends up with each one's entry side-by-side instead of one
# silently overwriting the other.
_PROVIDER_KEY = "conduit"
_PROVIDER_DISPLAY_NAME = "Conduit"
# Hermes wants a non-empty api_key even when the upstream server ignores
# it. The string itself isn't a secret — it just has to exist.
_PLACEHOLDER_KEY = "conduit"


def launch(endpoint: str, model: Model, extra_args: list[str]) -> int:
    bin_path = _common.find_binary_or_fail("hermes", _INSTALL_HINT)
    if bin_path is None:
        return 127
    _write_config(endpoint, model.id)
    os.execv(bin_path, [bin_path, *extra_args])


def _write_config(endpoint: str, model_id: str) -> None:
    base_url = endpoint.rstrip("/") + "/v1"

    cfg = _load_yaml(_CONFIG_PATH)

    # model:  ← the active provider/model selection.
    model_section = cfg.get("model")
    if not isinstance(model_section, dict):
        model_section = {}
    model_section["provider"] = _PROVIDER_KEY
    model_section["default"] = model_id
    model_section["base_url"] = base_url
    model_section["api_key"] = _PLACEHOLDER_KEY
    cfg["model"] = model_section

    # providers.<conduit>:  ← endpoint metadata used by hermes' /model picker.
    providers = cfg.get("providers")
    if not isinstance(providers, dict):
        providers = {}
    entry = providers.get(_PROVIDER_KEY)
    if not isinstance(entry, dict):
        entry = {}
    entry["name"] = _PROVIDER_DISPLAY_NAME
    entry["api"] = base_url
    entry["default_model"] = model_id
    # Single-model list since we only know about the picked model. Hermes is
    # happy with a one-element list and surfaces it as the only switchable
    # option for this provider — which matches the conduit UX (pick once,
    # configure, launch).
    entry["models"] = [model_id]
    providers[_PROVIDER_KEY] = entry
    cfg["providers"] = providers

    _atomic_write_yaml(_CONFIG_PATH, cfg)


def _load_yaml(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    try:
        loaded = yaml.safe_load(path.read_text())
    except (yaml.YAMLError, OSError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _atomic_write_yaml(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    # sort_keys=False so keys come out in the order PyYAML/Python put them,
    # which keeps the file diff-friendly after each conduit write.
    tmp.write_text(yaml.safe_dump(data, sort_keys=False, default_flow_style=False))
    os.replace(tmp, path)
