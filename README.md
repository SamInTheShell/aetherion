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
independent **namespaces** (each its own `$HOME`, its own image, its own
build dir) let you keep separate identities, model setups, or
experiments side by side without cross-talk. A second CLI, `conduit`,
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
aetherion                                # first run bootstraps the 'default'
                                         # namespace (writes ~/.aetherion/config.yaml,
                                         # populates the build dir, builds the
                                         # image, seeds $HOME, then enters)
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
enough — no bridge needed. For permanent setups, declare the same in the
namespace's `port-forwarding:` block in `~/.aetherion/config.yaml`.

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

A namespace is a single unit composed of four things on the host:

- a `$HOME` at `~/.aetherion/namespaces/<name>/` (bind-mounted into the container),
- a build directory at `~/.aetherion/containers/<name>/` (Dockerfile + skeleton),
- an image tag `localhost/aetherion:<name>`,
- and an entry under `namespaces:` in `~/.aetherion/config.yaml` tying them together.

Everything that lives under `$HOME` inside the container — agent logins,
runtime-installed npm/go/uv tools, nvim plugins once you've launched
nvim, shell history, dotfile edits — is just files in the namespace dir
and survives across sessions. Two namespaces share zero state: logging
into Claude under `work` doesn't log you in under `play`, and the
`work` image can carry tools the `play` image doesn't.

```shell
aetherion                                   # bootstrap + launch the default namespace
aetherion work                              # launch into 'work' (must exist)
aetherion work --create                     # create 'work' on the fly, then launch
aetherion work nvim                         # run nvim instead of an interactive shell
aetherion work --join aetherion-a1b2c3d4    # exec into an already-running session

aetherion create namespace work             # explicit creation (without launching)
aetherion list namespaces                   # see what's registered
aetherion list sessions                     # see running containers
aetherion reset namespace work              # wipe $HOME and re-seed from the image
aetherion rebuild namespace work            # rebuild the namespace's image
aetherion delete namespace work             # remove $HOME, build dir, image, config entry
```

`create namespace` does four things in one shot: populates the build dir
from the bundled Dockerfile + skeleton, builds
`localhost/aetherion:<name>`, seeds `$HOME` by `cp -a`-ing the freshly
built image's `/home/aetherion` out, and registers the namespace in
`~/.aetherion/config.yaml`. First launch into a new namespace is
working-environment-immediately — nvim plugins are already compiled into
the image, agent CLIs are already on `PATH`, and shell history starts
fresh.

Reserved namespace names: `config`, `list`, `create`, `reset`, `rebuild`,
`delete` — they're the verbs `aetherion` dispatches on, so they can't
double as namespaces.

### What updates when, and what doesn't

- **System tools (image-managed)**: `aetherion`, `conduit`, every agent CLI, every LSP/DAP, language runtimes. Run `aetherion rebuild namespace <name>` to refresh the image; the next launch picks it up.
- **Namespace contents (your state)**: agent logins, `npm install -g` packages, `go install`-ed binaries, nvim plugins, shell history, anything you `touch`ed inside. Stays put across rebuilds. To reset, use `aetherion reset namespace <name>`.
- **Skeleton dotfiles (frozen at seed)**: `.bashrc`, `.npmrc`, `.config/nvim/`, `.config/starship.toml`. Captured into the namespace at seed time; they don't refresh when the image changes. If a new image ships a `.bashrc` you want, the launcher prints a one-line drift notice suggesting `aetherion reset namespace <name>` — which drops every other namespace customization too, so use with care.

## Configuration

`~/.aetherion/config.yaml` is the source of truth for every namespace. The
launcher writes a minimal version on first run; edit it yourself or open
it via `aetherion config` (uses `$EDITOR`, falls back to `vi`).

A minimal namespace declaration is just an image tag and a build dir:

```yaml
namespaces:
  default:
    image: "localhost/aetherion:default"
    buildDir: "~/.aetherion/containers/default/"
```

Optional per-namespace fields cover environment, ports, and extra mounts:

```yaml
namespaces:
  work:
    image: "localhost/aetherion:work"
    buildDir: "~/.aetherion/containers/work/"
    environment:
      fromMap:
        FOO: BAR                                          # literal value
      fromFile:
        OPENAI_API_KEY: "~/.aetherion/secrets/openai"     # value = file contents
      fromEnv:
        GH_TOKEN: GH_TOKEN                                # inherit host env (rename ok)
    port-forwarding:
      - hostInterface: "127.0.0.1"
        hostPort: 8080
        containerPort: 5000
    volumes:
      - "~/repos/abc"                       # host ~/repos/abc → container ~/repos/abc
      - "~/repos/ZYX:~/repos/xyz"           # rename: host ~/repos/ZYX → container ~/repos/xyz
```

`buildDir:` may point anywhere; it doesn't have to live under
`~/.aetherion/containers/`. Custom paths are left alone on
`delete namespace` (only the default-location build dir is auto-removed).

CLI flags on the launch form layer on top of the YAML config — they don't
replace it. Use them for one-offs:

| flag | purpose |
| --- | --- |
| `--image REF` | Use a different image for this launch only (overrides the namespace's `image:`). |
| `-e`, `--env NAME=VALUE` | Add one env var (repeatable). Bare `--env NAME` inherits from the host. |
| `--forward [ADDR:[HOST_PORT:]]CONTAINER_PORT` | Publish a port (repeatable). Forms: `PORT`, `HOST:CONTAINER`, `ADDR:HOST:CONTAINER`, `:HOST:CONTAINER`, `[::1]:HOST:CONTAINER`. |
| `-v`, `--volume SRC[:DST]` | Mount a host path (repeatable). DST defaults to SRC; `~/` in DST anchors at the container's `$HOME` (`/home/aetherion`). |
| `--forward-openclaw [ADDR][:PORT]` | OpenClaw convenience — publishes container port 18789 *and* sets up the loopback bridge required to reach it. Bare = `127.0.0.1:18789`. |
| `--create` | Create the named namespace if it doesn't exist (then launch). |
| `--join SESSION` | `exec -it` into a running session (see `aetherion list sessions`). Drops at `bash` unless a trailing command is given. |

`AETHERION_CONTAINER_RUNTIME=docker` overrides runtime auto-detection
(podman is preferred when both are available).

## Customizing the image

Each namespace has its own build context — edit it in place, then
rebuild:

```shell
$EDITOR ~/.aetherion/containers/default/Dockerfile
aetherion rebuild namespace default
```

`rebuild` leaves your `Dockerfile` and `skeleton/` edits untouched and
only refreshes the bundled `aetherion-src/` overlay (used by the
Dockerfile's `uv tool install`). Pass `--no-cache` to force every layer
to re-fetch (e.g. when an apt mirror or upstream installer is pinned by
a cached layer). To start over from the bundled defaults, delete the
namespace and re-create it:

```shell
aetherion delete namespace default
aetherion                                   # bootstraps fresh
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

When `aetherion` runs from a source checkout, `create namespace` and
`rebuild namespace` overlay your live `src/` tree into the namespace's
build dir so in-progress edits flow into the next image build without a
PyPI publish.
