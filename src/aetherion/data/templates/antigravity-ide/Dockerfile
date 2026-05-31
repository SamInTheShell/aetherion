# antigravity-ide — Google Antigravity (Electron, VS Code fork with
# the bundled "Cascade" Gemini agent) inside an aetherion namespace
# with X11 forwarding to the host. Google ships native amd64 and
# arm64 .debs through their signed apt repo, so the image builds and
# runs natively on either host architecture — no qemu binfmts or
# emulation required.
#
# Structurally identical to the vscode-ide template — both are VS
# Code descendants (Antigravity is Google's fork; the .deb layout
# mirrors upstream Code-OSS down to chrome-sandbox / chrome_crashpad_
# handler / resources/app paths), and every X11 / no-GPU / OAuth-
# callback lesson learned in vscode-ide transfers verbatim. The only
# meaningful differences are the upstream channel (Google's Artifact
# Registry apt repo vs. Microsoft's), the URL scheme
# (`antigravity://` vs. `vscode://`), and the wrapper binary name.
#
# Conventions documented in src/aetherion/data/templates/STYLE.md:
# identity is aetherion@UID 1000, $HOME=/home/aetherion, starship is
# the default prompt, AETHERION_SPEC is the launcher-controlled spec
# for the bundled aetherion CLI (declared but unused — this template
# does NOT install aetherion in the container).
FROM debian:stable-slim

ARG AETHERION_SPEC=aetherion

# Antigravity apt version pin. Default unset = "track the apt repo's
# latest in this layer's apt-get install". Override at build time to
# pin a specific version:
#   aetherion rebuild namespace antigravity-ide \
#     --no-cache --build-arg ANTIGRAVITY_VERSION=1.23.2-1776332190
ARG ANTIGRAVITY_VERSION=

ENV DEBIAN_FRONTEND=noninteractive \
    LC_ALL=C.UTF-8 \
    LANG=C.UTF-8

# ---------------------------------------------------------------------------
# Bootstrap minimal trust anchors so we can add Google's signed apt
# repo before installing anything from it.
# ---------------------------------------------------------------------------
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        gnupg \
 && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Google's Antigravity apt repo (signed). The repo serves both amd64
# and arm64 from one Suite/Component; we pick the dpkg architecture
# at apt-install time. Repo metadata verified live at:
#   https://us-central1-apt.pkg.dev/projects/antigravity-auto-updater-dev/dists/antigravity-debian/Release
# (Architectures: all amd64 arm64; Components: main)
# ---------------------------------------------------------------------------
RUN install -m 0755 -d /etc/apt/keyrings \
 && curl -fsSL https://us-central1-apt.pkg.dev/doc/repo-signing-key.gpg \
        | gpg --dearmor -o /etc/apt/keyrings/antigravity.gpg \
 && chmod a+r /etc/apt/keyrings/antigravity.gpg \
 && printf '%s\n' \
        'Types: deb' \
        'URIs: https://us-central1-apt.pkg.dev/projects/antigravity-auto-updater-dev' \
        'Suites: antigravity-debian' \
        'Components: main' \
        "Architectures: $(dpkg --print-architecture)" \
        'Signed-By: /etc/apt/keyrings/antigravity.gpg' \
    > /etc/apt/sources.list.d/antigravity.sources

# ---------------------------------------------------------------------------
# Single big apt layer.
#
# - `antigravity` itself, optionally version-pinned via the build arg.
#   The .deb pulls its Electron/Chromium runtime deps automatically
#   (libnss3, libgbm1, libxkbfile1, libgtk-3-0, ...) so we don't have
#   to enumerate them. The explicit list below covers Recommends:
#   that --no-install-recommends would skip but we know matter from
#   the vscode-ide debugging experience.
# - X11 client + font + locale + xauth/xdg-utils for the forwarding
#   path itself.
# - firefox-esr in-container so the Google sign-in flow opens a
#   browser inside this namespace; the OAuth callback fires
#   `antigravity://` and we route that handler to the in-container
#   antigravity (see desktop-file block below). Without an in-
#   container browser the callback would hit the host browser and
#   try to open the host's Antigravity (if any) instead.
# - desktop-file-utils to update the MIME database after we register
#   antigravity.desktop as the antigravity:// handler.
# - libsecret-1-0 for keyring-backed credential persistence (the
#   .deb's Depends: doesn't list it but Antigravity uses it when
#   present; without it credentials fall through to plaintext or get
#   re-prompted each session).
# - lsof + net-tools (legacy `netstat`) for in-container debugging —
#   shared baseline across the post-2026 templates.
# - bash + bash-completion + less + locales: prompt comfort.
#
# `--no-install-recommends` is on every install line so we don't drag
# in random Recommends:; Depends: still get pulled, which is what the
# Antigravity .deb needs.
# ---------------------------------------------------------------------------
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ${ANTIGRAVITY_VERSION:+antigravity=${ANTIGRAVITY_VERSION}} \
        ${ANTIGRAVITY_VERSION:-antigravity} \
        bash \
        bash-completion \
        less \
        locales \
        lsof \
        net-tools \
        libnss3 libatk-bridge2.0-0 libatk1.0-0 libcups2 \
        libdrm2 libgbm1 libxkbcommon0 libxkbfile1 libxcomposite1 libxdamage1 \
        libxrandr2 libxshmfence1 libasound2 libgtk-3-0 \
        libpangocairo-1.0-0 libpango-1.0-0 \
        libgl1-mesa-dri libgl1 libegl1 libvulkan1 \
        libxss1 libxtst6 libsecret-1-0 \
        fonts-noto-core fonts-noto-color-emoji fontconfig \
        xauth xdg-utils \
        firefox-esr \
        desktop-file-utils \
 && sed -i 's/^# *\(en_US.UTF-8\)/\1/' /etc/locale.gen \
 && locale-gen en_US.UTF-8 \
 && rm -rf /var/lib/apt/lists/*

ENV LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 \
    BROWSER=firefox-esr

# ---------------------------------------------------------------------------
# starship — required by STYLE.md so the prompt looks the same regardless
# of which template a namespace was forked from.
# ---------------------------------------------------------------------------
RUN curl -fsSL https://starship.rs/install.sh \
        | sh -s -- --yes --bin-dir /usr/local/bin \
 && starship --version

# ---------------------------------------------------------------------------
# /usr/local/bin/antigravity wrapper. Same pattern + flag rationale
# as vscode-ide / cursor-ide wrappers:
#
# - `--no-sandbox` always — Chrome's namespace sandbox would need SUID
#   + cap-add gymnastics we'd rather not require in the launcher.
# - `--use-gl=angle --use-angle=swiftshader` when `/dev/dri` is absent
#   (macOS via Docker Desktop, any GPU-less Linux container). Chromium
#   130+ requires *some* GL backend even with `--disable-gpu` because
#   its software raster path goes through GL; ANGLE+SwiftShader is
#   the in-process backend that survives the XQuartz/TCP path.
#
# We call /usr/share/antigravity/antigravity (the bare Electron binary)
# directly, NOT /usr/share/antigravity/bin/antigravity (the VS Code-
# style wrapper script, which /usr/bin/antigravity symlinks to). The
# wrapper script runs the binary in ELECTRON_RUN_AS_NODE mode,
# executes resources/app/out/cli.js under it, and cli.js forks the
# actual GUI Electron process *detached* before exiting — perfect for
# desktop UX, fatal for our launcher's keep-alive semantics. When
# `antigravity` is the container's PID 1 (which `defaults.command:
# antigravity` makes it), the cli.js process returns immediately and
# the runtime tears the container down with the GUI still half-
# initialized.
#
# Exec'ing /usr/share/antigravity/antigravity directly skips the
# cli.js fork. Our process IS the main Electron GUI; it stays alive
# until all Antigravity windows close, then exits cleanly. The same
# approach is what vscode-ide / cursor-ide wrappers do.
#
# Tradeoff vs. the upstream wrapper: `antigravity file.txt` from a
# shell inside the container spawns a NEW Electron instance instead
# of IPC'ing the file into an existing one. Inside a per-namespace
# container there's rarely a second instance to merge into anyway.
# Our wrapper at /usr/local/bin/antigravity wins on PATH ordering
# against /usr/bin/antigravity (which the .deb symlinks to the
# upstream wrapper), so `antigravity` from any shell hits our flags
# + foreground launch.
#
# After writing the wrapper, register antigravity.desktop's Exec= to
# point back at /usr/local/bin/antigravity, and pin antigravity.
# desktop as the default handler for x-scheme-handler/antigravity.
# The .deb's antigravity.desktop already declares the MimeType; we
# just need the mimeapps.list defaults entry so the in-container
# Firefox routes antigravity:// callbacks to our wrapper.
# ---------------------------------------------------------------------------
RUN printf '%s\n' \
        '#!/bin/sh' \
        'flags="--no-sandbox"' \
        '# No /dev/dri (macOS Docker Desktop, Linux containers without' \
        '# GPU passthrough): use ANGLE + SwiftShader for an in-process' \
        '# software GL backend. Linux hosts with a real GPU bind' \
        '# /dev/dri at runtime via the launcher and skip this block.' \
        'if [ ! -e /dev/dri ]; then' \
        '    flags="$flags --use-gl=angle --use-angle=swiftshader"' \
        'fi' \
        'exec /usr/share/antigravity/antigravity $flags "$@"' \
        > /usr/local/bin/antigravity \
 && chmod +x /usr/local/bin/antigravity \
 && install -d /usr/share/applications \
 && for d in /usr/share/applications/antigravity.desktop \
             /usr/share/antigravity/resources/app/resources/linux/antigravity.desktop; do \
        [ -f "$d" ] && desktop="$d" && break; \
    done \
 && if [ -n "${desktop:-}" ]; then \
        sed -E 's|^Exec=.*|Exec=/usr/local/bin/antigravity --unity-launch %F|' "$desktop" \
            > /usr/share/applications/antigravity.desktop; \
        grep -q '^MimeType=' /usr/share/applications/antigravity.desktop \
            || printf 'MimeType=x-scheme-handler/antigravity;\n' \
               >> /usr/share/applications/antigravity.desktop; \
        printf '%s\n' \
            '[Default Applications]' \
            'x-scheme-handler/antigravity=antigravity.desktop' \
            > /usr/share/applications/mimeapps.list; \
        update-desktop-database /usr/share/applications 2>/dev/null || true; \
    fi

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
