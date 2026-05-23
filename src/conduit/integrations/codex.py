"""Configure OpenAI Codex CLI to use the conduit endpoint.

Codex reads provider config from ``~/.codex/config.toml`` and selects a
profile at run time via ``--profile <name>``. We maintain a single
launcher-owned profile named ``conduit`` plus the matching
``[model_providers.conduit]`` block, leaving any other profiles or
top-level keys the user has set untouched. Mirrors the layout
``ollama launch codex`` writes in cmd/launch/codex.go, just under our own
profile name.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from conduit.integrations import _common

NAME = "codex"
DISPLAY_NAME = "Codex"

_INSTALL_HINT = (
    "Inside the aetherion container, codex is installed via npm "
    "(@openai/codex). On a host machine: `npm install -g @openai/codex`."
)

_CONFIG_PATH = Path.home() / ".codex" / "config.toml"
_PROFILE_NAME = "conduit"

# Sections that belong to conduit. Anything else in the file is user-owned
# and survives a rewrite verbatim.
_MANAGED_HEADERS = (f"[profiles.{_PROFILE_NAME}]", f"[model_providers.{_PROFILE_NAME}]")


def launch(endpoint: str, model: str, extra_args: list[str]) -> int:
    bin_path = _common.find_binary_or_fail("codex", _INSTALL_HINT)
    if bin_path is None:
        return 127
    _write_config(endpoint, model)
    # `--profile` activates the launcher-managed [profiles.conduit] block
    # without overwriting the user's default profile in ~/.codex/config.toml.
    os.execv(bin_path, [bin_path, "--profile", _PROFILE_NAME, *extra_args])


def _write_config(endpoint: str, model: str) -> None:
    base_url = endpoint.rstrip("/") + "/v1"

    existing = ""
    if _CONFIG_PATH.is_file():
        try:
            existing = _CONFIG_PATH.read_text()
        except OSError:
            existing = ""

    preserved = _strip_managed_sections(existing).rstrip()
    block = _render_block(model, base_url)

    if preserved:
        new_text = preserved + "\n\n" + block + "\n"
    else:
        new_text = block + "\n"

    _common.atomic_write_text(_CONFIG_PATH, new_text)


def _render_block(model: str, base_url: str) -> str:
    # `forced_login_method = "api"` keeps codex from prompting for ChatGPT
    # auth on a profile that's clearly pointed at a local OpenAI-compatible
    # endpoint. `wire_api = "responses"` matches what `ollama launch codex`
    # writes and is the format every OpenAI-compatible server supports.
    return (
        f"[profiles.{_PROFILE_NAME}]\n"
        f'model = "{model}"\n'
        f'openai_base_url = "{base_url}"\n'
        f'model_provider = "{_PROFILE_NAME}"\n'
        f'forced_login_method = "api"\n'
        f"\n"
        f"[model_providers.{_PROFILE_NAME}]\n"
        f'name = "Conduit"\n'
        f'base_url = "{base_url}"\n'
        f'wire_api = "responses"\n'
    )


def _strip_managed_sections(text: str) -> str:
    """Remove every block that begins with one of our managed headers up to
    (but not including) the next section header or EOF. Preserves all other
    sections and top-of-file keys verbatim.
    """
    if not text:
        return ""

    # Regex match: a managed header on its own line, followed by everything
    # up to (but not including) the next section header line, or EOF. The
    # negative lookahead ``(?![ \t])`` ensures we stop at column-0 ``[``
    # rather than e.g. an array-of-tables expression mid-value.
    pattern = re.compile(
        r"^\[(?:profiles|model_providers)\." + re.escape(_PROFILE_NAME) + r"\][^\n]*\n"
        r"(?:(?!^\[).*\n?)*",
        re.MULTILINE,
    )
    return pattern.sub("", text)
