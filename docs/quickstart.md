# Quick start

This guide gets you from zero to a working aetherion namespace — with an agent
CLI or a GUI IDE — in a few minutes.

## Prerequisites

- **A container runtime**: [podman](https://podman.io/) (preferred) or
  [docker](https://www.docker.com/). Aetherion auto-detects whichever is on
  your `PATH`; set `AETHERION_CONTAINER_RUNTIME=docker` to force one.
- **Python 3.13+** to install the CLI (or use `uv`/`pipx`, which manage their
  own).
- **macOS only, for GUI templates**: [XQuartz](https://www.xquartz.org/)
  (`brew install --cask xquartz`). See [GUI on macOS](#gui-templates-on-macos).

## Install

```shell
uv tool install aetherion
```

(or `pipx install aetherion`). Both `aetherion` and `conduit` are installed.

Upgrade later with `uv tool upgrade aetherion`.

## 1. Bootstrap the default namespace

```shell
aetherion
```

On first run with an empty config, this bootstraps the `default` namespace: it
writes `~/.aetherion/config.yaml`, populates a build dir, builds the image,
seeds `$HOME`, and drops you into a shell.

The `default` template is a **language-toolchain baseline** — Python, Node, Go,
Rust, Ruby, a C/C++ toolchain, plus podman-in-container, `uv`, and `bun`. It has
**no editor and no agent CLIs**; those live in dedicated templates below. (If
you just want a clean Debian shell with nothing but a prompt, use the `base`
template instead.)

## 2a. Run AI agents (cli-agents template)

The agent CLIs and `conduit` ship in the `cli-agents` template. Create a
namespace from it and launch:

```shell
aetherion agents --create cli-agents
```

This creates a namespace called `agents` (from the `cli-agents` template), builds
its image, and enters a shell. Inside, point conduit at a model server running
on your **host** and launch an agent:

```shell
conduit set endpoint ollama        # → host's :11434
# conduit set endpoint lmstudio    # → host's :1234
# conduit set endpoint https://my.example   # any OpenAI-compatible /v1
conduit launch claude              # arrow-key model picker, then Claude Code
```

The container does no inference — it's a dev environment. Run your model server
(Ollama, LM Studio, vLLM, llama.cpp's `llama-server`, anything exposing
OpenAI-compatible `/v1/models`) on the host; the launcher wires host loopback so
the host's `127.0.0.1:<port>` is reachable from inside the container for both
docker and rootless podman. See [conduit design](design/conduit.md) for the full
list of supported agents and how each is wired.

## 2b. Run an editor in the ecosystem (nvim template)

If you want an editor that lives *inside* the aetherion ecosystem — same
namespace isolation, same toolchains — use the `nvim` template. It ships Neovim
0.11.x with a full LSP/DAP stack and opens straight into the editor:

```shell
aetherion edit --create nvim       # creates 'edit' from nvim, opens Neovim
aetherion edit bash                # drop to a shell instead
```

`nvim` is also the recommended editor when you build your own templates — see
[custom templates](custom-templates.md).

## 2c. Run a GUI IDE (cursor-ide / vscode-ide / zed-ide)

```shell
aetherion ide --create cursor-ide  # or vscode-ide, or zed-ide
```

These forward an X11 GUI to your host display and open the IDE on your mounted
project directory (`cursor .` / `code .` / `zed .`). The Electron IDEs
(`cursor-ide`, `vscode-ide`) bundle Firefox so sign-in/OAuth completes entirely
inside the namespace. `zed-ide` ships with telemetry off, the sign-in button
hidden, and a `files | editor | chat` three-column panel layout out of the box.

### GUI templates on macOS

The container runs inside a Linux VM, so GUI templates talk to **XQuartz** over
TCP. Install it once:

```shell
brew install --cask xquartz
```

The launcher handles the rest on each launch — enabling XQuartz's TCP listener,
starting it, and disabling X access control (`xhost +`) so the container's
connection (which arrives from the VM's address, not localhost) is accepted. If
XQuartz isn't installed, the launcher stops with an install hint rather than
dropping you into a doomed session. Expect some lag: X11-over-TCP through a VM is
chatty. See [aetherion design → display forwarding](design/aetherion.md#display-forwarding)
for details.

## 3. Day-to-day commands

```shell
aetherion agents                   # re-enter the 'agents' namespace
aetherion agents nvim              # run a one-off command instead of the default
aetherion agents --join SESSION    # exec into an already-running session
aetherion list namespaces          # what's registered (name, image, template)
aetherion list sessions            # running aetherion containers
aetherion config                   # edit ~/.aetherion/config.yaml in $EDITOR
```

When a new aetherion version ships new system tooling, refresh a namespace's
image:

```shell
aetherion rebuild namespace agents
```

To wipe a namespace's `$HOME` back to the image defaults (drops agent logins,
shell history, runtime-installed tools):

```shell
aetherion reset namespace agents
```

## What persists, what doesn't

- **Image-managed (refresh with `rebuild`)**: agent CLIs, LSP/DAP servers,
  language runtimes, `aetherion`/`conduit` themselves.
- **Your state (survives rebuilds)**: agent logins, `npm install -g` packages,
  `go install` binaries, nvim plugins, shell history — everything under `$HOME`.
- **Skeleton dotfiles (frozen at seed)**: `.bashrc`, `.config/nvim/`,
  `.config/starship.toml`. Captured once at create time; `reset` re-seeds them.

## Next steps

- [Templates](templates.md) — full rundown of every built-in template.
- [Custom templates](custom-templates.md) — build your own.
- [Aetherion design](design/aetherion.md) / [Conduit design](design/conduit.md).
