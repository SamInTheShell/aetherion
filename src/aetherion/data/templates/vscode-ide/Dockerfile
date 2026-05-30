# vscode-ide — Microsoft VS Code (Electron) inside an aetherion namespace
# with X11 forwarding to the host. Microsoft ships native amd64 and
# arm64 .debs through their signed apt repo, so the image builds and
# runs natively on either host architecture — no qemu binfmts or
# emulation required.
#
# Structurally identical to the cursor-ide template — both are Electron
# apps on Chromium 130+, and every X11 / no-GPU / OAuth-callback
# lesson learned there transfers verbatim. The only meaningful
# differences are the upstream channel (Microsoft apt vs. Cursor
# AppImage), the URL scheme (`vscode://` vs. `cursor://`), and the
# wrapper binary name.
#
# Conventions documented in src/aetherion/data/templates/STYLE.md:
# identity is aetherion@UID 1000, $HOME=/home/aetherion, starship is
# the default prompt, AETHERION_SPEC is the launcher-controlled spec
# for the bundled aetherion CLI (declared but unused — this template
# does NOT install aetherion in the container).
FROM debian:stable-slim

ARG AETHERION_SPEC=aetherion

# Microsoft's VS Code apt repo channel. "stable" or "insiders". Override
# at build time to track Insiders:
#   aetherion rebuild namespace vscode-ide \
#     --no-cache --build-arg VSCODE_CHANNEL=insiders
ARG VSCODE_CHANNEL=stable

ENV DEBIAN_FRONTEND=noninteractive \
    LC_ALL=C.UTF-8 \
    LANG=C.UTF-8

# ---------------------------------------------------------------------------
# Bootstrap minimal trust anchors so we can add Microsoft's signed apt
# repo before installing anything from it.
# ---------------------------------------------------------------------------
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        gnupg \
 && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Microsoft VS Code apt repo (signed). The single canonical repo handles
# both stable and insiders via Suites; we pick one at build time. Both
# amd64 and arm64 are first-class architectures on this repo.
# Ref: https://code.visualstudio.com/docs/setup/linux
# ---------------------------------------------------------------------------
RUN install -m 0755 -d /etc/apt/keyrings \
 && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
        | gpg --dearmor -o /etc/apt/keyrings/packages.microsoft.gpg \
 && chmod a+r /etc/apt/keyrings/packages.microsoft.gpg \
 && printf '%s\n' \
        'Types: deb' \
        'URIs: https://packages.microsoft.com/repos/code' \
        "Suites: ${VSCODE_CHANNEL}" \
        'Components: main' \
        "Architectures: $(dpkg --print-architecture)" \
        'Signed-By: /etc/apt/keyrings/packages.microsoft.gpg' \
    > /etc/apt/sources.list.d/vscode.sources

# ---------------------------------------------------------------------------
# Single big apt layer.
#
# - `code` itself. The .deb pulls its Electron/Chromium runtime deps
#   automatically (libnss3, libgbm1, libxkbfile1, libsecret-1-0, ...)
#   so we don't have to enumerate them like the cursor-ide template
#   does for its hand-fetched AppImage. The deps we DO list explicitly
#   are the ones VS Code's .deb doesn't pull but that we know we need
#   from the cursor-ide debugging experience.
# - X11 client + font + locale + xauth/xdg-utils for the forwarding
#   path itself.
# - firefox-esr in-container so the GitHub sign-in flow opens a browser
#   inside this namespace; the OAuth callback fires `vscode://` and we
#   route that handler to the in-container code (see desktop-file
#   block below). Without an in-container browser the callback would
#   hit the host browser and try to open the host's VS Code instead.
# - desktop-file-utils to update the MIME database after we rewrite
#   code.desktop.
# - lsof + net-tools (legacy `netstat`) for in-container debugging —
#   shared baseline across the post-2026 templates.
# - bash + bash-completion + less + locales: prompt comfort.
#
# `--no-install-recommends` is on every install line so we don't drag
# in random Recommends:; Depends: still get pulled, which is what the
# VS Code .deb needs.
# ---------------------------------------------------------------------------
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        code \
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
        libgl1-mesa-dri libgl1 libegl1 \
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
# of which template a namespace was forked from. Installer auto-detects
# arch and pulls the matching prebuilt binary.
# ---------------------------------------------------------------------------
RUN curl -fsSL https://starship.rs/install.sh \
        | sh -s -- --yes --bin-dir /usr/local/bin \
 && starship --version

# ---------------------------------------------------------------------------
# /usr/local/bin/code wrapper. Same pattern + flag rationale as the
# cursor wrapper in cursor-ide:
#
# - `--no-sandbox` always — Chrome's namespace sandbox would need SUID
#   + cap-add gymnastics we'd rather not require in the launcher.
# - `--use-gl=angle --use-angle=swiftshader` when `/dev/dri` is absent
#   (macOS via Docker Desktop, any GPU-less Linux container). Chromium
#   130+ requires *some* GL backend even with `--disable-gpu` because
#   its software raster path goes through GL. On macOS XQuartz over
#   TCP the system libGL gets no matching fbConfig and the renderer
#   aborts; ANGLE-with-SwiftShader routes through Chromium's GL
#   abstraction with the in-process software backend and survives.
#   Linux hosts with `/dev/dri` keep the real GPU path (hardware
#   acceleration via Mesa).
# - We intentionally do NOT add --disable-gpu / --disable-gpu-
#   compositing / --disable-mit-shm. Those combinations reliably
#   produced worse failure modes on the XQuartz path than leaving
#   Chromium alone with a working GL backend.
#
# We call /usr/share/code/code (the bare Electron binary) directly,
# NOT /usr/share/code/bin/code (Microsoft's shipped launcher script,
# which /usr/bin/code symlinks to). The launcher script runs the
# binary in ELECTRON_RUN_AS_NODE mode, executes resources/app/out/cli.js
# under it, and cli.js forks the actual GUI Electron process
# *detached* before exiting — perfect for desktop UX, fatal for our
# launcher's keep-alive semantics. When `code` is the container's
# PID 1 (which `defaults.command: code` makes it), the cli.js process
# returns immediately and the runtime tears the container down with
# the GUI still half-initialized.
#
# Exec'ing /usr/share/code/code directly skips the cli.js fork. Our
# process IS the main Electron GUI; it stays alive until all VS Code
# windows close, then exits cleanly. The same approach is what
# cursor-ide's wrapper does (it exec's /opt/cursor/AppRun, the
# extracted AppImage's Electron launcher, never cli.js).
#
# Tradeoff vs. the launcher: `code file.txt` from a shell inside the
# container spawns a NEW Electron instance instead of IPC'ing the
# file into an existing one. cursor-ide accepts the same tradeoff —
# inside a per-namespace container there's rarely a second instance
# to merge into anyway. Our wrapper at /usr/local/bin/code wins on
# PATH ordering against /usr/bin/code (which the .deb symlinks to
# the launcher), so `code` from any shell hits our flags + foreground
# launch.
#
# After writing the wrapper, register code.desktop's Exec= to point
# back at /usr/local/bin/code, and pin code.desktop as the default
# handler for x-scheme-handler/vscode + x-scheme-handler/vscode-insiders.
# The .deb's code.desktop already declares those MimeTypes; we just
# need the mimeapps.list defaults entry to make it the system pick.
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
        'exec /usr/share/code/code $flags "$@"' \
        > /usr/local/bin/code \
 && chmod +x /usr/local/bin/code \
 && install -d /usr/share/applications \
 && for d in /usr/share/applications/code.desktop \
             /usr/share/code/resources/app/resources/linux/code.desktop; do \
        [ -f "$d" ] && desktop="$d" && break; \
    done \
 && if [ -n "${desktop:-}" ]; then \
        sed -E 's|^Exec=.*|Exec=/usr/local/bin/code --unity-launch %F|' "$desktop" \
            > /usr/share/applications/code.desktop; \
        grep -q '^MimeType=' /usr/share/applications/code.desktop \
            || printf 'MimeType=x-scheme-handler/vscode;x-scheme-handler/vscode-insiders;\n' \
               >> /usr/share/applications/code.desktop; \
        printf '%s\n' \
            '[Default Applications]' \
            'x-scheme-handler/vscode=code.desktop' \
            'x-scheme-handler/vscode-insiders=code.desktop' \
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
