#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
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
# context. Order is purely cosmetic (used in log output).
BUNDLED_ASSETS: tuple[str, ...] = ("Dockerfile", "skeleton", "scripts")

# Per-agent state we preserve on the host so that login or first-run setup
# done inside one container session survives into the next. Each tuple lists
# the paths (relative to CONTAINER_HOME, mirrored under the host data dir)
# owned by that agent — keep new paths grouped under the agent that owns
# them so `--agents <name>` slicing keeps working with no extra plumbing.
AGENT_PATHS: dict[str, tuple[str, ...]] = {
    "claude":   (".claude", ".claude.json"),
    "cursor":   (".cursor", ".config/cursor"),
    "copilot":  (".copilot",),
    "gemini":   (".gemini",),
    "codex":    (".codex",),
    "pi":       (".pi",),
    "openclaw": (".openclaw",),
    "hermes":   (".hermes",),
    # Not an agent in its own right — this is the user-scoped npm prefix
    # (~/.npmrc redirects `npm install -g` here) plus npm's tarball/metadata
    # cache. Agents that install plugins at runtime (e.g. `ollama launch pi`
    # -> `@ollama/pi-web-search`) land their packages under .npm-global; the
    # cache at .npm/_cacache means even when those tools unconditionally rerun
    # `npm update <pkg>` on launch, npm serves from disk instead of re-fetching.
    "npm":      (".npm-global", ".npm"),
}


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


def _bundled_assets_dir() -> Path:
    # Dockerfile + skeleton/ + scripts/ ship inside the package itself, in a
    # sibling data/ directory. This resolves to the same real path whether
    # the launcher runs from a source checkout, an editable install, or a
    # pip-installed wheel — no importlib.resources dance required, because
    # we always need real filesystem paths anyway (docker build + shutil
    # both want them).
    return Path(__file__).resolve().parent / "data"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="aetherion",
        description="Launch the aetherion dev container.",
    )
    known = ", ".join(AGENT_PATHS)
    parser.add_argument(
        "--agents",
        metavar="LIST",
        type=lambda s: [a.strip() for a in s.split(",") if a.strip()],
        default=list(AGENT_PATHS),
        help=(
            "Comma-separated subset of agent toolchains whose login/setup state "
            "to expose into the container. Anything not listed is neither "
            f"mounted in nor preserved on exit. Default: all. Known: {known}. "
            "Pass an empty value (--agents '') to expose nothing."
        ),
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
        "--extract",
        metavar="PATH",
        default=None,
        help=(
            "Copy the bundled Dockerfile, skeleton/, and scripts/ into PATH "
            "and exit without launching. Use this to customize the image: "
            "edit, then `aetherion --build-image --build-dir PATH`."
        ),
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

    unknown = [a for a in args.agents if a not in AGENT_PATHS]
    if unknown:
        sys.stderr.write(
            f"aetherion: unknown agent(s): {', '.join(unknown)}\n"
            f"aetherion: known agents: {', '.join(AGENT_PATHS)}\n"
        )
        return 2

    selected: list[str] = args.agents
    if set(selected) != set(AGENT_PATHS):
        scope = ", ".join(selected) if selected else "(none)"
        sys.stderr.write(f"aetherion: agent scope limited to: {scope}\n")

    image: str = args.image

    # --build-image is terminal: it never launches the container, regardless
    # of build success or failure. The build's exit code propagates as-is.
    if args.build_image:
        context = (
            Path(args.build_dir).expanduser().resolve()
            if args.build_dir is not None
            else _bundled_assets_dir()
        )
        return _build_image(image, context)

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
    pwd = Path.cwd()

    data_dir = home / ".aetherion" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    # Rewrite host home → container home so a host path of ~/foo lands at ~/foo
    # inside the container too. Anything outside $HOME is mounted at its real
    # path, since there's no portable home-relative form for it.
    if pwd == home:
        container_workdir = CONTAINER_HOME
    elif home in pwd.parents:
        container_workdir = f"{CONTAINER_HOME}/{pwd.relative_to(home)}"
    else:
        container_workdir = str(pwd)

    mounts: list[str] = []
    # (agent, rel) for each deferred path so the first-run notice and the
    # post-exit "preserved <agent>" log can name the agent that owns it.
    deferred: list[tuple[str, str]] = []

    for agent in selected:
        for rel in AGENT_PATHS[agent]:
            host_path = data_dir / rel
            container_path = f"{CONTAINER_HOME}/{rel}"
            if host_path.exists():
                mounts += ["-v", f"{host_path}:{container_path}:z"]
            else:
                deferred.append((agent, rel))

    if deferred:
        sys.stderr.write(
            "aetherion: these agent paths are not yet preserved on the host;\n"
            "any that get created during the session will be extracted on clean exit:\n"
        )
        last_agent: str | None = None
        for agent, rel in deferred:
            if agent != last_agent:
                sys.stderr.write(f"  [{agent}]\n")
                last_agent = agent
            sys.stderr.write(f"    - {data_dir / rel}\n")
        sys.stderr.write("aetherion: let the container exit cleanly, do not SIGKILL the launcher.\n\n")

    # --cidfile instead of --rm: we need the container to outlive the shell so
    # we can diff and `cp` config out before removing it.
    instance_id = secrets.token_hex(4)
    instance_name = f"aetherion-{instance_id}"

    with tempfile.TemporaryDirectory(prefix="aetherion-cid-") as td:
        cidfile = Path(td) / "cid"

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

        run_argv = [
            CONTAINER_RUNTIME, "run",
            *user_ns_args(),
            "--name", instance_name,
            "--hostname", instance_id,
            "--cidfile", str(cidfile),
            *env_args,
            *publish_args,
            "-v", f"{pwd}:{container_workdir}:z",
            "-w", container_workdir,
            *mounts,
            "-it",
            image,
        ]

        rc = subprocess.run(run_argv).returncode

        if not cidfile.exists():
            return rc

        cid = cidfile.read_text().strip()

        try:
            if deferred:
                # Status line because on slower hosts the cp+rm phase can take
                # a couple seconds — without this the shell appears to hang
                # after `logout`, and the natural reflex is Ctrl+C.
                sys.stderr.write("aetherion: cleaning up container...\n")
                sys.stderr.flush()
                preserve_agent_state(cid, deferred, data_dir)
        finally:
            subprocess.run(
                [CONTAINER_RUNTIME, "rm", "-f", cid],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    return rc


def _image_exists(image: str) -> bool:
    return subprocess.run(
        [CONTAINER_RUNTIME, "image", "inspect", image],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0


def _build_image(image: str, context: Path) -> int:
    if not context.is_dir():
        sys.stderr.write(f"aetherion: build context does not exist: {context}\n")
        return 1
    if not (context / "Dockerfile").is_file():
        sys.stderr.write(
            f"aetherion: no Dockerfile found in build context: {context}\n"
            "aetherion: run `aetherion --extract <path>` to populate one.\n"
        )
        return 1
    sys.stderr.write(f"aetherion: building {image} from {context}\n")
    return subprocess.run(
        [CONTAINER_RUNTIME, "build", "-t", image, str(context)],
    ).returncode


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


def preserve_agent_state(cid: str, deferred: list[tuple[str, str]], data_dir: Path) -> None:
    # Try `<runtime> cp` directly for each deferred path. cp returns non-zero
    # when the source path doesn't exist in the container, which we treat as
    # "agent was never used this session, skip". We used to run `<runtime>
    # diff` first to filter, but that walks the entire container filesystem;
    # on a rich dev image the walk is slow enough that exit feels like a
    # hang. Going straight to cp is O(deferred paths) instead.
    for agent, rel in deferred:
        container_path = f"{CONTAINER_HOME}/{rel}"
        host_path = data_dir / rel
        if extract(cid, container_path, host_path):
            sys.stderr.write(f"aetherion: preserved {agent} state at {host_path}\n")


def extract(cid: str, src_in_container: str, dst_on_host: Path) -> bool:
    dst_on_host.parent.mkdir(parents=True, exist_ok=True)

    # Stage to a sibling tmp path so the final move into place is an atomic
    # rename on the same filesystem — no half-written config visible to a
    # future run.
    staging = dst_on_host.with_name(dst_on_host.name + ".tmp-extract")
    _remove(staging)

    cp = subprocess.run(
        [CONTAINER_RUNTIME, "cp", f"{cid}:{src_in_container}", str(staging)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    if cp.returncode != 0:
        _remove(staging)
        return False

    # Defensive: dst shouldn't exist (deferred = not on host at launch), but
    # if a concurrent run raced us, clear it so os.replace can land cleanly
    # even when staging is a directory.
    _remove(dst_on_host)
    os.replace(staging, dst_on_host)
    return True


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


if __name__ == "__main__":
    sys.exit(main())
