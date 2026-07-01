"""Shared helpers for ``conduit launch`` integrations.

Every integration module exposes ``NAME`` (the CLI keyword), an optional
``DISPLAY_NAME``, and ``launch(endpoint, model, extra_args) -> int``,
where ``model`` is an :class:`conduit.endpoint.Model` carrying the id
plus best-effort capability hints (context window, max output tokens).
The helpers here are the bits each integration tends to need: atomic
JSON writes, binary discovery with a uniform missing-binary message,
``execv`` so the agent binary inherits stdio + signals cleanly, and a
shared derivation for "what max output tokens should we tell the agent
to allow" when the endpoint doesn't surface a signal.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import NoReturn

from conduit.endpoint import Model


def load_json(path: Path) -> dict[str, object]:
    """Best-effort JSON read. Missing file or garbled JSON → empty dict so
    callers can always treat the result as a writable mapping."""
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def atomic_write_json(path: Path, data: dict[str, object]) -> None:
    """Write JSON via tmp-file + rename so a crash mid-write can never leave
    a half-baked config that the agent would then refuse to parse."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def find_binary_or_fail(name: str, install_hint: str) -> str | None:
    """Return absolute path to ``name`` on PATH, or print a focused error and
    return None. Callers should propagate ``127`` (POSIX "command not
    found") when this returns None.
    """
    bin_path = shutil.which(name)
    if bin_path is None:
        sys.stderr.write(
            f"conduit: `{name}` binary not found on PATH. {install_hint}\n"
        )
        return None
    return bin_path


def execv_with_env(bin_path: str, args: list[str], env_overrides: dict[str, str]) -> NoReturn:
    """Replace the current process with the agent binary, layering env vars
    on top of the inherited environment. We use execvpe rather than
    subprocess so signals (Ctrl-C, SIGTERM) and stdio flow straight through
    with no Python wrapper in the middle to interpret them.
    """
    env = {**os.environ, **env_overrides}
    os.execvpe(bin_path, [bin_path, *args], env)


def derive_max_output_tokens(model: Model) -> int | None:
    """Best-effort max output tokens for a model. Endpoint signal wins
    when present; otherwise we scale by the context window so a 260k
    model gets meaningfully more headroom than an 8k one. Returns
    ``None`` when we have no information at all and the integration
    should fall back to its own default.

    The ``context_window // 4`` heuristic balances two concerns: leave
    enough room for the agent's prompt + tool-call history (which on
    long-running sessions easily eats half the window), and cap at 32k
    so we don't tell a model server to allocate response-side buffers
    bigger than any realistic single completion will need.
    """
    if model.max_output_tokens is not None:
        return model.max_output_tokens
    if model.context_window is not None:
        return min(model.context_window // 4, 32_768)
    return None
