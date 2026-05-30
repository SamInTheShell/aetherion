# cursor-ide — Cursor (Electron) inside an aetherion namespace with X11
# forwarding to the host. Cursor now ships native AppImages for both
# linux-x64 and linux-arm64, so the image builds and runs natively on
# either host architecture — no qemu binfmts or emulation required.
#
# Conventions documented in src/aetherion/data/templates/STYLE.md:
# identity is aetherion@UID 1000, $HOME=/home/aetherion, starship is the
# default prompt, AETHERION_SPEC is the launcher-controlled spec for the
# bundled aetherion CLI.
FROM debian:stable-slim

ARG AETHERION_SPEC=aetherion

# Cursor's download endpoint pins the channel + version. Override at
# build time to track newer releases:
#   aetherion rebuild namespace cursor-ide \
#     --no-cache  (then edit ~/.aetherion/containers/cursor-ide/Dockerfile)
# Or use a custom template with a different pin.
ARG CURSOR_CHANNEL=golden
ARG CURSOR_VERSION=3.6

ENV DEBIAN_FRONTEND=noninteractive \
    LC_ALL=C.UTF-8 \
    LANG=C.UTF-8

# Electron / Chromium runtime deps + X11 client libs + fonts + locale +
# Firefox (so the Cursor sign-in flow opens a browser inside the same
# isolated namespace; no host xdg-desktop-portal / cursor:// handler
# required) + tools needed for the install steps below. Single apt
# layer keeps the image small.
#
# libxkbfile1 is load-bearing for Cursor's native-keymap module on
# Linux — without it the .node dlopen fails and Cursor crashes in the
# main process during keyboard-layout init. apt's
# --no-install-recommends would skip it, so list it explicitly.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        bash ca-certificates curl \
        libnss3 libatk-bridge2.0-0 libatk1.0-0 libcups2 \
        libdrm2 libgbm1 libxkbcommon0 libxkbfile1 libxcomposite1 libxdamage1 \
        libxrandr2 libxshmfence1 libasound2 libgtk-3-0 \
        libpangocairo-1.0-0 libpango-1.0-0 \
        libgl1-mesa-dri libgl1 libegl1 \
        libxss1 libxtst6 \
        fonts-noto-core fonts-noto-color-emoji fontconfig \
        xauth xdg-utils \
        firefox-esr \
        desktop-file-utils \
        locales \
 && sed -i 's/^# *\(en_US.UTF-8\)/\1/' /etc/locale.gen \
 && locale-gen en_US.UTF-8 \
 && rm -rf /var/lib/apt/lists/*

ENV LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 \
    BROWSER=firefox-esr

# starship — required by STYLE.md so the prompt looks the same regardless
# of which template a namespace was forked from. Installer auto-detects
# arch and pulls the matching prebuilt binary.
RUN curl -fsSL https://starship.rs/install.sh \
        | sh -s -- --yes --bin-dir /usr/local/bin \
 && starship --version

# Cursor (Electron). Map dpkg's amd64/arm64 names to Cursor's URL slugs
# (linux-x64 / linux-arm64) and fetch the matching native AppImage. The
# built-in FUSE mount is fragile inside rootless containers so we extract
# once at build time and exec the resulting AppRun directly.
#
# Wrapper flags, runtime-detected:
# - `--no-sandbox` always — Chrome's namespace sandbox would need SUID
#   + cap-add gymnastics we'd rather not require in the launcher.
# - `--use-gl=angle --use-angle=swiftshader` when `/dev/dri` is
#   absent (macOS via Docker Desktop, any GPU-less Linux container).
#   Chromium 130+ — which Cursor 3.6's Electron ships — requires
#   *some* GL backend even with `--disable-gpu`, because its
#   software raster path goes through GL. On macOS XQuartz over TCP
#   the system libGL gets no matching fbConfig and the renderer
#   aborts with exit code 5; with --use-gl=swiftshader (the
#   pre-Chromium-109 syntax) it asserts at GL backend init with
#   `Trace/breakpoint trap`. The current accepted form routes
#   through ANGLE (Chromium's GL abstraction) with SwiftShader as
#   ANGLE's software backend — ships in Electron's bundle, runs
#   in-process, never touches X GLX. Slower than a real GPU but
#   actually works through XQuartz forwarding. Linux hosts with
#   `/dev/dri` keep the real GPU path (full hardware acceleration
#   via Mesa).
# - We intentionally do NOT add --disable-gpu / --disable-gpu-
#   compositing / --disable-mit-shm. Those combinations reliably
#   produced worse failure modes on the XQuartz path than leaving
#   Chromium alone with a working GL backend.
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
        amd64) slug="linux-x64" ;; \
        arm64) slug="linux-arm64" ;; \
        *) echo "cursor-ide: unsupported arch '$arch'" >&2; exit 1 ;; \
    esac; \
    url="https://api2.cursor.sh/updates/download/${CURSOR_CHANNEL}/${slug}/cursor/${CURSOR_VERSION}"; \
    mkdir -p /opt; \
    cd /tmp; \
    curl -fL -o cursor.AppImage "$url"; \
    chmod +x cursor.AppImage; \
    ./cursor.AppImage --appimage-extract >/dev/null; \
    mv squashfs-root /opt/cursor; \
    chmod -R a+rX /opt/cursor; \
    rm -f cursor.AppImage; \
    printf '%s\n' \
        '#!/bin/sh' \
        'flags="--no-sandbox"' \
        '# No /dev/dri (macOS Docker Desktop, Linux containers without' \
        '# GPU passthrough): use ANGLE + SwiftShader for an in-process' \
        '# software GL backend. The pre-Chromium-109 --use-gl=swiftshader' \
        '# is gone; modern syntax routes through ANGLE.' \
        'if [ ! -e /dev/dri ]; then' \
        '    flags="$flags --use-gl=angle --use-angle=swiftshader"' \
        'fi' \
        'exec /opt/cursor/AppRun $flags "$@"' \
        > /usr/local/bin/cursor; \
    chmod +x /usr/local/bin/cursor; \
    # Register cursor.desktop so the in-container Firefox can hand
    # `cursor://` OAuth callbacks back to Cursor without prompting the
    # user. The AppImage's desktop file lands in one of a couple of
    # spots after extract; copy whichever we find, rewrite Exec= to our
    # wrapper, ensure the URL-scheme MimeType is declared, then make it
    # the system-wide default for x-scheme-handler/cursor.
    install -d /usr/share/applications; \
    for d in /opt/cursor/cursor.desktop \
             /opt/cursor/usr/share/applications/cursor.desktop; do \
        [ -f "$d" ] && desktop="$d" && break; \
    done; \
    if [ -n "${desktop:-}" ]; then \
        sed -E 's|^Exec=.*|Exec=/usr/local/bin/cursor %U|' "$desktop" \
            > /usr/share/applications/cursor.desktop; \
        grep -q '^MimeType=' /usr/share/applications/cursor.desktop \
            || printf 'MimeType=x-scheme-handler/cursor;\n' \
               >> /usr/share/applications/cursor.desktop; \
        printf '[Default Applications]\nx-scheme-handler/cursor=cursor.desktop\n' \
            > /usr/share/applications/mimeapps.list; \
        update-desktop-database /usr/share/applications 2>/dev/null || true; \
    fi

# Identity: user `aetherion`, UID 1000, $HOME=/home/aetherion. Required by
# the launcher's namespace bind mount + cp-a seed step.
RUN useradd --create-home --uid 1000 --shell /bin/bash aetherion

# Seed skeleton dotfiles into the image's $HOME so the launcher's first
# `cp -a /home/aetherion/.` lands them in the namespace.
COPY --chown=aetherion:aetherion skeleton/home/aetherion/ /home/aetherion/

USER aetherion
WORKDIR /home/aetherion

CMD ["bash"]
