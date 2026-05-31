# zed-ide — Zed (native Rust GPU editor) inside an aetherion namespace
# with X11 forwarding to the host. Zed ships native amd64 and arm64
# Linux tarballs from cloud.zed.dev, so the image builds and runs
# natively on either host architecture — no qemu binfmts or emulation.
#
# Different shape from vscode-ide / cursor-ide: those are Electron
# (Chromium) apps and lean on a fat libnss/libgtk/libgbm runtime, plus
# a `--use-gl=angle --use-angle=swiftshader` wrapper trick for the
# no-GPU XQuartz path. Zed is a native Rust app that paints via
# Vulkan, so the deps are entirely different and the no-GPU story is
# Mesa's `lavapipe` software ICD (shipped in mesa-vulkan-drivers) plus
# the `ZED_ALLOW_EMULATED_GPU=1` env var that suppresses Zed's
# unsupported-GPU halt screen. The wrapper sets that env when /dev/dri
# is absent — same shape as the other IDE wrappers.
#
# Conventions documented in src/aetherion/data/templates/STYLE.md:
# identity is aetherion@UID 1000, $HOME=/home/aetherion, starship is
# the default prompt, AETHERION_SPEC is the launcher-controlled spec
# for the bundled aetherion CLI (declared but unused — this template
# does NOT install aetherion in the container).
FROM debian:stable-slim

ARG AETHERION_SPEC=aetherion

# Zed release channel + version. Override at build time to pin or to
# track preview:
#   aetherion rebuild namespace zed-ide \
#     --no-cache --build-arg ZED_CHANNEL=preview --build-arg ZED_VERSION=0.230.0
ARG ZED_CHANNEL=stable
ARG ZED_VERSION=latest

ENV DEBIAN_FRONTEND=noninteractive \
    LC_ALL=C.UTF-8 \
    LANG=C.UTF-8

# ---------------------------------------------------------------------------
# Single apt layer.
#
# - mesa-vulkan-drivers: provides both Mesa hardware Vulkan ICDs (radv
#   for AMD, etc., used when the launcher binds /dev/dri at runtime)
#   AND lavapipe (the software ICD used when /dev/dri is absent —
#   macOS via Docker Desktop + XQuartz, or any GPU-less Linux
#   container). libvulkan1 is the loader that picks an ICD at runtime.
#   vulkan-tools is here for `vulkaninfo --summary` if you need to
#   debug the GPU path inside a session. NVIDIA proprietary Vulkan is
#   in the host driver's userspace and not reachable from a generic
#   Debian container — Linux/NVIDIA hosts fall back to lavapipe unless
#   you fork this template with the NVIDIA Container Toolkit pieces.
# - libxkbcommon* / libwayland-* / libxcb-* / libx11-* / libgl1 /
#   libegl1: window-system client libs Zed dlopens at startup. The
#   xkbcommon pair is required even for X11-only sessions; without
#   them Zed crashes during keyboard-layout init.
# - libfontconfig1 + libfreetype6 + fonts-noto-color-emoji +
#   fonts-dejavu-core: font discovery + a default UI font + emoji.
# - libasound2: ALSA, dlopened lazily; Zed doesn't make sound on its
#   own but blocks on the load if it's absent in some builds.
# - libssl3: TLS for Zed's auto-update / extension fetch / agent
#   download paths.
# - xauth + xdg-utils: X11 cookie reading + xdg-open for "open
#   browser" actions from the editor (extension docs, auth flows).
# - firefox-esr: in-namespace browser so sign-in flows that open a
#   URL land inside the container rather than the host. Symmetric
#   with vscode-ide / cursor-ide (same BROWSER=firefox-esr).
# - lsof + net-tools: in-container debugging, shared baseline.
# - bash + bash-completion + less + locales: prompt comfort.
# - ca-certificates + curl + tar + xz-utils: TLS + tarball extract
#   for the Zed install step below.
# ---------------------------------------------------------------------------
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        bash \
        bash-completion \
        less \
        locales \
        lsof \
        net-tools \
        ca-certificates \
        curl \
        tar \
        xz-utils \
        libvulkan1 \
        mesa-vulkan-drivers \
        vulkan-tools \
        libxkbcommon0 libxkbcommon-x11-0 \
        libwayland-client0 libwayland-cursor0 libwayland-egl1 \
        libxcb1 libx11-6 libx11-xcb1 \
        libxcb-randr0 libxcb-shape0 libxcb-xfixes0 \
        libgl1 libegl1 \
        libasound2 \
        libssl3 \
        libfontconfig1 libfreetype6 \
        fonts-noto-core fonts-noto-color-emoji fonts-dejavu-core fontconfig \
        xauth xdg-utils \
        firefox-esr \
 && sed -i 's/^# *\(en_US.UTF-8\)/\1/' /etc/locale.gen \
 && locale-gen en_US.UTF-8 \
 && rm -rf /var/lib/apt/lists/*

ENV LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 \
    BROWSER=firefox-esr

# ---------------------------------------------------------------------------
# starship — required by STYLE.md so the prompt looks the same regardless
# of which template a namespace was forked from. Installer auto-detects
# arch and pulls the matching prebuilt binary.
# ---------------------------------------------------------------------------
RUN curl -fsSL https://starship.rs/install.sh \
        | sh -s -- --yes --bin-dir /usr/local/bin \
 && starship --version

# ---------------------------------------------------------------------------
# Zed itself. Upstream's `install.sh` extracts to $HOME/.local/zed.app
# and symlinks $HOME/.local/bin/zed; we want a system-wide install
# instead so every namespace gets it for free without per-user state.
# Fetch the same tarball the script does, extract to /opt, and let our
# wrapper at /usr/local/bin/zed (built below) point at /opt/zed.app.
#
# URL shape:
#   https://cloud.zed.dev/releases/<channel>/<version>/download
#     ?asset=zed&arch=<x86_64|aarch64>&os=linux&source=install.sh
# Channel = stable | preview. Version = "latest" or a pin like
# 0.225.0. The ?source= param is what Zed expects from the official
# install path; cloud.zed.dev rejects requests without it.
# ---------------------------------------------------------------------------
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
        amd64) zarch="x86_64" ;; \
        arm64) zarch="aarch64" ;; \
        *) echo "zed-ide: unsupported arch '$arch'" >&2; exit 1 ;; \
    esac; \
    url="https://cloud.zed.dev/releases/${ZED_CHANNEL}/${ZED_VERSION}/download?asset=zed&arch=${zarch}&os=linux&source=install.sh"; \
    mkdir -p /opt; \
    cd /tmp; \
    curl -fL -o zed.tar.gz "$url"; \
    tar -xzf zed.tar.gz -C /opt/; \
    # Tarball layout: /opt/zed.app/{bin/zed, libexec/zed-editor, ...}
    test -x /opt/zed.app/bin/zed; \
    chmod -R a+rX /opt/zed.app; \
    rm -f zed.tar.gz

# ---------------------------------------------------------------------------
# /usr/local/bin/zed wrapper.
#
# - `bin/zed` is Zed's CLI launcher (analogous to VS Code's /usr/bin/code
#   wrapper script). It IPCs into a running Zed instance to open files
#   when one exists and forks a fresh GUI process otherwise. The fork
#   detaches by default — fatal for our launcher's keep-alive
#   semantics when `zed` is the container's PID 1 (the wrapper returns
#   immediately and the runtime tears the container down with the GUI
#   still half-initialized). `--foreground` makes the CLI block on the
#   spawned GUI process instead, the same fix cursor-ide / vscode-ide
#   apply by exec'ing the underlying Electron binary directly. The
#   tradeoff: `zed file.txt` from a shell inside the container spawns
#   a NEW Zed instance instead of merging into an existing one. Inside
#   a per-namespace container there's rarely a second instance to
#   merge into anyway.
#
# - ZED_ALLOW_EMULATED_GPU=1 is set when /dev/dri is absent (macOS via
#   Docker Desktop, or any GPU-less Linux container). Zed's renderer
#   refuses to start on a software-emulated Vulkan ICD by default and
#   shows an "unsupported GPU" halt screen; this env says "lavapipe is
#   fine by me" and lets it through. With real /dev/dri bound by the
#   launcher, this branch is skipped and Zed gets the host's GPU via
#   the radv / iris / etc. Mesa Vulkan ICDs.
# ---------------------------------------------------------------------------
RUN printf '%s\n' \
        '#!/bin/sh' \
        '# No /dev/dri: no hardware Vulkan ICD is reachable, so fall' \
        '# back to Mesa lavapipe (software). Zed halts on emulated GPUs' \
        '# unless this env opts in.' \
        'if [ ! -e /dev/dri ]; then' \
        '    export ZED_ALLOW_EMULATED_GPU=1' \
        'fi' \
        'exec /opt/zed.app/bin/zed --foreground "$@"' \
        > /usr/local/bin/zed \
 && chmod +x /usr/local/bin/zed

# ---------------------------------------------------------------------------
# Identity: user `aetherion`, UID 1000, $HOME=/home/aetherion. Required
# by the launcher's namespace bind mount + cp-a seed step.
# ---------------------------------------------------------------------------
RUN useradd --create-home --uid 1000 --shell /bin/bash aetherion

# Seed skeleton dotfiles into the image's $HOME so the launcher's first
# `cp -a /home/aetherion/.` lands them in the namespace.
COPY --chown=aetherion:aetherion skeleton/home/aetherion/ /home/aetherion/

USER aetherion
WORKDIR /home/aetherion

CMD ["bash"]
