"""Configure Claude Code to use the conduit endpoint.

Claude Code reads its provider config entirely from environment variables —
no on-disk file to edit. We just set ANTHROPIC_BASE_URL and the per-tier
model overrides, then exec the binary. Mirrors what
``ollama launch claude`` does in cmd/launch/claude.go.
"""
from __future__ import annotations

from conduit.endpoint import Model
from conduit.integrations import _common

NAME = "claude"
DISPLAY_NAME = "Claude Code"

_INSTALL_HINT = (
    "Inside the aetherion container, claude is installed via the official "
    "apt repo. On a host machine: see https://code.claude.com/docs/en/quickstart."
)


def launch(endpoint: str, model: Model, extra_args: list[str]) -> int:
    bin_path = _common.find_binary_or_fail("claude", _INSTALL_HINT)
    if bin_path is None:
        return 127

    env_overrides = {
        "ANTHROPIC_BASE_URL": endpoint.rstrip("/"),
        # Claude Code treats an empty API key + a non-empty AUTH_TOKEN as
        # "use the auth-token header instead of the standard Anthropic key
        # auth," which is what every OpenAI-compatible server expects.
        "ANTHROPIC_API_KEY": "",
        "ANTHROPIC_AUTH_TOKEN": "conduit",
        # Pin every model tier to the picked model so /switch in-app doesn't
        # silently flip back to a tier (Opus/Sonnet/Haiku) that the local
        # server doesn't serve.
        "ANTHROPIC_DEFAULT_OPUS_MODEL": model.id,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": model.id,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": model.id,
        "CLAUDE_CODE_SUBAGENT_MODEL": model.id,
        # Strip the attribution header so we don't leak conduit-internal
        # routing back to Anthropic from a non-Anthropic backend.
        "CLAUDE_CODE_ATTRIBUTION_HEADER": "0",
    }

    args = ["--model", model.id, *extra_args]
    _common.execv_with_env(bin_path, args, env_overrides)
