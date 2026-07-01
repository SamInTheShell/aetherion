# /etc/profile.d/aetherion-path.sh — restore the image's runtime PATH.
#
# Debian's /etc/profile hard-sets PATH to a minimal default, throwing
# away the Dockerfile's `ENV PATH=...` every time bash starts as a login
# shell (the default CMD here). profile.d scripts are sourced *after*
# the PATH= block in /etc/profile, so this is the correct place to put
# the dirs back. Value mirrors the runtime ENV at the bottom of the
# Dockerfile; keep them in sync if you edit either.

PATH="$HOME/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PATH
