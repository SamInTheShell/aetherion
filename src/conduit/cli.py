#!/usr/bin/env python3
"""Conduit: in-container utility that wires AI coding agents at an
OpenAI-compatible model server (Ollama, LM Studio, vLLM, ...).

Settings live at ``~/.conduit/config.json``; ``conduit set endpoint`` picks
the server, ``conduit launch <integration>`` rewrites the integration's
config to use it and execs the binary. Inside the aetherion container,
``~/.conduit/`` is mirrored back to the host at ``~/.aetherion/data/.conduit``
so the choice persists across sessions.
"""
from __future__ import annotations

import argparse
import sys

from conduit import settings as settings_mod
from conduit import endpoint as endpoint_mod
from conduit import picker
from conduit.integrations import claude as claude_integration
from conduit.integrations import codex as codex_integration
from conduit.integrations import copilot as copilot_integration
from conduit.integrations import droid as droid_integration
from conduit.integrations import hermes as hermes_integration
from conduit.integrations import openclaw as openclaw_integration
from conduit.integrations import pi as pi_integration

# Registry of `conduit launch <name>` targets. Each module exposes
# ``NAME`` and ``launch(endpoint, model, extra_args)`` — `launch` either
# returns an exit code (binary not found) or replaces the process via
# ``os.execv`` and never returns.
INTEGRATIONS = {
    claude_integration.NAME: claude_integration,
    codex_integration.NAME: codex_integration,
    copilot_integration.NAME: copilot_integration,
    droid_integration.NAME: droid_integration,
    hermes_integration.NAME: hermes_integration,
    openclaw_integration.NAME: openclaw_integration,
    pi_integration.NAME: pi_integration,
}


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(
        prog="conduit",
        description=(
            "Point AI coding agents at an OpenAI-compatible model server "
            "(Ollama, LM Studio, or any /v1/models endpoint)."
        ),
    )
    # Subcommands are optional at the argparse level so missing input lands
    # us in the "print help and exit cleanly" branch below, rather than
    # argparse's terse "the following arguments are required: command"
    # error which reads like a lecture.
    sub = parser.add_subparsers(dest="command")

    set_p = sub.add_parser("set", help="Update a conduit setting.")
    set_sub = set_p.add_subparsers(dest="key")
    endpoint_p = set_sub.add_parser(
        "endpoint",
        help="Set the model server endpoint.",
        description=(
            "Set the default model server. Accepts the shorthand aliases "
            f"({', '.join(settings_mod.alias_names())}) — these resolve to "
            "the host's address (host.docker.internal inside the aetherion "
            "container, 127.0.0.1 when run directly on the host) — or an "
            "explicit http(s):// URL."
        ),
    )
    endpoint_p.add_argument(
        "value",
        help=(
            "Endpoint alias (ollama | lmstudio) or full URL "
            "(e.g. https://my-llm.example.com)."
        ),
    )

    launch_p = sub.add_parser(
        "launch",
        help="Launch a coding agent against the configured endpoint.",
    )
    launch_p.add_argument(
        "integration",
        choices=sorted(INTEGRATIONS),
        # Optional so `conduit launch` (no integration) falls through to
        # the help-print branch below instead of argparse's
        # "argument integration: expected one argument" error.
        nargs="?",
        help="Which agent to launch.",
    )
    launch_p.add_argument(
        "args",
        nargs=argparse.REMAINDER,
        help="Extra arguments forwarded to the agent's binary.",
    )

    args = parser.parse_args(argv)

    # Bare `conduit` → top-level help.
    if args.command is None:
        parser.print_help()
        return 0
    # `conduit set` with no setting key → set's help.
    if args.command == "set" and args.key is None:
        set_p.print_help()
        return 0
    # `conduit launch` with no integration → launch's help.
    if args.command == "launch" and args.integration is None:
        launch_p.print_help()
        return 0

    if args.command == "set" and args.key == "endpoint":
        return _cmd_set_endpoint(args.value)
    if args.command == "launch":
        return _cmd_launch(args.integration, args.args or [])
    parser.print_help()
    return 0


def _cmd_set_endpoint(value: str) -> int:
    try:
        url, alias = settings_mod.resolve_endpoint(value)
    except ValueError as e:
        sys.stderr.write(f"conduit: {e}\n")
        return 2
    s = settings_mod.load()
    s.endpoint = url
    s.endpoint_alias = alias
    settings_mod.save(s)
    label = alias or url
    sys.stderr.write(f"conduit: endpoint set to {label} ({url})\n")
    return 0


def _cmd_launch(name: str, extra_args: list[str]) -> int:
    integration = INTEGRATIONS[name]
    s = settings_mod.load()
    if not s.endpoint:
        sys.stderr.write(
            "conduit: no endpoint configured. Set one first, e.g.:\n"
            "  conduit set endpoint ollama\n"
            "  conduit set endpoint lmstudio\n"
            "  conduit set endpoint https://my-llm.example.com\n"
        )
        return 2

    try:
        models = endpoint_mod.list_models(s.endpoint)
    except endpoint_mod.EndpointError as e:
        sys.stderr.write(f"conduit: {e}\n")
        return 1

    last = s.last_models.get(name)
    chosen = picker.pick(
        f"Select a model for {name} (via {s.endpoint_alias or s.endpoint}):",
        models,
        default=last,
    )
    if chosen is None:
        sys.stderr.write("conduit: cancelled.\n")
        return 130

    s.last_models[name] = chosen
    settings_mod.save(s)

    sys.stderr.write(f"conduit: launching {name} with {chosen}…\n")
    sys.stderr.flush()
    return integration.launch(s.endpoint, chosen, extra_args)


if __name__ == "__main__":
    sys.exit(main())
