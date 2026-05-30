# Baked-in template style guide

Rules every template that ships inside the `aetherion` package follows.
They cover the contract `aetherion` itself relies on (UID, layout, build
args) and the conventions a user expects no matter which baked-in
template a namespace was forked from.

User-defined templates under `~/.aetherion/templates/` aren't bound by
these rules, but most are worth honoring there too.

## Layout

Each template lives at `src/aetherion/data/templates/<name>/` with this
exact shape:

```
<name>/
├── Dockerfile
├── skeleton/              # baked into the image at the matching path
│   ├── etc/...            # system files (apt, profile.d, containers, ...)
│   └── home/aetherion/...  # per-user dotfiles that seed each namespace's $HOME
└── aetherion-src/         # COPY'd into the build; .keep placeholder when empty
```

`skeleton/home/aetherion/` is the seed surface — whatever lands there in
the image gets copied into a namespace's `$HOME` on first launch. Lock
this down to dotfiles; runtime tools belong in `/usr/local/bin` or
`/opt`.

`aetherion-src/` is overlaid with the live repo source when the launcher
runs from a source checkout. Always keep an `aetherion-src/.keep` so the
Dockerfile's `COPY` still resolves in normal package installs.

## Container identity

- User: `aetherion`, UID 1000, GID 1000
- `$HOME`: `/home/aetherion`
- Login shell: `/bin/bash`
- `WORKDIR`: `/home/aetherion`
- Default `CMD`: `["bash"]` (the launcher overrides when the user passes a trailing command)

Useradd recipe:

```dockerfile
RUN useradd --create-home --uid 1000 --shell /bin/bash aetherion
USER aetherion
WORKDIR /home/aetherion
```

These are load-bearing for the launcher: `user_ns_args()`, the namespace
bind mount at `/home/aetherion`, and the `cp -a /home/aetherion/.` seed
step all assume them.

## Build arguments

Every Dockerfile declares:

```dockerfile
ARG AETHERION_SPEC=aetherion
```

The launcher passes one of two values:

- `AETHERION_SPEC=aetherion==<version>` in normal builds (pins to the host launcher's installed version),
- `AETHERION_SPEC=/tmp/aetherion-src` when run from a source checkout (uses the overlaid `aetherion-src/`).

If the template installs the `aetherion` package inside (most should, so
the in-container `conduit` matches the host), feed `AETHERION_SPEC`
straight into `uv tool install` (or pip).

## Default prompt: starship

Every baked-in template installs and configures
[starship](https://starship.rs/). Same install pattern across the board
so the prompt is consistent:

```dockerfile
RUN curl -fsSL https://starship.rs/install.sh \
        | sh -s -- --yes --bin-dir /usr/local/bin \
 && starship --version
```

Plus a config file at `skeleton/home/aetherion/.config/starship.toml`
(reuse the one from the `default` template unless there's a strong
reason to diverge — keep the look project-consistent) and a `.bashrc`
that initializes it:

```bash
eval "$(starship init bash)"
```

## Per-template metadata (`template.yaml`)

Each template may ship a `template.yaml` next to the Dockerfile. The file
is optional — templates without one are treated as universally portable
with no defaults, matching the historical behavior.

```yaml
# Optional. Shown in `aetherion list templates`.
description: "Cursor IDE (Electron) with X11 forwarding into the host."

# Host (OS, arch, runtime) tuples this template supports. `create
# namespace` errors out on unsupported hosts with the list of supported
# combinations. `*` wildcards a field. Omit the whole block to skip the
# check.
platforms:
  - { os: linux, arch: amd64, runtime: podman }
  - { os: linux, arch: amd64, runtime: docker }

# Defaults the launcher writes into the new namespace's config.yaml at
# `create namespace` time. CLI overrides still win. Unknown keys are
# accepted (forward-compat) but silently ignored by older launchers.
defaults:
  display: x11
```

The launcher recognizes these `defaults:` keys today:

- `display`: `x11 | wayland | auto | none`. Sets the namespace's default
  display-forwarding mode at create time.

Add a `template.yaml` whenever the template needs platform validation or
sensible defaults; skip it for portable, defaults-free templates.

## Other conventions worth keeping

- **Layer hygiene**: `apt-get update && apt-get install … && rm -rf /var/lib/apt/lists/*` per layer; no orphaned package caches.
- **Pin upstream installers** by URL/sha when you can, so `--no-cache` rebuilds reproduce. Document anything that's intentionally tracking-latest.
- **System tools** install under `/usr/local/bin` or `/opt/<tool>/`; nothing user-installable belongs in the image.
- **Skeleton dotfiles are frozen at seed** — the README documents this for users. Don't put files in `skeleton/home/aetherion/` that you expect to update post-seed; ship those system-wide instead.
- **License** any third-party content you bake in; document it in the template's directory if it's not obvious from the Dockerfile.
