# Aetherion documentation

Aetherion is a containerized development environment for AI coding agents. It
runs your editors, agent CLIs, and toolchains inside disposable, per-project
**namespaces** (each its own `$HOME`, image, and build dir), and ships a second
CLI, **conduit**, that points agent CLIs at a model server on your host.

## Start here

- **[Quick start](quickstart.md)** — install, bootstrap your first namespace,
  and get an agent or IDE running in a few minutes.

## Guides

- **[Templates](templates.md)** — every built-in template (`base`, `default`,
  `nvim`, `cli-agents`, `vscode-ide`, `cursor-ide`, `zed-ide`,
  `antigravity-ide`), what each ships, and the quick-start commands for each.
- **[Custom templates](custom-templates.md)** — fork, edit, and manage your own
  templates; share them via git; pick the right base to build on.
- **[Security model](security.md)** — what the container boundary gives you and
  where it stops; network, filesystem, and display exposure; platform
  differences (Linux, gVisor, macOS).

## Design

- **[Aetherion design](design/aetherion.md)** — the namespace model, how the
  launcher resolves and runs a container, host-side state layout, display
  forwarding, and runtime detection.
- **[Conduit design](design/conduit.md)** — how conduit bridges agent CLIs to a
  local OpenAI-compatible model server, the per-agent integration strategy, and
  the request-rewriting shim.

## One-screen cheat sheet

```shell
# Bootstrap + launch the default (toolchains-only) namespace
aetherion

# Create + launch a namespace from a specific template
aetherion agents --create cli-agents      # all agent CLIs + conduit
aetherion edit   --create nvim            # Neovim + LSP/DAP stack
aetherion ide    --create cursor-ide      # Cursor IDE (X11 GUI)

# Inside a cli-agents namespace: wire agents at a host model server
conduit set endpoint ollama               # or lmstudio, or an http(s):// URL
conduit launch claude                     # pick a model, launch Claude Code

# Manage namespaces
aetherion list namespaces
aetherion list sessions
aetherion rebuild namespace agents        # refresh the image
aetherion reset   namespace agents        # wipe $HOME, re-seed
aetherion delete  namespace agents        # remove everything
```

State lives under `~/.aetherion/`. Run `aetherion --help` for the full command
surface.
