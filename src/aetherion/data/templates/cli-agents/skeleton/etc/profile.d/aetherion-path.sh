# /etc/profile.d/aetherion-path.sh — restore the image's runtime PATH.
#
# Debian's /etc/profile hard-sets PATH to a minimal default (`/usr/local/
# bin:/usr/bin:/bin:...`), throwing away the Dockerfile's `ENV PATH=...`
# every time bash starts as a login shell (the default CMD here). The
# `node`, `npm`, `cursor-agent`, `hermes`, etc. shims happen to be
# symlinked / installed under /usr/local/bin so they survive, but the
# npm-global agent CLIs (`codex`, `copilot`, `pi`, `gemini`, `openclaw`)
# live only at /opt/node/bin/* — they disappear from PATH unless we put
# /opt/node/bin (and the other baked-in tool dirs) back.
#
# profile.d scripts are sourced *after* the PATH= block in /etc/profile,
# so this is the correct place to do it. Value mirrors the runtime ENV
# at the bottom of the Dockerfile; keep them in sync if you edit either.

PATH="$HOME/.cargo/bin:$HOME/go/bin:$HOME/.local/bin:$HOME/.npm-global/bin:$HOME/.bun/bin:/usr/local/cargo/bin:/usr/local/sbin:/usr/local/bin:/opt/node/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PATH
