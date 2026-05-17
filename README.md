# Aetherion

A containerized development environment for AI coding agents.

Ships a Debian dev container preloaded with the four major agent CLIs (Claude
Code, Cursor Agent, GitHub Copilot CLI, Gemini CLI), Neovim with LSP/DAP
support, podman-in-podman, and toolchains for Python, Node, Go, Rust, and
Ruby. The `aetherion` launcher mounts the current directory at the same path
inside the container and preserves per-agent login state across sessions.

## Install

```shell
uv tool install aetherion
```

(or `pipx install aetherion`)

## Quickstart

```shell
aetherion --build-image    # one-time: build localhost/aetherion:dev
aetherion                  # launch a shell in $PWD
```

## What's in the container

- **Languages & runtimes**: Python (system + uv), Node (via bun), Go, Rust, Ruby, C/C++ toolchain
- **Agent CLIs**: Claude Code, Cursor Agent, GitHub Copilot CLI, Gemini CLI
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

## Flags

| flag | purpose |
| --- | --- |
| `--agents LIST` | Comma-separated subset of agents to expose (default: all). `--agents ''` for none. |
| `-e`, `--env NAME=VALUE` | Set a container environment variable. Repeatable. Quote at the shell for values with spaces: `--env 'NAME=has spaces'`. A bare `--env NAME` inherits from the host environment. |
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
