# /etc/profile.d/aetherion-bridge.sh — loopback bridge for forwarded ports.
#
# Many dev tools (openclaw is the canonical example) bind 127.0.0.1 inside
# the container as a "secure" default. That defeats podman/docker port
# publishing (`-p ...`), which terminates at the container's external
# interface — loopback bindings never see those packets. This script
# stands up a tiny userland TCP bridge (socat) per requested port: it
# listens on the container's primary non-loopback IPv4 and forwards to
# 127.0.0.1:<port>. The runtime's port mapping then reaches socat, and
# socat hands the connection off to the loopback-bound service.
#
# Triggered by AETHERION_BRIDGE_PORTS, set by the aetherion launcher when
# any `--forward-<agent>` convenience flag wants bridging. Format is a
# comma-separated list of `SERVICE:BRIDGE` pairs: socat listens on the
# container's external interface at BRIDGE and forwards to 127.0.0.1:SERVICE.
# The runtime's port mapping aims at BRIDGE; the service stays bound to its
# real port on loopback. Using a separate BRIDGE port avoids EADDRINUSE
# fights with services (e.g. openclaw) that do transient wildcard binds at
# startup. A single bare PORT is also accepted and treated as PORT:PORT for
# backward compat. Pidfile-guarded so re-sourcing on every login is cheap.

[ -z "${AETHERION_BRIDGE_PORTS:-}" ] && return 0
command -v socat >/dev/null 2>&1 || return 0

_aetherion_bridge_dir=/tmp/aetherion-bridge
mkdir -p "$_aetherion_bridge_dir" 2>/dev/null || return 0

# Pick the first non-loopback, non-link-local IPv4 from `hostname -I`.
# Doing it in pure shell avoids assuming any one interface name (eth0 in
# Docker bridge, tap0 under rootless podman+slirp4netns, etc.).
_aetherion_bridge_ip=""
for _ip in $(hostname -I 2>/dev/null); do
    case "$_ip" in
        127.*|::1|fe80:*|169.254.*) continue ;;
        *.*.*.*) _aetherion_bridge_ip="$_ip"; break ;;
    esac
done
[ -z "$_aetherion_bridge_ip" ] && {
    unset _aetherion_bridge_dir _aetherion_bridge_ip _ip
    return 0
}

IFS=',' read -ra _aetherion_bridge_pairs <<< "$AETHERION_BRIDGE_PORTS"
for _pair in "${_aetherion_bridge_pairs[@]}"; do
    [ -n "$_pair" ] || continue
    if [[ "$_pair" == *:* ]]; then
        _service_port="${_pair%:*}"
        _bridge_port="${_pair#*:}"
    else
        _service_port="$_pair"
        _bridge_port="$_pair"
    fi
    [ -n "$_service_port" ] && [ -n "$_bridge_port" ] || continue

    _pidfile="$_aetherion_bridge_dir/$_bridge_port.pid"
    _logfile="$_aetherion_bridge_dir/$_bridge_port.log"
    if [ -s "$_pidfile" ] && kill -0 "$(cat "$_pidfile")" 2>/dev/null; then
        continue
    fi
    : > "$_logfile"

    # Detach via `setsid -f`: socat ends up in its own session with init as
    # its parent, so it survives this shell's exit independent of job control
    # (login shells reached by `/bin/bash -l` don't always have it). Capture
    # socat's PID after the fact via pgrep — setsid forks, so `$!` would give
    # the wrong PID. `-lf <file>` makes socat log to the same place as the
    # redirected stderr, so socat's own startup messages get captured.
    setsid -f socat -d -lf "$_logfile" \
        "TCP-LISTEN:$_bridge_port,bind=$_aetherion_bridge_ip,fork,reuseaddr" \
        "TCP:127.0.0.1:$_service_port" \
        >>"$_logfile" 2>&1 </dev/null

    sleep 0.2
    _pid=$(pgrep -f "TCP-LISTEN:$_bridge_port,bind=$_aetherion_bridge_ip" 2>/dev/null | head -n1)
    if [ -n "$_pid" ]; then
        echo "$_pid" > "$_pidfile"
    else
        rm -f "$_pidfile"
        printf "aetherion-bridge: %s->%s failed to come up — see %s\n" \
            "$_bridge_port" "$_service_port" "$_logfile" >&2
    fi
done

unset _aetherion_bridge_dir _aetherion_bridge_ip _aetherion_bridge_pairs \
      _ip _pair _service_port _bridge_port _pidfile _logfile _pid
