# syntax=docker/dockerfile:1.7
#
# base — the smallest aetherion template that still feels like aetherion.
# debian:trixie-slim + locale + lsof/net-tools + starship prompt + the
# aetherion user identity. Nothing else. Use this when you want a clean
# room to install a single tool into, without dragging in the language
# toolchains, neovim, or agent CLIs that the bigger templates carry.
#
# All conventions documented in src/aetherion/data/templates/STYLE.md:
# identity is aetherion@UID 1000, $HOME=/home/aetherion, starship is the
# default prompt, AETHERION_SPEC is the launcher-controlled spec for the
# bundled aetherion CLI (this template does NOT install aetherion in
# the container — there is no conduit here).

ARG BASE_IMAGE=docker.io/library/debian:trixie-slim
FROM ${BASE_IMAGE} AS base

ARG USERNAME=aetherion
ARG USER_UID=1000
ARG USER_GID=1000

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    TZ=Etc/UTC \
    APT_LISTCHANGES_FRONTEND=none

# Apt: no recommends, no suggests, no cached packages. Must land before
# the first apt-get update.
COPY skeleton/etc/apt/apt.conf.d/99-aetherion-minimal /etc/apt/apt.conf.d/99-aetherion-minimal

# ---------------------------------------------------------------------------
# Minimal apt layer
# ---------------------------------------------------------------------------
# Trust anchors (ca-certificates + curl) so the starship installer can
# reach https://starship.rs. Shell niceties (bash-completion, less)
# because an interactive prompt without tab completion or a pager is a
# downgrade from /bin/sh. lsof + net-tools (legacy `netstat`) for
# in-container debugging — required across every aetherion template per
# the project-wide baseline. locales so en_US.UTF-8 is a real locale
# (C.UTF-8 is always present, this is belt+suspenders for tools that
# explicitly assume en_US).
RUN apt-get update \
 && apt-get install -y \
        bash \
        bash-completion \
        ca-certificates \
        curl \
        less \
        locales \
        lsof \
        net-tools \
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
# starship prompt (required by STYLE.md)
# ---------------------------------------------------------------------------
# The official installer detects arch and pulls the matching prebuilt
# binary, so this works on amd64 and arm64 alike. Lands in /usr/local/bin
# so every shell on every user sees it without PATH gymnastics.
RUN curl -fsSL https://starship.rs/install.sh \
        | sh -s -- --yes --bin-dir /usr/local/bin \
 && starship --version

# ---------------------------------------------------------------------------
# Identity: aetherion user, UID 1000
# ---------------------------------------------------------------------------
# Required by the launcher: user_ns_args(), the namespace bind mount at
# /home/aetherion, and the `cp -a /home/aetherion/.` seed step all
# assume this exact uid/gid/home triple.
RUN groupadd --gid ${USER_GID} ${USERNAME} \
 && useradd  --uid ${USER_UID} --gid ${USER_GID} \
        --create-home --shell /bin/bash \
        --comment "Aetherion dev user" \
        ${USERNAME}

# Skeleton dotfiles (.bashrc + starship.toml) overlay /etc/skel's seeds.
# Our .bashrc wins; .profile is left alone so the login-shell -> .bashrc
# chain works as standard.
COPY --chown=${USERNAME}:${USERNAME} skeleton/home/${USERNAME}/ /home/${USERNAME}/

# AETHERION_SPEC is declared (and intentionally unused) so the launcher
# can pass --build-arg AETHERION_SPEC=... uniformly across every
# baked-in template without erroring on "unknown build arg". This
# template does NOT install the aetherion package inside the container.
ARG AETHERION_SPEC=aetherion

# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------
USER ${USERNAME}
WORKDIR /home/${USERNAME}

ENV HOME=/home/${USERNAME} \
    PATH=/home/${USERNAME}/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

CMD ["/bin/bash", "-l"]
