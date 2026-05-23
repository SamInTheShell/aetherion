"""Configure GitHub Copilot CLI to use the conduit endpoint.

Copilot CLI reads provider config from environment variables — no on-disk
file to edit. We point its provider at the conduit endpoint's
OpenAI-compatible ``/v1`` path and exec the binary. Mirrors what
``ollama launch copilot`` does in cmd/launch/copilot.go.
"""
from __future__ import annotations

from conduit.integrations import _common

NAME = "copilot"
DISPLAY_NAME = "Copilot CLI"

_INSTALL_HINT = (
    "Inside the aetherion container, copilot is installed via npm "
    "(@github/copilot). On a host machine: "
    "https://docs.github.com/en/copilot/how-tos/set-up/install-copilot-cli."
)


def launch(endpoint: str, model: str, extra_args: list[str]) -> int:
    bin_path = _common.find_binary_or_fail("copilot", _INSTALL_HINT)
    if bin_path is None:
        return 127

    env_overrides = {
        # Copilot CLI's custom-provider knobs. /v1 because copilot speaks
        # OpenAI-flavoured "responses" wire format on top of it.
        "COPILOT_PROVIDER_BASE_URL": endpoint.rstrip("/") + "/v1",
        "COPILOT_PROVIDER_API_KEY": "",
        "COPILOT_PROVIDER_WIRE_API": "responses",
        "COPILOT_MODEL": model,
    }

    args = ["--model", model, *extra_args] if model else list(extra_args)
    _common.execv_with_env(bin_path, args, env_overrides)
