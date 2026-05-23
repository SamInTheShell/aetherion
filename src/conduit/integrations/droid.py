"""Configure Factory's Droid CLI to use the conduit endpoint.

Droid stores config at ``~/.factory/settings.json``. We add one
launcher-owned entry to its ``customModels`` array (marked by
``apiKey == "conduit"``) and set ``sessionDefaultSettings.model`` to that
entry's id, leaving any user-added rows untouched. Mirrors the shape
``ollama launch droid`` writes in cmd/launch/droid.go.

Note: droid is NOT installed in the stock aetherion container — Factory's
official installer is the host install path (https://docs.factory.ai/cli).
The integration writes its config either way; ``conduit launch droid``
will fail at the exec step inside the container until the binary is added
to the Dockerfile or installed by the user.
"""
from __future__ import annotations

import os
from pathlib import Path

from conduit.integrations import _common

NAME = "droid"
DISPLAY_NAME = "Droid"

_INSTALL_HINT = (
    "droid is not in the aetherion container by default. Install with "
    "`curl -fsSL https://app.factory.ai/cli | sh` on a host machine."
)

_CONFIG_PATH = Path.home() / ".factory" / "settings.json"

# Marker conduit stamps on every customModels entry it manages so a future
# launch (or a model switch) can evict our own stale rows without touching
# user-added providers.
_API_KEY_MARKER = "conduit"


def launch(endpoint: str, model: str, extra_args: list[str]) -> int:
    bin_path = _common.find_binary_or_fail("droid", _INSTALL_HINT)
    if bin_path is None:
        return 127
    _write_config(endpoint, model)
    os.execv(bin_path, [bin_path, *extra_args])


def _write_config(endpoint: str, model: str) -> None:
    base_url = endpoint.rstrip("/") + "/v1"
    cfg = _common.load_json(_CONFIG_PATH)

    # Filter out our own previous entries; keep everything else (other
    # providers the user added by hand or via a separate launcher).
    existing = cfg.get("customModels")
    preserved: list[dict[str, object]] = []
    if isinstance(existing, list):
        for entry in existing:
            if not isinstance(entry, dict):
                continue
            if entry.get("apiKey") == _API_KEY_MARKER:
                continue
            preserved.append(entry)

    # Index 0 means our conduit row sits at the top of droid's model picker.
    # Existing user rows get bumped down by one.
    model_id = f"conduit:{model}"
    conduit_entry: dict[str, object] = {
        "model": model,
        "displayName": model,
        "baseUrl": base_url,
        "apiKey": _API_KEY_MARKER,
        "provider": "generic-chat-completion-api",
        "maxOutputTokens": 64000,
        "supportsImages": False,
        "id": model_id,
        "index": 0,
    }
    cfg["customModels"] = [conduit_entry, *preserved]

    session_defaults = cfg.get("sessionDefaultSettings")
    if not isinstance(session_defaults, dict):
        session_defaults = {}
    session_defaults["model"] = model_id
    # reasoningEffort is required by droid; leave any user-set value, only
    # populate when missing so we don't smash a deliberate "high" choice.
    if session_defaults.get("reasoningEffort") not in ("high", "medium", "low", "none"):
        session_defaults["reasoningEffort"] = "none"
    cfg["sessionDefaultSettings"] = session_defaults

    _common.atomic_write_json(_CONFIG_PATH, cfg)
