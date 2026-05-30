# Creating and managing custom templates

A template is a `Dockerfile` + `skeleton/` + `aetherion-src/` bundle that
`create namespace` forks into a namespace's build dir. Two layers exist, and a
**user template shadows a baked-in one of the same name**:

- **Baked-in** — ship inside the package at
  `src/aetherion/data/templates/<name>/`. Read-only.
- **User-defined** — live at `~/.aetherion/templates/<name>/`. Yours to edit.

This guide covers forking, editing, the `template.yaml` schema, sharing via git,
and choosing a base.

## Quick start

```shell
aetherion create template my-stack                 # fork from 'default'
aetherion create template my-stack nvim            # fork from a specific base
aetherion edit   template my-stack                 # open its Dockerfile in $EDITOR
aetherion create namespace work my-stack           # spawn a namespace from it
aetherion list   templates                         # see baked-in + user templates
aetherion delete template my-stack                 # remove the user copy
```

> The base is an **optional positional**. `aetherion create template NAME [BASE]`
> and `aetherion create namespace NAME [TEMPLATE]` both default to `default` when
> the second argument is omitted. (The older `--template SPEC` flag still works
> as a deprecated alias; the positional wins if both are given.)

## Pick the right base to fork from

Fork from the tier closest to what you want, then add the rest:

| Want… | Fork from | Then add |
| --- | --- | --- |
| A clean room for one tool | `base` | just your tool |
| A multi-language build env | `default` | your deps |
| **An editor in the ecosystem** | **`nvim`** | your deps / config |
| Agents + an editor together | `cli-agents` | layer `nvim`'s editor bits |
| A GUI IDE variant | `cursor-ide` / `vscode-ide` | extensions, settings |

**If you want an editor inside an aetherion namespace, build on the bundled
`nvim` template.** It already wires Neovim 0.11.x with a complete LSP/DAP stack
(rust-analyzer, gopls, pyright, typescript-language-server, lua-language-server,
debugpy, delve, codelldb, js-debug-adapter) on top of the language toolchains,
and it bootstraps plugins on first launch. Forking `nvim` gets you a fully
working editor for free; you only add your project-specific dependencies. Reach
for the GUI IDE templates only when you specifically want an Electron GUI.

## Editing a template

```shell
aetherion edit template my-stack
```

Opens `~/.aetherion/templates/my-stack/Dockerfile` in `$EDITOR` (falls back to
`vi`). Editing a **baked-in** template auto-forks it into your user templates
first, so you always edit a writable copy:

```shell
aetherion edit template nvim         # auto-forks baked-in 'nvim', then opens it
```

### What goes in a template

```
my-stack/
├── Dockerfile        # required
├── skeleton/         # baked into the image at the matching path
│   ├── etc/...                       # system files
│   └── home/aetherion/...            # dotfiles that seed each namespace's $HOME
└── aetherion-src/    # COPY'd into the build; .keep placeholder when empty
```

Follow the conventions in `src/aetherion/data/templates/STYLE.md`:

- **Identity**: user `aetherion`, UID 1000, GID 1000, `$HOME=/home/aetherion`,
  login shell `/bin/bash`, `WORKDIR /home/aetherion`, default `CMD ["bash"]`.
- **Build arg**: declare `ARG AETHERION_SPEC=aetherion`. The launcher passes
  either `aetherion==<version>` (released) or a path to your live `src/` checkout
  (development) so the in-container `aetherion`/`conduit` stay in lockstep.
- **Prompt**: install starship into `/usr/local/bin`, ship
  `skeleton/home/aetherion/.config/starship.toml`, and `eval "$(starship init
  bash)"` from `.bashrc`.
- **Where things live**: system-wide tooling in `/usr/local/bin` or `/opt` so
  image rebuilds reach every namespace; per-user state stays under `$HOME`.

> **Skeleton dotfiles are frozen at seed.** Files under
> `skeleton/home/aetherion/` are copied into the namespace's `$HOME` once, at
> create time. Editing them later (or shipping new ones in a rebuilt image)
> won't update existing namespaces — only `aetherion reset namespace <name>`
> re-seeds, and that drops *all* of that namespace's `$HOME` customizations.
> Keep things you want to update via rebuild in `/usr/local/bin` or `/opt`.

## `template.yaml` (optional metadata)

A template may ship a `template.yaml` declaring supported host platforms and
per-namespace defaults. Omit it entirely and the template is treated as
universally portable with no defaults (its command falls through to the image's
`CMD`).

```yaml
description: "My stack: nvim + extra language servers."   # shown in `list templates`

platforms:                       # host tuples this template supports
  - { os: linux,  arch: amd64, runtime: podman }
  - { os: linux,  arch: arm64, runtime: docker }
  - { os: darwin, arch: arm64, runtime: docker }
  # `*` wildcards any field: { os: "*", arch: "*", runtime: "*" }

defaults:                        # written into the namespace's config.yaml at create time
  display: x11                   # x11 | wayland | auto | none
  command: nvim                  # string (shlex-split) or a list of strings
```

- **`description`** — human-readable; appears in `aetherion list templates`.
- **`platforms`** — `create namespace` validates the current host against this
  list and fails with a clear error if the combo isn't supported. Each field
  accepts `*` as a wildcard. Omit `platforms` to skip the check.
- **`defaults.display`** — initial display mode for namespaces created from this
  template. See [display forwarding](design/aetherion.md#display-forwarding).
- **`defaults.command`** — what bare `aetherion <ns>` runs instead of `bash`.
  A string is `shlex`-split into argv (e.g. `cursor .` → `["cursor", "."]`); a
  list is used verbatim (the way to spell argv elements containing whitespace).
  Note `command:` is **not** shell-expanded — use `.` (the launcher sets the
  container's working directory to your mounted project), not `${PWD}`.

Defaults only apply at create time and only when the user hasn't already set the
field; explicit CLI values always win. Unknown keys are accepted and ignored for
forward compatibility.

## Sharing templates via git

`create namespace` and `create template` accept a git URL (with an optional
`#REF` — tag, branch, or commit) anywhere a template name is accepted:

```shell
aetherion create namespace experimental https://github.com/me/aetherion-template.git
aetherion create namespace pinned       https://github.com/me/aetherion-template.git#v1.2.0
```

The clone is cached at `~/.aetherion/template-cache/<hash>/`, keyed by URL, so
later uses just `git fetch` and re-checkout. Each namespace records the template
name or URL it was forked from, so `list namespaces` shows it and a refresh
knows what to re-resolve:

```shell
aetherion rebuild namespace pinned --refresh-template   # re-fetch + re-fork + rebuild
```

A git-hosted template repo is just the template directory contents at its root
(`Dockerfile`, `skeleton/`, optional `template.yaml`, optional `aetherion-src/`).

## Rebuilding and refreshing

```shell
aetherion rebuild namespace work                     # rebuild from the current buildDir as-is
aetherion rebuild namespace work --no-cache          # force every layer to re-fetch
aetherion rebuild namespace work --refresh-template  # re-fork buildDir from the recorded template (discards local buildDir edits)
aetherion rebuild namespace work --template other    # swap to a different template and re-fork
```

- Plain `rebuild` leaves your `Dockerfile`/`skeleton/` edits in the namespace's
  build dir untouched and only refreshes the bundled `aetherion-src/` overlay.
- `--refresh-template` / `--template` re-fork the build dir from a template,
  **discarding** local edits to that build dir.

## One-off vs. reusable customization

- **One-off** — edit a single namespace's build dir in place and rebuild:
  ```shell
  $EDITOR ~/.aetherion/containers/work/Dockerfile
  aetherion rebuild namespace work
  ```
- **Reusable** — make a template you can spawn many namespaces from (the rest of
  this guide).

## Naming rules

Template names follow the same character rules as namespace names: letters,
digits, dot, underscore, dash; no leading dot. Unlike namespaces, template names
have no reserved-word restriction (they never appear in a verb slot).

## Deleting

```shell
aetherion delete template my-stack            # remove the user copy
```

Deleting a user template that shadowed a baked-in one **unshadows** the baked-in
version. Baked-in templates can't be deleted (there's nothing to remove on the
host side); `delete` warns if any namespace still records the template name.
