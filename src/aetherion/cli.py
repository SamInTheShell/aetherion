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

# Files shipped alongside the launcher that together form the docker build
# context. Order is purely cosmetic (used in log output).
BUNDLED_ASSETS: tuple[str, ...] = ("Dockerfile", "skeleton", "scripts")

# Per-agent state we preserve on the host so that login or first-run setup
# done inside one container session survives into the next. Each tuple lists
# the paths (relative to CONTAINER_HOME, mirrored under the host data dir)
# owned by that agent — keep new paths grouped under the agent that owns
# them so `--agents <name>` slicing keeps working with no extra plumbing.
AGENT_PATHS: dict[str, tuple[str, ...]] = {
    "claude":  (".claude", ".claude.json"),
    "cursor":  (".cursor", ".config/cursor"),
    "copilot": (".copilot",),
    "gemini":  (".gemini",),
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
    return parser.parse_args(argv)


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

        run_argv = [
            CONTAINER_RUNTIME, "run",
            *user_ns_args(),
            "--name", instance_name,
            "--hostname", instance_id,
            "--cidfile", str(cidfile),
            *env_args,
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
    # Use `<runtime> diff` to find which deferred paths the container actually
    # touched. Skipping `cp` on untouched paths avoids spurious errors and
    # keeps the host clean of empty agent dirs from sessions where the user
    # never logged in.
    touched = _diff_paths(cid)
    for agent, rel in deferred:
        container_path = f"{CONTAINER_HOME}/{rel}"
        if not _was_touched(container_path, touched):
            continue
        host_path = data_dir / rel
        if extract(cid, container_path, host_path):
            sys.stderr.write(f"aetherion: preserved {agent} state at {host_path}\n")


def _diff_paths(cid: str) -> set[str]:
    """Return the set of container-fs paths reported as Added or Changed by
    `<runtime> diff`. Deletes are ignored — nothing to extract."""
    result = subprocess.run(
        [CONTAINER_RUNTIME, "diff", cid],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return set()
    paths: set[str] = set()
    for line in result.stdout.splitlines():
        kind, _, path = line.partition(" ")
        if kind in ("A", "C") and path:
            paths.add(path)
    return paths


def _was_touched(target: str, touched: set[str]) -> bool:
    if target in touched:
        return True
    prefix = target + "/"
    return any(p.startswith(prefix) for p in touched)


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
