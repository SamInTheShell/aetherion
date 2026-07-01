# syntax=docker/dockerfile:1.7
#
# nvim — language toolchain + Neovim + the full LSP/DAP stack.
# Base: Debian 13 (Trixie) slim — glibc, Tier-1 amd64+arm64.
#
# Scope: every language toolchain `default` ships (compilers, build
# systems, podman-in-container, uv, bun, node, rustup) plus a
# pre-baked Neovim 0.11.7 from upstream and the LSP/DAP set that the
# bundled init.lua + lazy.nvim plugin spec wires up — lua-ls,
# rust-analyzer, codelldb, js-debug-adapter, debugpy, gopls, dlv,
# typescript-language-server, pyright, vim-language-server.
#
# NO agents (use `cli-agents` template) and NO bundled aetherion/conduit.
# Plugins + treesitter parsers bootstrap on first `nvim` launch, NOT at
# build time — see stage 9.

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
# Neovim release to install from the upstream tarball. Pinned to a 0.11.x
# patch release because nvim-treesitter's `master` branch (which our
# plugin spec uses) explicitly does NOT support Neovim 0.12 — the
# "stable" channel silently jumped to 0.12 and triggered "attempt to
# call method 'range' (a nil value)" crashes in the treesitter
# highlighter on markdown. Bump deliberately when upstream
# nvim-treesitter master regains 0.12 support (or when we migrate the
# plugin spec to its `main` branch). Debian's apt nvim is too old for
# nvim-lspconfig (requires 0.11+).
ARG NVIM_VERSION=v0.11.7

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
    PYTHONUNBUFFERED=1 \
    # Default editor for git, sudo, etc. — points at the upstream nvim
    # from stage 4 (system apt nvim is intentionally not installed).
    EDITOR=nvim \
    VISUAL=nvim

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
# Stage 2: install the kitchen sink from apt
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
        socat \
        # --- editor + base CLI tooling ---
        # nvim is NOT installed from apt — Debian 13 ships 0.10.x which
        # is too old for current nvim-lspconfig. See stage 4 below for
        # the upstream tarball install.
        ripgrep \
        fd-find \
        fzf \
        jq \
        yq \
        # --- ssh client ---
        openssh-client \
        # --- supply-chain visibility ---
        debian-repro-status \
        # --- python (system minimal; uv handles the real thing) ---
        python3 \
        python3-venv \
        python3-pip \
        # --- ruby + go from Debian ---
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
# Stage 3: starship prompt (required by STYLE.md)
# ---------------------------------------------------------------------------
RUN curl -fsSL https://starship.rs/install.sh \
        | sh -s -- --yes --bin-dir /usr/local/bin \
 && starship --version

# ---------------------------------------------------------------------------
# Stage 4: install latest stable Neovim from the official release tarball
# ---------------------------------------------------------------------------
# Debian 13 only ships nvim 0.10.x; nvim-lspconfig and many modern
# plugins require 0.11+. The upstream project publishes a glibc-linked
# tarball per arch — no sidecar checksum file is shipped, so trust here
# is TLS-to-GitHub plus an optional sha256 pin (see NVIM_SHA256_* args
# below). Lands in /opt/nvim with `nvim` and `vim` symlinks under
# /usr/local/bin.
# Ref: https://github.com/neovim/neovim/releases
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
# Stage 5: install Node.js LTS from the upstream tarball
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

ENV PATH=/usr/local/sbin:/usr/local/bin:/opt/node/bin:/usr/sbin:/usr/bin:/sbin:/bin

# ---------------------------------------------------------------------------
# Stage 6: npm-based LSPs
# ---------------------------------------------------------------------------
# Lands in /opt/node/lib/node_modules with bin shims at /opt/node/bin
# (on PATH via the symlinks above). System-wide; the user's $HOME stays
# clean. Image rebuilds deliver fresh versions to every namespace.
RUN npm install -g --omit=dev --no-fund --no-audit \
        typescript \
        typescript-language-server \
        pyright \
        vim-language-server \
 && command -v typescript-language-server \
 && command -v pyright \
 && command -v vim-language-server \
 && npm cache clean --force

# ---------------------------------------------------------------------------
# Stage 7: system-wide LSP / DAP binaries
# ---------------------------------------------------------------------------
# Replaces Mason as our nvim package manager. We fetch upstream release
# artifacts directly because Mason is unreliable in --headless build
# contexts: it kicks off async installs that don't survive `+qa`,
# orphans lockfiles, and its UI redraw path runs `:!clear` which trips
# `set -e` on missing TERM. Going to the source means:
#   - one network round-trip per tool, fully synchronous
#   - everything baked into the image (zero install on first nvim launch)
#   - nvim config points at fixed system paths, easy to introspect
# Versions default to the latest GitHub release at build time; pin them
# via --build-arg for reproducibility.
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
    # --break-system-packages flips the safety off, which is what we
    # want for a baked image where the system Python is ours to manage.
    pip3 install --break-system-packages --no-cache-dir --quiet debugpy; \
    python3 -c "import debugpy; print('debugpy', debugpy.__version__)"

# ---------------------------------------------------------------------------
# Stage 8: Rust toolchain (rustup, system-wide)
# ---------------------------------------------------------------------------
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
# Stage 9: create the non-root user with rootless-podman-friendly subid maps
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
# Stage 10: skeleton dotfiles (full nvim config tree + bashrc + starship)
# ---------------------------------------------------------------------------
# The nvim config under .config/nvim ships init.lua + lazy.nvim plugin
# spec + commands + the seeded lazy-lock.json. On first `nvim` launch
# inside a namespace, init.lua's bootstrap clones lazy.nvim into the
# namespace's $XDG_DATA_HOME, lazy auto-installs the locked plugins,
# and treesitter parsers compile on first file open (the C toolchain
# from stage 2 is present). Cost: a few minutes of one-time setup per
# namespace that actually uses nvim. Benefit: zero footprint for users
# who don't, and plugins track the lockfile rather than freezing at the
# version the image happened to ship.
COPY --chown=${USERNAME}:${USERNAME} skeleton/home/${USERNAME}/ /home/${USERNAME}/

# ---------------------------------------------------------------------------
# Stage 11: system-wide developer tooling (uv, bun, gopls, dlv)
# ---------------------------------------------------------------------------
# uv (Astral) — system binary at /usr/local/bin/uv.
RUN curl -fsSL https://astral.sh/uv/install.sh \
        | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh \
 && uv --version

# bun (Oven)
RUN curl -fsSL https://bun.sh/install \
        | env BUN_INSTALL=/opt/bun bash \
 && ln -sf /opt/bun/bin/bun /usr/local/bin/bun \
 && bun --version

# Go-based LSP + DAP (gopls, dlv). Built with a throwaway GOPATH so the
# module cache (which sets parent dirs to 0555 — historically the
# source of the launcher's seed-time chmod failures) never lands under
# any $HOME. GOBIN places the compiled binaries directly in
# /usr/local/bin.
RUN GOPATH=/tmp/gobuild GOBIN=/usr/local/bin \
        go install golang.org/x/tools/gopls@latest \
 && GOPATH=/tmp/gobuild GOBIN=/usr/local/bin \
        go install github.com/go-delve/delve/cmd/dlv@latest \
 && gopls version \
 && dlv version \
 && rm -rf /tmp/gobuild /root/.cache/go-build

# ---------------------------------------------------------------------------
# Stage 12: final cleanup of root-side caches
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
