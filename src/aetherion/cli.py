#!/usr/bin/env python3
from __future__ import annotations

import argparse
import errno
import importlib.metadata
import os
import re
import secrets
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CONTAINER_HOME = "/home/aetherion"
IMAGE_PREFIX = "localhost/aetherion"
DEFAULT_NAMESPACE = "default"

# Host-side layout, all under ~/.aetherion/:
#   config.yaml            — namespace registry (this file's source of truth)
#   namespaces/<name>/     — bind-mounted as $HOME inside the container
#   containers/<name>/     — build context (Dockerfile + skeleton + aetherion-src)
CONFIG_FILENAME = "config.yaml"
NAMESPACES_DIRNAME = "namespaces"
CONTAINERS_DIRNAME = "containers"

# The first positional after `aetherion` is either one of these verbs or
# a namespace name. Reserved words can't be used as namespace names so the
# dispatch is never ambiguous.
VERBS = ("config", "list", "create", "reset", "rebuild", "delete")
RESERVED_NAMESPACE_NAMES = frozenset(VERBS)

# Per-namespace state inside its $HOME — records which image digest this
# namespace was seeded from, so we can warn on drift when the image is
# rebuilt under it.
IMAGE_ID_STAMP = ".aetherion/image-id"

# Container labels attached at launch so `list sessions` can attribute
# running containers back to their namespace.
LABEL_NAMESPACE = "aetherion.namespace"
LABEL_IMAGE = "aetherion.image"
LABEL_WORKDIR = "aetherion.workdir"

# Port openclaw's gateway binds to inside the container. Fixed by openclaw
# itself — we only control how (and whether) it's published to the host.
OPENCLAW_GATEWAY_PORT = 18789

# When a `--forward-<agent>` alias needs to bridge an in-container loopback
# service, the socat bridge listens on a port DIFFERENT from the service's
# real port. Services (e.g. openclaw) do their own port-availability probes
# at startup — including transient wildcard binds — and a specific bind to
# <eth0>:<service_port> by us makes those probes fail with EADDRINUSE.
# Bridge port = service port + 40000 sidesteps the collision; `-p` then
# forwards the host's chosen port to the bridge port, and socat fans out to
# 127.0.0.1:service_port internally.
BRIDGE_PORT_OFFSET = 40000

BUNDLED_ASSETS: tuple[str, ...] = ("Dockerfile", "skeleton", "aetherion-src")

# Letters, digits, dot, underscore, dash; no leading dot. Anything else is
# a path-traversal or shell-surprise risk in ~/.aetherion/namespaces/.
_NAMESPACE_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]*$")


def _validate_namespace_name(name: str) -> str | None:
    """Return None on success, an error message on failure."""
    if name in RESERVED_NAMESPACE_NAMES:
        return (
            f"namespace name {name!r} is reserved (collides with the "
            f"`aetherion {name}` verb). Reserved: "
            f"{', '.join(sorted(RESERVED_NAMESPACE_NAMES))}."
        )
    if not _NAMESPACE_NAME_RE.fullmatch(name):
        return (
            f"invalid namespace name {name!r}: use letters, digits, dot, "
            "underscore, dash; no leading dot."
        )
    return None


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


CONTAINER_RUNTIME = _detect_runtime()
_RUNTIME_IS_DOCKER = Path(CONTAINER_RUNTIME).name == "docker"


def user_ns_args() -> list[str]:
    # Podman's `keep-id` maps the host UID/GID into the container so
    # bind-mounted config stays owned by the caller. Docker has no
    # `keep-id`; running as an explicit uid:gid achieves the equivalent
    # ownership on the mounts.
    if _RUNTIME_IS_DOCKER:
        return ["--user", "1000:1000"]
    return ["--userns=keep-id:uid=1000,gid=1000"]


def network_args() -> list[str]:
    # Rootless podman's default slirp4netns network disables host-loopback
    # proxying for hardening, which is why an LM Studio / Ollama bound on
    # 127.0.0.1 refuses connections from inside the container. Flip the
    # proxy on so connections to the gateway IP actually complete on the
    # host's loopback. Docker and rootful podman already reach host
    # loopback via their bridge.
    if _RUNTIME_IS_DOCKER:
        return []
    if os.geteuid() == 0:
        return []
    return ["--network", "slirp4netns:allow_host_loopback=true"]


def host_internal_args() -> list[str]:
    """Map `host.docker.internal` inside the container to whatever IP
    actually reaches the host's loopback under the active runtime."""
    if _RUNTIME_IS_DOCKER or os.geteuid() == 0:
        return ["--add-host", "host.docker.internal:host-gateway"]
    # Rootless podman: host-gateway resolves to the container's default
    # route, which on the host's LAN points at the physical interface;
    # traffic to that IP leaves the host entirely. Pin to slirp4netns's
    # gateway IP so paired with `allow_host_loopback` it proxies into the
    # host's loopback. 10.0.2.2 is slirp4netns's default gateway.
    return ["--add-host", "host.docker.internal:10.0.2.2"]


def _bundled_assets_dir() -> Path:
    # Dockerfile + skeleton/ ship inside the package itself, in a sibling
    # data/ directory.
    return Path(__file__).resolve().parent / "data"


def _aetherion_dir(home: Path) -> Path:
    return home / ".aetherion"


def _config_path(home: Path) -> Path:
    return _aetherion_dir(home) / CONFIG_FILENAME


def _namespaces_dir(home: Path) -> Path:
    return _aetherion_dir(home) / NAMESPACES_DIRNAME


def _namespace_home_dir(home: Path, name: str) -> Path:
    return _namespaces_dir(home) / name


def _containers_dir(home: Path) -> Path:
    return _aetherion_dir(home) / CONTAINERS_DIRNAME


def _namespace_build_dir(home: Path, name: str) -> Path:
    return _containers_dir(home) / name


def default_image_for(namespace: str) -> str:
    return f"{IMAGE_PREFIX}:{namespace}"


def _expand(p: str | Path) -> Path:
    return Path(os.path.expanduser(str(p)))


def _short_home_path(home: Path, p: Path) -> str:
    try:
        rel = p.relative_to(home)
        return f"~/{rel}"
    except ValueError:
        return str(p)


@dataclass
class PortForward:
    host_interface: str
    host_port: int
    container_port: int


@dataclass
class NamespaceConfig:
    name: str
    image: str
    build_dir: Path
    env_from_map: dict[str, str] = field(default_factory=dict)
    env_from_file: dict[str, str] = field(default_factory=dict)
    env_from_env: dict[str, str] = field(default_factory=dict)
    port_forwarding: list[PortForward] = field(default_factory=list)
    volumes: list[str] = field(default_factory=list)


def _make_default_namespace_config(home: Path, name: str = DEFAULT_NAMESPACE) -> NamespaceConfig:
    return NamespaceConfig(
        name=name,
        image=default_image_for(name),
        build_dir=_namespace_build_dir(home, name),
    )


def load_config(home: Path) -> dict[str, NamespaceConfig]:
    """Return namespace name → NamespaceConfig. Empty dict if no config."""
    path = _config_path(home)
    if not path.is_file():
        return {}
    try:
        with path.open("r") as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        sys.stderr.write(f"aetherion: failed to parse {path}: {e}\n")
        raise SystemExit(1)
    if not isinstance(data, dict):
        sys.stderr.write(f"aetherion: {path}: top-level must be a mapping\n")
        raise SystemExit(1)

    raw_ns = data.get("namespaces") or {}
    if not isinstance(raw_ns, dict):
        sys.stderr.write(f"aetherion: {path}: `namespaces` must be a mapping\n")
        raise SystemExit(1)

    result: dict[str, NamespaceConfig] = {}
    for name, conf in raw_ns.items():
        if not isinstance(name, str):
            sys.stderr.write(
                f"aetherion: {path}: namespace keys must be strings "
                f"(got {name!r})\n"
            )
            raise SystemExit(1)
        err = _validate_namespace_name(name)
        if err:
            sys.stderr.write(f"aetherion: {path}: {err}\n")
            raise SystemExit(1)
        conf = conf or {}
        env = conf.get("environment") or {}

        forwards: list[PortForward] = []
        for entry in (conf.get("port-forwarding") or []):
            forwards.append(
                PortForward(
                    host_interface=str(entry.get("hostInterface") or "127.0.0.1"),
                    host_port=int(entry["hostPort"]),
                    container_port=int(entry["containerPort"]),
                )
            )

        result[name] = NamespaceConfig(
            name=name,
            image=str(conf.get("image") or default_image_for(name)),
            build_dir=_expand(
                conf.get("buildDir") or _namespace_build_dir(home, name)
            ),
            env_from_map=dict(env.get("fromMap") or {}),
            env_from_file=dict(env.get("fromFile") or {}),
            env_from_env=dict(env.get("fromEnv") or {}),
            port_forwarding=forwards,
            volumes=list(conf.get("volumes") or []),
        )
    return result


def save_config(home: Path, configs: dict[str, NamespaceConfig]) -> None:
    path = _config_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {"namespaces": {}}
    for name, c in configs.items():
        ns: dict[str, Any] = {
            "image": c.image,
            "buildDir": _short_home_path(home, c.build_dir),
        }
        env_section: dict[str, Any] = {}
        if c.env_from_map:
            env_section["fromMap"] = dict(c.env_from_map)
        if c.env_from_file:
            env_section["fromFile"] = dict(c.env_from_file)
        if c.env_from_env:
            env_section["fromEnv"] = dict(c.env_from_env)
        if env_section:
            ns["environment"] = env_section
        if c.port_forwarding:
            ns["port-forwarding"] = [
                {
                    "hostInterface": pf.host_interface,
                    "hostPort": pf.host_port,
                    "containerPort": pf.container_port,
                }
                for pf in c.port_forwarding
            ]
        if c.volumes:
            ns["volumes"] = list(c.volumes)
        data["namespaces"][name] = ns

    with path.open("w") as f:
        yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False)


def _image_exists(image: str) -> bool:
    return subprocess.run(
        [CONTAINER_RUNTIME, "image", "inspect", image],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0


def _image_id(image: str) -> str | None:
    """Return the runtime's content-digest ID for `image`, or None if the
    runtime can't read it. Format is runtime-specific — we only ever compare
    values produced by the same runtime, so we don't normalize."""
    p = subprocess.run(
        [CONTAINER_RUNTIME, "image", "inspect", "--format", "{{.Id}}", image],
        capture_output=True,
        text=True,
    )
    if p.returncode != 0:
        return None
    out = p.stdout.strip()
    return out or None


def _installed_version() -> str | None:
    try:
        return importlib.metadata.version("aetherion")
    except importlib.metadata.PackageNotFoundError:
        return None


def _find_repo_root() -> Path | None:
    """Return the repo root iff the launcher is running from a source
    checkout. Used by the build path to overlay live source into the
    container so edits land inside without a PyPI publish."""
    here = Path(__file__).resolve()
    for ancestor in here.parents:
        if (
            (ancestor / "pyproject.toml").is_file()
            and (ancestor / "src" / "aetherion" / "__init__.py").is_file()
            and (ancestor / "src" / "conduit" / "__init__.py").is_file()
        ):
            return ancestor
    return None


def _overlay_repo_source(overlay: Path, repo: Path) -> None:
    """Replace `overlay` with a fresh copy of the repo's pyproject + src/
    tree so the Dockerfile's `uv tool install /tmp/aetherion-src` picks up
    live edits on the next build."""
    if overlay.exists():
        _rmtree_any(overlay)
    overlay.mkdir(parents=True)
    for name in ("pyproject.toml", "README.md", "LICENSE"):
        src = repo / name
        if src.is_file():
            shutil.copy2(src, overlay / name)
    shutil.copytree(
        repo / "src",
        overlay / "src",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )


def _populate_build_dir(dest: Path, *, fresh: bool) -> None:
    """Populate `dest` with the bundled Dockerfile, skeleton, and
    aetherion-src placeholder.

    `fresh=True` (used by `create namespace`): the dest is assumed to be
    empty or absent; everything is copied in. With a source checkout,
    aetherion-src/ is overlaid with the live repo so the first build picks
    up local edits.

    `fresh=False` (used by `rebuild namespace`): the user may have edited
    Dockerfile/skeleton, so we leave those alone. The aetherion-src/ overlay
    is still refreshed in dev mode so the launcher's latest source flows
    into the next build without forcing the user to re-create the namespace.
    """
    bundle = _bundled_assets_dir()
    dest.mkdir(parents=True, exist_ok=True)

    for name in ("Dockerfile", "skeleton"):
        src, dst = bundle / name, dest / name
        if not fresh and dst.exists():
            continue
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        elif src.is_file():
            shutil.copy2(src, dst)

    overlay = dest / "aetherion-src"
    repo = _find_repo_root()
    if repo is not None:
        _overlay_repo_source(overlay, repo)
        sys.stderr.write(
            f"aetherion: dev mode — overlaid repo source from {repo} "
            f"into {overlay}\n"
        )
    elif not overlay.exists():
        # No checkout, fresh setup: copy the bundled placeholder so the
        # Dockerfile's COPY succeeds with a no-op tree.
        src = bundle / "aetherion-src"
        if src.is_dir():
            shutil.copytree(src, overlay)


def _build_image(image: str, context: Path, *, no_cache: bool = False) -> int:
    if not context.is_dir():
        sys.stderr.write(f"aetherion: build context does not exist: {context}\n")
        return 1
    if not (context / "Dockerfile").is_file():
        sys.stderr.write(
            f"aetherion: no Dockerfile found in build context: {context}\n"
        )
        return 1

    # Source-checkout dev mode: if the aetherion-src/ overlay carries a
    # pyproject.toml, point uv tool install at it. Otherwise pin to the
    # installed launcher's version so host launcher and in-container CLI
    # match exactly.
    build_args: list[str] = []
    if (context / "aetherion-src" / "pyproject.toml").is_file():
        build_args = ["--build-arg", "AETHERION_SPEC=/tmp/aetherion-src"]
    else:
        v = _installed_version()
        if v is not None:
            build_args = ["--build-arg", f"AETHERION_SPEC=aetherion=={v}"]

    cache_args: list[str] = ["--no-cache"] if no_cache else []
    if no_cache:
        sys.stderr.write("aetherion: --no-cache: ignoring layer cache\n")

    sys.stderr.write(f"aetherion: building {image} from {context}\n")
    return subprocess.run(
        [
            CONTAINER_RUNTIME, "build",
            *cache_args, *build_args,
            "-t", image,
            str(context),
        ],
    ).returncode


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
    # The in-container cp runs as UID 1000; granting world-write on the
    # staging entry point avoids EACCES if that doesn't line up with the
    # host UID that created the dir. Contents keep their image-source modes.
    staging.chmod(0o777)

    # GNU cp inside the container instead of `<runtime> cp` from the host:
    # avoids cross-platform tar/cp quirks (macOS bsdtar lacking GNU
    # semantics) and `<runtime> cp`'s 0555-dir EACCES issue. Pre-pass
    # `chmod -R u+w` on the source tree is needed because GNU cp's final
    # "restore directory mode" step on recursive copies hits 0555 dirs (Go
    # module cache convention under ~/go/pkg/mod) and macOS Docker
    # Desktop's bind-mount driver rejects chmods that remove write
    # permission from bind-mounted files. The chmod writes land in the
    # container's RW overlay and get discarded with --rm.
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
    match the digest this namespace was seeded from. Doesn't auto-refresh
    — user-installed tools and customizations under $HOME are theirs to
    decide about."""
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
        f"of {image} ({stamped[:19]}...); current image is "
        f"{current[:19]}.... In-container customizations under $HOME persist "
        f"as-is. Run `aetherion reset namespace {ns_dir.name}` to drop "
        "customizations and re-seed.\n"
    )


def _has_real_content(path: Path) -> bool:
    """True if `path` contains any non-directory entry, anywhere in its tree.

    Empty-directory stubs (left behind by prior mounts) don't count; files,
    symlinks, sockets, etc. do. Bails on the first match for speed.
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


def _rmtree_any(path: Path) -> None:
    """Remove path whether it's a file, symlink, or directory. No-op if
    absent. Pre-walks and grants owner-write on every directory so cleanup
    succeeds even on Go's 0555 module-cache dirs."""
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    if not path.is_dir():
        return
    for root, _dirs, _files in os.walk(path):
        try:
            os.chmod(root, 0o700)
        except OSError:
            pass
    shutil.rmtree(path)


def _bridge_port_for(service_port: int) -> int:
    bp = service_port + BRIDGE_PORT_OFFSET
    if bp > 65535:
        bp = service_port - 10000
    if not (1 <= bp <= 65535):
        raise ValueError(
            f"could not compute a bridge port for service port {service_port}"
        )
    return bp


def _build_alias_publish_spec(raw: str, service_port: int, bridge_port: int) -> str:
    """Turn a `--forward-<agent>` value into a podman/docker -p spec
    (HOST_BIND:HOST_PORT:CONTAINER_PORT). Container side is the *bridge*
    port — socat listens there and forwards to the service port on
    127.0.0.1 internally. Host side still defaults to the service port so
    URLs the service prints match what the user opens."""
    default_addr = "127.0.0.1"
    if raw == "":
        return f"{default_addr}:{service_port}:{bridge_port}"
    if ":" in raw:
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
        CONTAINER_PORT                  → 127.0.0.1:CONTAINER:CONTAINER
        HOST_PORT:CONTAINER_PORT        → 127.0.0.1:HOST:CONTAINER
        ADDR:HOST_PORT:CONTAINER_PORT   → ADDR:HOST:CONTAINER
        :HOST_PORT:CONTAINER_PORT       → 127.0.0.1:HOST:CONTAINER
        [::1]:HOST_PORT:CONTAINER_PORT  → [::1]:HOST:CONTAINER  (IPv6)
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


def _parse_volume_spec(raw: str) -> tuple[Path, str]:
    """Parse a `volumes:` / `-v` entry into (host_path, container_path).

    Forms:
        SRC                  → mount SRC at the same logical path (~ rewrites
                               from host home to container $HOME)
        SRC:DST              → explicit src + dst (DST may start with ~/ to
                               anchor at container $HOME, or be absolute)
    """
    if ":" in raw:
        src_str, _, dst_str = raw.partition(":")
    else:
        src_str = raw
        dst_str = raw

    src = _expand(src_str)
    if not src.is_absolute():
        src = (Path.cwd() / src).resolve()

    if dst_str.startswith("~/"):
        dst = CONTAINER_HOME + dst_str[1:]
    elif dst_str == "~":
        dst = CONTAINER_HOME
    elif dst_str.startswith("/"):
        dst = dst_str
    else:
        raise ValueError(
            f"volume dst must be absolute or start with `~/`: {dst_str!r}"
        )
    return src, dst


# Verb implementations -------------------------------------------------------

def cmd_config(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="aetherion config",
        description="Open ~/.aetherion/config.yaml in $EDITOR (fallback: vi).",
    )
    parser.parse_args(argv)

    home = Path.home()
    path = _config_path(home)

    if not path.is_file():
        sys.stderr.write(
            f"aetherion: no config at {path} yet; writing a minimal default.\n"
        )
        save_config(home, {DEFAULT_NAMESPACE: _make_default_namespace_config(home)})

    editor = os.environ.get("EDITOR") or "vi"
    return subprocess.run([editor, str(path)]).returncode


def cmd_list(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="aetherion list")
    parser.add_argument(
        "what",
        choices=("namespace", "namespaces", "session", "sessions"),
        help="`namespaces` (registered namespaces) or `sessions` (running containers).",
    )
    args = parser.parse_args(argv)

    home = Path.home()
    if args.what in ("namespace", "namespaces"):
        return _list_namespaces(home)
    return _list_sessions()


def _print_table(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> None:
    widths = [
        max(len(h), max((len(r[i]) for r in rows), default=0))
        for i, h in enumerate(headers)
    ]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths) + "\n"
    sys.stdout.write(fmt.format(*headers))
    for r in rows:
        sys.stdout.write(fmt.format(*r))


def _list_namespaces(home: Path) -> int:
    configs = load_config(home)
    ns_root = _namespaces_dir(home)

    names: set[str] = set(configs.keys())
    if ns_root.is_dir():
        for p in ns_root.iterdir():
            if p.is_dir() and not p.name.endswith(".tmp-seed"):
                names.add(p.name)

    if not names:
        sys.stderr.write(
            f"aetherion: no namespaces yet. Run `aetherion` to bootstrap the "
            f"{DEFAULT_NAMESPACE!r} namespace.\n"
        )
        return 0

    rows: list[tuple[str, ...]] = []
    for name in sorted(names):
        ns_dir = _namespace_home_dir(home, name)
        stamp = ns_dir / IMAGE_ID_STAMP
        try:
            seed = stamp.read_text().strip() if stamp.exists() else ""
        except OSError:
            seed = ""
        if seed:
            seed = seed[:19] + "..." if len(seed) > 22 else seed
        else:
            seed = "(not seeded)"
        image = configs[name].image if name in configs else "(unregistered)"
        rows.append((name, image, seed))

    _print_table(("NAMESPACE", "IMAGE", "SEEDED FROM"), rows)
    return 0


def _list_sessions() -> int:
    # `{{.Label "key"}}` works on both podman and docker formatters.
    fmt_str = (
        "{{.Names}}\t"
        f'{{{{.Label "{LABEL_NAMESPACE}"}}}}\t'
        "{{.Image}}\t"
        f'{{{{.Label "{LABEL_WORKDIR}"}}}}\t'
        "{{.RunningFor}}"
    )
    p = subprocess.run(
        [
            CONTAINER_RUNTIME, "ps",
            "--filter", f"label={LABEL_NAMESPACE}",
            "--format", fmt_str,
        ],
        capture_output=True, text=True,
    )
    if p.returncode != 0:
        sys.stderr.write(f"aetherion: failed to list containers:\n{p.stderr}")
        return 1

    headers = ("SESSION", "NAMESPACE", "IMAGE", "WORKDIR", "UPTIME")
    lines = [ln for ln in p.stdout.splitlines() if ln.strip()]
    if not lines:
        sys.stderr.write("aetherion: no running sessions.\n")
        return 0

    rows: list[tuple[str, ...]] = []
    for ln in lines:
        parts = ln.split("\t")
        # Pad short tuples (older runtime versions sometimes emit fewer
        # tab-separated fields when labels are missing).
        if len(parts) < 5:
            parts = parts + [""] * (5 - len(parts))
        rows.append(tuple(parts[:5]))

    _print_table(headers, rows)
    return 0


def cmd_create(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="aetherion create")
    parser.add_argument("what", choices=("namespace",))
    parser.add_argument("name", help="namespace name to create")
    parser.add_argument(
        "--no-cache", action="store_true",
        help="Discard layer cache during the build (`--no-cache`).",
    )
    args = parser.parse_args(argv)

    home = Path.home()
    return _create_namespace(home, args.name, no_cache=args.no_cache)


def _create_namespace(home: Path, name: str, *, no_cache: bool = False) -> int:
    err = _validate_namespace_name(name)
    if err:
        sys.stderr.write(f"aetherion: {err}\n")
        return 2

    configs = load_config(home)
    if name in configs:
        sys.stderr.write(
            f"aetherion: namespace {name!r} is already registered in "
            f"{_config_path(home)}.\n"
        )
        return 1

    config = _make_default_namespace_config(home, name)
    build_dir = config.build_dir
    ns_home = _namespace_home_dir(home, name)

    if build_dir.exists() and _has_real_content(build_dir):
        sys.stderr.write(
            f"aetherion: build dir at {build_dir} already has content but "
            f"namespace {name!r} isn't registered. Move or remove it first: "
            f"rm -rf {build_dir}\n"
        )
        return 1
    if ns_home.exists() and _has_real_content(ns_home):
        sys.stderr.write(
            f"aetherion: namespace $HOME at {ns_home} already has content "
            f"but namespace {name!r} isn't registered. Move or remove it "
            f"first: rm -rf {ns_home}\n"
        )
        return 1

    sys.stderr.write(
        f"aetherion: creating namespace {name!r}: build dir at {build_dir}, "
        f"image {config.image}.\n"
    )
    _populate_build_dir(build_dir, fresh=True)
    rc = _build_image(config.image, build_dir, no_cache=no_cache)
    if rc != 0:
        return rc

    # If a stub $HOME dir exists empty, clear it so _seed_namespace's
    # atomic rename can land.
    if ns_home.exists():
        _rmtree_any(ns_home)
    rc = _seed_namespace(config.image, ns_home)
    if rc != 0:
        return rc

    configs[name] = config
    save_config(home, configs)
    sys.stderr.write(
        f"aetherion: created namespace {name!r}. Launch it with "
        f"`aetherion {name}`.\n"
    )
    return 0


def cmd_reset(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="aetherion reset")
    parser.add_argument("what", choices=("namespace",))
    parser.add_argument("name", help="namespace whose $HOME to wipe + re-seed")
    parser.add_argument(
        "--force", action="store_true",
        help="Skip the confirmation prompt.",
    )
    args = parser.parse_args(argv)

    home = Path.home()
    return _reset_namespace(home, args.name, force=args.force)


def _reset_namespace(home: Path, name: str, *, force: bool) -> int:
    configs = load_config(home)
    if name not in configs:
        sys.stderr.write(
            f"aetherion: namespace {name!r} not registered in "
            f"{_config_path(home)}.\n"
        )
        return 1
    config = configs[name]
    ns_home = _namespace_home_dir(home, name)

    running = _running_sessions_for(name)
    if running:
        sys.stderr.write(
            f"aetherion: warning: {len(running)} running session(s) use "
            f"namespace {name!r}: {', '.join(running)}. Resetting will pull "
            "their $HOME out from under them. Stop them with "
            f"`{CONTAINER_RUNTIME} kill <session>` first if you want a clean "
            "shutdown.\n"
        )

    if ns_home.exists():
        if not force:
            if not sys.stdin.isatty():
                sys.stderr.write(
                    "aetherion: `reset namespace` requires a tty for "
                    "confirmation, or pass --force.\n"
                )
                return 2
            sys.stderr.write(
                f"aetherion: this will delete namespace {name!r}'s $HOME at "
                f"{ns_home} and re-seed from {config.image}.\n"
                "aetherion: in-container customizations (agent logins, npm "
                "globals, go binaries, nvim plugin updates, shell history, "
                "etc.) will be lost.\n"
                "aetherion: continue? [y/N] "
            )
            sys.stderr.flush()
            reply = sys.stdin.readline().strip().lower()
            if reply not in ("y", "yes"):
                sys.stderr.write("aetherion: aborted.\n")
                return 1
        _rmtree_any(ns_home)
    else:
        sys.stderr.write(
            f"aetherion: namespace {name!r} has no $HOME at {ns_home}; "
            f"re-seeding from {config.image}.\n"
        )
    return _seed_namespace(config.image, ns_home)


def cmd_rebuild(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="aetherion rebuild")
    parser.add_argument("what", choices=("namespace",))
    parser.add_argument("name", help="namespace whose image to rebuild")
    parser.add_argument(
        "--no-cache", action="store_true",
        help="Discard layer cache during the build (`--no-cache`).",
    )
    args = parser.parse_args(argv)

    home = Path.home()
    return _rebuild_namespace(home, args.name, no_cache=args.no_cache)


def _rebuild_namespace(home: Path, name: str, *, no_cache: bool) -> int:
    configs = load_config(home)
    if name not in configs:
        sys.stderr.write(
            f"aetherion: namespace {name!r} not registered in "
            f"{_config_path(home)}.\n"
        )
        return 1
    config = configs[name]

    if not config.build_dir.is_dir():
        sys.stderr.write(
            f"aetherion: build dir for namespace {name!r} is missing at "
            f"{config.build_dir}; populating from bundled assets.\n"
        )
        _populate_build_dir(config.build_dir, fresh=True)
    else:
        # Preserve user edits to Dockerfile/skeleton; only refresh
        # aetherion-src/ in dev mode.
        _populate_build_dir(config.build_dir, fresh=False)

    return _build_image(config.image, config.build_dir, no_cache=no_cache)


def cmd_delete(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="aetherion delete")
    parser.add_argument("what", choices=("namespace",))
    parser.add_argument(
        "names", nargs="+", metavar="NAMESPACE",
        help="one or more namespace names to delete",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Skip the confirmation prompt.",
    )
    args = parser.parse_args(argv)

    home = Path.home()
    rc = 0
    for name in args.names:
        r = _delete_namespace(home, name, force=args.force)
        if r != 0:
            rc = r
    return rc


def _running_sessions_for(namespace: str) -> list[str]:
    p = subprocess.run(
        [
            CONTAINER_RUNTIME, "ps",
            "--filter", f"label={LABEL_NAMESPACE}={namespace}",
            "--format", "{{.Names}}",
        ],
        capture_output=True, text=True,
    )
    if p.returncode != 0:
        return []
    return [ln.strip() for ln in p.stdout.splitlines() if ln.strip()]


def _delete_namespace(home: Path, name: str, *, force: bool) -> int:
    configs = load_config(home)
    config = configs.get(name)
    ns_home = _namespace_home_dir(home, name)
    default_build_dir = _namespace_build_dir(home, name)
    build_dir = config.build_dir if config else default_build_dir
    image = config.image if config else default_image_for(name)

    image_present = _image_exists(image)
    have_anything = (
        config is not None
        or ns_home.exists()
        or build_dir.exists()
        or image_present
    )
    if not have_anything:
        sys.stderr.write(f"aetherion: nothing to delete for namespace {name!r}.\n")
        return 0

    running = _running_sessions_for(name)
    if running:
        sys.stderr.write(
            f"aetherion: warning: {len(running)} running session(s) use "
            f"namespace {name!r}: {', '.join(running)}. Stop them with "
            f"`{CONTAINER_RUNTIME} kill <session>` before deleting if you "
            "want a clean shutdown.\n"
        )

    if not force:
        if not sys.stdin.isatty():
            sys.stderr.write(
                "aetherion: `delete namespace` requires a tty for "
                "confirmation, or pass --force.\n"
            )
            return 2
        targets: list[str] = []
        if ns_home.exists():
            targets.append(f"  $HOME           {ns_home}")
        if build_dir.exists() and build_dir == default_build_dir:
            targets.append(f"  build dir       {build_dir}")
        elif build_dir.exists():
            targets.append(
                f"  build dir       {build_dir} "
                "(custom path, left in place — remove by hand)"
            )
        if image_present:
            targets.append(f"  image           {image}")
        if config is not None:
            targets.append(
                f"  config entry    {_config_path(home)}#namespaces.{name}"
            )
        sys.stderr.write(
            f"aetherion: this will permanently delete namespace {name!r}:\n"
            + "\n".join(targets) + "\n"
            "aetherion: continue? [y/N] "
        )
        sys.stderr.flush()
        reply = sys.stdin.readline().strip().lower()
        if reply not in ("y", "yes"):
            sys.stderr.write("aetherion: aborted.\n")
            return 1

    if ns_home.exists():
        _rmtree_any(ns_home)
    # Only auto-delete the build dir when it's the default location.
    # A custom `buildDir:` could be inside a user repo or shared between
    # namespaces — safer to leave it alone.
    if build_dir.exists() and build_dir == default_build_dir:
        _rmtree_any(build_dir)
    if image_present:
        r = subprocess.run(
            [CONTAINER_RUNTIME, "rmi", image],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            sys.stderr.write(
                f"aetherion: failed to remove image {image}: {r.stderr.strip()}\n"
            )
    if config is not None:
        del configs[name]
        save_config(home, configs)
    sys.stderr.write(f"aetherion: deleted namespace {name!r}.\n")
    return 0


# Launch ---------------------------------------------------------------------

@dataclass
class LaunchOptions:
    create: bool = False
    join: str | None = None
    image_override: str | None = None
    env_overrides: list[str] = field(default_factory=list)
    forward_overrides: list[str] = field(default_factory=list)
    volume_overrides: list[str] = field(default_factory=list)
    forward_openclaw: str | None = None


# Launch-form flag tables. Keep here (not constants at module top) so the
# parser stays close to the dataclass it populates.
_LAUNCH_VALUE_FLAGS: dict[str, str] = {
    "-e": "env_overrides",
    "--env": "env_overrides",
    "--forward": "forward_overrides",
    "-v": "volume_overrides",
    "--volume": "volume_overrides",
    "--image": "image_override",
    "--join": "join",
}
_LAUNCH_BOOL_FLAGS: frozenset[str] = frozenset({"--create"})
_LAUNCH_OPTIONAL_VALUE_FLAGS: dict[str, str] = {
    "--forward-openclaw": "forward_openclaw",
}


def _set_launch_option(options: LaunchOptions, attr: str, value: str) -> None:
    cur = getattr(options, attr)
    if isinstance(cur, list):
        cur.append(value)
    else:
        setattr(options, attr, value)


def _parse_launch_argv(argv: list[str]) -> tuple[str | None, list[str], LaunchOptions] | int:
    """Hand-rolled launch parser. argparse can't model "first non-flag
    positional after the namespace is the start of an opaque command" with
    interleaved aetherion flags, so we walk argv ourselves.

    Returns (namespace, command, options) on success, or an exit code on
    parse error.
    """
    options = LaunchOptions()
    namespace: str | None = None
    command: list[str] = []

    i = 0
    n = len(argv)
    while i < n:
        tok = argv[i]

        if tok == "--":
            command = argv[i + 1:]
            break

        if tok in ("-h", "--help"):
            _print_help()
            return 0

        head, eq, val = tok.partition("=")

        # --flag=value
        if eq and head in _LAUNCH_VALUE_FLAGS:
            _set_launch_option(options, _LAUNCH_VALUE_FLAGS[head], val)
            i += 1
            continue
        if eq and head in _LAUNCH_OPTIONAL_VALUE_FLAGS:
            _set_launch_option(options, _LAUNCH_OPTIONAL_VALUE_FLAGS[head], val)
            i += 1
            continue

        if tok in _LAUNCH_BOOL_FLAGS:
            options.create = True
            i += 1
            continue

        if tok in _LAUNCH_VALUE_FLAGS:
            if i + 1 >= n:
                sys.stderr.write(f"aetherion: {tok} requires a value\n")
                return 2
            _set_launch_option(options, _LAUNCH_VALUE_FLAGS[tok], argv[i + 1])
            i += 2
            continue

        if tok in _LAUNCH_OPTIONAL_VALUE_FLAGS:
            attr = _LAUNCH_OPTIONAL_VALUE_FLAGS[tok]
            if i + 1 < n and not argv[i + 1].startswith("-"):
                setattr(options, attr, argv[i + 1])
                i += 2
            else:
                setattr(options, attr, "")
                i += 1
            continue

        if tok.startswith("-"):
            # Unknown flag. Before the namespace, this is a user error;
            # after, it's part of the trailing command (docker semantics).
            if namespace is None:
                sys.stderr.write(f"aetherion: unknown flag {tok!r}\n")
                return 2
            command = argv[i:]
            break

        # Positional.
        if namespace is None:
            namespace = tok
            i += 1
            continue
        command = argv[i:]
        break

    return namespace, command, options


def _join_session(session: str, command: list[str]) -> int:
    cmd = [CONTAINER_RUNTIME, "exec", "-it", session]
    if command:
        cmd += command
    else:
        cmd += ["bash"]
    return subprocess.run(cmd).returncode


def cmd_launch(
    namespace: str | None,
    command: list[str],
    options: LaunchOptions,
) -> int:
    home = Path.home()

    if namespace is None:
        namespace = DEFAULT_NAMESPACE
    err = _validate_namespace_name(namespace)
    if err:
        sys.stderr.write(f"aetherion: {err}\n")
        return 2

    configs = load_config(home)

    # First-run bootstrap: empty config + targeting the default namespace
    # ⇒ create + build + seed end-to-end.
    if not configs and namespace == DEFAULT_NAMESPACE and options.join is None:
        sys.stderr.write(
            f"aetherion: no config at {_config_path(home)} — bootstrapping "
            f"the {DEFAULT_NAMESPACE!r} namespace.\n"
        )
        rc = _create_namespace(home, DEFAULT_NAMESPACE, no_cache=False)
        if rc != 0:
            return rc
        configs = load_config(home)

    if namespace not in configs:
        if options.create:
            rc = _create_namespace(home, namespace, no_cache=False)
            if rc != 0:
                return rc
            configs = load_config(home)
        else:
            sys.stderr.write(
                f"aetherion: namespace {namespace!r} is not registered in "
                f"{_config_path(home)}.\n"
                f"aetherion: create it with `aetherion create namespace "
                f"{namespace}` or launch with `aetherion {namespace} "
                "--create`.\n"
            )
            return 1
    elif options.create:
        sys.stderr.write(
            f"aetherion: namespace {namespace!r} already exists; --create "
            "had nothing to do.\n"
        )

    config = configs[namespace]
    image = options.image_override or config.image

    if options.join is not None:
        return _join_session(options.join, command)

    if not _image_exists(image):
        sys.stderr.write(
            f"aetherion: image {image!r} is not present locally.\n"
            f"aetherion: build it with `aetherion rebuild namespace "
            f"{namespace}`.\n"
        )
        return 1

    ns_home = _namespace_home_dir(home, namespace)
    if not ns_home.exists():
        sys.stderr.write(
            f"aetherion: namespace $HOME missing at {ns_home}. "
            f"Re-seed with `aetherion reset namespace {namespace} --force`.\n"
        )
        return 1

    _warn_on_image_drift(image, ns_home)

    pwd = Path.cwd()

    # Launching from $HOME is ambiguous: the namespace's $HOME is already
    # the container's $HOME, so there's no sensible workdir mount we could
    # add. Hard-fail rather than silently land the user in the namespace
    # $HOME — the silent-rewrite behavior tripped people up.
    if pwd == home:
        sys.stderr.write(
            f"aetherion: refusing to launch from your home directory ({home}).\n"
            "aetherion: cd into a project directory first. The container's "
            f"$HOME is the namespace at {ns_home}, so there's no useful "
            "workdir we could mount.\n"
        )
        return 2

    # Rewrite host home → container home so a host path of ~/foo lands at
    # ~/foo inside too. Anything outside $HOME mounts at its real path.
    if home in pwd.parents:
        container_workdir = f"{CONTAINER_HOME}/{pwd.relative_to(home)}"
        ns_path = ns_home / pwd.relative_to(home)
        if ns_path.exists() and (
            not ns_path.is_dir() or _has_real_content(ns_path)
        ):
            sys.stderr.write(
                f"aetherion: refusing to mount {pwd} over {container_workdir}: "
                f"{ns_path} already has content in the namespace and that "
                "content would be hidden by the mount.\n"
                f"aetherion: either cd elsewhere, or clear {ns_path} first.\n"
            )
            return 2
        workdir_mount = ["-v", f"{pwd}:{container_workdir}:z"]
    else:
        container_workdir = str(pwd)
        workdir_mount = ["-v", f"{pwd}:{container_workdir}:z"]

    # Env vars: config first (fromMap → fromFile → fromEnv), then CLI
    # overrides additively.
    env_args: list[str] = []
    for k, v in config.env_from_map.items():
        env_args += ["-e", f"{k}={v}"]
    for k, file_path in config.env_from_file.items():
        path = _expand(file_path)
        try:
            content = path.read_text().rstrip("\n")
        except OSError as e:
            sys.stderr.write(
                f"aetherion: failed to read env file for {k!r} at {path}: {e}\n"
            )
            return 1
        env_args += ["-e", f"{k}={content}"]
    for k, host_name in config.env_from_env.items():
        if host_name in os.environ:
            env_args += ["-e", f"{k}={os.environ[host_name]}"]
        else:
            sys.stderr.write(
                f"aetherion: host env var {host_name!r} not set (config asks "
                f"for it as {k!r} via environment.fromEnv); skipping.\n"
            )
    for kv in options.env_overrides:
        env_args += ["-e", kv]

    # Ports: config + CLI + openclaw alias.
    publish_args: list[str] = []
    bridge_pairs: list[tuple[int, int]] = []
    for pf in config.port_forwarding:
        publish_args += [
            "-p",
            f"{pf.host_interface}:{pf.host_port}:{pf.container_port}",
        ]
    try:
        for raw in options.forward_overrides:
            publish_args += ["-p", _parse_forward_spec(raw)]
        if options.forward_openclaw is not None:
            bp = _bridge_port_for(OPENCLAW_GATEWAY_PORT)
            publish_args += [
                "-p",
                _build_alias_publish_spec(
                    options.forward_openclaw, OPENCLAW_GATEWAY_PORT, bp,
                ),
            ]
            bridge_pairs.append((OPENCLAW_GATEWAY_PORT, bp))
    except ValueError as e:
        sys.stderr.write(f"aetherion: {e}\n")
        return 2

    if bridge_pairs:
        env_args += [
            "-e",
            f"AETHERION_BRIDGE_PORTS="
            f"{','.join(f'{s}:{b}' for s, b in bridge_pairs)}",
        ]

    # Configured volumes + CLI -v overrides. Mounts that land under
    # CONTAINER_HOME get the same overlap-check as the workdir mount.
    volume_args: list[str] = []
    seen_dst: set[str] = set()
    all_volume_specs = list(config.volumes) + list(options.volume_overrides)
    try:
        for raw in all_volume_specs:
            src, dst = _parse_volume_spec(raw)
            if dst == CONTAINER_HOME or dst.startswith(CONTAINER_HOME + "/"):
                relative = dst[len(CONTAINER_HOME):].lstrip("/")
                ns_path = ns_home / relative if relative else ns_home
                if ns_path.exists() and (
                    not ns_path.is_dir() or _has_real_content(ns_path)
                ):
                    sys.stderr.write(
                        f"aetherion: refusing to mount {src} over {dst}: "
                        f"{ns_path} already has content in the namespace.\n"
                    )
                    return 2
            if dst in seen_dst:
                sys.stderr.write(
                    f"aetherion: duplicate volume mount target {dst!r}; "
                    "later mounts shadow earlier ones.\n"
                )
            seen_dst.add(dst)
            volume_args += ["-v", f"{src}:{dst}:z"]
    except ValueError as e:
        sys.stderr.write(f"aetherion: {e}\n")
        return 2

    instance_id = secrets.token_hex(4)
    instance_name = f"aetherion-{instance_id}"

    label_args = [
        "--label", f"{LABEL_NAMESPACE}={namespace}",
        "--label", f"{LABEL_IMAGE}={image}",
        "--label", f"{LABEL_WORKDIR}={container_workdir}",
    ]

    # The namespace mount lands first; configured volumes layer on, and
    # the workdir mount (always a subpath of CONTAINER_HOME for host
    # paths under $HOME) wins for its subtree.
    run_argv = [
        CONTAINER_RUNTIME, "run", "--rm",
        *user_ns_args(),
        *network_args(),
        "--name", instance_name,
        "--hostname", instance_id,
        *host_internal_args(),
        *label_args,
        *env_args,
        *publish_args,
        "-v", f"{ns_home}:{CONTAINER_HOME}:z",
        *volume_args,
        *workdir_mount,
        "-w", container_workdir,
        "-it",
        image,
        *command,
    ]

    return subprocess.run(run_argv).returncode


# Dispatch / help ------------------------------------------------------------

def _print_help() -> None:
    sys.stdout.write(
        "aetherion — containerized dev environment for AI coding agents.\n"
        "\n"
        "Launch:\n"
        "  aetherion                                  # launch the default namespace\n"
        "  aetherion NAMESPACE                        # launch into NAMESPACE\n"
        "  aetherion NAMESPACE COMMAND [ARG...]       # run COMMAND instead of an interactive shell\n"
        "  aetherion NAMESPACE --create               # create NAMESPACE if missing, then launch\n"
        "  aetherion NAMESPACE --join SESSION [CMD]   # exec into a running session\n"
        "\n"
        "Launch flag overrides (additive on top of the YAML config):\n"
        "  --image REF                                # use a different image for this launch\n"
        "  -e, --env NAME=VALUE                       # add an env var (repeatable)\n"
        "  --forward [ADDR:[HOST_PORT:]]CONTAINER_PORT  # publish a port (repeatable)\n"
        "  -v, --volume SRC[:DST]                     # mount a host path (repeatable)\n"
        "  --forward-openclaw [ADDR][:PORT]           # publish OpenClaw + set up loopback bridge\n"
        "\n"
        "Management verbs:\n"
        "  aetherion config                           # open ~/.aetherion/config.yaml in $EDITOR\n"
        "  aetherion list namespaces                  # registered namespaces + image + seed digest\n"
        "  aetherion list sessions                    # running aetherion containers\n"
        "  aetherion create namespace NAME [--no-cache]   # populate buildDir, build image, seed $HOME\n"
        "  aetherion rebuild namespace NAME [--no-cache]  # rebuild the namespace's image\n"
        "  aetherion reset namespace NAME [--force]       # wipe $HOME and re-seed from the image\n"
        "  aetherion delete namespace NAME [NAME...] [--force]  # remove $HOME, build dir, image, config entry\n"
        "\n"
        f"State lives under ~/.aetherion/. Reserved namespace names: "
        f"{', '.join(sorted(RESERVED_NAMESPACE_NAMES))}.\n"
        "AETHERION_CONTAINER_RUNTIME=docker overrides runtime auto-detection "
        "(podman preferred).\n"
    )


def dispatch_verb(verb: str, argv: list[str]) -> int:
    if verb == "config":
        return cmd_config(argv)
    if verb == "list":
        return cmd_list(argv)
    if verb == "create":
        return cmd_create(argv)
    if verb == "reset":
        return cmd_reset(argv)
    if verb == "rebuild":
        return cmd_rebuild(argv)
    if verb == "delete":
        return cmd_delete(argv)
    raise AssertionError(f"unhandled verb: {verb}")


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    if argv and argv[0] in ("-h", "--help"):
        _print_help()
        return 0

    if argv and argv[0] in RESERVED_NAMESPACE_NAMES:
        return dispatch_verb(argv[0], argv[1:])

    parsed = _parse_launch_argv(argv)
    if isinstance(parsed, int):
        return parsed
    namespace, command, options = parsed
    return cmd_launch(namespace, command, options)


if __name__ == "__main__":
    sys.exit(main())
