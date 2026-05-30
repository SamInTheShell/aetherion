# Templates

A **template** is a `Dockerfile` + `skeleton/` + `aetherion-src/` bundle that
`create namespace` forks into a namespace's build dir. Aetherion ships six
built-in templates, carved up by responsibility so you only pay for what you
use:

| Template | Tier | Default command | What it's for |
| --- | --- | --- | --- |
| [`base`](#base) | minimal | `bash` | A bare Debian shell + prompt; a clean room for one tool. |
| [`default`](#default) | toolchains | `bash` | Language toolchains (Python, Node, Go, Rust, Ruby, C/C++). Bootstrapped on first run. |
| [`nvim`](#nvim) | editor | `nvim` | `default` + Neovim 0.11.x + a full LSP/DAP stack. |
| [`cli-agents`](#cli-agents) | agents | `bash` | `default` + every vendor agent CLI + `conduit`. |
| [`vscode-ide`](#vscode-ide) | GUI | `code .` | Microsoft VS Code (Electron) with X11 forwarding. |
| [`cursor-ide`](#cursor-ide) | GUI | `cursor .` | Cursor IDE (Electron) with X11 forwarding. |

**Layering principle.** The tiers are *peers*, not a stack. `nvim`, `cli-agents`,
and the two IDEs each build on the same language-toolchain base as `default`;
the IDEs are deliberately lighter (no agents, no LSP servers). Editors and
agents are orthogonal — run them in separate namespaces and switch between them,
or fork a template to combine them (see [custom templates](custom-templates.md)).

All templates share the same identity contract (user `aetherion`, UID 1000,
`$HOME=/home/aetherion`, starship prompt) and support both `amd64` and `arm64`,
on Linux and macOS, with podman or docker.

---

## base

> Minimal aetherion shell: Debian + starship + `lsof`/`netstat`. No toolchains.

```shell
aetherion sandbox --create base      # create 'sandbox' from base, enter a shell
aetherion sandbox                    # re-enter later
```

The smallest template: `debian:trixie-slim` with bash, bash-completion, less,
locales, `lsof`, `net-tools`, curl, ca-certificates, and the starship prompt.
No languages, no editor, no agents.

**Use it when** you want a clean room to install a single tool into without
dragging in language ecosystems, or as the leanest base to fork your own
template from.

---

## default

> The language-toolchain baseline. Bootstrapped automatically on first run.

```shell
aetherion                            # first run bootstraps the 'default' namespace
aetherion build --create default     # or create another namespace from it
```

Built on `debian:trixie-slim`. Ships the mainstream language toolchains and the
tools you need to build software in them:

- **Languages & runtimes**: Python (system + `uv`), Node.js LTS + `bun`, Go,
  Rust (rustup + clippy + rustfmt), Ruby, and a C/C++ toolchain (clang, lld,
  cmake, ninja).
- **Build & CLI tools**: git, git-lfs, make, tmux, ripgrep, fd, fzf, jq, yq,
  socat, openssh-client.
- **Containers**: podman-in-container (podman, fuse-overlayfs, slirp4netns,
  crun) so you can build/run containers from inside a namespace.

It has **no template.yaml**, so it's universally portable and its default
command falls through to `bash`. There is **no editor and no agent CLI** here —
those are in [`nvim`](#nvim) and [`cli-agents`](#cli-agents).

**Use it when** you want a general-purpose multi-language build environment and
will bring your own editor (host editor, or a separate `nvim` namespace).

---

## nvim

> Language toolchain + Neovim 0.11.x + full LSP/DAP stack.

```shell
aetherion edit --create nvim         # create 'edit' from nvim, open Neovim
aetherion edit                       # re-enter (opens Neovim)
aetherion edit bash                  # drop to a shell instead
```

Everything in [`default`](#default), plus Neovim 0.11.x (built from upstream —
Debian's package is too old for current `nvim-lspconfig`) and a complete,
pre-wired LSP/DAP toolset:

- **LSP servers**: `lua-language-server`, `rust-analyzer`, `gopls`, `pyright`,
  `typescript-language-server`, `vim-language-server`.
- **DAP adapters**: `debugpy`, `delve`, `codelldb`, `js-debug-adapter`.
- A full Lua config tree under `~/.config/nvim/` (lazy.nvim plugin spec,
  Telescope, treesitter, nvim-cmp, which-key, a dashboard, themes). Plugins and
  treesitter parsers **bootstrap on first launch** (kept out of the image to
  stay small), so the first `nvim` start in a namespace fetches them once.

`template.yaml` sets `command: nvim`, so bare `aetherion <ns>` opens the editor.
`EDITOR`/`VISUAL` are set to `nvim` so git, sudo, etc. open it too.

**Use it when** you want a full TUI editor that lives inside the aetherion
ecosystem — and as the recommended editor to layer into your own templates.

---

## cli-agents

> Language toolchain + every vendor CLI agent + `conduit`.

```shell
aetherion agents --create cli-agents # create 'agents', enter a shell
conduit set endpoint ollama          # point at a host model server
conduit launch claude                # pick a model, launch Claude Code
```

Everything in [`default`](#default), plus the agent CLIs and the `conduit`
bridge. This is the **only** template that ships `conduit`.

- **Agent CLIs**: Claude Code (`claude`), OpenAI Codex (`codex`), GitHub Copilot
  CLI (`copilot`), Gemini CLI (`gemini`), Pi (`pi`), Cursor Agent (`agent` /
  `cursor-agent`), OpenClaw (`openclaw`), Hermes (`hermes`).
- **conduit**: configures and launches the agents against a local
  OpenAI-compatible model server. conduit has built-in integrations for
  `claude`, `codex`, `copilot`, `pi`, `openclaw`, `hermes`, and `droid`
  (Factory's agent, not installed by default but supported if you add it).
  See [conduit design](design/conduit.md).

Default command is `bash` — agents are CLI tools you invoke in the shell.

**Use it when** you want to run AI coding agents pointed at a local model. Pair
it with a separate `nvim` namespace, or your host editor, for editing.

> **Publishing OpenClaw's gateway.** OpenClaw binds its gateway to `127.0.0.1`
> inside the container, which port-forwarding can't reach directly. Use
> `aetherion agents --forward-openclaw` to publish it and set up the loopback
> bridge in one shot. See the README's "Publishing in-container ports" section.

---

## vscode-ide

> Microsoft VS Code (Electron, native amd64/arm64) with X11 forwarding.

```shell
aetherion ide --create vscode-ide    # create 'ide' from vscode-ide, open VS Code
aetherion ide                        # re-enter (opens `code .`)
aetherion ide bash                   # drop to a shell instead
```

Built on `debian:stable-slim`. A GUI tier focused on running VS Code with an X11
GUI forwarded to your host — **not** a superset of `default` (no agents, no LSP
servers bundled; install extensions inside instead).

- VS Code from Microsoft's signed apt repo (native per-arch).
- Electron/Chromium runtime libs, X11 client libs, fonts.
- Firefox-ESR so GitHub OAuth sign-in completes inside the namespace
  (`vscode://` callbacks route back to the in-container Code).

`template.yaml` sets `display: x11` and `command: code .`, so a namespace gets
X11 forwarding and opens VS Code on your mounted project directory.

**Use it when** you want VS Code with namespace isolation. Works on Linux and on
macOS via XQuartz (see [display forwarding](design/aetherion.md#display-forwarding)).

---

## cursor-ide

> Cursor IDE (Electron, native amd64/arm64) with X11 forwarding.

```shell
aetherion ide --create cursor-ide    # create 'ide' from cursor-ide, open Cursor
aetherion ide                        # re-enter (opens `cursor .`)
aetherion ide bash                   # drop to a shell instead
```

Structurally identical to [`vscode-ide`](#vscode-ide), but ships the Cursor
AppImage (extracted at build time; the FUSE mount is fragile in rootless
containers) instead of VS Code. Same Electron/X11 plumbing and bundled
Firefox-ESR for `cursor://` OAuth callbacks.

`template.yaml` sets `display: x11` and `command: cursor .`.

**Use it when** you want Cursor's agentic IDE with namespace isolation. Same
Linux/macOS support and the same XQuartz path on macOS as `vscode-ide`.

---

## Listing and choosing

```shell
aetherion list templates             # baked-in + user templates, with status
```

To build on any of these — combine an editor with agents, pin tool versions, add
your own dependencies — see **[Custom templates](custom-templates.md)**.
