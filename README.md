# Aetherion

A containerized development environment for AI coding agents.

Ships a Debian dev container preloaded with the bundled agent CLIs (Claude
Code, Cursor Agent, GitHub Copilot CLI, Gemini CLI, OpenAI Codex, Pi,
OpenClaw, Hermes), Neovim with LSP/DAP support, podman-in-podman, and
toolchains for Python, Node, Go, Rust, and Ruby. The `aetherion` launcher
mounts the current directory at the same path inside the container and
preserves per-agent login state across sessions. A second CLI, `conduit`,
ships alongside and points the agents at a model server running on your
host (Ollama, LM Studio, or any OpenAI-compatible endpoint).

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

Inside the container, point agents at your host's model server:

```shell
conduit set endpoint lmstudio   # or `ollama`, or a full http(s):// URL
conduit launch pi               # pick a model in the TUI; pi launches against it
```

## Using a local model server

The container itself does no inference — it's a dev environment. Run your
model server on the host (LM Studio's local server, an `ollama serve` you
already had, vLLM, llama.cpp's `llama-server`, anything that exposes
OpenAI-compatible `/v1/models`) and `conduit` will wire the agent CLIs at
it. The launcher sets up host-loopback networking automatically for both
docker and rootless podman, so the host's `127.0.0.1:<port>` is reachable
from inside the container without reconfiguring the model server.

```shell
conduit set endpoint ollama                # → host's :11434
conduit set endpoint lmstudio              # → host's :1234
conduit set endpoint https://my.example    # any OpenAI-compatible /v1
conduit launch pi                          # arrow-key model picker → pi
```

Endpoint choice and last-used model per integration are stored at
`~/.conduit/config.json` and preserved across container sessions.

## Publishing in-container ports

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

## What's in the container

- **Launcher tooling**: `conduit` (endpoint configuration + integration launcher; ships with aetherion)
- **Languages & runtimes**: Python (system + uv), Node.js LTS + bun, Go, Rust, Ruby, C/C++ toolchain
- **Agent CLIs**: Claude Code, Cursor Agent, GitHub Copilot CLI, Gemini CLI, OpenAI Codex, Pi, OpenClaw, Hermes
- **Editor**: Neovim with bundled LSPs (`pyright`, `gopls`, `rust-analyzer`, `lua-language-server`, `typescript-language-server`, `vim-language-server`) and DAPs (`debugpy`, `delve`, `codelldb`, `js-debug-adapter`)
- **CLI tools**: git, podman, tmux, starship, ripgrep, fd, fzf, jq, yq, posting, openssh-client

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
| `hermes` | `.hermes/` |
| `conduit` | `.conduit/` (endpoint choice + last-model-per-integration) |
| `npm` | `.npm-global/`, `.npm/` (user-scoped npm prefix + cache — preserves runtime-installed agent plugins and avoids re-fetching when an agent reruns `npm update` on launch) |

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
| `--refresh-layers` | Discard the runtime's build cache for this build (`--no-cache`). Use to refresh anything pinned by an intermediate layer's snapshot: apt mirrors, the Node.js LTS tarball, the global npm install of agent CLIs, the cursor-agent installer, hermes-agent's PyPI release, neovim plugins. Without it you stay on whatever was current the first time the layer was built; with it every upstream gets re-fetched. Only meaningful with `--build-image`. |
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
