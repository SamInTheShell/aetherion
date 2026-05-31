# Aetherion

A containerized development environment for AI coding agents.

Aetherion runs your editors, agent CLIs, and toolchains inside disposable,
per-project **namespaces** — each its own `$HOME`, image, and build dir, so you
can keep separate identities, model setups, or experiments side by side without
cross-talk. The `aetherion` launcher mounts your current directory into the
container and bind-mounts a host directory as `$HOME`, so agent logins, shell
history, and per-user tool state persist across sessions. System tooling lives
in the image (under `/opt` and `/usr/local`), so an image rebuild delivers new
versions to a namespace immediately.

Functionality is **carved into templates** by responsibility, so you only pay
for what you use:

- **`base`** — a bare Debian shell + prompt.
- **`default`** — language toolchains (Python, Node, Go, Rust, Ruby, C/C++),
  plus podman-in-container, `uv`, `bun`. Bootstrapped on first run.
- **`nvim`** — `default` + Neovim 0.11.x + a full LSP/DAP stack.
- **`cli-agents`** — `default` + every vendor agent CLI (Claude Code, Codex,
  Copilot, Gemini, Pi, Cursor Agent, OpenClaw, Hermes) + the `conduit` bridge.
- **`vscode-ide`** / **`cursor-ide`** — GUI IDEs (Electron) with X11 forwarding.
- **`zed-ide`** — Zed (native Rust GPU editor) with X11 forwarding; ACP-ready.
- **`antigravity-ide`** — Google Antigravity (Electron VS Code fork + bundled
  Cascade Gemini agent) with X11 forwarding.

A second CLI, **`conduit`** (shipped in the `cli-agents` template), points the
agents at a model server running on your host (Ollama, LM Studio, or any
OpenAI-compatible endpoint).

## Documentation

Full guides live in [`docs/`](docs/):

- [Quick start](docs/quickstart.md) — install and get an agent or IDE running.
- [Templates](docs/templates.md) — every built-in template and its quick-start.
- [Custom templates](docs/custom-templates.md) — build and manage your own.
- [Design: aetherion](docs/design/aetherion.md) · [Design: conduit](docs/design/conduit.md).

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

The `default` namespace is a language-toolchain baseline — no editor, no agent
CLIs. To run AI agents, create a namespace from the `cli-agents` template (which
ships the agents and `conduit`):

```shell
aetherion agents --create cli-agents     # create 'agents' from cli-agents, enter it
```

Then, inside that container, point agents at your host's model server:

```shell
conduit set endpoint lmstudio   # or `ollama`, or a full http(s):// URL
conduit launch pi               # pick a model in the TUI; pi launches against it
```

See the [quick start guide](docs/quickstart.md) for the editor (`nvim`) and GUI
IDE (`cursor-ide` / `vscode-ide` / `zed-ide` / `antigravity-ide`) paths.

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

Each template ships a different set of tooling, installed system-wide (under
`/usr/local/bin` and `/opt`) so it's shared across the namespace and tracks the
image version automatically. Per-user state — agent logins, npm globals, runtime
`go install` binaries, nvim plugins, shell history — lives in the namespace's
`$HOME`. By template:

- **`default`** (shared base of `nvim`/`cli-agents`): Python (system + uv),
  Node.js LTS + bun, Go, Rust, Ruby, C/C++ toolchain; podman-in-container; git,
  tmux, starship, ripgrep, fd, fzf, jq, yq, socat, openssh-client.
- **`nvim`** adds Neovim 0.11.x with bundled LSPs (`pyright`, `gopls`,
  `rust-analyzer`, `lua-language-server`, `typescript-language-server`,
  `vim-language-server`) and DAPs (`debugpy`, `delve`, `codelldb`,
  `js-debug-adapter`). Plugins (`Lazy.nvim`-managed) auto-install on first
  `nvim` launch.
- **`cli-agents`** adds the agent CLIs — Claude Code, OpenAI Codex, GitHub
  Copilot CLI, Gemini CLI, Pi, Cursor Agent (`agent`), OpenClaw, Hermes — plus
  the `conduit` bridge.
- **`vscode-ide`** / **`cursor-ide`** ship the IDE (Electron), X11 client libs,
  and Firefox-ESR for in-namespace OAuth — a lighter image without the agents or
  LSP servers.
- **`zed-ide`** ships Zed (native Rust, Vulkan), Mesa lavapipe for the no-GPU
  fallback path, X11 client libs, and Firefox-ESR for in-namespace OAuth.
  Defaults: telemetry off, sign-in button hidden, auto-update disabled (the
  install is image-managed); panels arranged as `files | editor | chat` with
  the project tree pinned left and the agent / git / outline panels pinned
  right.
- **`antigravity-ide`** ships Google Antigravity (Electron, VS Code fork)
  from Google's signed apt repo, the same Electron/X11 runtime as
  `vscode-ide`, `libsecret-1-0` for credential keyring, and Firefox-ESR for
  in-namespace OAuth. Defaults: telemetry off, auto-update + extension
  auto-update disabled. The Cascade Gemini agent is in-tree; sign-in needs a
  Google account.

See [docs/templates.md](docs/templates.md) for the complete breakdown.

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
aetherion work --create cursor-ide                           # create from a different template
aetherion work bash                                          # run a command instead of the default
aetherion work --join aetherion-a1b2c3d4                     # exec into an already-running session

aetherion create namespace work                              # explicit creation (without launching)
aetherion create namespace work cursor-ide                   # pick a different template
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

- **Baked-in** ship inside the package at `src/aetherion/data/templates/<name>/`.
  Out of the box: `base`, `default`, `nvim`, `cli-agents`, `vscode-ide`,
  `cursor-ide`, `zed-ide`, and `antigravity-ide`. Each is documented in full —
  with what it ships and its quick-start commands — in
  [docs/templates.md](docs/templates.md). They're carved up by responsibility
  (toolchains / editor / agents / GUI), and the tiers are peers rather than a
  single stack, so a namespace only carries what it needs.
- **User-defined** live at `~/.aetherion/templates/<name>/`. Same shape; you
  write whatever Dockerfile you want. See
  [docs/custom-templates.md](docs/custom-templates.md).

A user template of the same name as a baked-in one shadows it; deleting
the user copy unshadows.

### Template metadata (`template.yaml`)

Templates may ship an optional `template.yaml` declaring supported host
platforms and per-namespace defaults. Schema:

```yaml
description: "Cursor IDE (Electron, native amd64/arm64) with X11 forwarding."
platforms:
  - { os: linux,  arch: amd64, runtime: podman }
  - { os: linux,  arch: amd64, runtime: docker }
  - { os: linux,  arch: arm64, runtime: podman }
  - { os: linux,  arch: arm64, runtime: docker }
  - { os: darwin, arch: amd64, runtime: docker }
  - { os: darwin, arch: amd64, runtime: podman }
  - { os: darwin, arch: arm64, runtime: docker }
  - { os: darwin, arch: arm64, runtime: podman }
defaults:
  display: x11        # written into the namespace's config.yaml at create
  command: cursor .   # bare `aetherion <ns>` runs `cursor .` instead of bash
```

`defaults.command` accepts either a string (shlex-split into argv) or a
list of strings (used verbatim, the way to spell argv elements that
contain whitespace). It is **not** shell-expanded — use `.` (the launcher sets
the container's working directory to your mounted project), not `${PWD}`. Unset
⇒ the launcher falls through to the image's `CMD` (bash for every baked-in
template).

`create namespace NAME <template>` validates the current host against
`platforms:` and fails with a clear error if the combo isn't supported.
Templates without a `template.yaml` skip the check entirely.

```shell
aetherion list templates                                     # see what's available
aetherion create template my-fork                            # fork from 'default' into ~/.aetherion/templates/my-fork/
aetherion create template my-fork cursor-ide                 # fork from a specific base
aetherion edit template my-fork                              # open Dockerfile in $EDITOR
aetherion edit template default                              # auto-forks the baked-in 'default' then opens it
aetherion delete template my-fork                            # remove user copy (baked-in stays)
```

The template argument also accepts a git URL with an optional `#REF` (tag,
branch, or commit). The clone is cached at `~/.aetherion/template-cache/<hash>/`
keyed by URL, so subsequent uses just `git fetch` and re-checkout:

```shell
aetherion create namespace experimental https://github.com/me/aetherion-template.git
aetherion create namespace pinned       https://github.com/me/aetherion-template.git#v1.2.0
aetherion rebuild namespace pinned --refresh-template        # re-fetch + re-fork
```

(The older `--template SPEC` flag still works everywhere as a deprecated alias;
the positional wins if both are given.)

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
    command: cursor .                                     # default when no trailing command; string (shlex-split) or list
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
| `--command "CMD [ARG...]"` | Override the namespace's default command (shlex-split into argv). Resolution: positional `COMMAND [ARG...]` after the namespace wins first, then `--command`, then the namespace YAML's `command:` field, then the image's `CMD`. |
| `--create [TEMPLATE]` | Create the named namespace if it doesn't exist (then launch). Optionally fork from `TEMPLATE` (local name or git URL[#REF]); defaults to `default`. |
| `--template SPEC` | Deprecated alias for the `--create` template (the positional after `--create` is preferred). Ignored without `--create`. |
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

- **`x11`** — mounts `/tmp/.X11-unix`, passes `DISPLAY` and the host's `$XAUTHORITY` (read-only at `~/.Xauthority` inside), adds `--device /dev/dri` when present, and `--ipc host` for Electron's MIT-SHM path. Works on pure X11 hosts and on Wayland hosts via XWayland (which every major Wayland compositor ships). On macOS, switches to a TCP path against XQuartz — see the macOS caveats below.
- **`wayland`** — mounts the host's Wayland socket from `$XDG_RUNTIME_DIR/$WAYLAND_DISPLAY` into `/run/user/1000/$WAYLAND_DISPLAY`, passes `WAYLAND_DISPLAY` and `XDG_RUNTIME_DIR`. Downgrades to `none` with a warning if `$WAYLAND_DISPLAY` isn't set on the host.
- **`auto`** — picks `wayland` when `$WAYLAND_DISPLAY` is set, otherwise `x11` when `$DISPLAY` is set, otherwise `none` on Linux. On macOS, always picks `x11` (XQuartz is the only display backend). Convenient for namespaces you launch from both graphical and SSH sessions.
- **`none`** — no GUI plumbing. Default when nothing's set.

Both `x11` and `wayland` additionally:

- Mount a UID-1000-writable tmpfs at `/run/user/1000` so apps that drop sockets next to the bus (Cursor's `vscode-*.sock`, gpg-agent, dbus-launch, etc.) don't trip on the EACCES that rootless podman's default subuid-owned stub causes.
- Forward the host's D-Bus session bus (under `/run/user/1000/bus`, with `DBUS_SESSION_BUS_ADDRESS` pointed at it) and the system bus (`/run/dbus/system_bus_socket`) when each is present. Notifications, secret-service, and `xdg-desktop-portal` integrations need the session bus to be reachable.

Containers that ship their own browser (like the `cursor-ide` template,
which bundles Firefox-ESR) can complete OAuth flows entirely
in-namespace and don't depend on the host having `xdg-desktop-portal`
configured.

The baked-in `cursor-ide` template ships `display: x11` and
`command: cursor .` in its `template.yaml` defaults, so a namespace
created from it gets X11 forwarding *and* opens Cursor on the mounted
project directory at launch without any per-namespace config edits.

### macOS host caveats

On macOS (Docker Desktop or podman-machine) the container runs inside a
Linux VM, so the Linux-host plumbing the launcher normally mounts —
`/tmp/.X11-unix`, `$XAUTHORITY`, the D-Bus buses, `/dev/dri` — would
resolve against the VM, not macOS itself. The launcher detects darwin
hosts and takes a separate code path:

- **`display: x11`** — sets `DISPLAY=host.docker.internal:0` inside the
  container, pointing at [XQuartz](https://www.xquartz.org/) on the
  macOS host over TCP. No socket or D-Bus mounts. The only thing you
  have to set up yourself is XQuartz itself:
  ```sh
  brew install --cask xquartz       # Homebrew: https://brew.sh
  ```
  Everything else — flipping `org.xquartz.X11 nolisten_tcp` to enable
  the TCP listener, restarting XQuartz, waiting for it to actually
  answer X11, and disabling access control with `xhost +` to authorize
  the container — the launcher does on each `--display x11` (or `auto`
  on darwin) launch. (`xhost +localhost` isn't enough: the container's
  connection arrives from the VM's gateway/NAT address, not localhost,
  and that address changes each run.) It's idempotent: a no-op when
  things are already set up, a one-line stderr note for each step it
  actually has to take. If XQuartz isn't installed, the launcher halts
  with a `brew install --cask xquartz` hint instead of dropping you
  into a doomed cursor session. The launcher also sets `XDG_RUNTIME_DIR`
  to an in-container tmpfs so Cursor's single-instance socket lands off
  the virtiofs `$HOME` (which can't `listen()` on a unix socket).
- **`display: wayland`** — not supported (macOS has no Wayland
  compositor); the launcher warns and skips forwarding.
- **D-Bus forwarding** — also skipped on darwin. Notifications,
  secret-service / keyring, and `xdg-desktop-portal` integrations
  silently don't carry. The `cursor-ide` template ships Firefox-ESR in
  the image, so OAuth sign-in still works end-to-end without any host
  portal integration.
- **Performance** — X11-over-TCP through XQuartz is *chatty* by
  design: every primitive crosses the Docker/podman VM boundary, and
  Electron apps in particular emit many small X requests per paint.
  Cursor will render but feel laggy on hover, scroll, and completion
  popups. The cursor wrapper detects the no-GPU case (`/dev/dri`
  absent, which is always true on macOS) and adds
  `--use-gl=angle --use-angle=swiftshader` so Chromium uses its
  bundled in-process software GL via ANGLE instead of trying to
  negotiate fbConfigs through XQuartz's GLX (which fails on macOS
  and aborts the renderer with exit code 5 — and the older
  `--use-gl=swiftshader` form, removed in Chromium 109+, asserts
  with a Trace/breakpoint trap during GL backend init). Linux
  hosts with `/dev/dri` keep the real GPU path. The SwiftShader
  fallback is the floor of "works"; further optimization is
  future work.

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
