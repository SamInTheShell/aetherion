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

    # Split at the first bare `--`: everything after is passthrough to the
    # agent binary (e.g. `conduit launch claude -- --debug`). Doing this
    # *before* argparse means agent flags can't accidentally collide with
    # ours — claude's `--model`, codex's `--profile`, etc. all reach the
    # agent verbatim.
    if "--" in argv:
        sep = argv.index("--")
        argv_for_parse, passthrough = argv[:sep], argv[sep + 1:]
    else:
        argv_for_parse, passthrough = argv, []

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

    sub.add_parser(
        "list-models",
        help="List models available at the configured endpoint (alphabetical).",
        description=(
            "Print every model the configured endpoint reports at "
            "/v1/models, one per line, sorted alphabetically. No model "
            "selection — use this to discover ids you can then pass to "
            "`conduit launch <agent> <model>`."
        ),
    )

    launch_p = sub.add_parser(
        "launch",
        help="Launch a coding agent against the configured endpoint.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Launch a coding agent. With no model supplied, the configured "
            "endpoint is queried for available models and an interactive "
            "picker runs. With a MODEL positional, the picker is skipped "
            "and the model is validated against the endpoint's list. Pass "
            "additional arguments through to the agent binary after `--`."
        ),
        epilog=(
            "Examples:\n"
            "  conduit launch pi\n"
            "        interactive model picker, then launch pi\n"
            "  conduit launch claude qwen2.5:7b\n"
            "        skip the picker, launch claude with qwen2.5:7b\n"
            "  conduit launch codex -- --search 'goroutines'\n"
            "        picker, then forward `--search goroutines` to codex\n"
            "  conduit launch openclaw llama3:8b -- daemon stop\n"
            "        pinned model + forward `daemon stop` to openclaw\n"
        ),
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
        "model",
        nargs="?",
        metavar="MODEL",
        help=(
            "Model id to use, as reported by the endpoint's /v1/models. "
            "Omit to choose interactively. Validated against the endpoint "
            "before launch; an unknown id errors with the available list."
        ),
    )

    args = parser.parse_args(argv_for_parse)

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
    if args.command == "list-models":
        return _cmd_list_models()
    if args.command == "launch":
        return _cmd_launch(args.integration, args.model, passthrough)
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


def _cmd_list_models() -> int:
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
    # Print to stdout (not stderr) so users can pipe to grep/sort/wc without
    # losing the data to stderr-only filters.
    for name in sorted(models):
        print(name)
    return 0


def _cmd_launch(name: str, model_override: str | None, extra_args: list[str]) -> int:
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

    if model_override is not None:
        # Validate against the endpoint's catalog so a typo doesn't silently
        # launch the agent against a model the server can't actually serve.
        if model_override not in models:
            sys.stderr.write(
                f"conduit: model {model_override!r} is not available at "
                f"{s.endpoint_alias or s.endpoint}.\n"
                f"conduit: available models:\n"
            )
            for m in models:
                sys.stderr.write(f"  - {m}\n")
            return 2
        chosen = model_override
    else:
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
