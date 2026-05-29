#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime
import errno
import importlib.metadata
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_IMAGE = "localhost/aetherion:dev"
CONTAINER_HOME = "/home/aetherion"

# Port openclaw's gateway binds to inside the container. Fixed by openclaw
# itself — we only control how (and whether) it's published to the host.
OPENCLAW_GATEWAY_PORT = 18789

# When a `--forward-<agent>` alias needs to bridge an in-container loopback
# service, the socat bridge has to listen on a port DIFFERENT from the
# service's real port. Services (e.g. openclaw) do their own port-availability
# probes at startup — including transient wildcard binds — and a specific
# bind to <eth0>:<service_port> by us makes those probes fail with EADDRINUSE.
# By picking bridge_port = service_port + 40000 we sidestep the collision
# entirely. `-p` then forwards the host's chosen port to bridge_port, and
# socat fans out to 127.0.0.1:service_port internally.
BRIDGE_PORT_OFFSET = 40000


def _bridge_port_for(service_port: int) -> int:
    bp = service_port + BRIDGE_PORT_OFFSET
    if bp > 65535:
        # Fold back if the service port itself is already high.
        bp = service_port - 10000
    if not (1 <= bp <= 65535):
        raise ValueError(
            f"could not compute a bridge port for service port {service_port}"
        )
    return bp

# Files shipped alongside the launcher that together form the docker build
# context. Order is purely cosmetic (used in log output). `aetherion-src/`
# is a placeholder directory that the Dockerfile COPYs in; the launcher
# overlays it with the live repo contents when building from a checkout,
# so `uv tool install /tmp/aetherion-src` inside the build picks up local
# edits without a PyPI publish. In installed-mode builds the dir stays
# empty (its only payload is a .keep file) and the Dockerfile's default
# AETHERION_SPEC=aetherion installs the published wheel instead.
BUNDLED_ASSETS: tuple[str, ...] = (
    "Dockerfile",
    "skeleton",
    "aetherion-src",
)

# Namespace layout: the entire container $HOME is bind-mounted from one
# host directory per namespace under ~/.aetherion/namespaces/<name>/. The
# first time a namespace is used we seed it by `<runtime> cp`-ing
# /home/aetherion out of the image; subsequent launches reuse the saved
# tree as-is. There is no per-path tracking and no exit-time extraction.
# Multiple namespaces are independent — each is a complete $HOME snapshot,
# so an agent logged in under one namespace is not logged in under another.
NAMESPACES_DIRNAME = "namespaces"
DEFAULT_NAMESPACE = "default"
LEGACY_DATA_DIRNAME = "data"
# Lives inside each namespace dir. Stores the content-digest ID of the
# image the namespace was seeded from, so we can warn on drift when the
# launcher / image is upgraded but the namespace is still pinned to the
# prior baseline.
IMAGE_ID_STAMP = ".aetherion/image-id"

# Anything other than letters, digits, dot, underscore, dash trips a path
# traversal or shell-surprise risk (`..`, `/`, leading `-`, whitespace).
# Leading dot is rejected on top of that so the namespace dir doesn't
# pretend to be a dotfile under ~/.aetherion/namespaces/.
_NAMESPACE_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]*$")


def _is_valid_namespace_name(name: str) -> bool:
    return bool(_NAMESPACE_NAME_RE.fullmatch(name))


def _detect_runtime() -> str:
    override = os.environ.get("AETHERION_CONTAINER_RUNTIME")
    if override:
        return override
    for candidate in ("podman", "docker"):
        if shutil.which(candidate):
            return candidate
    sys.stderr.write(
        "aetherion: container runtime detection failed "
        "(tried podman and docker, neither found in PATH).\n"
        "Set AETHERION_CONTAINER_RUNTIME to override.\n"
    )
    raise SystemExit(2)


# Container runtime: env var overrides; auto-detect prefers podman over docker.
CONTAINER_RUNTIME = _detect_runtime()
# Match against the basename so a full path (e.g. /usr/bin/docker) still works.
_RUNTIME_IS_DOCKER = Path(CONTAINER_RUNTIME).name == "docker"


def user_ns_args() -> list[str]:
    # Podman and Docker diverge on user-namespace handling. Podman's
    # `keep-id` maps the host UID/GID into the container so bind-mounted
    # config stays owned by the caller. Docker has no `keep-id`; running as
    # an explicit uid:gid achieves the equivalent ownership on the mounts.
    if _RUNTIME_IS_DOCKER:
        return ["--user", "1000:1000"]
    return ["--userns=keep-id:uid=1000,gid=1000"]


def network_args() -> list[str]:
    # Docker on every platform routes the bridge gateway IP through to
    # whatever the host's 127.0.0.1 is reachable as (a VM proxy on macOS /
    # Windows, the docker bridge on Linux), so the `--add-host` mapping is
    # enough on its own. Podman rootless on Linux is the odd one out: its
    # default slirp4netns network includes a proxy from the gateway IP
    # (10.0.2.2) to the host's loopback, but that proxy is *disabled* by
    # default for hardening, which is why an LM Studio / Ollama bound on
    # 127.0.0.1 refuses connections from inside the container. Turning on
    # `allow_host_loopback` flips that proxy on so connections to the
    # gateway IP actually complete on the host's loopback. We skip it on
    # rootful podman (where bridge networking already reaches host
    # loopback) and on docker; passing the slirp4netns specifier in those
    # modes would force the wrong network backend.
    if _RUNTIME_IS_DOCKER:
        return []
    if os.geteuid() == 0:
        return []
    return ["--network", "slirp4netns:allow_host_loopback=true"]


def host_internal_args() -> list[str]:
    """Map `host.docker.internal` inside the container to whatever IP
    actually reaches the host's loopback under the active runtime."""
    if _RUNTIME_IS_DOCKER or os.geteuid() == 0:
        # Docker (any platform) and rootful podman both wire host-gateway
        # to a bridge IP that already routes to the host's loopback, so
        # the symbolic keyword works.
        return ["--add-host", "host.docker.internal:host-gateway"]
    # Rootless podman: the host-gateway keyword resolves to the container's
    # default route, which on the host's LAN points at the physical
    # interface (a 192.168.x / 10.x address). Traffic to that IP leaves
    # the host entirely instead of crossing slirp4netns's proxy, so a
    # service bound to 127.0.0.1 is unreachable. Pin the hostname directly
    # to the slirp4netns gateway IP — paired with `allow_host_loopback`
    # above, that's the path that proxies into the host's loopback.
    # 10.0.2.2 is slirp4netns's default gateway IP, and we don't override
    # the default CIDR, so it's the same for everyone on rootless podman.
    return ["--add-host", "host.docker.internal:10.0.2.2"]


def _bundled_assets_dir() -> Path:
    # Dockerfile + skeleton/ ship inside the package itself, in a sibling
    # data/ directory. This resolves to the same real path whether the
    # launcher runs from a source checkout, an editable install, or a
    # pip-installed wheel — no importlib.resources dance required, because
    # we always need real filesystem paths anyway (docker build + shutil
    # both want them).
    return Path(__file__).resolve().parent / "data"


def _namespaces_dir(home: Path) -> Path:
    return home / ".aetherion" / NAMESPACES_DIRNAME


def _namespace_dir(home: Path, name: str) -> Path:
    return _namespaces_dir(home) / name


def _legacy_data_dir(home: Path) -> Path:
    return home / ".aetherion" / LEGACY_DATA_DIRNAME


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="aetherion",
        description="Launch the aetherion dev container.",
    )
    parser.add_argument(
        "--image",
        metavar="REF",
        default=DEFAULT_IMAGE,
        help=f"Container image to run (and to tag when building). Default: {DEFAULT_IMAGE}.",
    )
    parser.add_argument(
        "--build-image",
        action="store_true",
        help=(
            "Build the image and exit (does not launch the container). Uses "
            "--build-dir as context if given, otherwise the Dockerfile + "
            "skeleton bundled with this script. Chain with && to launch after."
        ),
    )
    parser.add_argument(
        "--build-dir",
        metavar="PATH",
        default=None,
        help=(
            "Directory to use as the build context. Must contain a Dockerfile. "
            "Combine with --build-image to build from a customized copy "
            "(see --extract)."
        ),
    )
    parser.add_argument(
        "--refresh-layers",
        action="store_true",
        help=(
            "Discard the runtime's build cache for this build "
            "(podman/docker `--no-cache`). Use when you suspect a cached "
            "intermediate layer is stale — apt mirrors, upstream installer "
            "scripts, npm registry — and you want a from-scratch run. "
            "Only meaningful alongside --build-image."
        ),
    )
    parser.add_argument(
        "--extract",
        metavar="PATH",
        default=None,
        help=(
            "Copy the bundled Dockerfile and skeleton/ into PATH and exit "
            "without launching. Use this to customize the image: edit, "
            "then `aetherion --build-image --build-dir PATH`."
        ),
    )
    parser.add_argument(
        "-n", "--namespace",
        metavar="NAME",
        default=None,
        help=(
            f"Namespace whose $HOME to mount into the container. Each "
            f"namespace is an independent directory at "
            f"~/.aetherion/{NAMESPACES_DIRNAME}/<name>/ — agent logins, "
            f"installed tools, and shell history under one namespace are "
            f"invisible to another. Default: {DEFAULT_NAMESPACE!r}, which "
            f"is auto-created on first use. Other namespaces error if "
            f"they don't exist; pass --create-namespace to create one on "
            f"the fly. Names: letters, digits, dot, underscore, dash (no "
            f"leading dot)."
        ),
    )
    parser.add_argument(
        "--create-namespace",
        action="store_true",
        help=(
            "Create the namespace selected by --namespace if it does not "
            "exist (seeded from the current image's /home/aetherion). "
            "No-op when the namespace already exists. Not required for "
            f"the {DEFAULT_NAMESPACE!r} namespace — that one auto-creates."
        ),
    )
    parser.add_argument(
        "--list-namespaces",
        action="store_true",
        help=(
            "List existing namespaces under ~/.aetherion/"
            f"{NAMESPACES_DIRNAME}/ with the image digest each was seeded "
            "from, then exit."
        ),
    )
    parser.add_argument(
        "--reset-namespace",
        action="store_true",
        help=(
            "Delete the namespace selected by --namespace and re-seed it "
            "from the current image, then exit. Drops every in-container "
            "customization in that namespace (agent logins, npm globals, "
            "go binaries, nvim plugin updates, shell history, etc.) — use "
            "this when you want the image's current defaults instead of "
            "whatever was baked in at the time the namespace was first "
            "populated. Prompts for confirmation unless --force is also "
            "passed."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip the confirmation prompt for --reset-namespace.",
    )
    parser.add_argument(
        "-e", "--env",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help=(
            "Set an environment variable inside the container. Repeat for "
            "multiple (e.g. -e ONE=1 --env TWO=2). Values may contain spaces "
            "if quoted at the shell: --env 'NAME=has spaces'. A bare name "
            "with no `=` inherits the value from the host environment."
        ),
    )
    parser.add_argument(
        "--forward",
        action="append",
        default=[],
        metavar="[ADDR:[HOST_PORT:]]CONTAINER_PORT",
        help=(
            "Publish a container port to the host (podman/docker `-p` "
            "semantics). Repeatable. Forms: `CONTAINER_PORT` (host bind "
            "127.0.0.1, host port matches), `HOST_PORT:CONTAINER_PORT`, "
            "`ADDR:HOST_PORT:CONTAINER_PORT`, `:HOST_PORT:CONTAINER_PORT` "
            "(empty addr = 127.0.0.1), `[::1]:HOST:CONTAINER` (IPv6). "
            "Note: services that bind 127.0.0.1 inside the container "
            "(common default) won't be reachable through `--forward` alone "
            "— use a `--forward-<agent>` alias, which also sets up a "
            "loopback bridge."
        ),
    )
    parser.add_argument(
        "--forward-openclaw",
        metavar="[ADDR][:PORT]",
        nargs="?",
        const="",
        default=None,
        help=(
            f"Convenience alias for OpenClaw's gateway (container port "
            f"{OPENCLAW_GATEWAY_PORT}). Publishes the port AND sets up the "
            "loopback bridge required to reach it (openclaw binds 127.0.0.1 "
            "inside the container). Forms: bare (127.0.0.1:18789), `ADDR` "
            "(port stays 18789), `PORT` (addr stays 127.0.0.1), `ADDR:PORT`, "
            "or `:PORT`."
        ),
    )
    return parser.parse_args(argv)


def _build_alias_publish_spec(raw: str, service_port: int, bridge_port: int) -> str:
    """Turn a `--forward-<agent>` value into a podman/docker -p spec
    (HOST_BIND:HOST_PORT:CONTAINER_PORT). The container-side port published
    by `-p` is the *bridge* port — socat listens there and forwards to the
    real service port on 127.0.0.1 internally. Host-side port still defaults
    to the service port so URLs the service prints (e.g.
    `http://127.0.0.1:18789`) match what the user opens.
    """
    default_addr = "127.0.0.1"

    if raw == "":
        return f"{default_addr}:{service_port}:{bridge_port}"

    if ":" in raw:
        # rpartition handles `[::1]:9999` correctly — the right-most ':' is
        # always the addr/port separator. `:9999` (empty addr) is also fine.
        addr, _, port_str = raw.rpartition(":")
        addr = addr or default_addr
        host_port = int(port_str)
    elif raw.isdigit():
        addr = default_addr
        host_port = int(raw)
    else:
        addr = raw
        host_port = service_port

    if not (1 <= host_port <= 65535):
        raise ValueError(f"--forward-* host port out of range: {host_port}")
    return f"{addr}:{host_port}:{bridge_port}"


def _parse_forward_spec(raw: str) -> str:
    """Turn a `--forward` value into a podman/docker -p spec
    (HOST_BIND:HOST_PORT:CONTAINER_PORT).

    Forms (right-most colon always separates the container port):
        CONTAINER_PORT                           → 127.0.0.1:CONTAINER:CONTAINER
        HOST_PORT:CONTAINER_PORT                 → 127.0.0.1:HOST:CONTAINER
        ADDR:HOST_PORT:CONTAINER_PORT            → ADDR:HOST:CONTAINER
        :HOST_PORT:CONTAINER_PORT                → 127.0.0.1:HOST:CONTAINER
        [::1]:HOST_PORT:CONTAINER_PORT           → [::1]:HOST:CONTAINER  (IPv6)
    """
    default_addr = "127.0.0.1"

    if ":" not in raw:
        port = int(raw)
        if not (1 <= port <= 65535):
            raise ValueError(f"--forward port out of range: {port}")
        return f"{default_addr}:{port}:{port}"

    rest, _, container_str = raw.rpartition(":")
    container_port = int(container_str)
    if ":" not in rest:
        host_port = int(rest)
        addr = default_addr
    else:
        addr, _, host_str = rest.rpartition(":")
        addr = addr or default_addr
        host_port = int(host_str)

    for label, p in (("host", host_port), ("container", container_port)):
        if not (1 <= p <= 65535):
            raise ValueError(f"--forward {label} port out of range: {p}")
    return f"{addr}:{host_port}:{container_port}"


def _has_real_content(path: Path) -> bool:
    """True if `path` contains any non-directory entry, anywhere in its tree.

    Empty-directory stubs (left behind by prior mounts) don't count; files,
    symlinks, sockets, etc. do. Bails on the first match, so populated paths
    are detected in O(depth-to-first-file) syscalls rather than a full walk.
    """
    try:
        with os.scandir(path) as it:
            for entry in it:
                if not entry.is_dir(follow_symlinks=False):
                    return True
                if _has_real_content(Path(entry.path)):
                    return True
    except FileNotFoundError:
        return False
    except PermissionError:
        # Can't read it from the host UID — assume content and refuse the
        # mount so the user investigates rather than silently shadowing.
        return True
    return False


def main(argv: list[str] | None = None) -> int:
    # argv=None lets the console-script entry point (pyproject.toml) call
    # main() with no args while keeping the parameter explicit for tests
    # and for the `python -m aetherion` wrapper.
    if argv is None:
        argv = sys.argv[1:]
    args = _parse_args(argv)

    # --extract is terminal: it never launches the container.
    if args.extract is not None:
        return _extract_bundle(Path(args.extract).expanduser().resolve())

    # --list-namespaces is terminal: just reads the host filesystem, no
    # image / runtime needed.
    if args.list_namespaces:
        return _list_namespaces(Path.home())

    image: str = args.image

    # --build-image is terminal: it never launches the container, regardless
    # of build success or failure. The build's exit code propagates as-is.
    if args.build_image:
        if args.build_dir is not None:
            # User-managed context: build it verbatim. Anyone using --build-dir
            # has either run --extract (which ships an empty aetherion-src/
            # placeholder) or wired their own equivalent, so the Dockerfile's
            # COPY succeeds either way. Dev-mode overlay is reserved for the
            # default path below.
            return _build_image(
                image,
                Path(args.build_dir).expanduser().resolve(),
                refresh_layers=args.refresh_layers,
            )
        with tempfile.TemporaryDirectory(prefix="aetherion-build-") as td:
            ctx = _stage_build_context(Path(td))
            return _build_image(image, ctx, refresh_layers=args.refresh_layers)

    if not _image_exists(image):
        sys.stderr.write(
            f"aetherion: image '{image}' is not present locally.\n"
            "aetherion: this launcher does not pull images — build it locally:\n"
            f"  aetherion --build-image"
            + (f" --image {image}" if image != DEFAULT_IMAGE else "")
            + "\n"
            "aetherion: or extract a copy of the build files first to customize:\n"
            "  aetherion --extract <path>\n"
        )
        return 1

    home = Path.home()
    # args.namespace is None when -n/--namespace wasn't passed; fall back
    # to the default. Explicit `-n ''` would fail the validity check
    # below (not the silent fall-through `or` operator gives), preserving
    # the "garbage in, error out" rule.
    namespace: str = args.namespace if args.namespace is not None else DEFAULT_NAMESPACE
    if not _is_valid_namespace_name(namespace):
        sys.stderr.write(
            f"aetherion: invalid namespace name {namespace!r}. "
            "Names: letters, digits, dot, underscore, dash; no leading dot.\n"
        )
        return 2
    ns_dir = _namespace_dir(home, namespace)

    # --reset-namespace is terminal: nukes the namespace, re-seeds, exits.
    if args.reset_namespace:
        return _reset_namespace(image, ns_dir, namespace=namespace, force=args.force)

    # Legacy data is namespace-less, so it folds into 'default' specifically.
    # The check runs regardless of which namespace was selected on this
    # invocation — if you migrate from a legacy install but launch into a
    # fresh new namespace, your old state still lands somewhere
    # discoverable instead of being orphaned indefinitely.
    rc = _migrate_legacy_data(image, home)
    if rc != 0:
        return rc

    if not ns_dir.exists():
        # The default namespace auto-creates whenever it's the selected
        # one — whether `aetherion` was run with no -n at all (the common
        # case) or with `-n default` explicitly. Non-default namespaces
        # require --create-namespace to opt in, which keeps a typo in
        # `-n produciton` from silently spawning a new namespace and
        # losing the user's actual state.
        if namespace == DEFAULT_NAMESPACE or args.create_namespace:
            rc = _seed_namespace(image, ns_dir)
            if rc != 0:
                return rc
        else:
            sys.stderr.write(
                f"aetherion: namespace {namespace!r} does not exist at {ns_dir}.\n"
                f"aetherion: pass --create-namespace to create it (seeded from "
                f"{image}), or `aetherion --list-namespaces` to see what's "
                f"available.\n"
            )
            return 1
    else:
        if args.create_namespace:
            # Explicit no-op acknowledgement so it's not silent — useful when
            # this lives in a script that always passes --create-namespace
            # idempotently.
            sys.stderr.write(
                f"aetherion: namespace {namespace!r} already exists; "
                "--create-namespace had nothing to do.\n"
            )
        _warn_on_image_drift(image, ns_dir)

    pwd = Path.cwd()

    # Rewrite host home → container home so a host path of ~/foo lands at ~/foo
    # inside the container too. Anything outside $HOME is mounted at its real
    # path, since there's no portable home-relative form for it.
    if pwd == home:
        # Launching from host $HOME is ambiguous: the namespace's $HOME is
        # already the container's $HOME, so there's no sensible workdir
        # mount we could add. Hard-fail instead of silently landing the
        # user in the namespace $HOME — the silent-rewrite behavior tripped
        # people up because `aetherion` from ~ felt like it should put them
        # "in the same place" inside, but it doesn't.
        sys.stderr.write(
            f"aetherion: refusing to launch from your home directory ({home}).\n"
            "aetherion: cd into a project directory first. The container's "
            "$HOME is the namespace at "
            f"{ns_dir}, so there's no useful workdir we could mount from "
            "your host $HOME here.\n"
        )
        return 2
    elif home in pwd.parents:
        container_workdir = f"{CONTAINER_HOME}/{pwd.relative_to(home)}"
        # A workdir mount under $HOME lands on top of whatever's at the
        # same relative path inside the namespace. Empty dirs are fine
        # (mount-point stubs auto-created by prior runs as a side effect),
        # but real content — seeded skeleton files, anything the user put
        # there inside the container — would be silently shadowed. Refuse
        # in that case so the user notices and either clears the namespace
        # path or cd's somewhere else.
        ns_path = ns_dir / pwd.relative_to(home)
        if ns_path.exists() and (
            not ns_path.is_dir() or _has_real_content(ns_path)
        ):
            sys.stderr.write(
                f"aetherion: refusing to mount {pwd} over {container_workdir}: "
                f"{ns_path} already has content in the namespace and that "
                "content would be hidden by the mount.\n"
                f"aetherion: either cd elsewhere, or clear {ns_path} first "
                "if you really want the host directory to take over.\n"
            )
            return 2
        workdir_mount = ["-v", f"{pwd}:{container_workdir}:z"]
    else:
        container_workdir = str(pwd)
        workdir_mount = ["-v", f"{pwd}:{container_workdir}:z"]

    instance_id = secrets.token_hex(4)
    instance_name = f"aetherion-{instance_id}"

    # Passed through subprocess as separate argv entries, so values with
    # spaces or shell-special characters are safe — no shell evaluation.
    env_args: list[str] = []
    for kv in args.env:
        env_args += ["-e", kv]

    # Port publishing. `--forward` is the generic flag (podman/docker
    # `-p` semantics); `--forward-<agent>` flags are convenience aliases
    # that also enroll the container port in AETHERION_BRIDGE_PORTS so
    # /etc/profile.d/aetherion-bridge.sh stands up a loopback bridge —
    # required for services that hardcode 127.0.0.1 as their bind, since
    # `-p` forwarding terminates at the container's external interface.
    publish_args: list[str] = []
    # Each pair is (service_port, bridge_port). socat inside the container
    # listens on bridge_port and forwards to 127.0.0.1:service_port.
    bridge_pairs: list[tuple[int, int]] = []
    try:
        for raw in args.forward:
            publish_args += ["-p", _parse_forward_spec(raw)]
        if args.forward_openclaw is not None:
            bp = _bridge_port_for(OPENCLAW_GATEWAY_PORT)
            publish_args += [
                "-p",
                _build_alias_publish_spec(args.forward_openclaw, OPENCLAW_GATEWAY_PORT, bp),
            ]
            bridge_pairs.append((OPENCLAW_GATEWAY_PORT, bp))
    except ValueError as e:
        sys.stderr.write(f"aetherion: {e}\n")
        return 2

    if bridge_pairs:
        env_args += [
            "-e",
            f"AETHERION_BRIDGE_PORTS={','.join(f'{s}:{b}' for s, b in bridge_pairs)}",
        ]

    # The namespace mount lands first so the workdir mount (when present, and
    # always a subpath of CONTAINER_HOME for host paths under $HOME) layers
    # on top of it cleanly — both runtimes process binds in declaration
    # order and the deeper-path mount wins for its subtree.
    run_argv = [
        CONTAINER_RUNTIME, "run", "--rm",
        *user_ns_args(),
        *network_args(),
        "--name", instance_name,
        "--hostname", instance_id,
        *host_internal_args(),
        *env_args,
        *publish_args,
        "-v", f"{ns_dir}:{CONTAINER_HOME}:z",
        *workdir_mount,
        "-w", container_workdir,
        "-it",
        image,
    ]

    return subprocess.run(run_argv).returncode


def _image_exists(image: str) -> bool:
    return subprocess.run(
        [CONTAINER_RUNTIME, "image", "inspect", image],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0


def _image_id(image: str) -> str | None:
    """Return the runtime's content-digest ID for image, or None if the
    runtime can't read it (image absent, runtime error). Format is whatever
    the runtime emits — podman and docker each have their own conventions;
    we only ever compare values produced by the same runtime, so we don't
    normalize."""
    p = subprocess.run(
        [CONTAINER_RUNTIME, "image", "inspect", "--format", "{{.Id}}", image],
        capture_output=True,
        text=True,
    )
    if p.returncode != 0:
        return None
    out = p.stdout.strip()
    return out or None


def _build_image(image: str, context: Path, *, refresh_layers: bool = False) -> int:
    if not context.is_dir():
        sys.stderr.write(f"aetherion: build context does not exist: {context}\n")
        return 1
    if not (context / "Dockerfile").is_file():
        sys.stderr.write(
            f"aetherion: no Dockerfile found in build context: {context}\n"
            "aetherion: run `aetherion --extract <path>` to populate one.\n"
        )
        return 1

    # If the build context's aetherion-src/ overlay carries a pyproject.toml,
    # the launcher (or the user, via --build-dir) has staged a local source
    # tree there. Point the Dockerfile's `uv tool install` at it instead of
    # the default `aetherion` PyPI spec, so in-progress edits flow into the
    # container without a publish. Otherwise pin the PyPI install to the
    # launcher's own version: pyproject.toml is the single source of truth
    # and importlib.metadata reads it back from the installed dist, so the
    # in-container `aetherion`/`conduit` match the host launcher exactly
    # instead of drifting to whatever's currently latest on PyPI.
    build_args: list[str] = []
    if (context / "aetherion-src" / "pyproject.toml").is_file():
        build_args = ["--build-arg", "AETHERION_SPEC=/tmp/aetherion-src"]
    else:
        launcher_version = _installed_version()
        if launcher_version is not None:
            build_args = [
                "--build-arg",
                f"AETHERION_SPEC=aetherion=={launcher_version}",
            ]

    # --refresh-layers maps directly to the runtime's `--no-cache`. Same
    # flag name in podman and docker, so no per-runtime branching needed.
    cache_args: list[str] = ["--no-cache"] if refresh_layers else []
    if refresh_layers:
        sys.stderr.write("aetherion: --refresh-layers: ignoring layer cache\n")

    sys.stderr.write(f"aetherion: building {image} from {context}\n")
    return subprocess.run(
        [CONTAINER_RUNTIME, "build", *cache_args, *build_args, "-t", image, str(context)],
    ).returncode


def _installed_version() -> str | None:
    """Return the launcher's own installed version (read from package
    metadata, which is generated from pyproject.toml at build time — so
    pyproject is the single source of truth shared by host launcher and
    in-container install). Returns None only in environments where the
    package isn't a real installed dist (rare; mostly direct-from-checkout
    runs without an editable install)."""
    try:
        return importlib.metadata.version("aetherion")
    except importlib.metadata.PackageNotFoundError:
        return None


def _find_repo_root() -> Path | None:
    """Return the repo root iff the launcher is running from a source checkout
    where both src/aetherion/ and src/conduit/ live as siblings. Used by the
    build path to overlay live source into the container so edits land
    inside without a PyPI publish. Returns None for installed-from-wheel
    runs, where the only available source is whatever the wheel shipped."""
    here = Path(__file__).resolve()
    for ancestor in here.parents:
        if (
            (ancestor / "pyproject.toml").is_file()
            and (ancestor / "src" / "aetherion" / "__init__.py").is_file()
            and (ancestor / "src" / "conduit" / "__init__.py").is_file()
        ):
            return ancestor
    return None


def _stage_build_context(dest: Path) -> Path:
    """Materialize a docker build context under `dest`: copy bundled assets in,
    then (when running from a source checkout) overlay the repo's pyproject +
    src/ trees under aetherion-src/ so the Dockerfile's
    `uv tool install /tmp/aetherion-src` picks up live edits. Returns the
    populated context dir."""
    bundle = _bundled_assets_dir()
    for name in BUNDLED_ASSETS:
        src, dst = bundle / name, dest / name
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        elif src.is_file():
            shutil.copy2(src, dst)

    repo = _find_repo_root()
    if repo is not None:
        overlay = dest / "aetherion-src"
        # Wipe the .keep placeholder so the overlay isn't polluted by it. The
        # Dockerfile install path doesn't care, but leaving the stub lying
        # around inside `uv tool install`'s source tree is just noise.
        if overlay.exists():
            shutil.rmtree(overlay)
        overlay.mkdir()
        for name in ("pyproject.toml", "README.md", "LICENSE"):
            src = repo / name
            if src.is_file():
                shutil.copy2(src, overlay / name)
        shutil.copytree(
            repo / "src",
            overlay / "src",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        sys.stderr.write(
            f"aetherion: dev mode — overlaying repo source from {repo} "
            "into build context (live edits to src/conduit/ will land in "
            "the container without a publish)\n"
        )
    return dest


def _extract_bundle(dest: Path) -> int:
    src = _bundled_assets_dir()
    missing = [name for name in BUNDLED_ASSETS if not (src / name).exists()]
    if missing:
        sys.stderr.write(
            f"aetherion: bundled asset(s) missing from {src}: {', '.join(missing)}\n"
            "aetherion: this launcher must be run from a complete source tree.\n"
        )
        return 1

    dest.mkdir(parents=True, exist_ok=True)
    for name in BUNDLED_ASSETS:
        s = src / name
        d = dest / name
        if s.is_dir():
            # dirs_exist_ok overlays into an existing tree rather than failing,
            # but it still only touches files that exist in the source — so any
            # extra files the user added under dest/<name>/ stay put.
            shutil.copytree(s, d, dirs_exist_ok=True)
        else:
            shutil.copy2(s, d)

    sys.stderr.write(
        f"aetherion: extracted {', '.join(BUNDLED_ASSETS)} to {dest}\n"
        f"aetherion: build with: aetherion --build-image --build-dir {dest}\n"
    )
    return 0


def _seed_namespace(image: str, ns_dir: Path) -> int:
    """Populate `ns_dir` from the image's /home/aetherion. Caller must
    ensure `ns_dir` does not exist yet. Extracts to a staging sibling and
    atomically renames into place so a SIGINT mid-copy never leaves a
    half-populated namespace visible to the next launch."""
    sys.stderr.write(
        f"aetherion: seeding namespace at {ns_dir} from {image} "
        "(copies the image's $HOME including nvim plugins, treesitter "
        "parsers, gopls, etc.; may take a minute)...\n"
    )
    sys.stderr.flush()

    ns_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = ns_dir.with_name(ns_dir.name + ".tmp-seed")
    _rmtree_any(staging)
    staging.mkdir()
    # The in-container cp runs as UID 1000 (mapped to the launcher's host
    # UID on macOS docker desktop / rootless podman keep-id; possibly a
    # different host UID on Linux-native docker). Granting world-write
    # on the staging *entry point* avoids EACCES when the container UID
    # doesn't line up with whoever created the dir; the populated
    # contents inside keep their image-source modes.
    staging.chmod(0o777)

    # Delegate the copy to GNU cp inside the container instead of
    # `<runtime> cp` from the host. Reasons:
    #   1. Cross-platform tar/cp quirks (macOS bsdtar lacking GNU
    #      semantics) never apply — everything runs in the container's
    #      Linux userspace.
    #   2. `<runtime> cp` extracts directory entries in tar order: it
    #      creates a 0555 dir, then tries to mkdir children inside it
    #      and hits EACCES.
    #   3. One container invocation instead of three (create + cp + rm).
    #
    # Pre-pass: chmod -R u+w on the source tree. This is needed because
    # GNU cp's final "restore directory mode" step ALWAYS runs on
    # recursive copies — and for source dirs at 0555 (the Go module
    # cache convention under ~/go/pkg/mod), the target mode after
    # `src_mode & ~umask` is still 0555. On macOS Docker Desktop's
    # bind-mount driver (gRPC-fuse / VirtIOFS), any chmod that removes
    # write permission from a bind-mounted file is rejected with EACCES.
    # Pre-adding owner-write to the source means the final chmod lands
    # at 0755-ish (writable, no driver rejection). The chmod writes
    # land in the container's RW overlay layer — the image's
    # underlying read-only layers are unchanged — and get discarded
    # when --rm fires. The trailing `|| true` ignores chmod noise on
    # any entries we don't own (none expected in /home/aetherion, but
    # safe).
    #
    # We use `cp -a` for the actual copy: preserves symlinks, ownership
    # (a no-op when src and dst are both UID 1000 anyway), timestamps,
    # and mode bits (now all writable thanks to the pre-pass), so
    # executable bits on ~/.local/bin/aetherion, compiled treesitter
    # parsers, and similar carry through.
    script = (
        f"chmod -R u+w {CONTAINER_HOME} 2>/dev/null || true; "
        f"cp -a {CONTAINER_HOME}/. /staging/"
    )
    run = subprocess.run(
        [
            CONTAINER_RUNTIME, "run", "--rm",
            *user_ns_args(),
            "-v", f"{staging}:/staging:z",
            "--entrypoint", "bash",
            image,
            "-c", script,
        ],
        stderr=subprocess.PIPE,
    )
    if run.returncode != 0:
        sys.stderr.write(
            f"aetherion: failed to copy {CONTAINER_HOME} out of image:\n"
            f"{run.stderr.decode(errors='replace')}"
        )
        _rmtree_any(staging)
        return 1

    image_id = _image_id(image)
    if image_id:
        stamp = staging / IMAGE_ID_STAMP
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(image_id + "\n")

    try:
        os.replace(staging, ns_dir)
    except OSError as e:
        if e.errno in (errno.ENOTEMPTY, errno.EEXIST):
            # Another launcher raced us and populated the namespace
            # first. Theirs wins — discard ours and continue as if
            # seeding succeeded (because, from our caller's perspective,
            # it did: the namespace is now populated).
            sys.stderr.write(
                f"aetherion: namespace at {ns_dir} was populated by "
                "another aetherion process while we were seeding; "
                "discarded our copy.\n"
            )
            _rmtree_any(staging)
            return 0
        raise
    return 0


def _warn_on_image_drift(image: str, ns_dir: Path) -> None:
    """Print a one-liner when the image's current content-digest doesn't
    match the digest this namespace was seeded from. Doesn't auto-refresh —
    user-installed tools and customizations under $HOME are theirs to
    decide about. Silent on missing stamp / runtime inspect failure."""
    current = _image_id(image)
    if current is None:
        return
    stamp = ns_dir / IMAGE_ID_STAMP
    try:
        stamped = stamp.read_text().strip() or None
    except OSError:
        stamped = None
    if stamped is None or current == stamped:
        return
    sys.stderr.write(
        f"aetherion: namespace at {ns_dir} was seeded from a different build "
        f"of {image} ({stamped[:19]}...); current image is {current[:19]}.... "
        "Defaults baked into the image (skeleton config, vendor CLIs, npm "
        "globals, nvim plugins, etc.) are frozen at the seed; in-container "
        "customizations under $HOME persist as-is. Run "
        "`aetherion --reset-namespace` to drop customizations and re-seed.\n"
    )


def _list_namespaces(home: Path) -> int:
    """Print every namespace under ~/.aetherion/namespaces/ to stdout
    alongside the image digest it was seeded from, sorted by name.
    Suppresses staging dirs (`*.tmp-seed` from in-flight or interrupted
    seeds) so they don't masquerade as namespaces."""
    ns_root = _namespaces_dir(home)
    if not ns_root.is_dir():
        sys.stderr.write(
            f"aetherion: no namespaces yet at {ns_root}.\n"
            "aetherion: run `aetherion` to create the default namespace.\n"
        )
        return 0
    entries = sorted(
        p for p in ns_root.iterdir()
        if p.is_dir() and not p.name.endswith(".tmp-seed")
    )
    if not entries:
        sys.stderr.write(
            f"aetherion: no namespaces yet under {ns_root}.\n"
            "aetherion: run `aetherion` to create the default namespace.\n"
        )
        return 0

    name_w = max(len("NAMESPACE"), max(len(p.name) for p in entries))
    fmt = f"{{:<{name_w + 2}}}{{}}\n"
    sys.stdout.write(fmt.format("NAMESPACE", "SEEDED FROM"))
    for p in entries:
        stamp = p / IMAGE_ID_STAMP
        try:
            text = stamp.read_text().strip() if stamp.exists() else ""
        except OSError:
            text = ""
        # Truncate the digest so the table stays readable. Format varies
        # by runtime (podman keeps the `sha256:` prefix; docker drops it),
        # so cap on character count rather than slicing past the prefix.
        if text:
            seed = text[:19] + "..." if len(text) > 22 else text
        else:
            seed = "(no image-id stamp)"
        sys.stdout.write(fmt.format(p.name, seed))
    return 0


def _reset_namespace(image: str, ns_dir: Path, *, namespace: str, force: bool) -> int:
    if not ns_dir.exists():
        sys.stderr.write(
            f"aetherion: namespace {namespace!r} does not exist at {ns_dir}; "
            "nothing to reset.\n"
            f"aetherion: create it with `aetherion --namespace {namespace} "
            "--create-namespace`.\n"
        )
        return 0
    if not force:
        if not sys.stdin.isatty():
            sys.stderr.write(
                "aetherion: --reset-namespace requires a tty for "
                "confirmation, or pass --force to skip the prompt.\n"
            )
            return 2
        sys.stderr.write(
            f"aetherion: this will delete namespace {namespace!r} at {ns_dir} "
            f"and re-seed from {image}.\n"
            "aetherion: all in-container customizations in this namespace "
            "(agent logins, npm globals, go binaries, nvim plugin updates, "
            "shell history, etc.) will be lost.\n"
            "aetherion: continue? [y/N] "
        )
        sys.stderr.flush()
        reply = sys.stdin.readline().strip().lower()
        if reply not in ("y", "yes"):
            sys.stderr.write("aetherion: aborted.\n")
            return 1
    shutil.rmtree(ns_dir)
    return _seed_namespace(image, ns_dir)


def _migrate_legacy_data(image: str, home: Path) -> int:
    """One-shot: fold the old per-agent layout under ~/.aetherion/data into
    the new ~/.aetherion/namespaces/default layout. Runs only when legacy
    data exists and the default namespace does not — i.e. exactly once per
    host, on the first launch after upgrading to the namespaces design.
    Legacy state was namespace-less, so 'default' is its natural home.

    Order: seed the default namespace from the image first (fresh defaults),
    then overlay-copy each top-level entry from legacy data on top so
    existing agent logins and other saved state win over the image's empty
    placeholders. Finally rename the legacy dir aside as a safety net the
    user can delete once they've confirmed the new layout works."""
    legacy = _legacy_data_dir(home)
    default_ns = _namespace_dir(home, DEFAULT_NAMESPACE)
    if not legacy.is_dir() or default_ns.exists():
        return 0
    sys.stderr.write(
        f"aetherion: migrating legacy {legacy} into the new "
        f"~/.aetherion/{NAMESPACES_DIRNAME}/{DEFAULT_NAMESPACE} namespace.\n"
    )
    rc = _seed_namespace(image, default_ns)
    if rc != 0:
        return rc
    for entry in legacy.iterdir():
        dst = default_ns / entry.name
        if entry.is_dir() and not entry.is_symlink():
            shutil.copytree(entry, dst, dirs_exist_ok=True, symlinks=True)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(entry, dst, follow_symlinks=False)

    today = datetime.date.today().strftime("%Y%m%d")
    archive = legacy.with_name(f"{legacy.name}.migrated-{today}")
    # If a previous failed migration left an archive at the same date,
    # suffix with a counter so we don't clobber it.
    n = 2
    final_archive = archive
    while final_archive.exists():
        final_archive = archive.with_name(f"{archive.name}.{n}")
        n += 1
    legacy.rename(final_archive)
    sys.stderr.write(
        f"aetherion: legacy state preserved at {final_archive}; delete it "
        "once you've confirmed the new layout works.\n"
    )
    return 0


def _rmtree_any(path: Path) -> None:
    """Remove path whether it's a file, symlink, or directory. No-op if
    absent. Used by the seeding path to clear stale staging dirs without
    caring what's there.

    Handles read-only directories: Go's module cache deliberately sets
    parent dirs to 0555 with content underneath, and shutil.rmtree fails
    on those because it can't unlink children inside a non-writable parent.
    We pre-walk and grant owner-write on every directory before the rmtree
    call so cleanup of a partial seed (or a reset of an established
    namespace) actually completes."""
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    if not path.is_dir():
        return
    for root, _dirs, _files in os.walk(path):
        try:
            os.chmod(root, 0o700)
        except OSError:
            # If we can't chmod (e.g. someone else owns it), let rmtree
            # surface the underlying error so it's not silently swallowed.
            pass
    shutil.rmtree(path)


if __name__ == "__main__":
    sys.exit(main())
