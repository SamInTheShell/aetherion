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
aetherion                                                    # bootstrap + launch the default namespace
aetherion work                                               # launch into 'work' (must exist)
aetherion work --create                                      # create 'work' from 'default', then launch
aetherion work --create --template cursor-ide                # create from a different template
aetherion work nvim                                          # run nvim instead of an interactive shell
aetherion work --join aetherion-a1b2c3d4                     # exec into an already-running session

aetherion create namespace work                              # explicit creation (without launching)
aetherion create namespace work --template cursor-ide        # pick a different template
aetherion list namespaces                                    # see what's registered
aetherion list sessions                                      # see running containers
aetherion reset namespace work                               # wipe $HOME and re-seed from the image
aetherion rebuild namespace work                             # rebuild from the current buildDir as-is
aetherion rebuild namespace work --refresh-template          # re-fork buildDir from the recorded template
aetherion rebuild namespace work --template python-heavy     # swap to a different template and re-fork
aetherion delete namespace work                              # remove $HOME, build dir, image, config entry
```

`create namespace` does four things in one shot: populates the build dir
from the chosen template (default: the baked-in `default`), builds
`localhost/aetherion:<name>`, seeds `$HOME` by `cp -a`-ing the freshly
built image's `/home/aetherion` out, and registers the namespace in
`~/.aetherion/config.yaml`. First launch into a new namespace is
working-environment-immediately — nvim plugins are already compiled into
the image, agent CLIs are already on `PATH`, and shell history starts
fresh.

Reserved namespace names: `config`, `create`, `delete`, `edit`, `list`,
`rebuild`, `reset` — they're the verbs `aetherion` dispatches on, so
they can't double as namespaces.

### What updates when, and what doesn't

- **System tools (image-managed)**: `aetherion`, `conduit`, every agent CLI, every LSP/DAP, language runtimes. Run `aetherion rebuild namespace <name>` to refresh the image; the next launch picks it up.
- **Namespace contents (your state)**: agent logins, `npm install -g` packages, `go install`-ed binaries, nvim plugins, shell history, anything you `touch`ed inside. Stays put across rebuilds. To reset, use `aetherion reset namespace <name>`.
- **Skeleton dotfiles (frozen at seed)**: `.bashrc`, `.npmrc`, `.config/nvim/`, `.config/starship.toml`. Captured into the namespace at seed time; they don't refresh when the image changes. If a new image ships a `.bashrc` you want, the launcher prints a one-line drift notice suggesting `aetherion reset namespace <name>` — which drops every other namespace customization too, so use with care.

## Templates

A template is a `Dockerfile` + `skeleton/` + `aetherion-src/` bundle that
`create namespace` forks into a namespace's build dir. Two layers
participate, user winning on name collisions:

- **Baked-in** ships inside the package at `src/aetherion/data/templates/<name>/`. Out of the box:
  - `default` — the full dev image (every agent CLI, Neovim+LSPs, language toolchains).
  - `cursor-ide` — Cursor IDE (Electron) with X11 forwarding into the host. The Dockerfile detects host arch at build time and pulls the matching native `linux-x64` or `linux-arm64` AppImage (no emulation on either side), and bundles Firefox-ESR so the OAuth sign-in round-trip stays entirely inside the namespace — no host browser or `cursor://` URL-handler setup required.
- **User-defined** lives at `~/.aetherion/templates/<name>/`. Same shape; you write whatever Dockerfile you want.

A user template of the same name as a baked-in one shadows it; deleting
the user copy unshadows.

### Template metadata (`template.yaml`)

Templates may ship an optional `template.yaml` declaring supported host
platforms and per-namespace defaults. Schema:

```yaml
description: "Cursor IDE (Electron, native amd64/arm64) with X11 forwarding."
platforms:
  - { os: linux, arch: amd64, runtime: podman }
  - { os: linux, arch: amd64, runtime: docker }
  - { os: linux, arch: arm64, runtime: podman }
  - { os: linux, arch: arm64, runtime: docker }
defaults:
  display: x11        # written into the namespace's config.yaml at create
```

`create namespace --template <name>` validates the current host against
`platforms:` and fails with a clear error if the combo isn't supported.
Templates without a `template.yaml` skip the check entirely.

```shell
aetherion list templates                                     # see what's available
aetherion create template my-fork                            # fork from 'default' into ~/.aetherion/templates/my-fork/
aetherion create template my-fork --template cursor-ide      # fork from a specific base
aetherion edit template my-fork                              # open Dockerfile in $EDITOR
aetherion edit template default                              # auto-forks the baked-in 'default' then opens it
aetherion delete template my-fork                            # remove user copy (baked-in stays)
```

`--template` also accepts a git URL with an optional `#REF` (tag, branch,
or commit). The clone is cached at `~/.aetherion/template-cache/<hash>/`
keyed by URL, so subsequent uses just `git fetch` and re-checkout:

```shell
aetherion create namespace experimental --template https://github.com/me/aetherion-template.git
aetherion create namespace pinned       --template https://github.com/me/aetherion-template.git#v1.2.0
aetherion rebuild namespace pinned --refresh-template        # re-fetch + re-fork
```

Each namespace records the template name (or URL) it was forked from in
its `config.yaml` entry, so `list namespaces` shows it and
`rebuild namespace … --refresh-template` knows what to re-resolve.

Template names follow the same character rules as namespace names; no
reserved-word check applies (template names never appear in a verb
dispatch slot).

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
    template: "default"                                     # what was forked at create time
```

`template:` is optional and informational — `list namespaces` displays it
and `rebuild namespace … --refresh-template` re-resolves it. It can be a
local template name or a git URL with `#REF`.

Optional per-namespace fields cover environment, ports, and extra mounts:

```yaml
namespaces:
  work:
    image: "localhost/aetherion:work"
    buildDir: "~/.aetherion/containers/work/"
    template: "https://github.com/me/aetherion-template.git#v1.2.0"
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
| `--display x11\|wayland\|auto\|none` | Override display forwarding for this launch. See **Display forwarding** below for what each mode mounts. |
| `--create` | Create the named namespace if it doesn't exist (then launch). |
| `--template SPEC` | When paired with `--create`, fork from SPEC (local name or git URL[#REF]) instead of `default`. Ignored without `--create`. |
| `--join SESSION` | `exec -it` into a running session (see `aetherion list sessions`). Drops at `bash` unless a trailing command is given. |

`AETHERION_CONTAINER_RUNTIME=docker` overrides runtime auto-detection
(podman is preferred when both are available).

## Display forwarding

GUI namespaces (Cursor, anything else Electron- or X-based) need a way
into the host's display server. Per-namespace setting in `config.yaml`:

```yaml
namespaces:
  cursor-ide:
    display: x11        # x11 | wayland | auto | none
```

Resolution order: `--display` on the launch form wins, then the namespace
YAML, then any `defaults.display` from the source template, then the
built-in `none`.

Modes:

- **`x11`** — mounts `/tmp/.X11-unix`, passes `DISPLAY` and the host's `$XAUTHORITY` (read-only at `~/.Xauthority` inside), adds `--device /dev/dri` when present, and `--ipc host` for Electron's MIT-SHM path. Works on pure X11 hosts and on Wayland hosts via XWayland (which every major Wayland compositor ships).
- **`wayland`** — mounts the host's Wayland socket from `$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY` into `/run/user/1000/$WAYLAND_DISPLAY`, passes `WAYLAND_DISPLAY` and `XDG_RUNTIME_DIR`. Downgrades to `none` with a warning if `$WAYLAND_DISPLAY` isn't set on the host.
- **`auto`** — picks `wayland` when `$WAYLAND_DISPLAY` is set, otherwise `x11` when `$DISPLAY` is set, otherwise `none`. Convenient for namespaces you launch from both graphical and SSH sessions.
- **`none`** — no GUI plumbing. Default when nothing's set.

Both `x11` and `wayland` additionally:

- Mount a UID-1000-writable tmpfs at `/run/user/1000` so apps that drop sockets next to the bus (Cursor's `vscode-*.sock`, gpg-agent, dbus-launch, etc.) don't trip on the EACCES that rootless podman's default subuid-owned stub causes.
- Forward the host's D-Bus session bus (under `/run/user/1000/bus`, with `DBUS_SESSION_BUS_ADDRESS` pointed at it) and the system bus (`/run/dbus/system_bus_socket`) when each is present. Notifications, secret-service, and `xdg-desktop-portal` integrations need the session bus to be reachable.

Containers that ship their own browser (like the `cursor-ide` template,
which bundles Firefox-ESR) can complete OAuth flows entirely
in-namespace and don't depend on the host having `xdg-desktop-portal`
configured.

The baked-in `cursor-ide` template ships `display: x11` in its
`template.yaml` defaults, so a namespace created from it gets X11
forwarding out of the box.

### macOS host caveats

Display forwarding is currently tested on Linux hosts only. On macOS
(podman-machine or Docker Desktop), the container runs inside a Linux
VM and the host-side plumbing the launcher reaches for —
`/tmp/.X11-unix`, `$DISPLAY`, `$WAYLAND_DISPLAY`, `$XDG_RUNTIME_DIR`,
`/run/dbus/system_bus_socket` — resolves against the VM, not macOS
itself. What this means in practice:

- **`display: wayland`** doesn't apply — macOS has no Wayland compositor.
- **`display: x11`** needs [XQuartz](https://www.xquartz.org/) running on
  the host with `$DISPLAY` pointed at the XQuartz socket (typically
  `host.docker.internal:0` reachable from the VM) and `xhost` permissions
  opened up to the VM's address. The launcher does none of that for you
  today.
- **D-Bus forwarding** silently skips — macOS has no session bus on the
  VM side and no `xdg-desktop-portal` to talk to. Anything that would
  rely on host portal integration (system notifications, secret-service
  / keyring, opening a host browser via `xdg-open`) just won't carry.

The `cursor-ide` template ships Firefox-ESR in the image, so once X11
forwarding is set up, the OAuth sign-in round-trip can complete entirely
inside the container without needing the host to handle `cursor://`.
End-to-end macOS testing is still future work; for now, treat it as
"might work if you can get XQuartz to behave."

## Customizing the image

You have two places you can customize an image, depending on whether you
want a one-off tweak to a single namespace or a reusable base for new
namespaces:

**One-off** — each namespace has its own build dir at
`~/.aetherion/containers/<name>/`; edit it in place, then rebuild:

```shell
$EDITOR ~/.aetherion/containers/default/Dockerfile
aetherion rebuild namespace default
```

`rebuild` (without `--refresh-template` or `--template`) leaves your
`Dockerfile` and `skeleton/` edits untouched and only refreshes the
bundled `aetherion-src/` overlay (used by the Dockerfile's
`uv tool install`). Pass `--no-cache` to force every layer to re-fetch.

**Reusable** — make a template you can spawn many namespaces from:

```shell
aetherion create template my-fork              # fork from 'default'
aetherion edit template my-fork                # tweak the Dockerfile
aetherion create namespace work --template my-fork
```

To start a namespace over from the bundled defaults, delete it and
re-create explicitly:

```shell
aetherion delete namespace default --force
aetherion create namespace default
```

(Bare `aetherion` only auto-bootstraps when the config has *no*
namespaces; if other namespaces are registered, you need the explicit
`create namespace`.)

If you've made local edits to a buildDir and want to pull in upstream
changes from the source template,
`aetherion rebuild namespace <name> --refresh-template` re-forks the
buildDir from the template and rebuilds — note this discards your local
edits to `Dockerfile`/`skeleton/`.

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
