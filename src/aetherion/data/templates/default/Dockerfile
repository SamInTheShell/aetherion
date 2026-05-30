# syntax=docker/dockerfile:1.7
#
# default — aetherion's language-toolchain baseline.
# Base: Debian 13 (Trixie) slim — glibc, Tier-1 amd64+arm64, modern toolchains.
#
# Scope: enough to build software in the major mainstream languages, with
# the prompt + identity every aetherion template shares. NO editor (use
# `nvim` template), NO LSPs/DAPs (they belong with the editor), NO agent
# CLIs (use `cli-agents` template), NO bundled aetherion/conduit (this
# image's `aetherion` ARG is declared but unused). Everything that's
# specifically dev-loop tooling stays — uv, bun, rustup, podman-in-
# container — because those are how you actually build code, not what
# you read it with.
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

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    TZ=Etc/UTC \
    APT_LISTCHANGES_FRONTEND=none \
    # uv: standardize copy semantics. UV_PYTHON_INSTALL_DIR and
    # UV_TOOL_DIR are set per-invocation during the build (system paths)
    # and again at the end as user paths for runtime.
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
# Stage 2: install the kitchen sink from apt (system-level, glibc-linked)
# Cleanup happens in the SAME RUN to keep the layer slim.
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
        # interface so `--forward-…` flags can publish them. The script
        # silently no-ops without AETHERION_BRIDGE_PORTS, so socat being
        # installed here costs nothing for users who never set it.
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
# generate a UTF-8 locale (C.UTF-8 is always present; en_US is belt+suspenders)
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

# ===========================================================================
# END OPTIONAL FEATURE BLOCKS
# ===========================================================================

# ---------------------------------------------------------------------------
# Stage 3: starship prompt (required by STYLE.md)
# ---------------------------------------------------------------------------
RUN curl -fsSL https://starship.rs/install.sh \
        | sh -s -- --yes --bin-dir /usr/local/bin \
 && starship --version

# ---------------------------------------------------------------------------
# Stage 4: install Node.js LTS from the upstream tarball
# ---------------------------------------------------------------------------
# Some user-facing JS tools rely on node-specific error-code semantics in
# their launcher scripts that bun doesn't replicate. Real node is the
# path of least resistance: it ships its own npm/npx, so tools that
# shell out to npm all just work. Bun stays installed (stage 6) as a
# fast user-scoped runtime — it just doesn't pretend to be node.
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

# Put /opt/node/bin on PATH for subsequent build stages so anything that
# shells out to npm during the build resolves without absolute paths.
ENV PATH=/usr/local/sbin:/usr/local/bin:/opt/node/bin:/usr/sbin:/usr/bin:/sbin:/bin

# ---------------------------------------------------------------------------
# Stage 5: Rust toolchain (rustup, system-wide)
# ---------------------------------------------------------------------------
# rustup installs to RUSTUP_HOME (toolchain) + CARGO_HOME (binaries). We
# point both at /usr/local so rustc/cargo land on the system PATH and
# every user picks them up without per-$HOME bootstrap. Profile is
# `minimal` (no rust-docs — a few hundred MB saved) plus rustfmt and
# clippy components because no Rust dev loop is really complete without
# them. At runtime we re-point CARGO_HOME at the user's $HOME so
# `cargo install <crate>` writes user-owned binaries to ~/.cargo/bin
# without colliding with the root-owned baked toolchain.
# Ref: https://rustup.rs/
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
# Stage 6: create the non-root user with rootless-podman-friendly subid maps
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

# Loopback bridge hook — sourced by /etc/profile when AETHERION_BRIDGE_PORTS
# is set (the aetherion launcher does this for `--forward-<agent>`
# convenience flags). Silently no-ops otherwise.
COPY --chmod=0644 skeleton/etc/profile.d/aetherion-bridge.sh /etc/profile.d/aetherion-bridge.sh

# ---------------------------------------------------------------------------
# Stage 7: skeleton dotfiles (root → /home/${USERNAME})
# ---------------------------------------------------------------------------
# Stays as root throughout the user-tool installs below — everything that
# could land under /home/${USERNAME} has been moved to system paths
# (/usr/local/bin, /opt/*) so the image's $HOME contains only genuine
# config that ships in skeleton/. Keeps the aetherion namespace seed
# (which captures /home/aetherion verbatim at first launch) small and
# avoids freezing toolchain versions into individual namespaces.
COPY --chown=${USERNAME}:${USERNAME} skeleton/home/${USERNAME}/ /home/${USERNAME}/

# ---------------------------------------------------------------------------
# Stage 8: system-wide developer tooling
# ---------------------------------------------------------------------------
# Everything in this stage installs to /usr/local/bin or /opt — never under
# /home/${USERNAME}. Rationale: the aetherion launcher bind-mounts a
# per-namespace host dir at /home/aetherion, and on first launch it seeds
# that dir by copying the image's $HOME out. Anything we baked under
# $HOME would freeze into the seed and stop tracking image upgrades. By
# keeping toolchains in /opt and /usr/local, image rebuilds deliver new
# versions to every namespace immediately.

# uv (Astral) — system binary at /usr/local/bin/uv. UV_INSTALL_DIR steers
# the installer; UV_NO_MODIFY_PATH skips its bashrc-rewriting step (we
# put /usr/local/bin on PATH ourselves anyway).
RUN curl -fsSL https://astral.sh/uv/install.sh \
        | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh \
 && uv --version

# bun (Oven) — binary tree under /opt/bun, with a symlink at
# /usr/local/bin/bun so it's discoverable without /opt/bun/bin on PATH.
# Bun is the system JS runtime + bundler; real node + npm live at
# /opt/node (stage 4) for tools that need genuine node semantics.
RUN curl -fsSL https://bun.sh/install \
        | env BUN_INSTALL=/opt/bun bash \
 && ln -sf /opt/bun/bin/bun /usr/local/bin/bun \
 && bun --version

# ---------------------------------------------------------------------------
# Stage 9: final cleanup of root-side caches
# ---------------------------------------------------------------------------
RUN rm -rf \
        /root/.cache/pip \
        /root/.cache/uv \
        /root/.cache/go-build \
        /tmp/* /var/tmp/* 2>/dev/null || true

# ---------------------------------------------------------------------------
# AETHERION_SPEC declared (and intentionally unused) so the launcher can
# pass --build-arg AETHERION_SPEC=... uniformly across every baked-in
# template without erroring on "unknown build arg". This template does
# NOT install aetherion/conduit inside the container — use the
# `cli-agents` template if you want conduit available in-container.
# ---------------------------------------------------------------------------
ARG AETHERION_SPEC=aetherion

# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------
# Switch to the non-root user. Per-tool env that should live in the
# namespace (so user-installed packages land there, not in image-baked
# root-owned dirs):
#   GOPATH/GOBIN — runtime `go install` lands binaries in ~/go/bin;
#                  module downloads land in ~/go/pkg/mod.
#   BUN_INSTALL  — runtime `bun install -g <pkg>` puts globals under
#                  ~/.bun (the bun binary itself is system-wide at
#                  /usr/local/bin/bun via the symlink above).
#   CARGO_HOME   — `cargo install <crate>` lands user binaries in
#                  ~/.cargo/bin. RUSTUP_HOME stays system-wide so the
#                  baked stable toolchain is the default rustc;
#                  user `rustup toolchain install` would need sudo,
#                  which is the right default for a baked system tool.
#   UV_PYTHON_INSTALL_DIR — runtime `uv` downloads Python interpreters
#                  into ~/.local/share/uv/python so user-installed
#                  tools don't try to write to /opt/uv-pythons (which
#                  would be root-owned if it existed; it doesn't here).
#
# PATH puts user-scoped install dirs first so anything the user installs
# at runtime takes precedence over the baked-in system versions.
USER ${USERNAME}
WORKDIR /home/${USERNAME}

ENV HOME=/home/${USERNAME} \
    GOPATH=/home/${USERNAME}/go \
    GOBIN=/home/${USERNAME}/go/bin \
    BUN_INSTALL=/home/${USERNAME}/.bun \
    CARGO_HOME=/home/${USERNAME}/.cargo \
    RUSTUP_HOME=/usr/local/rustup \
    UV_PYTHON_INSTALL_DIR=/home/${USERNAME}/.local/share/uv/python \
    PATH=/home/${USERNAME}/.cargo/bin:/home/${USERNAME}/go/bin:/home/${USERNAME}/.local/bin:/home/${USERNAME}/.bun/bin:/usr/local/cargo/bin:/usr/local/sbin:/usr/local/bin:/opt/node/bin:/usr/sbin:/usr/bin:/sbin:/bin

CMD ["/bin/bash", "-l"]
