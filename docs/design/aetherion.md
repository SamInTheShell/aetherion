# Aetherion design

Aetherion is a thin, dependency-light launcher (one runtime dep, `pyyaml`) that
turns a directory of declarative state under `~/.aetherion/` into reproducible,
isolated dev containers. This document explains the model and the moving parts.

## The namespace model

A **namespace** is the unit of isolation. Each one is four things on the host,
tied together by a single entry in `~/.aetherion/config.yaml`:

1. **A `$HOME`** at `~/.aetherion/namespaces/<name>/`, bind-mounted into the
   container as `/home/aetherion`.
2. **A build directory** at `~/.aetherion/containers/<name>/` — the `Dockerfile`
   + `skeleton/` + `aetherion-src/` forked from a template.
3. **An image tag** `localhost/aetherion:<name>`.
4. **A config entry** under `namespaces:` describing how to run it.

Everything that lives under `$HOME` inside the container — agent logins,
runtime-installed npm/go/uv tools, nvim plugins, shell history, dotfile edits —
is just files in the namespace dir and survives across sessions. Two namespaces
share zero state: logging into Claude under `work` doesn't log you in under
`play`, and the `work` image can carry tools the `play` image doesn't.

This split is deliberate:

- **Image-managed** (refresh with `rebuild`): language runtimes, agent CLIs,
  LSP/DAP servers, `aetherion`/`conduit`. System-wide under `/usr/local` and
  `/opt`, so a rebuild reaches every namespace at once.
- **Namespace state** (survives rebuilds): everything you create under `$HOME`.
- **Skeleton dotfiles** (frozen at seed): `.bashrc`, `.config/nvim/`,
  `.config/starship.toml` — copied into `$HOME` once at create time. The
  launcher prints a one-line drift notice when a rebuilt image ships a newer
  skeleton, but won't overwrite your `$HOME`; `reset` re-seeds (and drops other
  customizations).

## Host-side state layout

Everything lives under `~/.aetherion/`:

```
~/.aetherion/
  config.yaml            # namespace registry — the source of truth
  namespaces/<name>/     # bind-mounted as $HOME inside the container
  containers/<name>/     # per-namespace build context (forked from a template)
  templates/<name>/      # user-defined templates (shadow baked-in by name)
  template-cache/<hash>/ # git-cloned template sources, keyed by URL hash
```

`config.yaml` is the source of truth and is safe to hand-edit (`aetherion config`
opens it in `$EDITOR`). A namespace entry:

```yaml
namespaces:
  work:
    image: "localhost/aetherion:work"
    buildDir: "~/.aetherion/containers/work/"
    template: "cursor-ide"            # informational: what was forked; can be a git URL#REF
    display: x11                      # x11 | wayland | auto | none
    command: cursor .                 # string (shlex-split) or list; default when no trailing command
    environment:
      fromMap:   { FOO: BAR }                          # literal values
      fromFile:  { OPENAI_API_KEY: "~/.secrets/oai" }  # value = file contents
      fromEnv:   { GH_TOKEN: GH_TOKEN }                # inherit from host env (rename ok)
    port-forwarding:
      - { hostInterface: "127.0.0.1", hostPort: 8080, containerPort: 5000 }
    volumes:
      - "~/repos/abc"                  # host ~/repos/abc → container ~/repos/abc
      - "~/repos/ZYX:~/repos/xyz"      # rename across the boundary
```

Only `image` and `buildDir` are required. `buildDir` may point anywhere; a custom
location is left alone on `delete` (only the default-location build dir is
auto-removed).

## The command surface

Aetherion has one **launch form** and a set of **verbs**. Anything that isn't a
reserved verb is treated as a namespace to launch.

```
aetherion [NAMESPACE] [COMMAND [ARG...]] [flags]      # launch
aetherion <verb> ...                                   # manage
```

Reserved verbs (can't be namespace names): `config`, `list`, `create`, `edit`,
`reset`, `rebuild`, `delete`.

### Launch

- Bare `aetherion` targets the `default` namespace. With an empty config, it
  **bootstraps** `default` (create + build + seed + enter) on first run.
- `aetherion NAME` launches an existing namespace.
- `aetherion NAME --create [TEMPLATE]` creates `NAME` from `TEMPLATE` (default
  `default`) if missing, then launches.
- `aetherion NAME --join SESSION [CMD]` execs into a running container (see
  `aetherion list sessions`).

**Command resolution** (highest priority first):

1. Positional command after the namespace — `aetherion work bash`.
2. `--command "CMD [ARG...]"` (shlex-split).
3. The namespace's `command:` field (often set from a template default).
4. The image's `CMD` (`bash` for every baked-in template).

**Launch flags** layer on top of the YAML config (additive, for one-offs):

| flag | purpose |
| --- | --- |
| `--image REF` | Use a different image for this launch only. |
| `-e, --env NAME=VALUE` | Add an env var (repeatable). |
| `--forward [ADDR:[HOST_PORT:]]CONTAINER_PORT` | Publish a port (repeatable). |
| `-v, --volume SRC[:DST]` | Mount a host path (repeatable); `~/` in DST anchors at the container `$HOME`. |
| `--forward-openclaw [ADDR][:PORT]` | Publish OpenClaw's gateway (18789) and set up the loopback bridge. |
| `--display x11\|wayland\|auto\|none` | Override display forwarding. |
| `--command "CMD [ARG...]"` | Override the default command. |
| `--create [TEMPLATE]` | Create the namespace if missing (optional template), then launch. |
| `--template SPEC` | Deprecated alias for the `--create` template. |
| `--join SESSION` | `exec -it` into a running session. |

### Verbs

```shell
aetherion config                                    # edit config.yaml in $EDITOR
aetherion list namespaces | sessions | templates
aetherion create namespace NAME [TEMPLATE] [--no-cache]
aetherion create template  NAME [TEMPLATE]
aetherion edit   template  NAME                     # auto-forks baked-in first
aetherion reset  namespace NAME [--force]           # wipe $HOME, re-seed from image
aetherion rebuild namespace NAME [--no-cache] [--refresh-template | --template SPEC]
aetherion delete namespace NAME [NAME...] [--force]
aetherion delete template  NAME [--force]
```

`create namespace` does four things in one shot: populates the build dir from the
chosen template, builds `localhost/aetherion:<name>`, seeds `$HOME` by `cp -a`-ing
the freshly built image's `/home/aetherion` out, and registers the namespace in
`config.yaml`.

## How a launch becomes a container

When you launch a namespace, the launcher:

1. **Resolves the namespace** and loads its config; bootstraps `default` if the
   config is empty.
2. **Resolves the command** (the four-step order above) and **display mode**
   (`--display` → namespace `display:` → template default → `none`).
3. **Assembles the runtime args**: the `$HOME` bind mount, the working-directory
   mount (your current directory, rewritten so `~/foo` on the host lands at
   `~/foo` inside), config + CLI env vars, port publishes, volume mounts, and
   display plumbing.
4. **Runs the container** with the chosen runtime (`<runtime> run --rm -it …`).

The working directory you launch from is mounted into the container and set as
the working dir, so tools that take `.` (like `code .` / `cursor .`) open your
project.

## Templates

A template is the `Dockerfile` + `skeleton/` + `aetherion-src/` bundle that
`create namespace` forks. Resolution checks user templates
(`~/.aetherion/templates/<name>/`) first — they **shadow** baked-in ones — then
the package's baked-in set, then treats the spec as a git URL (with optional
`#REF`) cached under `template-cache/`. See [templates](../templates.md) and
[custom templates](../custom-templates.md) for the full story and the
`template.yaml` schema.

## Display forwarding

GUI namespaces need a path to the host's display server. The mode resolves
`--display` → namespace `display:` → template `defaults.display` → `none`.

On **Linux**:

- **`x11`** — mounts `/tmp/.X11-unix`, passes `DISPLAY` and the host's
  `$XAUTHORITY` (read-only inside), adds `--device /dev/dri` when present and
  `--ipc host` for Electron's MIT-SHM path. Works on X11 and, via XWayland, on
  Wayland hosts.
- **`wayland`** — mounts the host's Wayland socket into `/run/user/1000/` and
  passes `WAYLAND_DISPLAY` + `XDG_RUNTIME_DIR`. Downgrades to `none` with a
  warning if `$WAYLAND_DISPLAY` isn't set.
- **`auto`** — `wayland` if `$WAYLAND_DISPLAY` is set, else `x11` if `$DISPLAY`
  is set, else `none`.
- **`none`** — no GUI plumbing (the default).

Both GUI modes also mount a UID-1000-writable tmpfs at `/run/user/1000` (so apps
that drop sockets next to the bus don't hit EACCES on rootless podman's stub) and
forward the host's D-Bus session + system buses when present.

On **macOS** (Docker Desktop / podman-machine), the container runs in a Linux VM,
so the Linux-host plumbing would resolve against the VM. The launcher detects
darwin and takes a TCP path instead:

- **`x11`** — sets `DISPLAY=host.docker.internal:0`, pointing at
  [XQuartz](https://www.xquartz.org/) over TCP. The launcher makes XQuartz ready
  on each launch: enable the TCP listener (`org.xquartz.X11 nolisten_tcp` →
  `false`), start/restart XQuartz, verify it's actually serving via a real X11
  handshake, then disable access control with **`xhost +`**. (`+localhost` is
  *not* enough — the container's connection arrives from the VM's gateway/NAT
  address, not localhost, and that address changes each run.) If XQuartz isn't
  installed, the launcher halts with a `brew install --cask xquartz` hint. It
  also sets `XDG_RUNTIME_DIR` to an in-container tmpfs so Electron's
  single-instance socket isn't created on the virtiofs `$HOME` (which can't
  `listen()` on a unix socket).
- **`wayland`** — unsupported (no compositor); warns and skips.
- **D-Bus** — skipped on darwin. GUI templates that bundle Firefox (the IDEs)
  still complete OAuth in-namespace without host portals.
- **Performance** — X11-over-TCP through a VM is chatty; expect lag. The IDE
  wrappers detect the no-GPU case and add `--use-gl=angle --use-angle=swiftshader`
  so Chromium uses its bundled in-process software GL rather than failing to
  negotiate GLX through XQuartz.

## Container runtime detection

`AETHERION_CONTAINER_RUNTIME` wins if set; otherwise the launcher scans `PATH`
for `podman` first, then `docker`. If neither is found it exits with a hint.
Rootless podman is the preferred target; docker is supported as a fallback, with
a few runtime-specific differences (user-namespace and host-loopback args).

## Networking to the host

The launcher wires host-loopback networking for both docker and rootless podman,
so the host's `127.0.0.1:<port>` is reachable from inside the container without
reconfiguring services on the host. This is what lets `conduit` reach a model
server you're already running on the host (see [conduit design](conduit.md)).

## Relationship to conduit

`aetherion` and `conduit` ship from one distribution (two console scripts) but
are otherwise independent — they don't call each other or share code beyond the
package build. `conduit` is installed inside the `cli-agents` image and runs
*inside* a namespace, where it reaches the host model server over the loopback
networking the launcher set up. See [conduit design](conduit.md).
