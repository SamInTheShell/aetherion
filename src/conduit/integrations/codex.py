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

import json
import re
import signal
import subprocess
from pathlib import Path

from conduit.endpoint import Model
from conduit.integrations import _common
from conduit.shim import ShimServer

NAME = "codex"
DISPLAY_NAME = "Codex"

_INSTALL_HINT = (
    "Inside the aetherion container, codex is installed via npm "
    "(@openai/codex). On a host machine: `npm install -g @openai/codex`."
)

_CODEX_DIR = Path.home() / ".codex"
_CONFIG_PATH = _CODEX_DIR / "config.toml"
# Codex (≥0.81) loads model capabilities from a JSON catalog when the
# profile sets `model_catalog_json = "<path>"`. Without one, it falls back
# to a built-in capability set that advertises experimental tools (e.g.
# `local_shell`, `web_search`) whose `type` strings aren't `"function"`.
# OpenAI-compatible servers like LM Studio reject any tool not typed
# `"function"`, so we ship a catalog that explicitly lists no experimental
# tools — matching what `ollama launch codex` writes.
_CATALOG_PATH = _CODEX_DIR / "model.json"
# Used only when the endpoint didn't surface a context window for the
# chosen model AND post-pick enrichment (Ollama /api/show, etc.) couldn't
# find one either. 128k matches codex's own fallback
# (codexFallbackContextWindow in .idea/ollama/cmd/launch/codex.go) — high
# enough not to clip prompts on any model we'd plausibly point at, low
# enough not to lie about a tiny local model's real window when we have
# zero information.
_FALLBACK_CONTEXT_WINDOW = 128_000
_PROFILE_NAME = "conduit"

# Sections that belong to conduit. Anything else in the file is user-owned
# and survives a rewrite verbatim.
_MANAGED_HEADERS = (f"[profiles.{_PROFILE_NAME}]", f"[model_providers.{_PROFILE_NAME}]")


def launch(endpoint: str, model: Model, extra_args: list[str]) -> int:
    bin_path = _common.find_binary_or_fail("codex", _INSTALL_HINT)
    if bin_path is None:
        return 127

    # Codex talks only the Responses API (``wire_api = "chat"`` was removed,
    # see https://github.com/openai/codex/discussions/7782). Strict
    # OpenAI-compatible servers like LM Studio reject Responses requests
    # that omit ``text.format`` even though the spec marks it optional, and
    # codex never emits it. The shim sits between codex and the real
    # endpoint, injecting ``text.format = {type: "text"}`` on each request
    # body so strict servers accept it — invisible to the model, no agent
    # patch required. See conduit/shim.py for the transport.
    shim = ShimServer(upstream_url=endpoint, rewriter=_responses_rewriter)
    shim.start()
    try:
        _write_model_catalog(model)
        _write_config(shim.local_url, model.id)
        # `--profile` activates the launcher-managed [profiles.conduit] block
        # without overwriting the user's default profile in ~/.codex/config.toml.
        return _run_codex(bin_path, extra_args)
    finally:
        shim.stop()


def _run_codex(bin_path: str, extra_args: list[str]) -> int:
    # We can't ``os.execv`` like the other integrations: the shim thread
    # has to outlive codex so it can serve requests, which means conduit
    # has to stay alive too. Run codex as a subprocess in the same
    # foreground process group; the terminal delivers SIGINT to codex
    # directly, so we ignore it in the parent to avoid a race between our
    # KeyboardInterrupt handler and codex's own shutdown path.
    old_handler = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        return subprocess.run(
            [bin_path, "--profile", _PROFILE_NAME, *extra_args]
        ).returncode
    finally:
        signal.signal(signal.SIGINT, old_handler)


def _responses_rewriter(body: bytes, method: str, path: str) -> bytes:
    """Patch codex's Responses request body so strict OpenAI-compatible
    servers (LM Studio, notably) accept it. Two adjustments, both
    transparent to codex:

      1. Inject ``text.format = {type: "text"}`` when missing. Strict
         servers reject Responses requests without it
         (``text.format: missing_required_parameter``); codex never emits
         it because the OpenAI spec marks it optional.

      2. Drop every entry from ``tools`` whose ``type`` isn't
         ``"function"``. Codex's stable feature flags (``multi_agent``,
         ``browser_use``, ``computer_use``, ``image_generation``, ...)
         each contribute a tool with a non-standard ``type`` string
         (``namespace``, ``web_search``, ``computer_use_preview``, ...)
         that LM Studio rejects with ``tools.N.type: invalid_string``.
         The model_catalog_json fields that ought to gate these (e.g.
         ``supports_search_tool``, ``apply_patch_tool_type``) are
         partly ignored by current codex builds — see
         .idea/ollama/cmd/launch/codex_app.go for the catalog shape we
         already write. Stripping them at the wire is version-agnostic
         and authoritative.

    Local models can't drive most of those tools anyway (no browser, no
    sub-agents, no native image generation), so removing them loses no
    real capability for this routing path.
    """
    if method != "POST" or "/responses" not in path:
        return body
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return body
    if not isinstance(data, dict):
        return body

    changed = False

    text = data.get("text")
    if not isinstance(text, dict):
        text = {}
        data["text"] = text
        changed = True
    fmt = text.get("format")
    if not isinstance(fmt, dict):
        text["format"] = {"type": "text"}
        changed = True

    tools = data.get("tools")
    if isinstance(tools, list):
        kept = [t for t in tools if isinstance(t, dict) and t.get("type") == "function"]
        if len(kept) != len(tools):
            data["tools"] = kept
            changed = True

    if not changed:
        return body
    return json.dumps(data).encode("utf-8")


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
    # endpoint. `model_catalog_json` points codex at the catalog we wrote
    # next to config.toml — without it codex falls back to default
    # capabilities and emits experimental tool types that strict
    # OpenAI-compat servers reject as `tools.N.type: invalid_string`.
    #
    # `wire_api = "responses"`: codex removed `wire_api = "chat"` (see
    # https://github.com/openai/codex/discussions/7782), so Responses is
    # the only supported value. LM Studio's strict Responses validator
    # demands `text.format` on every request and codex doesn't emit it —
    # we patch that over in conduit/shim.py by injecting the field into
    # request bodies before they reach upstream. base_url here points at
    # the shim, not the real endpoint.
    return (
        f"[profiles.{_PROFILE_NAME}]\n"
        f'model = "{model}"\n'
        f'openai_base_url = "{base_url}"\n'
        f'model_provider = "{_PROFILE_NAME}"\n'
        f'forced_login_method = "api"\n'
        f'model_catalog_json = "{_CATALOG_PATH}"\n'
        f"\n"
        f"[model_providers.{_PROFILE_NAME}]\n"
        f'name = "Conduit"\n'
        f'base_url = "{base_url}"\n'
        f'wire_api = "responses"\n'
    )


def _write_model_catalog(model: Model) -> None:
    """Write a single-entry model.json catalog at ~/.codex/model.json that
    advertises only the standard `function`-typed tool schema. Mirrors
    `codexAppCatalogEntry` in .idea/ollama/cmd/launch/codex_app.go — the
    newer codex (post-wire_api-removal) catalog shape.

    The older `buildCodexModelEntry` shape from codex.go isn't enough for
    current codex builds: it omits `apply_patch_tool_type` and
    `web_search_tool_type`, so codex still synthesizes an apply_patch
    (or similar) tool entry whose `type` string isn't `"function"`. The
    fields that gate non-function tool emission are:

      * `experimental_supported_tools: []` — no experimental tools
      * `apply_patch_tool_type: null`     — no apply_patch tool
      * `supports_search_tool: false`     — no web_search tool
      * `web_search_tool_type: "text"`    — fall-back for any web_search
                                            mention to a vanilla text tool

    Strict OpenAI-compatible servers (LM Studio) reject any tool whose
    type isn't `"function"` with `tools.N.type: invalid_string`, so the
    catalog has to suppress all of them.
    """
    context_window = model.context_window or _FALLBACK_CONTEXT_WINDOW
    entry: dict[str, object] = {
        "slug": model.id,
        "display_name": model.id,
        "description": "conduit-routed local model",
        "default_reasoning_level": None,
        "supported_reasoning_levels": [],
        "shell_type": "default",
        "visibility": "list",
        "supported_in_api": True,
        "priority": 0,
        "additional_speed_tiers": [],
        "availability_nux": None,
        "upgrade": None,
        "base_instructions": "",
        "model_messages": None,
        "supports_reasoning_summaries": False,
        "default_reasoning_summary": "auto",
        "support_verbosity": False,
        "default_verbosity": None,
        "apply_patch_tool_type": None,
        "web_search_tool_type": "text",
        "truncation_policy": {"mode": "bytes", "limit": 10_000},
        "supports_parallel_tool_calls": False,
        "supports_image_detail_original": False,
        "context_window": context_window,
        "max_context_window": context_window,
        "auto_compact_token_limit": None,
        "effective_context_window_percent": 95,
        "experimental_supported_tools": [],
        "input_modalities": ["text"],
        "supports_search_tool": False,
    }
    _common.atomic_write_text(
        _CATALOG_PATH,
        json.dumps({"models": [entry]}, indent=2) + "\n",
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
