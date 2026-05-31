# Security model

Containers can be secured to many levels: choice of runtime, user-namespace
mode, seccomp / AppArmor / SELinux profiles, sandbox technologies like
[gVisor](https://gvisor.dev/docs/), microVMs, egress policies, and so on.
Aetherion uses containers as its isolation primitive — your editors, agent
CLIs, and toolchains run inside a per-namespace container instead of directly
on the host. This document describes what that gives you out of the box and
where it stops, so you can decide what to layer on for your own threat model.

The short version: a namespace contains a non-root process that has network
access to the open internet, has read/write access to the files you mount in
(at minimum your project directory and the namespace's `$HOME`), and on Linux
shares the host's kernel. Aetherion does not currently restrict outbound
traffic from inside a namespace.

## Runtime and platform

The boundary between an agent and the rest of your machine depends on what's
running the container.

- **Linux + rootless podman or docker (default).** The container is a normal
  Linux process tree in user, mount, pid, ipc, uts, and network namespaces.
  Aetherion runs it as UID 1000 with no added capabilities, no `--privileged`,
  and the runtime's default seccomp profile. The kernel is shared with the
  host, so a kernel-level container escape lands directly on the host.
- **Linux + [gVisor](https://gvisor.dev/docs/) (`runsc`) as the OCI runtime.**
  Not configured by Aetherion, but you can point podman or docker at it. gVisor
  intercepts syscalls in userspace, so a workload sees a much smaller kernel
  surface. The trade-off is real: slower syscall and I/O performance, some
  syscalls and `/proc` interfaces missing, and a few container features
  (notably podman-in-container, which the `default` and `cli-agents` templates
  use) that don't work under it. If you want a meaningful sandbox boundary on
  Linux without a VM, this is the usual answer.
- **macOS (Docker Desktop or podman-machine).** The container runtime itself
  runs inside a Linux VM managed by the desktop product. A kernel-level
  container escape lands inside that VM, not on macOS. This is a stronger
  boundary than rootless containers on a bare Linux host, but it is not a
  security feature Aetherion adds — it's a property of how containers work on
  macOS. The VM still has filesystem and network access to the host through
  the bind mounts and port forwards Aetherion sets up.
- **Windows.** Not currently supported.

Aetherion does not set seccomp, AppArmor, or SELinux profiles itself. Whatever
your runtime applies by default is what you get. On rootless podman, mounts
are labeled with `:z` so SELinux-enabled hosts don't deny access to the
namespace's `$HOME`.

## Network exposure

There is no outbound network isolation today. Anything inside a namespace can
reach the open internet over whatever route the host has, and can reach
services on the host's loopback interface.

Specifically:

- The container runs on the runtime's default bridge (docker) or
  `slirp4netns` with `allow_host_loopback=true` (rootless podman). Outbound
  DNS, HTTP, and arbitrary TCP/UDP are permitted by default.
- `host.docker.internal` resolves to the host inside the container on both
  runtimes. This is how `conduit` reaches a model server on the host's
  `127.0.0.1`. Any other process in the namespace can use it too.
- Inbound access from outside the host is opt-in: nothing is published unless
  you configure it via the namespace's `port-forwarding:` block, `--forward`,
  or `--forward-openclaw`.

Restricting egress (allow-listed domains, blocked CIDRs, proxy-only routing)
is not implemented. It is on the roadmap. Until it lands, treat anything
inside a namespace as having the same network reach as a logged-in shell on
your host.

If you need egress controls today, options include: running the container
runtime under a host firewall rule that filters by uid or cgroup, running an
HTTP proxy on the host and setting `HTTPS_PROXY` per namespace via the
`environment:` block, or pointing the runtime at a network plugin that
enforces policy.

## Filesystem exposure

A namespace can see the following host paths by default. All are read-write
unless noted.

- **The namespace `$HOME`.** `~/.aetherion/namespaces/<name>/` on the host is
  bind-mounted at `/home/aetherion` inside the container. Agent logins, shell
  history, `npm install -g` output, nvim plugins, and any dotfiles the agent
  writes live here and persist across sessions. A compromised agent that
  writes a backdoor here will still be there on the next launch — `reset` is
  the way to wipe it.
- **Your current working directory.** Whatever directory you ran `aetherion`
  in is bind-mounted into the container. If it's under your host `$HOME` it
  appears at the matching path under `/home/aetherion`; if it's elsewhere it
  appears at the same absolute path. The agent has full read/write on
  everything under that tree, including files outside what you happened to
  edit. If you launch from `~`, the entire home directory is exposed.
- **Anything you mount with `volumes:` or `-v`.** Same rules — full
  read/write at the destination path inside the container.
- **Anything you pass via `environment:`.** `fromEnv` and `fromFile` values
  end up as environment variables visible to every process in the namespace.
  Tokens passed this way (`GH_TOKEN`, `OPENAI_API_KEY`, etc.) are readable by
  the agent and anything it spawns.

The container does **not** see host paths that aren't mounted in. The rest of
the host filesystem is invisible from inside the namespace.

Two namespaces have separate `$HOME` directories on the host, so an agent in
`work` cannot read `play`'s agent logins or shell history. They can,
however, both see the same host working directory if you launch them from
the same place, and they share the host kernel and host loopback.

## Display, GPU, and IPC exposure (GUI templates only)

When `display: x11`, `display: wayland`, or `--display` puts a namespace in
GUI mode on Linux, Aetherion mounts more of the host in so the IDE can paint:

- The X11 socket at `/tmp/.X11-unix` (X11 mode) or the Wayland socket under
  `$XDG_RUNTIME_DIR` (Wayland mode). An X11 client inside the container can
  read keystrokes and screen contents from every other X11 client on the
  same display, and can synthesize input events into them. Wayland is
  stricter by design but still gives the client access to its own surfaces
  and clipboard.
- The host's D-Bus session and system buses, when present. The agent can
  call into desktop services that accept session-bus calls — notifications,
  secret-service (keyring), `xdg-open`, portals.
- `/dev/dri` for GPU access, when the device exists.
- `--ipc host`, so the container shares the host's SysV/POSIX IPC namespace.
  This is required for Electron's MIT-SHM rendering path and means an
  attacker in the container can attach to host SHM segments.

If you run agent workloads in a GUI namespace, factor this in: the agent has
a path to your desktop session, not just to your project directory.

On macOS the GUI path uses XQuartz over TCP, with the launcher running
`xhost +` to authorize the VM's gateway address. This removes X11 access
control on the host for the duration of the session — any process that can
reach the XQuartz TCP port (`:6000`) can connect. The macOS firewall is
your control here.

## Cross-namespace and host-process isolation

- Two namespaces share no `$HOME` and no image. Logging into Claude under
  `work` doesn't log you in under `play`.
- Two namespaces share the host kernel (Linux) or the same Linux VM
  (macOS). A kernel-level escape from one is an escape from both.
- A namespace cannot see host processes outside the container's PID
  namespace. It can reach host services that are listening on a network
  socket (including `127.0.0.1` services, via `host.docker.internal`).
- The `default` and `cli-agents` templates ship podman-in-container, so an
  agent inside can build and run further containers. Those nested
  containers are sandboxed by the outer container's user namespace and do
  not gain additional host privileges, but they do run on the same host
  kernel.

## What Aetherion does not promise

- It does not restrict what an agent can do over the network.
- It does not restrict what an agent can read or write inside the mounts
  you give it.
- It does not protect against kernel-level container escapes on Linux. Use
  a stronger sandbox (gVisor, a VM, a separate machine) if your threat
  model includes that.
- It does not prevent an agent from leaving persistent state in the
  namespace `$HOME`. Use `aetherion reset namespace <name>` to wipe.
- It does not isolate the host display from a GUI namespace beyond what
  the chosen display protocol enforces.

If any of these are load-bearing for what you're doing, layer the
appropriate control on top — gVisor or a VM for runtime isolation, a host
firewall or HTTP proxy for egress, a fresh namespace per untrusted task for
persistence, and a separate user session (or a separate machine) for GUI
workloads you don't trust.
