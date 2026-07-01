# /etc/profile.d/aetherion-path.sh — restore the image's runtime PATH.
#
# Debian's /etc/profile hard-sets PATH to a minimal default, throwing
# away the Dockerfile's `ENV PATH=...` every time bash starts as a login
# shell (the default CMD here). User-local tool dirs ($HOME/.cargo/bin,
# $HOME/.local/bin, $HOME/go/bin, $HOME/.bun/bin), the system Rust
# toolchain (/usr/local/cargo/bin), and node (/opt/node/bin) all get
# silently stripped — so any `cargo install`, `pip install --user`,
# `go install`, etc. binary the user drops into those locations only
# works from non-login shells.
#
# profile.d scripts are sourced *after* the PATH= block in /etc/profile,
# so this is the correct place to put the dirs back. Value mirrors the
# runtime ENV at the bottom of the Dockerfile; keep them in sync if you
# edit either.

PATH="$HOME/.cargo/bin:$HOME/go/bin:$HOME/.local/bin:$HOME/.bun/bin:/usr/local/cargo/bin:/usr/local/sbin:/usr/local/bin:/opt/node/bin:/usr/sbin:/usr/bin:/sbin:/bin"
export PATH
