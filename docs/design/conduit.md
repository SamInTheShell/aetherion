# Conduit design

**Conduit** is a small, agent-agnostic CLI that points AI coding agents at a
local, OpenAI-compatible model server. It ships alongside `aetherion` (same
distribution, separate `conduit` console script) and runs **inside** an
aetherion namespace — specifically the `cli-agents` template, which is the only
image that bundles it.

The problem it solves: every agent CLI (Claude Code, Codex, Copilot, Pi,
OpenClaw, Hermes, Droid) has its own way of being told "use *this* endpoint and
*this* model" — different env vars, different config files in different formats,
sometimes a request shape the server won't accept. Conduit normalizes that to a
single workflow.

## The workflow

```shell
conduit set endpoint ollama        # or `lmstudio`, or a full http(s):// URL
conduit list-models                # what the endpoint serves
conduit launch claude              # pick a model in a TUI, launch Claude Code
```

Three commands:

- **`conduit set endpoint <spec>`** — resolve and persist the model-server
  endpoint. `<spec>` is an alias (`ollama`, `lmstudio`) or an explicit URL.
- **`conduit list-models`** — query the endpoint's `/v1/models` and print the
  available model IDs.
- **`conduit launch <integration> [model]`** — configure an agent for the
  endpoint + model and exec it. With no `model`, an interactive picker shows the
  endpoint's catalog (arrow keys or `j`/`k`, Enter to choose); headless (no TTY)
  picks the first.

## Endpoints

Conduit speaks the OpenAI-compatible API: it discovers models with a single
`GET <endpoint>/v1/models` and validates your chosen model against the result.
Anything that exposes that surface works — Ollama, LM Studio, vLLM, llama.cpp's
`llama-server`, or a custom server.

Two aliases expand to host-loopback addresses, and conduit detects whether it's
running in a container (via `/run/.containerenv` or `/.dockerenv`) to choose the
right host:

| alias | host (bare) | host (in container) | port |
| --- | --- | --- | --- |
| `ollama` | `127.0.0.1` | `host.docker.internal` | `11434` |
| `lmstudio` | `127.0.0.1` | `host.docker.internal` | `1234` |

Inside an aetherion container, `host.docker.internal` reaches your host thanks to
the loopback networking the launcher sets up — so a model server already running
on your host is reachable with no extra configuration. The container itself does
no inference; it's a dev environment.

## Persistence

Settings live at `~/.conduit/config.json`:

```json
{
  "endpoint": "http://host.docker.internal:11434",
  "endpoint_alias": "ollama",
  "last_models": { "claude": "qwen2.5-coder:32b", "pi": "..." }
}
```

Because a namespace's `$HOME` is a bind-mounted host directory, your endpoint
choice and per-agent last-used model survive across container sessions
automatically.

## Integrations

Each integration is a module exposing `NAME`, an optional `DISPLAY_NAME`, and
`launch(endpoint, model, extra_args) -> int`. There's no base class — just a
shared helper library (`_common.py`) the integrations use idiomatically. They
fall into three strategies:

### Env-var only

The agent reads its endpoint and model from environment variables; conduit sets
them and `execvpe`s the binary (replacing the process so signals and stdio flow
straight through).

- **`claude`** (Claude Code) — sets `ANTHROPIC_BASE_URL`, an empty
  `ANTHROPIC_API_KEY` with `ANTHROPIC_AUTH_TOKEN=conduit` (header auth, what
  OpenAI-compatible servers expect), pins every model tier
  (`ANTHROPIC_DEFAULT_*_MODEL`, `CLAUDE_CODE_SUBAGENT_MODEL`) to the chosen
  model, and disables the attribution header.
- **`copilot`** (GitHub Copilot CLI) — sets `COPILOT_PROVIDER_BASE_URL` (with the
  `/v1` path), empty key, `COPILOT_PROVIDER_WIRE_API=responses`, and
  `COPILOT_MODEL`.

### Config-file rewrite

The agent reads a config file; conduit rewrites it, **preserving the user's
existing entries** and marking its own so re-runs replace the conduit entry
without clobbering yours. Writes are atomic (temp file + rename).

- **`pi`** (Pi) — JSON at `~/.pi/agent/{models,settings}.json`. Reuses an
  `ollama` provider entry, marks conduit models with `_conduit: true`, sets the
  default provider/model.
- **`openclaw`** (OpenClaw) — JSON at `~/.openclaw/openclaw.json`. Reuses the
  `ollama` provider (pointed at the raw endpoint — OpenClaw handles `/v1`
  itself), marks conduit models, sets the primary agent model.
- **`hermes`** (Hermes) — YAML at `~/.hermes/config.yaml`. Round-trips through
  PyYAML to preserve user keys, updates only the `model` and `providers.conduit`
  sections (a distinct provider key so it coexists with other launchers).
- **`droid`** (Factory's Droid) — JSON at `~/.factory/settings.json`. Inserts a
  conduit-marked entry at the top of `customModels` and sets the session default.
  *(Not installed in the stock `cli-agents` image, but supported if you add it.)*

### Config-file rewrite + request shim

- **`codex`** (OpenAI Codex) — TOML at `~/.codex/config.toml` plus a model
  catalog at `~/.codex/model.json`, **and** a loopback HTTP proxy. Codex emits
  Responses-API requests that omit `text.format`, which strict servers (e.g. LM
  Studio) reject. Conduit starts a small `ShimServer` on `127.0.0.1:<random>`,
  points Codex at it, and the shim rewrites each request — injecting
  `text.format` and stripping experimental non-function tools — before forwarding
  upstream, streaming the response back in real time. Because the shim must
  outlive the request, Codex runs as a subprocess here rather than via `execv`.

## Shared helpers (`_common.py`)

The integrations share a focused toolbox instead of inheritance:

- `load_json(path)` — tolerant read; missing/invalid file → empty dict so callers
  always get a writable mapping.
- `atomic_write_json(path, data)` / `atomic_write_text(path, text)` — temp-file +
  rename so a config is never left half-written.
- `find_binary_or_fail(name, hint)` — `shutil.which` wrapper; prints a focused
  install hint and signals exit code 127 if the agent binary is missing.
- `execv_with_env(bin_path, args, env_overrides)` — `os.execvpe` layering env
  overrides on the inherited environment, replacing the process.

## Command flow

```
conduit launch <agent> [model]
        │
        ├─ load ~/.conduit/config.json  ──────────────► endpoint
        ├─ GET <endpoint>/v1/models      ─────────────► catalog
        ├─ validate model, or pick interactively
        ├─ record last-used model for <agent>
        └─ integration.launch(endpoint, model, extra_args)
                 ├─ find the agent binary on PATH
                 ├─ set env vars  -or-  rewrite config file(s)  [± start shim]
                 └─ exec the agent  (process replaced; or subprocess if a shim is live)
```

## Relationship to aetherion

Conduit is a sibling of the launcher, not a layer on top of it. They share the
PyPI distribution and the `cli-agents` image but no runtime code. The launcher's
job ends once the container is running; conduit's begins inside it, using the
host-loopback networking the launcher arranged to reach your model server. See
[aetherion design](aetherion.md).
