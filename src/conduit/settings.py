"""Persistent settings for the conduit CLI.

Settings live at ``~/.conduit/config.json``. The directory is mirrored to
``~/.aetherion/data/.conduit`` on the host by the aetherion launcher (see
``AGENT_PATHS`` in ``src/aetherion/cli.py``) so endpoint choice and last
model survive between container sessions.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Ports the aliases map to. The hostname half is resolved at use time by
# `_alias_host()` so the same conduit binary works whether it's running
# inside the aetherion container (→ host.docker.internal, mapped by the
# launcher's --add-host to the host's gateway IP) or directly on the host
# (→ 127.0.0.1, where LM Studio / Ollama actually bind by default).
_ALIAS_PORTS: dict[str, int] = {
    "ollama": 11434,
    "lmstudio": 1234,
}


def _in_container() -> bool:
    # Both runtimes drop a sentinel file at known paths:
    # `/run/.containerenv` (podman) and `/.dockerenv` (docker). Detecting
    # via these is cheaper and more reliable than parsing /proc/1/cgroup.
    return Path("/run/.containerenv").exists() or Path("/.dockerenv").exists()


def _alias_host() -> str:
    return "host.docker.internal" if _in_container() else "127.0.0.1"


def _aliases() -> dict[str, str]:
    host = _alias_host()
    return {name: f"http://{host}:{port}" for name, port in _ALIAS_PORTS.items()}


# Exposed for help-text rendering and tests. Reads at call time so a host /
# container difference is reflected immediately rather than locked in at
# import time.
def alias_names() -> list[str]:
    return sorted(_ALIAS_PORTS)


@dataclass
class Settings:
    endpoint: str | None = None
    # Free-form label so we can echo back "ollama" / "lmstudio" rather than
    # the resolved URL when the user set an alias. Custom URLs leave this None.
    endpoint_alias: str | None = None
    # Most-recently picked model per integration name, e.g. {"pi": "llama3:8b"}.
    # Surfaces at the top of the launch picker on subsequent runs.
    last_models: dict[str, str] = field(default_factory=dict)


def settings_dir() -> Path:
    return Path.home() / ".conduit"


def settings_path() -> Path:
    return settings_dir() / "config.json"


def load() -> Settings:
    path = settings_path()
    if not path.is_file():
        return Settings()
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return Settings()
    alias = raw.get("endpoint_alias")
    endpoint = raw.get("endpoint")
    # When the stored value came from an alias, recompute the URL from the
    # current environment instead of trusting what was on disk. The same
    # config file gets mounted into the container by aetherion AND read
    # back on the host, so the resolved URL has to swap between
    # 127.0.0.1 and host.docker.internal depending on where load() runs.
    if isinstance(alias, str):
        aliases = _aliases()
        if alias in aliases:
            endpoint = aliases[alias]
    return Settings(
        endpoint=endpoint,
        endpoint_alias=alias if isinstance(alias, str) else None,
        last_models=dict(raw.get("last_models") or {}),
    )


def save(settings: Settings) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write to a sibling temp file then atomic-rename so a crash mid-write
    # never leaves a half-written config that the next load would silently
    # reset to defaults.
    fd, tmp_name = tempfile.mkstemp(prefix=".config.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(asdict(settings), f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def resolve_endpoint(value: str) -> tuple[str, str | None]:
    """Turn a user-supplied endpoint spec into (url, alias).

    `alias` is set for the known shorthand names so we can render the
    chosen endpoint back to the user the same way they typed it.
    Persisting only the alias label (not the resolved URL) lets the URL
    re-resolve at load time, so a config saved on the host still works
    when it gets mounted back into the container — and vice versa.
    """
    aliases = _aliases()
    if value in aliases:
        return aliases[value], value
    # Reject obviously-malformed input — anything that doesn't look like a
    # URL is almost certainly a typo of an alias name.
    if not value.startswith(("http://", "https://")):
        raise ValueError(
            f"endpoint must be one of {', '.join(alias_names())} or an "
            f"http(s):// URL; got {value!r}"
        )
    return value.rstrip("/"), None
