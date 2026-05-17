# Aetherion

A containerized development environment for AI coding agents.

Ships a Debian dev container preloaded with the bundled agent CLIs (Claude
Code, Cursor Agent, GitHub Copilot CLI, Gemini CLI, OpenAI Codex, Pi,
OpenClaw), the Ollama client for running them against a local model
daemon on the host, Neovim with LSP/DAP support, podman-in-podman, and
toolchains for Python, Node, Go, Rust, and Ruby. The `aetherion` launcher
mounts the current directory at the same path inside the container and
preserves per-agent login state across sessions.

## Install

```shell
uv tool install aetherion
```

(or `pipx install aetherion`)

To upgrade later:

```shell
uv tool upgrade aetherion
```

## Quickstart

```shell
aetherion --build-image    # one-time: build localhost/aetherion:dev
aetherion                  # launch a shell in $PWD
```

## Using local Ollama models

The container ships the `ollama` client only — no server, no GPU runners.
Point it at a daemon running on the host (or anywhere reachable) via
`OLLAMA_HOST`. On Docker Desktop / podman-machine the host is reachable
as `host.docker.internal`:

```shell
OLLAMA_HOST=http://host.docker.internal:11434 ollama list
```

`ollama launch <agent>` writes the matching agent CLI's per-provider config
so it routes through the daemon, then execs the agent for you. Tested:

```shell
OLLAMA_HOST=http://host.docker.internal:11434 ollama launch pi
OLLAMA_HOST=http://host.docker.internal:11434 ollama launch openclaw
OLLAMA_HOST=http://host.docker.internal:11434 ollama launch codex
OLLAMA_HOST=http://host.docker.internal:11434 ollama launch claude
```

OpenClaw runs a gateway on port 18789 inside the container — but binds it
to `127.0.0.1`, which podman/docker port forwarding can't reach. Use
`--forward-openclaw` to publish AND set up a loopback bridge in one shot:

```shell
aetherion --forward-openclaw                  # bind 127.0.0.1:18789 (host-local)
aetherion --forward-openclaw 0.0.0.0          # bind all interfaces, port 18789
aetherion --forward-openclaw 9999             # bind 127.0.0.1:9999  (remap host port)
aetherion --forward-openclaw 0.0.0.0:9999     # both: all interfaces + custom port
aetherion --forward-openclaw '[::1]:9999'     # IPv6 loopback, custom port
```

Then open `http://<host-bind>:<host-port>` on the host (container-side
port is always 18789 — openclaw's own). For ports that already bind
0.0.0.0 inside the container, `--forward CONTAINER_PORT` (repeatable) is
enough — no bridge needed.

If you'd rather not retype the env var, set it on the host and pass it
through with `--env`:

```shell
export OLLAMA_HOST=http://host.docker.internal:11434
aetherion --env OLLAMA_HOST    # bare name inherits from host
```

The container ships real Node.js LTS, so ollama's preflight checks (which
gate on `LookPath("npm")` and call `npm root -g` to find globally-installed
agents) work natively. Bun is still installed as a fast user-scoped runtime;
it just isn't pretending to be node.

## What's in the container

- **Languages & runtimes**: Python (system + uv), Node.js LTS + bun, Go, Rust, Ruby, C/C++ toolchain
- **Agent CLIs**: Claude Code, Cursor Agent, GitHub Copilot CLI, Gemini CLI, OpenAI Codex, Pi, OpenClaw
- **Editor**: Neovim with bundled LSPs (`pyright`, `gopls`, `rust-analyzer`, `lua-language-server`, `typescript-language-server`, `vim-language-server`) and DAPs (`debugpy`, `delve`, `codelldb`, `js-debug-adapter`)
- **CLI tools**: git, podman, tmux, starship, ripgrep, fd, fzf, jq, yq, posting, openssh-client, ollama (client only — point at a host daemon via `OLLAMA_HOST`)

## State preservation

The first time you log in to a bundled agent CLI, the launcher detects the new
config inside the container and copies it to `~/.aetherion/data/` on the host.
Subsequent launches bind-mount the saved config so you stay logged in.
`~/.aetherion/data/` is the only host directory the launcher writes to.

| agent | preserved paths |
| --- | --- |
| `claude` | `.claude/`, `.claude.json` |
| `cursor` | `.cursor/`, `.config/cursor/` |
| `copilot` | `.copilot/` |
| `gemini` | `.gemini` |
| `codex` | `.codex/` |
| `pi` | `.pi/` |
| `openclaw` | `.openclaw/` |
| `npm` | `.npm-global/`, `.npm/` (user-scoped npm prefix + cache — preserves runtime-installed plugins like `@ollama/pi-web-search` and avoids re-fetching them when an agent reruns `npm update` on launch) |

## Flags

| flag | purpose |
| --- | --- |
| `--agents LIST` | Comma-separated subset of agents to expose (default: all). `--agents ''` for none. |
| `-e`, `--env NAME=VALUE` | Set a container environment variable. Repeatable. Quote at the shell for values with spaces: `--env 'NAME=has spaces'`. A bare `--env NAME` inherits from the host environment. |
| `--forward [ADDR:[HOST_PORT:]]CONTAINER_PORT` | Publish a container port (podman/docker `-p` semantics). Repeatable. Forms: `PORT`, `HOST:CONTAINER`, `ADDR:HOST:CONTAINER`, `:HOST:CONTAINER`, `[::1]:HOST:CONTAINER`. Default host bind is `127.0.0.1`. Services that bind 127.0.0.1 inside the container won't be reachable through this alone — use a `--forward-<agent>` alias. |
| `--forward-openclaw [ADDR][:PORT]` | Convenience alias for OpenClaw's gateway (container port 18789). Publishes the port AND sets up a loopback bridge so the publish actually reaches it (openclaw binds 127.0.0.1 inside the container). Bare = `127.0.0.1:18789`; otherwise accepts `ADDR`, `PORT`, `ADDR:PORT`, `:PORT`, `[::1]:PORT`. |
| `--image REF` | Image ref to run, and to tag when building. Default: `localhost/aetherion:dev`. |
| `--build-image` | Build the image and exit. Does not launch the container. |
| `--build-dir PATH` | Build context directory. Defaults to the Dockerfile bundled with the launcher. |
| `--extract PATH` | Copy the bundled Dockerfile, skeleton/, and scripts/ to PATH and exit. |

`AETHERION_CONTAINER_RUNTIME=docker` overrides runtime auto-detection (podman is preferred when both are available).

## Customizing the image

The launcher ships its own Dockerfile and skeleton tree inside the Python
package. To fork them:

```shell
aetherion --extract ~/my-aetherion
$EDITOR ~/my-aetherion/Dockerfile
aetherion --build-image --build-dir ~/my-aetherion --image my:tag
aetherion --image my:tag
```

## Development

```shell
git clone https://github.com/samintheshell/aetherion
cd aetherion
uv sync
uv run aetherion --help
```

Build and publish the Python package with the included Makefile:

```shell
make            # show available targets
make build      # produce sdist + wheel in dist/
make publish    # upload dist/* to PyPI (UV_PUBLISH_TOKEN required)
```

The container image itself has `uv` plus the standard CPython toolchain
installed, so you can also run `make publish` from inside an `aetherion`
shell if you prefer keeping credentials in the container.
