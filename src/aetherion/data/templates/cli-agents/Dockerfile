# syntax=docker/dockerfile:1.7
#
# cli-agents — the aetherion "agentic coding" image.
# Base: Debian 13 (Trixie) slim — glibc, Tier-1 amd64+arm64.
#
# Scope: every shipping vendor CLI agent we know of, on top of the same
# language toolchain baseline the `default` template carries (compilers,
# build systems, podman-in-container, uv, bun, node, rustup). This is
# the template you fork from when you want Claude Code / Codex / Cursor
# Agent / Gemini / Copilot / Pi / openclaw / Hermes all wired up out of
# the box. The aetherion+conduit package is installed in-container so
# `conduit` (the bridge between the host launcher and the in-container
# agent CLIs) is available.
#
# NO editor (use `nvim` template if you want one) and NO LSPs/DAPs —
# the agents bring their own integrations.

ARG BASE_IMAGE=docker.io/library/debian:trixie-slim
FROM ${BASE_IMAGE} AS base

# ---------------------------------------------------------------------------
# Build-time args
# ---------------------------------------------------------------------------
ARG TARGETARCH
ARG TARGETOS
ARG USERNAME=aetherion
ARG USER_UID=1000
ARG USER_GID=1000
# Channels for Claude Code apt repo: "stable" or "latest"
ARG CLAUDE_CODE_CHANNEL=latest

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    TZ=Etc/UTC \
    APT_LISTCHANGES_FRONTEND=none \
    UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Tell apt: don't pull recommends, don't pull suggests, don't cache pkgs.
# Must land before the first apt-get update below.
COPY skeleton/etc/apt/apt.conf.d/99-aetherion-minimal /etc/apt/apt.conf.d/99-aetherion-minimal

# ---------------------------------------------------------------------------
# Stage 1: bootstrap minimal trust anchors
# ---------------------------------------------------------------------------
RUN apt-get update \
 && apt-get install -y \
        ca-certificates \
        curl \
        gnupg \
 && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Stage 2: Claude Code signed apt repo
# ---------------------------------------------------------------------------
# Anthropic's official distribution path for the `claude-code` apt
# package. Key fingerprint pinned and verified before trusting.
# Ref: https://code.claude.com/docs/en/setup
RUN install -m 0755 -d /etc/apt/keyrings \
 && curl -fsSL https://downloads.claude.ai/keys/claude-code.asc \
        | gpg --dearmor -o /etc/apt/keyrings/claude-code.gpg \
 && chmod a+r /etc/apt/keyrings/claude-code.gpg \
 && printf '%s\n' \
        'Types: deb' \
        "URIs: https://downloads.claude.ai/claude-code/apt/${CLAUDE_CODE_CHANNEL}" \
        "Suites: ${CLAUDE_CODE_CHANNEL}" \
        'Components: main' \
        "Architectures: $(dpkg --print-architecture)" \
        'Signed-By: /etc/apt/keyrings/claude-code.gpg' \
    > /etc/apt/sources.list.d/claude-code.sources

# ---------------------------------------------------------------------------
# Stage 3: install the kitchen sink from apt
# ---------------------------------------------------------------------------
RUN apt-get update \
 && apt-get install -y \
        # --- baseline shell + observability (shared with every template) ---
        bash \
        bash-completion \
        less \
        locales \
        lsof \
        net-tools \
        # --- core build / dev essentials ---
        build-essential \
        clang \
        lld \
        cmake \
        ninja-build \
        pkg-config \
        git \
        git-lfs \
        make \
        patch \
        unzip \
        xz-utils \
        zstd \
        tar \
        rsync \
        # --- shells & TUI niceties ---
        man-db \
        tmux \
        # --- network / debug utilities ---
        iproute2 \
        iputils-ping \
        dnsutils \
        netcat-openbsd \
        # socat: powers /etc/profile.d/aetherion-bridge.sh, which proxies
        # in-container loopback services to the container's external
        # interface so `--forward-<agent>` flags can publish them
        # (openclaw, hermes, etc. all bind 127.0.0.1 by default).
        socat \
        # --- base CLI tooling ---
        ripgrep \
        fd-find \
        fzf \
        jq \
        yq \
        # --- ssh client (the cli launcher manages ssh contexts at runtime) ---
        openssh-client \
        # --- supply-chain visibility ---
        debian-repro-status \
        # --- agentic coding ---
        claude-code \
        # --- python (system minimal; uv handles the real thing) ---
        python3 \
        python3-venv \
        python3-pip \
        # --- ruby + go from Debian (upstream-packaged, no version manager) ---
        ruby \
        ruby-dev \
        golang \
        # --- godot build deps (Linux/X11 target) ---
        scons \
        libx11-dev \
        libxcursor-dev \
        libxinerama-dev \
        libgl1-mesa-dev \
        libglu1-mesa-dev \
        libasound2-dev \
        libpulse-dev \
        libudev-dev \
        libxi-dev \
        libxrandr-dev \
        # --- BLAS (free perf win for llama.cpp CPU prompt processing) ---
        libopenblas-dev \
        # --- rootless podman-in-container ---
        podman \
        podman-docker \
        fuse-overlayfs \
        slirp4netns \
        uidmap \
        crun \
 && sed -i -e 's/# *en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen \
 && locale-gen \
 && apt-get clean \
 && rm -rf \
        /var/lib/apt/lists/* \
        /var/cache/apt/* \
        /var/cache/debconf/* \
        /usr/share/doc/* \
        /usr/share/man/* \
        /tmp/* /var/tmp/* \
 && find /usr/share/locale -mindepth 1 -maxdepth 1 \
        ! -name C ! -name en ! -name en_US ! -name 'en_US.UTF-8' \
        -exec rm -rf {} +

# ---------------------------------------------------------------------------
# Stage 4: starship prompt (required by STYLE.md)
# ---------------------------------------------------------------------------
RUN curl -fsSL https://starship.rs/install.sh \
        | sh -s -- --yes --bin-dir /usr/local/bin \
 && starship --version

# ---------------------------------------------------------------------------
# Stage 5: install Node.js LTS from the upstream tarball
# ---------------------------------------------------------------------------
# Some vendor CLIs (openclaw in particular — `engines: {"node":
# ">=22.16.0"}`) rely on node-specific error-code semantics in their
# launcher scripts that bun doesn't replicate. Real node is the path
# of least resistance: it ships its own npm/npx, so tools that gate
# on `LookPath("npm")` or shell out to `npm install -g` are all happy.
# Ref: https://nodejs.org/dist/
ARG NODE_VERSION=v22.22.3
ARG NODE_SHA256_AMD64=""
ARG NODE_SHA256_ARM64=""
RUN set -eu; \
    case "${TARGETARCH:-$(dpkg --print-architecture)}" in \
        amd64) NODE_ARCH=linux-x64;   NODE_SHA="${NODE_SHA256_AMD64}" ;; \
        arm64) NODE_ARCH=linux-arm64; NODE_SHA="${NODE_SHA256_ARM64}" ;; \
        *) echo "unsupported arch: ${TARGETARCH:-unknown}" >&2; exit 1 ;; \
    esac; \
    NODE_TARBALL="node-${NODE_VERSION}-${NODE_ARCH}.tar.xz"; \
    cd /tmp; \
    curl -fsSL -O "https://nodejs.org/dist/${NODE_VERSION}/${NODE_TARBALL}"; \
    if [ -n "${NODE_SHA}" ]; then \
        echo "${NODE_SHA}  ${NODE_TARBALL}" | sha256sum -c -; \
    else \
        echo "WARN: NODE_SHA256_${TARGETARCH:-host} unset — trusting TLS only"; \
    fi; \
    tar -C /opt -xJf "${NODE_TARBALL}"; \
    mv "/opt/node-${NODE_VERSION}-${NODE_ARCH}" /opt/node; \
    ln -sf /opt/node/bin/node     /usr/local/bin/node; \
    ln -sf /opt/node/bin/npm      /usr/local/bin/npm; \
    ln -sf /opt/node/bin/npx      /usr/local/bin/npx; \
    ln -sf /opt/node/bin/corepack /usr/local/bin/corepack; \
    rm -f "/tmp/${NODE_TARBALL}"; \
    node --version; \
    npm --version

ENV PATH=/usr/local/sbin:/usr/local/bin:/opt/node/bin:/usr/sbin:/usr/bin:/sbin:/bin

# ---------------------------------------------------------------------------
# Stage 6: vendor agent CLIs (global via npm)
# ---------------------------------------------------------------------------
# Lands in /opt/node/lib/node_modules with bin shims at /opt/node/bin (on
# PATH via the symlinks above). Installed as root before the aetherion
# user exists — these are system-wide tools and the user's $HOME stays
# clean. Image rebuilds deliver fresh versions to every namespace.
#
# Agent CLIs covered:
#   gemini   (@google/gemini-cli)
#   copilot  (@github/copilot)
#   codex    (@openai/codex)
#   pi       (@earendil-works/pi-coding-agent)
#   openclaw (openclaw — needs real node, see stage 5)
# (claude-code lives outside npm — it's the apt package installed
# in stage 3 via Anthropic's signed repo.)
RUN npm install -g --omit=dev --no-fund --no-audit \
        @google/gemini-cli \
        @github/copilot \
        @openai/codex \
        @earendil-works/pi-coding-agent \
        openclaw \
 && command -v gemini \
 && command -v copilot \
 && command -v codex \
 && command -v pi \
 && command -v openclaw \
 && npm cache clean --force

# ---------------------------------------------------------------------------
# Stage 7: Rust toolchain (rustup, system-wide)
# ---------------------------------------------------------------------------
# See default/Dockerfile for the longer explanation. CARGO_HOME is set
# to /usr/local/cargo at build time so rustc/cargo land on the system
# PATH; runtime ENV at the bottom re-points CARGO_HOME at $HOME/.cargo
# so user `cargo install` goes to user-owned dirs.
ENV RUSTUP_HOME=/usr/local/rustup \
    CARGO_HOME=/usr/local/cargo \
    PATH=/usr/local/cargo/bin:/usr/local/sbin:/usr/local/bin:/opt/node/bin:/usr/sbin:/usr/bin:/sbin:/bin
RUN curl -fsSL https://sh.rustup.rs -o /tmp/rustup-init.sh \
 && chmod +x /tmp/rustup-init.sh \
 && /tmp/rustup-init.sh \
        -y \
        --no-modify-path \
        --default-toolchain stable \
        --profile minimal \
        --component rustfmt \
        --component clippy \
 && rustc --version \
 && cargo --version \
 && rm /tmp/rustup-init.sh \
 && chmod -R a+rX /usr/local/rustup /usr/local/cargo

# ---------------------------------------------------------------------------
# Stage 8: create the non-root user with rootless-podman-friendly subid maps
# ---------------------------------------------------------------------------
RUN groupadd --gid ${USER_GID} ${USERNAME} \
 && useradd  --uid ${USER_UID} --gid ${USER_GID} \
        --create-home --shell /bin/bash \
        --comment "Aetherion dev user" \
        ${USERNAME} \
 && echo "${USERNAME}:100000:65536" >> /etc/subuid \
 && echo "${USERNAME}:100000:65536" >> /etc/subgid

COPY skeleton/etc/containers/ /etc/containers/
COPY --chmod=0644 skeleton/etc/profile.d/aetherion-bridge.sh /etc/profile.d/aetherion-bridge.sh
COPY --chmod=0644 skeleton/etc/profile.d/aetherion-path.sh /etc/profile.d/aetherion-path.sh

# ---------------------------------------------------------------------------
# Stage 9: skeleton dotfiles
# ---------------------------------------------------------------------------
COPY --chown=${USERNAME}:${USERNAME} skeleton/home/${USERNAME}/ /home/${USERNAME}/

# ---------------------------------------------------------------------------
# Stage 10: system-wide developer tooling
# ---------------------------------------------------------------------------
RUN curl -fsSL https://astral.sh/uv/install.sh \
        | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh \
 && uv --version

RUN curl -fsSL https://bun.sh/install \
        | env BUN_INSTALL=/opt/bun bash \
 && ln -sf /opt/bun/bin/bun /usr/local/bin/bun \
 && bun --version

# ---------------------------------------------------------------------------
# Stage 11: uv-tool agents (Hermes)
# ---------------------------------------------------------------------------
# Hermes Agent (https://github.com/NousResearch/hermes-agent) — Nous
# Research's self-improving CLI agent, distributed as the
# `hermes-agent` PyPI package with a `hermes` entry point. Same uv-tool
# shape used for aetherion/conduit at stage 13: lands in /opt/uv-tools
# with shims in /usr/local/bin, sharing the /opt/uv-pythons Python 3.13
# interpreter pool. Config/state lives in ~/.hermes (HERMES_HOME)
# inside the user's namespace, persisted by the namespace mount.
RUN UV_TOOL_DIR=/opt/uv-tools \
    UV_TOOL_BIN_DIR=/usr/local/bin \
    UV_PYTHON_INSTALL_DIR=/opt/uv-pythons \
        uv tool install --python 3.13 hermes-agent \
 && hermes --help >/dev/null

# ---------------------------------------------------------------------------
# Stage 12: Cursor Agent
# ---------------------------------------------------------------------------
# Vendor agent CLI from Cursor. Installer hardcodes ~/.local/bin
# (relative to $HOME). We point HOME at a system directory
# (/opt/cursor-agent-home) so the install lands there permanently;
# symlinks into /usr/local/bin put it on PATH for any user. Self-update
# writes back into the same system directory — users can't self-update
# without sudo, which is the right default for a baked system tool.
#
# Cursor renamed the binary from `cursor-agent` → `agent` somewhere in
# late 2025/early 2026. We sniff for whichever name the installer
# dropped under .local/bin and expose it under BOTH names from
# /usr/local/bin: `agent` (current canonical) and `cursor-agent`
# (historical, kept so any scripts/muscle memory still work). Errors
# out only if NEITHER name was installed.
# Ref: https://docs.cursor.com/en/cli/overview
RUN install -d /opt/cursor-agent-home \
 && curl -fsSL https://cursor.com/install \
        | env HOME=/opt/cursor-agent-home bash \
 && ls /opt/cursor-agent-home/.local/bin/ \
 && BIN= \
 && for name in agent cursor-agent; do \
        if [ -e "/opt/cursor-agent-home/.local/bin/$name" ]; then \
            BIN="/opt/cursor-agent-home/.local/bin/$name"; \
            break; \
        fi; \
    done \
 && if [ -z "$BIN" ]; then \
        echo "cursor installer produced neither 'agent' nor 'cursor-agent'" >&2; \
        exit 1; \
    fi \
 && ln -sf "$BIN" /usr/local/bin/agent \
 && ln -sf "$BIN" /usr/local/bin/cursor-agent \
 && agent --version

# ---------------------------------------------------------------------------
# Stage 13: aetherion + conduit
# ---------------------------------------------------------------------------
# Both binaries ship from one PyPI distribution (`aetherion`); installing
# it lands `conduit` (the in-container bridge to the agent CLIs above)
# and also drops the launcher itself in /usr/local/bin for symmetry.
# AETHERION_SPEC defaults to the unversioned `aetherion` (latest from
# PyPI) as a fallback when the build is driven by hand. The aetherion
# launcher always overrides it: to /tmp/aetherion-src in dev-mode
# source-checkout runs, or to a pinned `aetherion==<launcher-version>`
# otherwise — keeping the in-container conduit in lockstep with
# whatever launcher kicked off the build.
#
# Position rationale (same as default/Dockerfile): source overlays
# change often during development. Putting this layer first would
# invalidate every downstream layer on each edit. Parking it as the
# last RUN before runtime means a conduit edit only re-runs the COPY +
# the (sub-second) `uv tool install`.
ARG AETHERION_SPEC=aetherion
COPY aetherion-src/ /tmp/aetherion-src/
RUN UV_TOOL_DIR=/opt/uv-tools \
    UV_TOOL_BIN_DIR=/usr/local/bin \
    UV_PYTHON_INSTALL_DIR=/opt/uv-pythons \
        uv tool install --python 3.13 "$AETHERION_SPEC" \
 && conduit --help >/dev/null \
 && aetherion --help >/dev/null \
 && rm -rf /tmp/aetherion-src /root/.cache/uv

# ---------------------------------------------------------------------------
# Stage 14: final cleanup of root-side caches
# ---------------------------------------------------------------------------
RUN rm -rf \
        /root/.cache/pip \
        /root/.cache/uv \
        /root/.cache/go-build \
        /tmp/* /var/tmp/* 2>/dev/null || true

# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------
USER ${USERNAME}
WORKDIR /home/${USERNAME}

ENV HOME=/home/${USERNAME} \
    GOPATH=/home/${USERNAME}/go \
    GOBIN=/home/${USERNAME}/go/bin \
    BUN_INSTALL=/home/${USERNAME}/.bun \
    CARGO_HOME=/home/${USERNAME}/.cargo \
    RUSTUP_HOME=/usr/local/rustup \
    UV_PYTHON_INSTALL_DIR=/home/${USERNAME}/.local/share/uv/python \
    PATH=/home/${USERNAME}/.cargo/bin:/home/${USERNAME}/go/bin:/home/${USERNAME}/.local/bin:/home/${USERNAME}/.npm-global/bin:/home/${USERNAME}/.bun/bin:/usr/local/cargo/bin:/usr/local/sbin:/usr/local/bin:/opt/node/bin:/usr/sbin:/usr/bin:/sbin:/bin

CMD ["/bin/bash", "-l"]
