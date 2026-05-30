#!/usr/bin/env python3
from __future__ import annotations

import argparse
import errno
import hashlib
import importlib.metadata
import os
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CONTAINER_HOME = "/home/aetherion"
IMAGE_PREFIX = "localhost/aetherion"
DEFAULT_NAMESPACE = "default"
DEFAULT_TEMPLATE = "default"

# Host-side layout, all under ~/.aetherion/:
#   config.yaml            — namespace registry (this file's source of truth)
#   namespaces/<name>/     — bind-mounted as $HOME inside the container
#   containers/<name>/     — per-namespace build context (forked from a template)
#   templates/<name>/      — user-defined templates (shadow baked-in by name)
#   template-cache/<hash>/ — git-cloned template sources, keyed by URL hash
CONFIG_FILENAME = "config.yaml"
NAMESPACES_DIRNAME = "namespaces"
CONTAINERS_DIRNAME = "containers"
TEMPLATES_DIRNAME = "templates"
TEMPLATE_CACHE_DIRNAME = "template-cache"

# The first positional after `aetherion` is either one of these verbs or
# a namespace name. Reserved words can't be used as namespace names so the
# dispatch is never ambiguous.
VERBS = ("config", "list", "create", "edit", "reset", "rebuild", "delete")
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

# A template directory holds these entries; `_populate_build_dir` copies
# them into a namespace's build dir (and the dev-mode overlay replaces
# aetherion-src/ when running from a source checkout).
TEMPLATE_ENTRIES: tuple[str, ...] = ("Dockerfile", "skeleton", "aetherion-src")

# Letters, digits, dot, underscore, dash; no leading dot. Anything else is
# a path-traversal or shell-surprise risk in ~/.aetherion/namespaces/.
# Reused for template names — same safety rules apply.
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


def _user_templates_dir(home: Path) -> Path:
    return _aetherion_dir(home) / TEMPLATES_DIRNAME


def _user_template_dir(home: Path, name: str) -> Path:
    return _user_templates_dir(home) / name


def _bundled_templates_dir() -> Path:
    return _bundled_assets_dir() / TEMPLATES_DIRNAME


def _bundled_template_dir(name: str) -> Path:
    return _bundled_templates_dir() / name


def _template_cache_dir(home: Path) -> Path:
    return _aetherion_dir(home) / TEMPLATE_CACHE_DIRNAME


def _cache_dir_for_url(home: Path, url: str) -> Path:
    # 16 hex chars = 64 bits of namespace; collisions on a single user's
    # template list are vanishingly unlikely and the human-facing key is
    # the URL the user typed, not this hash.
    return _template_cache_dir(home) / hashlib.sha256(url.encode()).hexdigest()[:16]


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
    # The template spec the buildDir was forked from, recorded at create
    # time. Either a local template name (e.g. "default") or a git URL
    # with an optional `#ref` suffix. Informational for `list namespaces`;
    # `rebuild namespace … --refresh-template` re-resolves this. None for
    # legacy namespaces created before templates were tracked.
    template: str | None = None
    # Display forwarding mode: x11 | wayland | auto | none. None ⇒ field
    # absent in YAML; resolved to the built-in default ("none") at launch.
    # CLI `--display` overrides; template `defaults.display` is the
    # initial value at namespace create time.
    display: str | None = None
    # Default command argv for `aetherion NAMESPACE` (no trailing
    # command). None ⇒ field absent in YAML; the launcher falls
    # through to the image's CMD (bash for every baked-in template).
    # CLI `--command` overrides; template `defaults.command` is the
    # initial value at namespace create time. YAML accepts either a
    # string (shlex-split into argv) or a list of strings (used
    # verbatim); we always store as a list here.
    command: list[str] | None = None
    env_from_map: dict[str, str] = field(default_factory=dict)
    env_from_file: dict[str, str] = field(default_factory=dict)
    env_from_env: dict[str, str] = field(default_factory=dict)
    port_forwarding: list[PortForward] = field(default_factory=list)
    volumes: list[str] = field(default_factory=list)


def _parse_command_value(
    raw: Any, *, context: str,
) -> list[str] | None:
    """Coerce a `command:` value (from template defaults, namespace
    YAML, or `--command`) into an argv list.

    Accepts a string (shlex-split — convenient for `command: cursor
    --no-update`) or an explicit list of strings (used verbatim — the
    way to spell argv whose elements contain whitespace). None / empty
    string / empty list all mean "no command set" and return None so
    the launcher falls through to the next resolution step.

    `context` is included in error messages so the caller's source
    (which file or which flag) makes it into the diagnostic."""
    if raw is None:
        return None
    if isinstance(raw, str):
        if not raw.strip():
            return None
        try:
            argv = shlex.split(raw)
        except ValueError as e:
            sys.stderr.write(
                f"aetherion: {context}: `command` failed shlex parse: {e}\n"
            )
            raise SystemExit(1)
        return argv or None
    if isinstance(raw, list):
        argv = [str(x) for x in raw]
        return argv or None
    sys.stderr.write(
        f"aetherion: {context}: `command` must be a string or list of "
        f"strings (got {type(raw).__name__})\n"
    )
    raise SystemExit(1)


def _make_default_namespace_config(home: Path, name: str = DEFAULT_NAMESPACE) -> NamespaceConfig:
    return NamespaceConfig(
        name=name,
        image=default_image_for(name),
        build_dir=_namespace_build_dir(home, name),
        template=DEFAULT_TEMPLATE,
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

        template_raw = conf.get("template")
        template = str(template_raw) if template_raw is not None else None

        display_raw = conf.get("display")
        if display_raw is None:
            display = None
        else:
            display = str(display_raw)
            if display not in DISPLAY_MODES:
                sys.stderr.write(
                    f"aetherion: {path}: namespace {name!r} has invalid "
                    f"`display` value {display!r}; must be one of "
                    f"{', '.join(sorted(DISPLAY_MODES))}.\n"
                )
                raise SystemExit(1)

        command = _parse_command_value(
            conf.get("command"),
            context=f"{path}: namespace {name!r}",
        )

        result[name] = NamespaceConfig(
            name=name,
            image=str(conf.get("image") or default_image_for(name)),
            build_dir=_expand(
                conf.get("buildDir") or _namespace_build_dir(home, name)
            ),
            template=template,
            display=display,
            command=command,
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
        if c.template is not None:
            ns["template"] = c.template
        if c.display is not None:
            ns["display"] = c.display
        if c.command is not None:
            # Always persist as a list — round-trips cleanly even when
            # the user originally wrote a string form in YAML.
            ns["command"] = list(c.command)
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


def _populate_build_dir(dest: Path, source: Path, *, fresh: bool) -> None:
    """Populate `dest` with the template's Dockerfile, skeleton, and
    aetherion-src placeholder.

    `source` is the resolved template directory (either a user-defined
    one under ~/.aetherion/templates/, the package's baked-in
    data/templates/<name>/, or a git-cache clone).

    `fresh=True` (used by `create namespace` and template re-forks): the
    dest is assumed to be empty or absent; everything is copied in. With
    a source checkout, aetherion-src/ is overlaid with the live repo so
    the first build picks up local edits.

    `fresh=False` (used by `rebuild namespace`): the user may have edited
    Dockerfile/skeleton in the buildDir, so we leave those alone. The
    aetherion-src/ overlay is still refreshed in dev mode so the launcher's
    latest source flows into the next build without forcing the user to
    re-create the namespace.
    """
    dest.mkdir(parents=True, exist_ok=True)

    for name in ("Dockerfile", "skeleton"):
        src, dst = source / name, dest / name
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
    elif fresh or not overlay.exists():
        # No checkout: copy the template's aetherion-src/ placeholder so
        # the Dockerfile's COPY succeeds. Skipped on non-fresh populates
        # when an overlay already exists (preserves user edits).
        src = source / "aetherion-src"
        if src.is_dir():
            if overlay.exists() and fresh:
                _rmtree_any(overlay)
            if not overlay.exists():
                shutil.copytree(src, overlay)


# Template resolution -------------------------------------------------------

@dataclass
class TemplateSource:
    """A resolved template ready to read files from. `display_name` is
    what we record in the namespace's config.template (a local name like
    `default`, or a git URL like `https://x.git#v1.0`)."""
    path: Path
    display_name: str


def _validate_template_name(name: str) -> str | None:
    """Validate a local template name (same character rules as namespaces;
    no reserved-word check because template names never appear in a verb
    dispatch slot)."""
    if not _NAMESPACE_NAME_RE.fullmatch(name):
        return (
            f"invalid template name {name!r}: use letters, digits, dot, "
            "underscore, dash; no leading dot."
        )
    return None


def _looks_like_git_url(spec: str) -> bool:
    """Best-effort detection. Two shapes are common in practice:
    `scheme://...` (https, git, ssh) and `user@host:path` (the SSH form
    git itself accepts). Anything else is treated as a local template
    name."""
    if "://" in spec:
        return True
    if re.match(r"^[A-Za-z0-9_.-]+@[A-Za-z0-9_.-]+:", spec):
        return True
    return False


def _split_url_ref(spec: str) -> tuple[str, str | None]:
    """Split `URL#REF` into (URL, REF). `#` is used (not `@`) because
    `git@host:` SSH URLs already contain an @ that would be ambiguous."""
    if "#" in spec:
        url, _, ref = spec.rpartition("#")
        return url, (ref or None)
    return spec, None


def _ensure_git_template(home: Path, url: str, ref: str | None) -> Path:
    """Clone or refresh the git repo into the per-URL cache and check
    out the requested ref. Returns the cache path."""
    cache = _cache_dir_for_url(home, url)
    if not cache.exists():
        cache.parent.mkdir(parents=True, exist_ok=True)
        sys.stderr.write(f"aetherion: cloning {url} into {cache}\n")
        rc = subprocess.run(["git", "clone", url, str(cache)]).returncode
        if rc != 0:
            raise RuntimeError(f"git clone failed for {url}")
    else:
        sys.stderr.write(f"aetherion: fetching updates for {url}\n")
        rc = subprocess.run(
            ["git", "-C", str(cache), "fetch", "--all", "--tags", "--force",
             "--prune"],
        ).returncode
        if rc != 0:
            sys.stderr.write(
                f"aetherion: warning: git fetch failed for {url}; "
                "using cached state.\n"
            )

    if ref is not None:
        rc = subprocess.run(
            ["git", "-C", str(cache), "checkout", "--detach", ref],
        ).returncode
        if rc != 0:
            raise RuntimeError(f"git checkout {ref!r} failed in {cache}")
    else:
        # Fast-forward the cache's currently checked-out branch when no
        # explicit ref was given. Best-effort: if the worktree is detached
        # or the upstream is gone, leave it.
        subprocess.run(
            ["git", "-C", str(cache), "pull", "--ff-only"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    return cache


def _resolve_template_source(home: Path, spec: str) -> TemplateSource:
    """Resolve a `--template VALUE`-style spec to a directory we can copy
    from. Local names check the user dir first, then the baked-in dir.
    URLs hit the cache (cloned/updated)."""
    if _looks_like_git_url(spec):
        url, ref = _split_url_ref(spec)
        cache = _ensure_git_template(home, url, ref)
        return TemplateSource(path=cache, display_name=spec)

    err = _validate_template_name(spec)
    if err:
        raise ValueError(err)
    user = _user_template_dir(home, spec)
    if user.is_dir():
        return TemplateSource(path=user, display_name=spec)
    bundled = _bundled_template_dir(spec)
    if bundled.is_dir():
        return TemplateSource(path=bundled, display_name=spec)
    available = sorted(_known_template_names(home))
    raise ValueError(
        f"no such template {spec!r}; available: "
        f"{', '.join(available) if available else '(none)'}"
    )


def _known_template_names(home: Path) -> set[str]:
    """Union of user-defined and baked-in template names."""
    names: set[str] = set()
    user_root = _user_templates_dir(home)
    if user_root.is_dir():
        for p in user_root.iterdir():
            if p.is_dir():
                names.add(p.name)
    bundled = _bundled_templates_dir()
    if bundled.is_dir():
        for p in bundled.iterdir():
            if p.is_dir():
                names.add(p.name)
    return names


def _template_sources_for(home: Path, name: str) -> tuple[Path | None, Path | None]:
    """Returns (user_path_or_None, bundled_path_or_None). Either can be
    None if that source doesn't have the template."""
    user = _user_template_dir(home, name)
    bundled = _bundled_template_dir(name)
    return (user if user.is_dir() else None,
            bundled if bundled.is_dir() else None)


# Per-template metadata (template.yaml) ------------------------------------

TEMPLATE_CONFIG_FILENAME = "template.yaml"

# Recognized display modes for namespaces. None of the YAML / CLI / template
# layers is forced to pick one of these; they're validated centrally so
# unknown values fail loud rather than silently no-op.
DISPLAY_MODES: frozenset[str] = frozenset({"x11", "wayland", "auto", "none"})


@dataclass
class PlatformSpec:
    """One supported-host tuple from a template's `platforms:` list.
    Any field may be `*` to wildcard it."""
    os: str
    arch: str
    runtime: str

    def matches(self, host: "HostPlatform") -> bool:
        return (
            (self.os == "*" or self.os == host.os)
            and (self.arch == "*" or self.arch == host.arch)
            and (self.runtime == "*" or self.runtime == host.runtime)
        )

    def __str__(self) -> str:
        return f"{self.os}/{self.arch}/{self.runtime}"


@dataclass
class HostPlatform:
    os: str
    arch: str
    runtime: str

    def __str__(self) -> str:
        return f"{self.os}/{self.arch}/{self.runtime}"


@dataclass
class TemplateConfig:
    """Parsed template.yaml. All fields are optional; templates without a
    template.yaml end up with an instance whose every field is None / [] /
    {} (i.e., universally portable, no defaults, no description)."""
    description: str | None = None
    platforms: list[PlatformSpec] | None = None  # None ⇒ skip validation
    defaults: dict[str, Any] = field(default_factory=dict)


def _detect_host_platform() -> HostPlatform:
    """Best-effort host detection. arch is normalized to amd64/arm64 so it
    matches the values templates would naturally write."""
    sys_name = sys.platform  # 'linux', 'darwin', ...
    host_os = "linux" if sys_name.startswith("linux") else (
        "darwin" if sys_name == "darwin" else sys_name
    )

    raw_arch = os.uname().machine.lower()
    arch_map = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }
    host_arch = arch_map.get(raw_arch, raw_arch)

    # CONTAINER_RUNTIME may be an absolute path on some hosts; the
    # template-side declaration uses the basename.
    runtime = Path(CONTAINER_RUNTIME).name

    return HostPlatform(os=host_os, arch=host_arch, runtime=runtime)


def _template_config_path(template_dir: Path) -> Path:
    return template_dir / TEMPLATE_CONFIG_FILENAME


def load_template_config(template_dir: Path) -> TemplateConfig:
    """Read `<template_dir>/template.yaml` if present. Missing file is
    treated as 'no metadata' (universally portable, no defaults). Malformed
    files SystemExit with a pointer at what went wrong."""
    path = _template_config_path(template_dir)
    if not path.is_file():
        return TemplateConfig()
    try:
        with path.open("r") as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        sys.stderr.write(f"aetherion: failed to parse {path}: {e}\n")
        raise SystemExit(1)
    if not isinstance(data, dict):
        sys.stderr.write(f"aetherion: {path}: top-level must be a mapping\n")
        raise SystemExit(1)

    description = data.get("description")
    if description is not None and not isinstance(description, str):
        sys.stderr.write(f"aetherion: {path}: `description` must be a string\n")
        raise SystemExit(1)

    platforms_raw = data.get("platforms")
    platforms: list[PlatformSpec] | None
    if platforms_raw is None:
        platforms = None
    elif isinstance(platforms_raw, list):
        platforms = []
        for entry in platforms_raw:
            if not isinstance(entry, dict):
                sys.stderr.write(
                    f"aetherion: {path}: each `platforms` entry must be a "
                    f"mapping (got {entry!r})\n"
                )
                raise SystemExit(1)
            platforms.append(PlatformSpec(
                os=str(entry.get("os") or "*"),
                arch=str(entry.get("arch") or "*"),
                runtime=str(entry.get("runtime") or "*"),
            ))
    else:
        sys.stderr.write(f"aetherion: {path}: `platforms` must be a list\n")
        raise SystemExit(1)

    defaults_raw = data.get("defaults") or {}
    if not isinstance(defaults_raw, dict):
        sys.stderr.write(f"aetherion: {path}: `defaults` must be a mapping\n")
        raise SystemExit(1)
    # Validate any defaults we recognize; unknown keys are accepted (forward
    # compatibility with templates ahead of the launcher).
    if "display" in defaults_raw:
        mode = str(defaults_raw["display"])
        if mode not in DISPLAY_MODES:
            sys.stderr.write(
                f"aetherion: {path}: defaults.display must be one of "
                f"{', '.join(sorted(DISPLAY_MODES))} (got {mode!r})\n"
            )
            raise SystemExit(1)
    if "command" in defaults_raw:
        # Validate at parse time so a broken template default fails
        # loud at namespace-create time rather than silently dropping
        # the field. Result discarded — we re-parse on apply so the
        # defaults dict stays the raw shape templates wrote.
        _parse_command_value(
            defaults_raw["command"],
            context=f"{path}: defaults.command",
        )

    return TemplateConfig(
        description=description,
        platforms=platforms,
        defaults=dict(defaults_raw),
    )


def _check_template_platform(
    template_name: str,
    config: TemplateConfig,
    host: HostPlatform,
) -> str | None:
    """Returns None when supported, otherwise an error string ready to
    show to the user. Templates with no `platforms:` field skip the check."""
    if config.platforms is None:
        return None
    for spec in config.platforms:
        if spec.matches(host):
            return None
    supported = ", ".join(str(p) for p in config.platforms) or "(none declared)"
    return (
        f"template {template_name!r} does not support this host "
        f"({host}). Supported: {supported}."
    )


def _apply_template_defaults(
    ns_config: NamespaceConfig,
    defaults: dict[str, Any],
) -> None:
    """Merge a template's `defaults:` block into a fresh NamespaceConfig
    that's about to be saved. Only fields the launcher knows about get
    applied — unknown keys are accepted in the YAML (forward-compat with
    templates ahead of the launcher) but silently ignored here.

    Caller is responsible for letting explicit user values win; this
    helper assumes ns_config is the freshly-defaulted scaffold so any
    template default it sets is wanted."""
    if "display" in defaults and ns_config.display is None:
        mode = str(defaults["display"])
        if mode in DISPLAY_MODES:
            ns_config.display = mode
    if "command" in defaults and ns_config.command is None:
        ns_config.command = _parse_command_value(
            defaults["command"],
            context="template defaults.command",
        )


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
    succeeds even on Go's 0555 module-cache dirs.

    Rootless podman creates bind-mount targets inside the user namespace,
    so any stub it creates ends up owned by subuid-root on the host —
    `shutil.rmtree` then fails with PermissionError because our normal UID
    can't unlink them. We fall back to `<runtime> unshare`, which
    re-enters the userns (no sudo required) where the same files appear
    as our normal UID and a vanilla `rm -rf` can do its thing. The
    fallback only kicks in for rootless podman; docker (which runs
    `--user 1000:1000` end-to-end) and rootful runtimes either won't hit
    the problem or have plain rm available."""
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
    try:
        shutil.rmtree(path)
    except PermissionError:
        if _RUNTIME_IS_DOCKER or os.geteuid() == 0:
            raise
        sys.stderr.write(
            f"aetherion: subuid-owned files under {path}; retrying via "
            f"`{CONTAINER_RUNTIME} unshare rm -rf` (no sudo required).\n"
        )
        r = subprocess.run([
            CONTAINER_RUNTIME, "unshare",
            "sh", "-c",
            # chmod first so 0555 dirs (Go module cache) inside the userns
            # become writable; both steps run inside the userns so subuid
            # ownership is transparent to them.
            'chmod -R u+w "$1" 2>/dev/null; rm -rf "$1"',
            "sh", str(path),
        ])
        if r.returncode != 0 or path.exists():
            sys.stderr.write(
                f"aetherion: `{CONTAINER_RUNTIME} unshare` cleanup of "
                f"{path} failed. You may need to remove it manually.\n"
            )
            raise


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
        choices=(
            "namespace", "namespaces",
            "session", "sessions",
            "template", "templates",
        ),
        help=(
            "`namespaces` (registered), `sessions` (running containers), "
            "or `templates` (baked-in + user-defined)."
        ),
    )
    args = parser.parse_args(argv)

    home = Path.home()
    if args.what in ("namespace", "namespaces"):
        return _list_namespaces(home)
    if args.what in ("template", "templates"):
        return _list_templates(home)
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
        if name in configs:
            cfg = configs[name]
            image = cfg.image
            template = cfg.template or "(unknown)"
        else:
            image = "(unregistered)"
            template = "(unknown)"
        rows.append((name, image, template, seed))

    _print_table(("NAMESPACE", "IMAGE", "TEMPLATE", "SEEDED FROM"), rows)
    return 0


def _list_templates(home: Path) -> int:
    names = sorted(_known_template_names(home))
    if not names:
        # This shouldn't happen — the bundled default ships with the
        # package — but bail gracefully if the package data is missing.
        sys.stderr.write("aetherion: no templates available.\n")
        return 0

    rows: list[tuple[str, ...]] = []
    for name in names:
        user, bundled = _template_sources_for(home, name)
        if user is not None and bundled is not None:
            active, shadowed = "user", "baked-in"
            active_path = user
        elif user is not None:
            active, shadowed = "user", ""
            active_path = user
        else:
            active, shadowed = "baked-in", ""
            assert bundled is not None
            active_path = bundled
        tcfg = load_template_config(active_path)
        desc = tcfg.description or ""
        rows.append((name, active, shadowed, desc))

    _print_table(
        ("TEMPLATE", "ACTIVE", "SHADOWED", "DESCRIPTION"),
        rows,
    )
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
    parser.add_argument("what", choices=("namespace", "template"))
    parser.add_argument("name", help="name to create")
    parser.add_argument(
        "--template", metavar="SPEC", default=None,
        help=(
            "Template to fork from. Either a local template name "
            f"(default: {DEFAULT_TEMPLATE!r}) or a git URL with optional "
            "`#REF` (e.g. `https://example.com/foo.git#v1.0`)."
        ),
    )
    parser.add_argument(
        "--no-cache", action="store_true",
        help="Discard layer cache during the image build (`namespace` only).",
    )
    args = parser.parse_args(argv)

    home = Path.home()
    if args.what == "namespace":
        return _create_namespace(
            home, args.name,
            template=args.template,
            no_cache=args.no_cache,
        )
    return _create_template(home, args.name, base=args.template)


def _create_namespace(
    home: Path,
    name: str,
    *,
    template: str | None = None,
    no_cache: bool = False,
) -> int:
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

    template_spec = template or DEFAULT_TEMPLATE
    try:
        src = _resolve_template_source(home, template_spec)
    except (ValueError, RuntimeError) as e:
        sys.stderr.write(f"aetherion: {e}\n")
        return 1
    config.template = src.display_name

    # Read template.yaml for platform validation + defaults to merge into
    # the new namespace's config. Templates without one are universally
    # portable and supply no defaults (current behavior).
    tcfg = load_template_config(src.path)
    host = _detect_host_platform()
    platform_err = _check_template_platform(src.display_name, tcfg, host)
    if platform_err is not None:
        sys.stderr.write(f"aetherion: {platform_err}\n")
        return 1

    # Apply template defaults the launcher recognizes. Unknown keys are
    # ignored (forward compat); anything explicitly passed on the CLI
    # would have already won at this point.
    _apply_template_defaults(config, tcfg.defaults)

    sys.stderr.write(
        f"aetherion: creating namespace {name!r} from template "
        f"{src.display_name!r}: build dir at {build_dir}, image "
        f"{config.image}.\n"
    )
    _populate_build_dir(build_dir, src.path, fresh=True)
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
    template_group = parser.add_mutually_exclusive_group()
    template_group.add_argument(
        "--refresh-template", action="store_true",
        help=(
            "Re-fork the buildDir from the template currently recorded "
            "for this namespace. Drops any in-place edits to Dockerfile / "
            "skeleton."
        ),
    )
    template_group.add_argument(
        "--template", metavar="SPEC", default=None,
        help=(
            "Swap to a different template (local name or git URL[#REF]) "
            "and re-fork the buildDir from it. Updates the namespace's "
            "stored template."
        ),
    )
    args = parser.parse_args(argv)

    home = Path.home()
    return _rebuild_namespace(
        home, args.name,
        no_cache=args.no_cache,
        refresh_template=args.refresh_template,
        new_template=args.template,
    )


def _rebuild_namespace(
    home: Path,
    name: str,
    *,
    no_cache: bool,
    refresh_template: bool = False,
    new_template: str | None = None,
) -> int:
    configs = load_config(home)
    if name not in configs:
        sys.stderr.write(
            f"aetherion: namespace {name!r} not registered in "
            f"{_config_path(home)}.\n"
        )
        return 1
    config = configs[name]

    template_to_apply: str | None = None
    if new_template is not None:
        template_to_apply = new_template
    elif refresh_template:
        if config.template is None:
            sys.stderr.write(
                f"aetherion: namespace {name!r} has no recorded template "
                "to refresh from. Pass `--template SPEC` to set one.\n"
            )
            return 1
        template_to_apply = config.template

    if template_to_apply is not None:
        try:
            src = _resolve_template_source(home, template_to_apply)
        except (ValueError, RuntimeError) as e:
            sys.stderr.write(f"aetherion: {e}\n")
            return 1
        action = "swapping to" if new_template is not None else "refreshing from"
        sys.stderr.write(
            f"aetherion: {action} template {src.display_name!r}; "
            f"re-forking {config.build_dir} (existing Dockerfile / skeleton "
            "edits will be replaced).\n"
        )
        _populate_build_dir(config.build_dir, src.path, fresh=True)
        config.template = src.display_name
        configs[name] = config
        save_config(home, configs)
    elif not config.build_dir.is_dir():
        # Build dir missing but no template flag: re-fork from the stored
        # template (or default if none) so we have something to build.
        spec = config.template or DEFAULT_TEMPLATE
        sys.stderr.write(
            f"aetherion: build dir for namespace {name!r} is missing at "
            f"{config.build_dir}; re-forking from template {spec!r}.\n"
        )
        try:
            src = _resolve_template_source(home, spec)
        except (ValueError, RuntimeError) as e:
            sys.stderr.write(f"aetherion: {e}\n")
            return 1
        _populate_build_dir(config.build_dir, src.path, fresh=True)
        if config.template is None:
            config.template = src.display_name
            configs[name] = config
            save_config(home, configs)
    else:
        # Preserve user edits to Dockerfile/skeleton; only refresh
        # aetherion-src/ in dev mode. Use the recorded template (or the
        # default) as the placeholder source for aetherion-src/ when not
        # in dev mode.
        spec = config.template or DEFAULT_TEMPLATE
        try:
            src = _resolve_template_source(home, spec)
        except (ValueError, RuntimeError) as e:
            sys.stderr.write(f"aetherion: {e}\n")
            return 1
        _populate_build_dir(config.build_dir, src.path, fresh=False)

    return _build_image(config.image, config.build_dir, no_cache=no_cache)


def cmd_delete(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="aetherion delete")
    parser.add_argument("what", choices=("namespace", "template"))
    parser.add_argument(
        "names", nargs="+", metavar="NAME",
        help="one or more namespace or template names to delete",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Skip the confirmation prompt.",
    )
    args = parser.parse_args(argv)

    home = Path.home()
    rc = 0
    for name in args.names:
        if args.what == "namespace":
            r = _delete_namespace(home, name, force=args.force)
        else:
            r = _delete_template(home, name, force=args.force)
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


# Template operations -------------------------------------------------------

def _create_template(home: Path, name: str, *, base: str | None) -> int:
    err = _validate_template_name(name)
    if err:
        sys.stderr.write(f"aetherion: {err}\n")
        return 2

    user_dir = _user_template_dir(home, name)
    if user_dir.exists() and _has_real_content(user_dir):
        sys.stderr.write(
            f"aetherion: template {name!r} already exists at {user_dir}. "
            f"Delete it first with `aetherion delete template {name}` or "
            "edit it in place.\n"
        )
        return 1

    base_spec = base or DEFAULT_TEMPLATE
    try:
        src = _resolve_template_source(home, base_spec)
    except (ValueError, RuntimeError) as e:
        sys.stderr.write(f"aetherion: {e}\n")
        return 1

    # Warn when the new user template shadows a baked-in one of the same
    # name. The user can revert by deleting the user copy.
    if _bundled_template_dir(name).is_dir() and not _looks_like_git_url(name):
        sys.stderr.write(
            f"aetherion: warning: user template {name!r} shadows a baked-in "
            "template of the same name. Run "
            f"`aetherion delete template {name}` to revert to the baked-in "
            "version.\n"
        )

    user_dir.parent.mkdir(parents=True, exist_ok=True)
    if user_dir.exists():
        _rmtree_any(user_dir)
    user_dir.mkdir()

    for entry in TEMPLATE_ENTRIES:
        s, d = src.path / entry, user_dir / entry
        if s.is_dir():
            shutil.copytree(s, d, dirs_exist_ok=True)
        elif s.is_file():
            shutil.copy2(s, d)
        else:
            # Missing entry in source: write a placeholder for aetherion-src
            # so downstream Dockerfile COPYs succeed; skip others.
            if entry == "aetherion-src":
                d.mkdir(parents=True)
                (d / ".keep").touch()

    sys.stderr.write(
        f"aetherion: created template {name!r} at {user_dir} "
        f"(forked from {src.display_name!r}).\n"
    )
    return 0


def _delete_template(home: Path, name: str, *, force: bool) -> int:
    err = _validate_template_name(name)
    if err:
        sys.stderr.write(f"aetherion: {err}\n")
        return 2

    user_dir = _user_template_dir(home, name)
    bundled = _bundled_template_dir(name)
    bundled_present = bundled.is_dir()

    if not user_dir.exists():
        if bundled_present:
            sys.stderr.write(
                f"aetherion: template {name!r} is baked-in and read-only; "
                "nothing to delete on the host side. "
                f"Run `aetherion create template {name}` to fork it first.\n"
            )
        else:
            sys.stderr.write(
                f"aetherion: no template {name!r} to delete.\n"
            )
        return 1

    # Warn if any namespace still references this template name (string
    # match — git URLs that resolve via the cache won't match a local name).
    configs = load_config(home)
    referring = sorted(
        n for n, c in configs.items() if c.template == name
    )
    if referring:
        sys.stderr.write(
            f"aetherion: warning: namespace(s) {', '.join(referring)} record "
            f"template {name!r}; future `--refresh-template` will resolve "
            f"to the {'baked-in' if bundled_present else 'next match or fail'}.\n"
        )

    if not force:
        if not sys.stdin.isatty():
            sys.stderr.write(
                "aetherion: `delete template` requires a tty for "
                "confirmation, or pass --force.\n"
            )
            return 2
        sys.stderr.write(
            f"aetherion: this will delete user template {name!r} at "
            f"{user_dir}.\n"
        )
        if bundled_present:
            sys.stderr.write(
                f"aetherion: the baked-in {name!r} template will become "
                "active again after deletion.\n"
            )
        sys.stderr.write("aetherion: continue? [y/N] ")
        sys.stderr.flush()
        reply = sys.stdin.readline().strip().lower()
        if reply not in ("y", "yes"):
            sys.stderr.write("aetherion: aborted.\n")
            return 1

    _rmtree_any(user_dir)
    sys.stderr.write(f"aetherion: deleted user template {name!r}.\n")
    return 0


def _edit_template(home: Path, name: str) -> int:
    err = _validate_template_name(name)
    if err:
        sys.stderr.write(f"aetherion: {err}\n")
        return 2

    user_dir = _user_template_dir(home, name)
    if not user_dir.is_dir():
        if not _bundled_template_dir(name).is_dir():
            sys.stderr.write(
                f"aetherion: no template {name!r}. Available: "
                f"{', '.join(sorted(_known_template_names(home))) or '(none)'}.\n"
            )
            return 1
        # Auto-fork the baked-in template so the user has something writable.
        sys.stderr.write(
            f"aetherion: template {name!r} is baked-in (read-only); "
            "forking into your user templates so it's editable.\n"
        )
        rc = _create_template(home, name, base=name)
        if rc != 0:
            return rc

    dockerfile = user_dir / "Dockerfile"
    if not dockerfile.is_file():
        sys.stderr.write(
            f"aetherion: template {name!r} has no Dockerfile at {dockerfile}; "
            "create one or restore from a fresh fork.\n"
        )
        return 1
    editor = os.environ.get("EDITOR") or "vi"
    return subprocess.run([editor, str(dockerfile)]).returncode


def cmd_edit(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="aetherion edit",
        description="Open a template's Dockerfile in $EDITOR (auto-forks "
                    "baked-in templates).",
    )
    parser.add_argument("what", choices=("template",))
    parser.add_argument("name", help="template name to edit")
    args = parser.parse_args(argv)

    return _edit_template(Path.home(), args.name)


# Launch ---------------------------------------------------------------------

@dataclass
class LaunchOptions:
    create: bool = False
    join: str | None = None
    image_override: str | None = None
    template: str | None = None
    display: str | None = None
    # Raw `--command` value as the user typed it. Resolved to an argv
    # list at launch via _parse_command_value. None ⇒ flag not given.
    command_override: str | None = None
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
    "--template": "template",
    "--display": "display",
    "--command": "command_override",
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


def _resolve_display_mode(mode: str) -> str:
    """Turn `auto` into the concrete mode it should bind to. Other
    values pass through unchanged.

    Linux hosts read $WAYLAND_DISPLAY / $DISPLAY directly and fall
    through to `none` when neither is set — a reasonable default for
    SSH sessions and other headless contexts.

    macOS has exactly one display backend (XQuartz), so `auto` on
    darwin collapses to `x11` unconditionally and lets the downstream
    x11 path probe + halt with an install hint. Silently falling
    through to `none` like Linux would just defer the failure to
    "Missing X server or $DISPLAY" inside the container — actively
    unhelpful when the user clearly wanted display (they passed
    `auto`, not `none`). Users who actually want headless on macOS can
    pass `--display none`."""
    if mode != "auto":
        return mode
    if sys.platform == "darwin":
        return "x11"
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return "none"


_CONTAINER_RUNTIME_DIR = "/run/user/1000"


def _add_dbus_args(
    volumes: list[str],
    env_dict: dict[str, str],
) -> None:
    """Mount the host's D-Bus session + system buses through to the
    container, when present. Without the session bus, Electron's
    `shell.openExternal` (xdg-open) silently no-ops — the most visible
    symptom is sign-in / OAuth flows where clicking the button just does
    nothing because no browser ever opens. Forwarding also makes
    notifications, the secret service / keyring, and other portals work.

    Both buses are best-effort: if the host doesn't have a session bus
    socket where we expect it, we just skip rather than refusing to
    launch."""
    session_sock: Path | None = None
    addr = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
    if addr.startswith("unix:path="):
        # Standard form: unix:path=/run/user/1000/bus[,…]
        candidate = Path(addr[len("unix:path="):].partition(",")[0])
        if candidate.exists():
            session_sock = candidate
    if session_sock is None:
        rd = os.environ.get("XDG_RUNTIME_DIR")
        if rd:
            candidate = Path(rd) / "bus"
            if candidate.exists():
                session_sock = candidate

    if session_sock is not None:
        in_sock = f"{_CONTAINER_RUNTIME_DIR}/bus"
        volumes += ["-v", f"{session_sock}:{in_sock}:rw"]
        env_dict["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={in_sock}"
        env_dict.setdefault("XDG_RUNTIME_DIR", _CONTAINER_RUNTIME_DIR)

    # System bus — mount at the same canonical path so libdbus inside
    # the container finds it without extra env hints, and silences the
    # "Failed to connect to socket /run/dbus/system_bus_socket" startup
    # noise that Electron prints during init.
    sys_sock = Path("/run/dbus/system_bus_socket")
    if sys_sock.exists():
        volumes += [
            "-v",
            "/run/dbus/system_bus_socket:/run/dbus/system_bus_socket:rw",
        ]


def _xquartz_installed() -> bool:
    """XQuartz drops both an app bundle and an /opt/X11 prefix on install.
    Either being present is enough to say it's installed; we don't care
    which channel (cask, .pkg, source) put it there."""
    return (
        Path("/Applications/Utilities/XQuartz.app").is_dir()
        or Path("/opt/X11").is_dir()
    )


def _xquartz_listening() -> bool:
    """Probe localhost:6000 — the well-known X11 display-0 TCP port — to
    see if XQuartz (or something else) is actually accepting connections.
    A short timeout keeps the launch path responsive when nothing's
    there."""
    try:
        with socket.create_connection(("localhost", 6000), timeout=0.5):
            return True
    except OSError:
        return False


def _xquartz_ensure_ready() -> bool:
    """Make XQuartz ready for the container: TCP listener enabled,
    process running, localhost authorized via xhost. Returns True if
    we end up in that state, False if any step failed (caller then
    falls back to the manual hint).

    Three things have to be true for the container to talk to XQuartz:
      1. `org.xquartz.X11 nolisten_tcp` == false — the network listener
         the Preferences → Security checkbox toggles.
      2. XQuartz process is running so the listener actually binds.
      3. `xhost +localhost` so XQuartz accepts the container's
         connection (which originates from the VM's NAT address).

    Each step is a one-line command, but failing any of them leaves
    the user staring at "Missing X server" inside the container. Doing
    them ourselves removes the entire macOS prerequisite checklist.

    Idempotent: every step is a no-op when already in the target
    state. Prints a one-line note for each step we actually take so
    the user can see what changed."""
    # Step 1 — pref. `defaults read` exits 0 with the value on stdout
    # when set, nonzero when the key is absent (which means "use the
    # XQuartz default", which is 1 / don't-listen).
    pref = subprocess.run(
        ["defaults", "read", "org.xquartz.X11", "nolisten_tcp"],
        capture_output=True, text=True, timeout=5, check=False,
    )
    nolisten = pref.stdout.strip() if pref.returncode == 0 else "1"

    restart_needed = False
    if nolisten != "0":
        write = subprocess.run(
            ["defaults", "write", "org.xquartz.X11",
             "nolisten_tcp", "-bool", "false"],
            capture_output=True, timeout=5, check=False,
        )
        if write.returncode != 0:
            return False
        sys.stderr.write(
            "aetherion: enabled XQuartz TCP listener "
            "(org.xquartz.X11 nolisten_tcp=false).\n"
        )
        restart_needed = True

    # Step 2 — process. Pref changes only take effect on the next
    # launch, so if we flipped it we have to restart; otherwise we only
    # touch XQuartz if it isn't already listening.
    if restart_needed or not _xquartz_listening():
        if restart_needed:
            sys.stderr.write("aetherion: restarting XQuartz…\n")
            # Send Quit via AppleScript first (graceful — XQuartz can
            # tear down clients cleanly). pkill is the brute fallback
            # if osascript times out or the app ignores the event.
            subprocess.run(
                ["osascript", "-e",
                 'tell application "XQuartz" to quit'],
                capture_output=True, timeout=10, check=False,
            )
            for _ in range(20):
                time.sleep(0.25)
                if not _xquartz_listening():
                    break
            else:
                subprocess.run(
                    ["pkill", "-x", "Xquartz"],
                    capture_output=True, check=False,
                )
                time.sleep(0.5)
        else:
            sys.stderr.write("aetherion: launching XQuartz…\n")
        subprocess.run(
            ["open", "-a", "XQuartz"],
            capture_output=True, timeout=10, check=False,
        )
        # XQuartz cold-start is a couple of seconds; give it up to ~10.
        for _ in range(40):
            time.sleep(0.25)
            if _xquartz_listening():
                break
        else:
            return False

    # Step 3 — authorization. The container's connection comes in from
    # the VM's NAT address, which xhost has to allow. `+localhost` is
    # the wide-open form; narrower (per-IP) auth doesn't help since
    # that NAT address changes each container start. Safe under
    # "trusted single-user dev machine" assumptions, which match the
    # rest of aetherion's threat model.
    #
    # xhost lives at /opt/X11/bin/xhost, which isn't always on PATH.
    # DISPLAY=:0 routes the call through XQuartz's unix socket; the
    # socket is owned by the calling user and doesn't itself need
    # xhost auth.
    xhost = shutil.which("xhost") or "/opt/X11/bin/xhost"
    if Path(xhost).exists():
        subprocess.run(
            [xhost, "+localhost"],
            capture_output=True, timeout=5, check=False,
            env={**os.environ, "DISPLAY": ":0"},
        )
    return True


def _display_runtime_args_darwin(
    mode: str,
) -> tuple[list[str], list[str], list[str]]:
    """macOS host (Docker Desktop / podman-machine). The container runs
    inside a Linux VM, so the Linux host's display plumbing — the
    /tmp/.X11-unix socket dir, $XAUTHORITY, the D-Bus buses, /dev/dri —
    would all resolve against the VM, not macOS, if we tried to mount
    them. Instead we point X11 at XQuartz running on the macOS host over
    TCP via the well-known host.docker.internal alias.

    Prereqs the user has to set up themselves, in this order: install
    XQuartz, launch it with "Allow connections from network clients"
    enabled (Preferences → Security; quit + relaunch for the listener
    to actually bind), and `xhost +localhost` so XQuartz accepts the
    connection from the VM. We probe for each here; missing prereqs
    halt the launch with SystemExit(2) because `display: x11` is a
    committed declaration that the namespace needs X11, and silently
    dropping to no-display would just produce an opaque crash inside
    the container (Cursor's "Missing X server or $DISPLAY" segfault).
    Users who genuinely want headless can pass `--display none` to
    override.

    Note: the `auto` mode flows through _resolve_display_mode, which on
    darwin only returns x11 when the prereqs are already satisfied —
    so the halts below only fire when the user (or a template default)
    explicitly committed to x11."""
    if mode == "wayland":
        sys.stderr.write(
            "aetherion: display: wayland not supported on macOS hosts "
            "(no Wayland compositor); skipping forwarding.\n"
        )
        return [], [], []
    if mode != "x11":
        return [], [], []

    if not _xquartz_installed():
        # No X server at all. This is the one prereq the launcher can't
        # satisfy itself — Homebrew, App Store, and the .pkg installer
        # all need user interaction. Lead with the brew one-liner;
        # fall back to a brew.sh pointer when Homebrew isn't around.
        sys.stderr.write(
            "aetherion: display: x11 requires XQuartz on macOS, which "
            "isn't installed.\n"
        )
        if shutil.which("brew") is not None:
            sys.stderr.write(
                "aetherion:   install:  brew install --cask xquartz\n"
            )
        else:
            sys.stderr.write(
                "aetherion:   install Homebrew first (https://brew.sh), "
                "then:\n"
                "aetherion:     brew install --cask xquartz\n"
            )
        sys.stderr.write(
            "aetherion: refusing to launch without the X server "
            "(pass --display none to override).\n"
        )
        raise SystemExit(2)

    # Auto-configure XQuartz: enable the TCP listener, restart if
    # needed, run `xhost +localhost`. Idempotent — silent when already
    # in the right state.
    if not _xquartz_ensure_ready():
        # Auto-config bailed somewhere (osascript timeout, defaults
        # write failed, listener never came up). Fall back to the
        # manual hint and halt.
        sys.stderr.write(
            "aetherion: display: x11 — couldn't auto-configure XQuartz. "
            "Try manually:\n"
            "aetherion:   - XQuartz menu → Preferences → Security → "
            "\"Allow connections from\n"
            "aetherion:     network clients\", then quit + relaunch "
            "XQuartz.\n"
            "aetherion:   - In a macOS terminal: `xhost +localhost`.\n"
            "aetherion:   - Verify: `lsof -iTCP:6000 -sTCP:LISTEN` "
            "should show XQuartz.\n"
            "aetherion: refusing to launch without the X server "
            "(pass --display none to override).\n"
        )
        raise SystemExit(2)

    env = ["-e", "DISPLAY=host.docker.internal:0"]
    return [], env, []


def _display_runtime_args(mode: str) -> tuple[list[str], list[str], list[str]]:
    """For a resolved display mode (x11/wayland/none — never `auto`),
    return the `-v`, `-e`, and extra runtime args to inject. Empty lists
    for `none`. The caller passes them through to `<runtime> run`.

    Both GUI modes also forward the host's D-Bus session + system buses;
    Electron / xdg-open / notifications / secret-service all depend on
    the session bus being reachable, and without it features like the
    Cursor sign-in flow silently no-op."""
    if mode == "none":
        return [], [], []
    if sys.platform == "darwin":
        return _display_runtime_args_darwin(mode)

    volumes: list[str] = []
    env_dict: dict[str, str] = {}
    extra: list[str] = []

    if mode == "x11":
        display = os.environ.get("DISPLAY")
        if not display:
            sys.stderr.write(
                "aetherion: display: x11 requested but $DISPLAY isn't set "
                "on the host; skipping forwarding.\n"
            )
            return [], [], []
        # Mount the host's X socket dir read-write — Cursor/Electron writes
        # MIT-SHM segments via the socket and read-only would error.
        x11_sock = Path("/tmp/.X11-unix")
        if x11_sock.is_dir():
            volumes += ["-v", "/tmp/.X11-unix:/tmp/.X11-unix:rw"]
        env_dict["DISPLAY"] = display
        xauth = os.environ.get("XAUTHORITY")
        if xauth and Path(xauth).is_file():
            # Mount the auth cookie at a stable in-container path so
            # XAUTHORITY can point at it without depending on the host's
            # path layout (which differs across distros).
            in_xauth = f"{CONTAINER_HOME}/.Xauthority"
            volumes += ["-v", f"{xauth}:{in_xauth}:ro"]
            env_dict["XAUTHORITY"] = in_xauth

    elif mode == "wayland":
        wd = os.environ.get("WAYLAND_DISPLAY")
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
        if not wd or not runtime_dir:
            sys.stderr.write(
                "aetherion: display: wayland requested but "
                "$WAYLAND_DISPLAY / $XDG_RUNTIME_DIR isn't set on the "
                "host; skipping forwarding.\n"
            )
            return [], [], []
        host_sock = Path(runtime_dir) / wd
        if not host_sock.exists():
            sys.stderr.write(
                f"aetherion: display: wayland — host socket {host_sock} "
                "not found; skipping forwarding.\n"
            )
            return [], [], []
        # Inside the container we mount the runtime dir at /run/user/1000
        # (the UID-1000 conventional spot) and set XDG_RUNTIME_DIR
        # accordingly so Electron + GTK pick it up.
        volumes += ["-v", f"{host_sock}:{_CONTAINER_RUNTIME_DIR}/{wd}:rw"]
        env_dict["WAYLAND_DISPLAY"] = wd
        env_dict["XDG_RUNTIME_DIR"] = _CONTAINER_RUNTIME_DIR

    # Writable tmpfs at $XDG_RUNTIME_DIR. Without this, rootless podman
    # creates `/run/user/1000` as a stub parent for our bind mounts
    # (session bus, wayland socket) owned by container-root mode 0755,
    # and apps that try to drop their own sockets next to the bus
    # (Cursor's `vscode-*.sock`, gpg-agent, dbus-launch, etc.) fail with
    # EACCES. mode=1777 (sticky-bit, world-writable, same as /tmp) lets
    # UID 1000 write there without per-runtime branching — podman's
    # `--tmpfs` doesn't accept uid=/gid= options. The tmpfs has to come
    # before the bind mounts in the run argv so they layer onto it;
    # --tmpfs is added to `extra` which is emitted before display_vols.
    extra += [
        "--tmpfs",
        f"{_CONTAINER_RUNTIME_DIR}:rw,mode=1777",
    ]

    _add_dbus_args(volumes, env_dict)

    # GPU + IPC are useful for both X11 and Wayland Electron paths. /dev/dri
    # gates access to the host's render nodes (software fallback kicks in
    # if absent or unusable). `--ipc host` shares the SHM namespace so MIT-
    # SHM / Chrome shared-memory paths work without copy-through.
    if Path("/dev/dri").exists():
        extra += ["--device", "/dev/dri"]
    extra += ["--ipc", "host"]

    env: list[str] = []
    for k, v in env_dict.items():
        env += ["-e", f"{k}={v}"]
    return volumes, env, extra


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
            rc = _create_namespace(
                home, namespace,
                template=options.template,
                no_cache=False,
            )
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
    elif options.template is not None:
        sys.stderr.write(
            f"aetherion: --template only applies when creating a namespace; "
            f"ignoring (use `aetherion rebuild namespace {namespace} "
            f"--template {options.template}` to switch).\n"
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
        # Pre-create the mountpoint as the host user so rootless podman
        # doesn't make a subuid-owned stub during container setup. Empty
        # dirs get shadowed by the bind mount during the run, then survive
        # cleanup because our UID can still `rmdir` them later.
        ns_path.mkdir(parents=True, exist_ok=True)
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
                # Pre-create as host user — same reasoning as the workdir
                # mount above: rootless podman would otherwise stub it as
                # subuid-owned and break cleanup later.
                if relative:
                    ns_path.mkdir(parents=True, exist_ok=True)
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

    # Display forwarding. Resolution order: CLI --display > namespace
    # YAML > built-in "none". Invalid CLI values error out; invalid YAML
    # already errored at config load.
    display_mode: str = "none"
    if options.display is not None:
        if options.display not in DISPLAY_MODES:
            sys.stderr.write(
                f"aetherion: --display {options.display!r} invalid; "
                f"choose one of {', '.join(sorted(DISPLAY_MODES))}.\n"
            )
            return 2
        display_mode = options.display
    elif config.display is not None:
        display_mode = config.display
    display_mode = _resolve_display_mode(display_mode)
    display_vols, display_envs, display_extra = _display_runtime_args(display_mode)

    instance_id = secrets.token_hex(4)
    instance_name = f"aetherion-{instance_id}"

    label_args = [
        "--label", f"{LABEL_NAMESPACE}={namespace}",
        "--label", f"{LABEL_IMAGE}={image}",
        "--label", f"{LABEL_WORKDIR}={container_workdir}",
    ]

    # Command resolution order:
    #   1. Positional trailing command after NAMESPACE (`aetherion
    #      cursor-ide bash` → bash). Existing behavior; explicit always
    #      wins.
    #   2. CLI `--command FOO` (shlex-split into argv).
    #   3. Namespace config `command:` (set by hand, or written from
    #      the source template's `defaults.command` at create time).
    #   4. Empty argv ⇒ defer to the image's CMD (bash for every
    #      baked-in template).
    final_command: list[str] = list(command)
    if not final_command and options.command_override is not None:
        parsed = _parse_command_value(
            options.command_override,
            context="--command",
        )
        if parsed:
            final_command = parsed
    if not final_command and config.command is not None:
        final_command = list(config.command)

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
        *display_envs,
        *publish_args,
        *display_extra,
        "-v", f"{ns_home}:{CONTAINER_HOME}:z",
        *volume_args,
        *display_vols,
        *workdir_mount,
        "-w", container_workdir,
        "-it",
        image,
        *final_command,
    ]

    return subprocess.run(run_argv).returncode


# Dispatch / help ------------------------------------------------------------

def _print_help() -> None:
    sys.stdout.write(
        "aetherion — containerized dev environment for AI coding agents.\n"
        "\n"
        "Launch:\n"
        "  aetherion                                       # launch the default namespace\n"
        "  aetherion NAMESPACE                             # launch into NAMESPACE (runs namespace's `command` or bash)\n"
        "  aetherion NAMESPACE COMMAND [ARG...]            # run COMMAND instead (positional override)\n"
        "  aetherion NAMESPACE --create [--template SPEC]  # create NAMESPACE if missing, then launch\n"
        "  aetherion NAMESPACE --join SESSION [CMD]        # exec into a running session\n"
        "\n"
        "Launch flag overrides (additive on top of the YAML config):\n"
        "  --image REF                                # use a different image for this launch\n"
        "  --display x11|wayland|auto|none            # override display forwarding for this launch\n"
        "  --command \"CMD [ARG...]\"                   # override the namespace's default command (shlex-split)\n"
        "  -e, --env NAME=VALUE                       # add an env var (repeatable)\n"
        "  --forward [ADDR:[HOST_PORT:]]CONTAINER_PORT  # publish a port (repeatable)\n"
        "  -v, --volume SRC[:DST]                     # mount a host path (repeatable)\n"
        "  --forward-openclaw [ADDR][:PORT]           # publish OpenClaw + set up loopback bridge\n"
        "\n"
        "Namespace verbs:\n"
        "  aetherion config                                              # open ~/.aetherion/config.yaml in $EDITOR\n"
        "  aetherion list namespaces                                     # registered namespaces + image + template + seed\n"
        "  aetherion list sessions                                       # running aetherion containers\n"
        "  aetherion create namespace NAME [--template SPEC] [--no-cache]\n"
        "  aetherion rebuild namespace NAME [--no-cache] [--refresh-template | --template SPEC]\n"
        "  aetherion reset namespace NAME [--force]                      # wipe $HOME, re-seed from the image\n"
        "  aetherion delete namespace NAME [NAME...] [--force]           # remove $HOME, build dir, image, config entry\n"
        "\n"
        "Template verbs:\n"
        "  aetherion list templates                                      # baked-in + user templates\n"
        "  aetherion create template NAME [--template SPEC]              # fork from SPEC (default: 'default')\n"
        "  aetherion edit template NAME                                  # open Dockerfile in $EDITOR (auto-forks baked-in)\n"
        "  aetherion delete template NAME [--force]                      # remove the user copy (baked-in stays)\n"
        "\n"
        "Template SPEC:\n"
        "  - a local template name (`default`, `cursor-ide`, or your own)\n"
        "  - a git URL with optional ref: `https://github.com/foo/bar.git#v1.0`\n"
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
    if verb == "edit":
        return cmd_edit(argv)
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

    # Top-level KeyboardInterrupt guard so Ctrl-C at any confirmation
    # prompt — or during any subprocess we're waiting on — exits cleanly
    # with the conventional 128+SIGINT (130) code instead of dumping a
    # Python traceback. User-initiated abort is not a bug.
    try:
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
    except KeyboardInterrupt:
        sys.stderr.write("\naetherion: interrupted.\n")
        return 130


if __name__ == "__main__":
    sys.exit(main())
