# Aetherion

A containerized development environment for AI coding agents.

Ships a Debian dev container preloaded with the bundled agent CLIs (Claude
Code, Cursor Agent, GitHub Copilot CLI, Gemini CLI, OpenAI Codex, Pi,
OpenClaw, Hermes), Neovim with LSP/DAP support, podman-in-podman, and
toolchains for Python, Node, Go, Rust, and Ruby. Toolchains and agent
binaries live system-wide in the image (under `/opt` and `/usr/local`),
so image rebuilds deliver new versions to every workspace immediately.
The `aetherion` launcher mounts the current directory inside the
container and bind-mounts a host directory as `$HOME` — agent logins,
shell history, and per-user tool state persist across sessions. Multiple
independent **namespaces** (each its own `$HOME`) let you keep separate
identities, model setups, or experiments side by side without cross-talk.
A second CLI, `conduit`, ships alongside and points the agents at a
model server running on your host (Ollama, LM Studio, or any
OpenAI-compatible endpoint).

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
aetherion --build-image                  # one-time: build localhost/aetherion:dev
aetherion                                # first run auto-creates the 'default' namespace and enters
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

Everything below is installed system-wide (under `/usr/local/bin` and
`/opt`), so it's shared across every namespace and tracks the image
version automatically. Per-user state — agent logins, npm globals,
runtime `go install` binaries, nvim plugins, shell history — lives in
the namespace's `$HOME`.

- **Launcher tooling**: `conduit` (endpoint configuration + integration launcher; ships with aetherion)
- **Languages & runtimes**: Python (system + uv), Node.js LTS + bun, Go, Rust, Ruby, C/C++ toolchain
- **Agent CLIs**: Claude Code, Cursor Agent (`agent`), GitHub Copilot CLI, Gemini CLI, OpenAI Codex, Pi, OpenClaw, Hermes
- **Editor**: Neovim with bundled LSPs (`pyright`, `gopls`, `rust-analyzer`, `lua-language-server`, `typescript-language-server`, `vim-language-server`) and DAPs (`debugpy`, `delve`, `codelldb`, `js-debug-adapter`). Plugins (`Lazy.nvim`-managed) auto-install on first `nvim` launch in each namespace.
- **CLI tools**: git, podman, tmux, starship, ripgrep, fd, fzf, jq, yq, posting, openssh-client

## Namespaces

A namespace is an independent host directory at
`~/.aetherion/namespaces/<name>/` that the launcher bind-mounts as the
container's `$HOME`. Everything that lives under `$HOME` inside the
container — agent logins, runtime-installed npm/go/uv tools, nvim plugins
once you've launched nvim, shell history, dotfile edits — is just files
in that host directory and survives across sessions. Two namespaces
share zero state: logging into Claude under `work` doesn't log you in
under `play`.

The first time a namespace is used, the launcher seeds it from the
image's `/home/aetherion`. The seed is small — just the skeleton
dotfiles (`.bashrc`, `.npmrc`, `.config/nvim/`, `.config/starship.toml`)
— because system tools and agent binaries live outside `$HOME` in the
image. First entry is near-instant; first `nvim` launch in a namespace
then runs Lazy's plugin install (a few minutes; cached afterward) and
treesitter parsers compile on first file open.

```shell
aetherion                                 # auto-creates 'default' on first use, then enters
aetherion --namespace work --create-namespace
aetherion -n work                         # next launches into 'work'
aetherion --list-namespaces               # show what's been created and what image each came from
aetherion --reset-namespace               # nuke 'default' and re-seed from current image
aetherion -n work --reset-namespace --force
```

### What updates when, and what doesn't

- **System tools (image-managed)**: `aetherion`, `conduit`, every agent CLI, every LSP/DAP, language runtimes. Rebuild the image and every existing namespace gets the new version on next launch — nothing to migrate.
- **Namespace contents (your state)**: agent logins, `npm install -g` packages, `go install`-ed binaries, nvim plugins, shell history, anything you `touch`ed inside. Stays put across image upgrades. To reset, use `--reset-namespace`.
- **Skeleton dotfiles (frozen at seed)**: `.bashrc`, `.npmrc`, `.config/nvim/`, `.config/starship.toml`. These are captured into the namespace at seed time and don't refresh when the image changes — if a new image ships a `.bashrc` you want, the launcher prints a one-line drift notice on launch suggesting `aetherion --reset-namespace` (which drops everything else in the namespace too, so use with care).

### Migrating from older aetherion

A prior install's `~/.aetherion/data/` (the per-agent layout) is
auto-migrated into the `default` namespace on next launch: the launcher
seeds `default` from the current image, overlays the legacy per-agent
dirs (`.claude/`, `.gemini/`, `go/`, etc.) on top so existing logins win,
and renames the old directory to `data.migrated-YYYYMMDD` as a safety
net. Delete the safety copy once you've confirmed everything works.

## Flags

| flag | purpose |
| --- | --- |
| `-n`, `--namespace NAME` | Namespace whose `$HOME` to mount. Default: `default` (auto-created on first use). Other namespaces error if they don't exist; pair with `--create-namespace`. Names: letters, digits, dot, underscore, dash (no leading dot). |
| `--create-namespace` | Create the namespace selected by `-n` if it doesn't exist (seeded from the current image). No-op when it already exists. Not needed for `default`. Combines with launch. |
| `--list-namespaces` | List existing namespaces with the image digest each was seeded from, then exit. |
| `--reset-namespace` | Delete the namespace selected by `-n` and re-seed from the current image, then exit. Drops every in-namespace customization. Confirms unless `--force` is also passed. |
| `--force` | Skip the confirmation prompt for `--reset-namespace`. |
| `-e`, `--env NAME=VALUE` | Set a container environment variable. Repeatable. Quote at the shell for values with spaces: `--env 'NAME=has spaces'`. A bare `--env NAME` inherits from the host environment. |
| `--forward [ADDR:[HOST_PORT:]]CONTAINER_PORT` | Publish a container port (podman/docker `-p` semantics). Repeatable. Forms: `PORT`, `HOST:CONTAINER`, `ADDR:HOST:CONTAINER`, `:HOST:CONTAINER`, `[::1]:HOST:CONTAINER`. Default host bind is `127.0.0.1`. Services that bind 127.0.0.1 inside the container won't be reachable through this alone — use a `--forward-<agent>` alias. |
| `--forward-openclaw [ADDR][:PORT]` | Convenience alias for OpenClaw's gateway (container port 18789). Publishes the port AND sets up a loopback bridge so the publish actually reaches it (openclaw binds 127.0.0.1 inside the container). Bare = `127.0.0.1:18789`; otherwise accepts `ADDR`, `PORT`, `ADDR:PORT`, `:PORT`, `[::1]:PORT`. |
| `--image REF` | Image ref to run, and to tag when building. Default: `localhost/aetherion:dev`. |
| `--build-image` | Build the image and exit. Does not launch the container. |
| `--build-dir PATH` | Build context directory. Defaults to the Dockerfile bundled with the launcher. |
| `--refresh-layers` | Discard the runtime's build cache for this build (`--no-cache`). Use to refresh anything pinned by an intermediate layer's snapshot: apt mirrors, the Node.js LTS tarball, the system npm install of agent CLIs, the `cursor.com/install` script, hermes-agent's PyPI release, the gopls/dlv `go install`. Without it you stay on whatever was current the first time the layer was built; with it every upstream gets re-fetched. Only meaningful with `--build-image`. |
| `--extract PATH` | Copy the bundled Dockerfile and skeleton/ to PATH and exit. |

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
