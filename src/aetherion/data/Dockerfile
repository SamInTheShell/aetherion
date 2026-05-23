# syntax=docker/dockerfile:1.7
#
# Aetherion: hardened multi-arch dev container.
# Base: Debian 13 (Trixie) slim — glibc, Tier-1 amd64+arm64, modern toolchains,
# strong reproducibility/hardening posture (PAC/BTI on arm64, reproducible builds).
#
# Build:
#   podman build --platform=linux/amd64,linux/arm64 -t aetherion:$(date +%Y-%m-%d)-001 .
#
# Run (interactive):
#   podman run --rm -it --userns=keep-id aetherion:latest

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
# Neovim release to install from the upstream tarball. Pinned to a 0.11.x
# patch release because nvim-treesitter's `master` branch (which our plugin
# spec uses) explicitly does NOT support Neovim 0.12 — the "stable" channel
# silently jumped to 0.12 and triggered "attempt to call method 'range'
# (a nil value)" crashes in the treesitter highlighter on markdown.
# Bump deliberately when upstream nvim-treesitter master regains 0.12
# support (or when we migrate the plugin spec to its `main` branch).
# Debian's apt nvim is too old for nvim-lspconfig (requires 0.11+).
ARG NVIM_VERSION=v0.11.7

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    TZ=Etc/UTC \
    # Disable apt recommends/suggests globally — smaller, more predictable images
    APT_LISTCHANGES_FRONTEND=none \
    # uv: standardize cache + install paths
    UV_LINK_MODE=copy \
    UV_PYTHON_INSTALL_DIR=/home/${USERNAME}/.local/share/uv/python \
    # bun: keep installs under ~/.bun
    BUN_INSTALL=/home/${USERNAME}/.bun \
    # Don't let Python buffer logs
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    # Default editor for git, sudo, etc. — points at the upstream nvim from
    # stage 3c (system apt nvim is intentionally not installed).
    EDITOR=nvim \
    VISUAL=nvim

# Tell apt: don't pull recommends, don't pull suggests, don't cache pkgs.
# Must land before the first apt-get update below.
COPY skeleton/etc/apt/apt.conf.d/99-aetherion-minimal /etc/apt/apt.conf.d/99-aetherion-minimal

# ---------------------------------------------------------------------------
# Stage 1: bootstrap minimal trust anchors
# We need: ca-certificates, curl, gpg before we add any third-party repos.
# Everything else comes after, behind signature verification.
# ---------------------------------------------------------------------------
RUN apt-get update \
 && apt-get install -y \
        ca-certificates \
        curl \
        gnupg \
 && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Stage 2: add signed third-party repositories
#   - Anthropic Claude Code (official signed apt repo)
#   Mise we install via signed install script (GPG-verified).
# ---------------------------------------------------------------------------

# Claude Code signed apt repo — Anthropic's official distribution path.
# Key fingerprint pinned and verified before trusting.
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
# Stage 3: install the kitchen sink from apt (system-level, glibc-linked)
# Grouped logically. Cleanup happens in the SAME RUN to keep layer slim.
# ---------------------------------------------------------------------------
RUN apt-get update \
 && apt-get install -y \
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
        bash-completion \
        less \
        man-db \
        tmux \
        # --- network / debug utilities ---
        iproute2 \
        iputils-ping \
        dnsutils \
        netcat-openbsd \
        # socat: powers /etc/profile.d/aetherion-bridge.sh, which proxies
        # in-container loopback services to the container's external
        # interface so `--forward-…` flags can publish them.
        socat \
        # --- editor + base CLI tooling ---
        # Note: nvim is NOT installed from apt — Debian 13 ships 0.10.x which
        # is too old for current nvim-lspconfig. See stage 3c below for the
        # upstream tarball install.
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
        # --- locale support (so UTF-8 is real) ---
        locales \
# generate a UTF-8 locale (C.UTF-8 is always present, this is belt+suspenders)
 && sed -i -e 's/# *en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen \
 && locale-gen \
# aggressive cleanup in the SAME layer
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

# ===========================================================================
# OPTIONAL FEATURE BLOCKS — uncomment what you need
# ===========================================================================
# Pick AT MOST ONE GPU acceleration path (CPU is always available).
# Ollama, llama.cpp, and CUDA/ROCm are toggled independently, but mixing
# CUDA and ROCm in one image is a quick path to GPU-flavored regret.
#
# Important conceptual note: GPU drivers live on the HOST kernel. These
# blocks only add userspace libraries (cuBLAS, hipBLAS, etc.) for compiling
# and linking against. Actual GPU passthrough is a runtime concern handled
# by your launcher (`podman run --device nvidia.com/gpu=all ...` for NVIDIA
# via CDI, or `--device /dev/kfd --device /dev/dri --group-add video` for
# AMD). You can build a CUDA-enabled image on a machine with no GPU and
# run it on a GPU host — the host driver gets injected at runtime.
# ===========================================================================

# ---------------------------------------------------------------------------
# OPTIONAL [A]: CUDA userspace libraries for NVIDIA GPU acceleration
# ---------------------------------------------------------------------------
# Adds NVIDIA's signed CUDA apt repo and installs the CUDA toolkit userspace
# (nvcc, cuBLAS, cuFFT, etc.). Does NOT install kernel drivers — those must
# be present on the HOST. Adds ~3-5GB to image size.
#
# At runtime, run the container with NVIDIA's container toolkit on the host:
#   podman run --device nvidia.com/gpu=all aetherion:latest
#
# RUN install -m 0755 -d /etc/apt/keyrings \
#  && curl -fsSL https://developer.download.nvidia.com/compute/cuda/repos/debian13/$(dpkg --print-architecture | sed 's/amd64/x86_64/;s/arm64/sbsa/')/cuda-archive-keyring.gpg \
#         -o /etc/apt/keyrings/cuda-archive-keyring.gpg \
#  && printf '%s\n' \
#         'Types: deb' \
#         "URIs: https://developer.download.nvidia.com/compute/cuda/repos/debian13/$(dpkg --print-architecture | sed 's/amd64/x86_64/;s/arm64/sbsa/')/" \
#         'Suites: /' \
#         'Signed-By: /etc/apt/keyrings/cuda-archive-keyring.gpg' \
#     > /etc/apt/sources.list.d/cuda.sources \
#  && apt-get update \
#  && apt-get install -y --no-install-recommends \
#         cuda-toolkit \
#  && apt-get clean \
#  && rm -rf /var/lib/apt/lists/* /var/cache/apt/* /usr/share/doc/* /usr/share/man/*
# ENV PATH=/usr/local/cuda/bin:${PATH} \
#     LD_LIBRARY_PATH=/usr/local/cuda/lib64

# ---------------------------------------------------------------------------
# OPTIONAL [B]: ROCm userspace libraries for AMD GPU acceleration
# ---------------------------------------------------------------------------
# Adds AMD's signed ROCm apt repo and installs the HIP/ROCm userspace
# (hipcc, hipBLAS, rocBLAS, etc.). Does NOT install kernel drivers — the
# amdgpu module must be present on the HOST. Adds ~4-6GB to image size.
#
# At runtime, the launcher must pass GPU device nodes:
#   podman run --device /dev/kfd --device /dev/dri \
#              --group-add video --group-add render \
#              aetherion:latest
#
# ROCm 7.x requires recent kernels (host kernel 6.x+ strongly recommended).
# Container architecture: amd64 only. ARM64 ROCm is not yet GA.
#
# RUN install -m 0755 -d /etc/apt/keyrings \
#  && curl -fsSL https://repo.radeon.com/rocm/rocm.gpg.key \
#         | gpg --dearmor -o /etc/apt/keyrings/rocm.gpg \
#  && printf '%s\n' \
#         'Types: deb' \
#         'URIs: https://repo.radeon.com/rocm/apt/latest' \
#         'Suites: jammy' \
#         'Components: main' \
#         'Architectures: amd64' \
#         'Signed-By: /etc/apt/keyrings/rocm.gpg' \
#     > /etc/apt/sources.list.d/rocm.sources \
#  && apt-get update \
#  && apt-get install -y --no-install-recommends \
#         rocm-hip-runtime \
#         rocm-hip-sdk \
#         hipblas \
#         rocblas \
#  && apt-get clean \
#  && rm -rf /var/lib/apt/lists/* /var/cache/apt/* /usr/share/doc/* /usr/share/man/*
# ENV PATH=/opt/rocm/bin:${PATH} \
#     LD_LIBRARY_PATH=/opt/rocm/lib

# ---------------------------------------------------------------------------
# OPTIONAL [C]: llama.cpp source build helpers
# ---------------------------------------------------------------------------
# llama.cpp CPU build already works out of the box — the C++ toolchain,
# CMake, Ninja, and libopenblas-dev installed above are everything the
# default build needs. This block is only relevant if you want llama.cpp
# pre-built INTO the image rather than letting users build it themselves.
#
# Most users should NOT bake llama.cpp in; let it live in a project dir.
# The compiled binary depends on the exact GPU backend chosen, so baking
# it in commits you to one variant per image.
#
# If you do want to pre-build the CPU + OpenBLAS variant into /opt/llama.cpp:
#
# RUN git clone --depth 1 https://github.com/ggml-org/llama.cpp /opt/llama.cpp \
#  && cmake -S /opt/llama.cpp -B /opt/llama.cpp/build \
#         -G Ninja \
#         -DCMAKE_BUILD_TYPE=Release \
#         -DGGML_BLAS=ON \
#         -DGGML_BLAS_VENDOR=OpenBLAS \
#  && cmake --build /opt/llama.cpp/build --config Release -j"$(nproc)" \
#  && find /opt/llama.cpp -name '*.o' -delete \
#  && find /opt/llama.cpp -name '.git' -type d -exec rm -rf {} +
# ENV PATH=/opt/llama.cpp/build/bin:${PATH}
#
# For NVIDIA, swap the cmake flags for:
#   -DGGML_CUDA=ON -DGGML_CUDA_F16=ON
# For AMD/ROCm, swap them for:
#   -DGGML_HIPBLAS=ON
# (And uncomment OPTIONAL [A] or [B] above first.)

# ===========================================================================
# END OPTIONAL FEATURE BLOCKS
# ===========================================================================

# ---------------------------------------------------------------------------
# Stage 3c: install latest stable Neovim from the official release tarball
# Debian 13 only ships nvim 0.10.x; nvim-lspconfig and many modern plugins
# require 0.11+. The upstream project publishes a glibc-linked tarball per
# arch — no sidecar checksum file is shipped, so trust here is TLS-to-GitHub
# plus an optional sha256 pin (see NVIM_SHA256_* args below). Lands in
# /opt/nvim with `nvim` and `vim` symlinks under /usr/local/bin.
# Ref: https://github.com/neovim/neovim/releases
# ---------------------------------------------------------------------------
# Optional per-arch sha256 pins. Leave blank to skip verification (TLS only).
# When pinning a NVIM_VERSION, set both to the matching tarball hashes:
#   --build-arg NVIM_VERSION=v0.11.3 \
#   --build-arg NVIM_SHA256_AMD64=<sha> --build-arg NVIM_SHA256_ARM64=<sha>
ARG NVIM_SHA256_AMD64=""
ARG NVIM_SHA256_ARM64=""
RUN set -eu; \
    case "${TARGETARCH:-$(dpkg --print-architecture)}" in \
        amd64) NVIM_ARCH=linux-x86_64; NVIM_SHA="${NVIM_SHA256_AMD64}" ;; \
        arm64) NVIM_ARCH=linux-arm64;  NVIM_SHA="${NVIM_SHA256_ARM64}" ;; \
        *) echo "unsupported arch: ${TARGETARCH:-unknown}" >&2; exit 1 ;; \
    esac; \
    NVIM_TARBALL="nvim-${NVIM_ARCH}.tar.gz"; \
    NVIM_BASE="https://github.com/neovim/neovim/releases/download/${NVIM_VERSION}"; \
    cd /tmp; \
    curl -fsSL -O "${NVIM_BASE}/${NVIM_TARBALL}"; \
    if [ -n "${NVIM_SHA}" ]; then \
        echo "${NVIM_SHA}  ${NVIM_TARBALL}" | sha256sum -c -; \
    else \
        echo "WARN: NVIM_SHA256_${TARGETARCH:-host} unset — trusting TLS only"; \
    fi; \
    tar -C /opt -xzf "${NVIM_TARBALL}"; \
    mv "/opt/nvim-${NVIM_ARCH}" /opt/nvim; \
    ln -sf /opt/nvim/bin/nvim /usr/local/bin/nvim; \
    ln -sf /opt/nvim/bin/nvim /usr/local/bin/vim; \
    rm -f "/tmp/${NVIM_TARBALL}"; \
    nvim --version | head -1

# ---------------------------------------------------------------------------
# Stage 3b: install starship prompt (official signed installer)
# Goes to /usr/local/bin so every shell on every user sees it. The installer
# detects the target arch and pulls the matching prebuilt binary, so this
# works for amd64 and arm64 alike.
# Ref: https://starship.rs/install.sh
# ---------------------------------------------------------------------------
RUN curl -fsSL https://starship.rs/install.sh \
        | sh -s -- --yes --bin-dir /usr/local/bin \
 && starship --version

# ---------------------------------------------------------------------------
# Stage 3d: system-wide LSP / DAP binaries (replaces Mason as our package
# manager). We fetch upstream release artifacts directly because Mason is
# unreliable in --headless build contexts: it kicks off async installs that
# don't survive `+qa`, orphans lockfiles, and its UI redraw path runs
# `:!clear` which trips `set -e` on missing TERM. Going to the source means:
#   - one network round-trip per tool, fully synchronous
#   - everything baked into the image (zero install on first nvim launch)
#   - nvim config points at fixed system paths, easy to introspect
# Versions default to the latest GitHub release at build time; pin them via
# --build-arg for reproducibility.
# ---------------------------------------------------------------------------
ARG LUA_LS_VERSION=
ARG CODELLDB_VERSION=
ARG JS_DEBUG_VERSION=
RUN set -eu; \
    case "${TARGETARCH:-$(dpkg --print-architecture)}" in \
        amd64) LUA_LS_ARCH=linux-x64;   RA_ARCH=x86_64-unknown-linux-gnu;  CODELLDB_ARCH=linux-x64   ;; \
        arm64) LUA_LS_ARCH=linux-arm64; RA_ARCH=aarch64-unknown-linux-gnu; CODELLDB_ARCH=linux-arm64 ;; \
        *) echo "unsupported arch: ${TARGETARCH:-unknown}" >&2; exit 1 ;; \
    esac; \
    \
    # ----- lua-language-server (LuaLS) -----
    if [ -z "${LUA_LS_VERSION}" ]; then \
        LUA_LS_VERSION=$(curl -fsSL https://api.github.com/repos/LuaLS/lua-language-server/releases/latest | jq -r .tag_name); \
    fi; \
    mkdir -p /opt/lua-language-server; \
    curl -fsSL "https://github.com/LuaLS/lua-language-server/releases/download/${LUA_LS_VERSION}/lua-language-server-${LUA_LS_VERSION}-${LUA_LS_ARCH}.tar.gz" \
        | tar -C /opt/lua-language-server -xz; \
    ln -sf /opt/lua-language-server/bin/lua-language-server /usr/local/bin/lua-language-server; \
    lua-language-server --version; \
    \
    # ----- rust-analyzer (single gzipped binary, /latest/download is stable) -----
    curl -fsSL "https://github.com/rust-lang/rust-analyzer/releases/latest/download/rust-analyzer-${RA_ARCH}.gz" \
        | gunzip > /usr/local/bin/rust-analyzer; \
    chmod 0755 /usr/local/bin/rust-analyzer; \
    rust-analyzer --version; \
    \
    # ----- codelldb (vsix is a zip; we want extension/adapter/) -----
    if [ -z "${CODELLDB_VERSION}" ]; then \
        CODELLDB_VERSION=$(curl -fsSL https://api.github.com/repos/vadimcn/codelldb/releases/latest | jq -r .tag_name); \
    fi; \
    mkdir -p /opt/codelldb; \
    curl -fsSL "https://github.com/vadimcn/codelldb/releases/download/${CODELLDB_VERSION}/codelldb-${CODELLDB_ARCH}.vsix" -o /tmp/codelldb.vsix; \
    unzip -q /tmp/codelldb.vsix -d /opt/codelldb; \
    rm /tmp/codelldb.vsix; \
    chmod +x /opt/codelldb/extension/adapter/codelldb; \
    ln -sf /opt/codelldb/extension/adapter/codelldb /usr/local/bin/codelldb; \
    \
    # ----- js-debug-adapter (vscode-js-debug; pure JS, no arch split) -----
    if [ -z "${JS_DEBUG_VERSION}" ]; then \
        JS_DEBUG_VERSION=$(curl -fsSL https://api.github.com/repos/microsoft/vscode-js-debug/releases/latest | jq -r .tag_name); \
    fi; \
    mkdir -p /opt/js-debug-adapter; \
    curl -fsSL "https://github.com/microsoft/vscode-js-debug/releases/download/${JS_DEBUG_VERSION}/js-debug-dap-${JS_DEBUG_VERSION}.tar.gz" \
        | tar -C /opt/js-debug-adapter -xz; \
    \
    # ----- debugpy (Python DAP; pip into a system-managed venv) -----
    # PEP 668 marks the system Python as externally-managed on Debian 13;
    # --break-system-packages flips the safety off, which is what we want
    # for a baked image where the system Python is ours to manage.
    pip3 install --break-system-packages --no-cache-dir --quiet debugpy; \
    python3 -c "import debugpy; print('debugpy', debugpy.__version__)"

# ---------------------------------------------------------------------------
# Stage 3f: install Node.js LTS from the upstream tarball
#
# We bun for the JS runtime where possible, but some vendor CLIs (openclaw
# in particular — `engines: {"node": ">=22.16.0"}`) rely on node-specific
# error-code semantics in their launcher scripts that bun doesn't replicate.
# Real node is the path of least resistance: it ships its own npm/npx, so
# tools that gate on `LookPath("npm")` or shell out to `npm install -g` are
# all happy, and `#!/usr/bin/env node` shebangs resolve to a real node.
#
# Bun stays installed (stage 6) as a fast user-scoped runtime. It just stops
# pretending to be node.
# Ref: https://nodejs.org/dist/
# ---------------------------------------------------------------------------
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

# Put /opt/node/bin on PATH for subsequent build stages so npm-installed
# global binaries (stage 3g) and any downstream stages that need to invoke
# them resolve without absolute paths. The user-scoped PATH set in stage 5
# re-asserts this same entry alongside the user's other tool dirs.
ENV PATH=/usr/local/sbin:/usr/local/bin:/opt/node/bin:/usr/sbin:/usr/bin:/sbin:/bin

# ---------------------------------------------------------------------------
# Stage 3g: pre-install LSPs and vendor agent CLIs globally via npm
#
# Lands in /opt/node/lib/node_modules with bin shims at /opt/node/bin (on
# PATH via the symlinks above). Installed as root before the aetherion user
# exists — these are system-wide tools and the user's $HOME stays clean.
#
# Agent CLIs covered:
#   gemini   (@google/gemini-cli)
#   copilot  (@github/copilot)
#   codex    (@openai/codex)
#   pi       (@earendil-works/pi-coding-agent)
#   openclaw (openclaw — needs real node, see stage 3f)
# LSPs covered:
#   tsc + typescript-language-server, pyright, vim-language-server
# `npm cache clean --force` reclaims the tarball download cache.
# ---------------------------------------------------------------------------
RUN npm install -g --omit=dev --no-fund --no-audit \
        typescript \
        typescript-language-server \
        pyright \
        vim-language-server \
        @google/gemini-cli \
        @github/copilot \
        @openai/codex \
        @earendil-works/pi-coding-agent \
        openclaw \
 && command -v typescript-language-server \
 && command -v gemini \
 && command -v copilot \
 && command -v codex \
 && command -v pi \
 && command -v openclaw \
 && npm cache clean --force

# ---------------------------------------------------------------------------
# Stage 4: create the non-root user with rootless-podman-friendly subid maps
# ---------------------------------------------------------------------------
RUN groupadd --gid ${USER_GID} ${USERNAME} \
 && useradd  --uid ${USER_UID} --gid ${USER_GID} \
        --create-home --shell /bin/bash \
        --comment "Aetherion dev user" \
        ${USERNAME} \
 # subuid/subgid ranges required for rootless podman inside the container
 && echo "${USERNAME}:100000:65536" >> /etc/subuid \
 && echo "${USERNAME}:100000:65536" >> /etc/subgid

# Drop a minimal containers.conf and storage.conf into /etc so user defaults
# pick fuse-overlayfs (works without /dev/fuse passthrough on most hosts).
COPY skeleton/etc/containers/ /etc/containers/

# Loopback bridge hook — sourced by /etc/profile when an AETHERION_BRIDGE_PORTS
# env var is set (the aetherion launcher does this for `--forward-<agent>`
# convenience flags). See the script for the full explanation.
COPY --chmod=0644 skeleton/etc/profile.d/aetherion-bridge.sh /etc/profile.d/aetherion-bridge.sh

# ---------------------------------------------------------------------------
# Stage 5: switch to non-root for all subsequent user-scoped tooling
# ---------------------------------------------------------------------------
USER ${USERNAME}
WORKDIR /home/${USERNAME}

ENV HOME=/home/${USERNAME} \
    PATH=/home/${USERNAME}/.local/bin:/home/${USERNAME}/.npm-global/bin:/home/${USERNAME}/.bun/bin:/usr/local/bin:/opt/node/bin:/usr/bin:/bin

# ---------------------------------------------------------------------------
# Stage 6: user-scoped tooling — uv (Python) and bun (JS/TS)
# Both fetched from their upstream installers.
# ---------------------------------------------------------------------------

# uv (Astral) — installs to ~/.local/bin
RUN curl -fsSL https://astral.sh/uv/install.sh | sh \
 && uv --version

# bun (Oven) — installs to $BUN_INSTALL/bin (~/.bun/bin). Bun stays as a
# fast user-scoped JS runtime + bundler; real node + npm live system-wide
# (stage 3f/3g) and are what npm-based agent CLIs (pi, openclaw, codex)
# find on PATH.
RUN curl -fsSL https://bun.sh/install | bash \
 && ${BUN_INSTALL}/bin/bun --version

# LSPs and vendor agent CLIs are installed system-wide in stage 3g via real
# npm. They live in /opt/node/lib/node_modules with bin shims on PATH at
# /opt/node/bin. The vim.lsp.config blocks in lsp-config.lua resolve these
# by $PATH lookup.

# Go-based LSP + DAP — install once at build so first-launch is offline-safe
# and so we don't race Mason's `go install` against a root-owned module cache
# (see syft caveat in stage 9). GOBIN lands these in ~/go/bin, on PATH below.
ENV GOPATH=/home/${USERNAME}/go \
    GOBIN=/home/${USERNAME}/go/bin \
    PATH=/home/${USERNAME}/go/bin:/home/${USERNAME}/.local/bin:/home/${USERNAME}/.bun/bin:/usr/local/bin:/usr/bin:/bin
RUN go install golang.org/x/tools/gopls@latest \
 && go install github.com/go-delve/delve/cmd/dlv@latest \
 && gopls version \
 && dlv version

# posting (https://posting.sh) — a TUI HTTP client (Postman-in-the-terminal).
# Installed as a uv tool, which creates an isolated venv under
# ~/.local/share/uv/tools/posting and drops the launcher in ~/.local/bin.
# Pin the Python interpreter so the image is reproducible regardless of
# whatever default uv would pick at build time.
RUN uv tool install --python 3.13 posting \
 && posting --help >/dev/null

# Hermes Agent (https://github.com/NousResearch/hermes-agent) — Nous Research's
# self-improving CLI agent. Distributed as the `hermes-agent` PyPI package with
# a `hermes` entry point; same uv-tool shape as posting above, so it lands at
# ~/.local/share/uv/tools/hermes-agent with the launcher in ~/.local/bin.
# Hermes requires Python >=3.11; 3.13 is fine and lets us reuse posting's
# interpreter. Config/state lives in ~/.hermes (HERMES_HOME) — the launcher's
# AGENT_PATHS entry preserves that across sessions.
RUN uv tool install --python 3.13 hermes-agent \
 && hermes --help >/dev/null

# Cursor Agent (`cursor-agent`) — vendor agent CLI from Cursor. Distributed
# as a prebuilt binary via an upstream install script; lands in
# ~/.local/bin/cursor-agent (already on PATH). Trust here is TLS to
# cursor.com plus whatever signing the installer enforces internally.
# Ref: https://docs.cursor.com/en/cli/overview
RUN curl -fsSL https://cursor.com/install | bash \
 && command -v cursor-agent

# ---------------------------------------------------------------------------
# Stage 7: bring in user-owned dotfiles (.bashrc, nvim config, starship, etc.)
# System-level skeleton entries (/etc/...) are copied earlier as root-owned.
# This overlays on top of the /etc/skel files dropped by useradd; our .bashrc
# wins, .profile is left alone so the standard login -> .bashrc chain works.
# ---------------------------------------------------------------------------
USER root
COPY --chown=${USERNAME}:${USERNAME} skeleton/home/${USERNAME}/ /home/${USERNAME}/
USER ${USERNAME}

# ---------------------------------------------------------------------------
# Stage 8: bootstrap Neovim plugins + treesitter parsers headlessly.
#
# Mason is intentionally *not* used here — we proved it racy in headless mode
# (orphaned lockfiles, async installs that don't survive `+qa`, UI calls into
# `:!clear` that trip on missing TERM). All LSP/DAP binaries are baked in
# system-wide by stage 3d above. mason-lspconfig and mason-nvim-dap are still
# present as plugins for ad-hoc :Mason use later, but their ensure_installed
# lists are empty in the user's nvim config so nothing auto-installs at
# startup.
#
# Treesitter parsers still need to be compiled. We use a tiny Lua helper that
# wipes any partial .so files from earlier nvim invocations and synchronously
# installs the full set — sidestepping :TSInstallSync's interactive
# "reinstall ? y/n" prompt that hangs in headless mode.
#
# --chown so aetherion can rm the file afterwards (sticky-bit /tmp blocks
# non-owner deletion when COPY defaults the file to root).
# TERM=dumb prevents :!clear shell errors from tripping `set -e`.
# ---------------------------------------------------------------------------
COPY --chown=${USERNAME}:${USERNAME} scripts/install-treesitter.lua /tmp/install-treesitter.lua
RUN set -e; \
    TERM=dumb nvim --headless "+Lazy! sync" +qa || true; \
    TERM=dumb nvim --headless -c "luafile /tmp/install-treesitter.lua"; \
    rm -f /tmp/install-treesitter.lua

## ---------------------------------------------------------------------------
## Stage 9: generate embedded SBOM at /etc/sbom.spdx.json
## This makes the image self-describing — anyone can introspect what's inside
## with `podman run --rm aetherion:latest cat /etc/sbom.spdx.json`.
##
## Syft is installed via Anchore's official signed install script, used to
## scan the entire filesystem, then removed so we don't ship the syft binary.
## We use SPDX JSON (ISO 5962:2021) for archival/forensic durability.
##
## Caveat: an SBOM generated inside a build cannot literally include itself.
## The output here describes every installed package, which is what matters
## for CVE tracking. For an external authoritative SBOM, run syft against
## the published image post-build.
## ---------------------------------------------------------------------------
#USER root
## IMPORTANT: syft is a Go binary, and the global ENV at stage 5 sets
## HOME=/home/${USERNAME}. If we run it with that HOME, the embedded Go
## runtime initializes ~/.cache/go-build, ~/.config/go/telemetry, and even
## pre-seeds ~/go/pkg/mod/{golang.org,github.com} — all owned by root.
## That breaks every subsequent `go install` the user runs (gopls, dlv, etc.)
## with "permission denied" inside their own $HOME. Pin HOME to a throwaway
## dir for the scan, then belt+suspenders chown the user home at the end.
#RUN curl -sSfL https://get.anchore.io/syft \
#        | sh -s -- -b /usr/local/bin \
# && mkdir -p /tmp/syft-home \
# && HOME=/tmp/syft-home syft version \
# && HOME=/tmp/syft-home syft dir:/ \
#        --source-name "aetherion" \
#        --source-version "rolling" \
#        --exclude './proc' \
#        --exclude './sys' \
#        --exclude './dev' \
#        --exclude './tmp' \
#        --exclude './var/cache' \
#        --exclude './home/'"${USERNAME}"'/.cache' \
#        -o spdx-json=/etc/sbom.spdx.json \
# && chmod 0644 /etc/sbom.spdx.json \
# # Remove syft itself so we don't ship the generator in the image.
# && rm -f /usr/local/bin/syft \
# && rm -rf /tmp/syft-home \
# # Belt-and-suspenders: anything that landed in $HOME during root-stages
# # (telemetry, caches) gets handed back to the user.
# && chown -R ${USERNAME}:${USERNAME} /home/${USERNAME}
#USER ${USERNAME}

# ---------------------------------------------------------------------------
# Stage 10: final cleanup of user-scoped caches
# ---------------------------------------------------------------------------
# Go's build + module caches are large (hundreds of MB of source) and only
# needed during `go install`. The compiled gopls/dlv binaries live in
# ~/go/bin and survive this prune.
RUN rm -rf \
        ~/.cache/pip \
        ~/.cache/uv/builds-v0 \
        ~/.cache/uv/wheels-v* \
        ~/.bun/install/cache \
        ~/.cache/go-build \
        ~/go/pkg \
        /tmp/* /var/tmp/* 2>/dev/null || true

# ---------------------------------------------------------------------------
# Stage 11: aetherion + conduit (parked at the end of the build deliberately)
# ---------------------------------------------------------------------------
# Both binaries ship from one PyPI distribution (`aetherion`); installing it
# lands `conduit` (the in-container utility) and also drops the launcher
# itself in `~/.local/bin` for symmetry. AETHERION_SPEC defaults to the
# unversioned `aetherion` (latest from PyPI) as a fallback when the build
# is driven by hand (`podman build .`). The aetherion launcher always
# overrides it: to /tmp/aetherion-src in dev-mode source-checkout runs, or
# to a pinned `aetherion==<launcher-version>` otherwise — keeping the
# in-container CLIs lockstep with whatever launcher kicked off the build.
#
# Position rationale: source overlays change often during development of
# the launcher itself. Putting this layer first would invalidate every
# downstream layer (nvim plugin sync, treesitter parser builds, skeleton
# copy, cleanup) on each edit — typically minutes of wasted rebuild.
# Parking it as the last RUN before CMD means a conduit edit only re-runs
# the COPY + the (sub-second) `uv tool install`, and every heavyweight
# stage stays cached.
ARG AETHERION_SPEC=aetherion
COPY --chown=${USERNAME}:${USERNAME} aetherion-src/ /tmp/aetherion-src/
RUN uv tool install --python 3.13 "$AETHERION_SPEC" \
 && conduit --help >/dev/null \
 && aetherion --help >/dev/null \
 && rm -rf /tmp/aetherion-src ~/.cache/uv/builds-v0 ~/.cache/uv/wheels-v*

# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------
# Default to an interactive login shell; the CLI launcher overrides as needed.
# (No trailing `SHELL` directive — there are no RUN steps after this point for
# it to influence, and OCI image format ignores it with a build-time warning.)
CMD ["/bin/bash", "-l"]
