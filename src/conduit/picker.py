"""Minimal arrow-key model picker.

Stdlib-only (termios + tty + ANSI) so conduit ships with no runtime
dependencies. We only target Linux containers, where ``sys.stdin`` is a tty
and ``termios`` is available — non-tty input falls back to printing the
options and accepting the first item, which keeps headless / piped uses
working.
"""
from __future__ import annotations

import os
import sys

_HIDE_CURSOR = "\x1b[?25l"
_SHOW_CURSOR = "\x1b[?25h"
_CLEAR_LINE = "\x1b[2K"
_RESET = "\x1b[0m"
_BOLD = "\x1b[1m"
_REVERSE = "\x1b[7m"
_DIM = "\x1b[2m"


def pick(title: str, items: list[str], default: str | None = None) -> str | None:
    """Show a single-select picker and return the chosen item.

    Returns None when the user cancels (Esc / Ctrl-C / Ctrl-D). ``default``
    is rendered first when present in ``items`` so the most recent choice
    sits under the initial cursor — chosen consciously over moving the
    cursor mid-list, since the top is also where the eye lands.
    """
    if not items:
        return None

    ordered = _reorder_with_default(items, default)

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        # Non-interactive: print the list once so callers can see what
        # we would have offered, then auto-pick whatever sits at the top
        # (the default if provided, otherwise the first model).
        print(title, file=sys.stderr)
        for item in ordered:
            print(f"  {item}", file=sys.stderr)
        return ordered[0]

    import termios
    import tty

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    selected = 0
    rendered_lines = 0

    def render() -> None:
        nonlocal rendered_lines
        if rendered_lines:
            # Move cursor up to the title row and wipe everything we
            # rendered last frame so navigation doesn't leak ghost rows.
            sys.stdout.write(f"\x1b[{rendered_lines}A")
        lines = [f"{_BOLD}{title}{_RESET}", ""]
        for i, item in enumerate(ordered):
            if i == selected:
                lines.append(f"{_REVERSE} > {item} {_RESET}")
            else:
                lines.append(f"   {item}")
        lines.append("")
        lines.append(
            f"{_DIM}↑/↓ navigate · enter select · esc cancel{_RESET}"
        )
        for line in lines:
            sys.stdout.write(f"\r{_CLEAR_LINE}{line}\n")
        sys.stdout.flush()
        rendered_lines = len(lines)

    try:
        tty.setraw(fd)
        sys.stdout.write(_HIDE_CURSOR)
        render()
        while True:
            ch = os.read(fd, 8)
            if not ch:
                return None
            # Ctrl-C, Ctrl-D, Esc (bare): cancel.
            if ch in (b"\x03", b"\x04", b"\x1b"):
                return None
            # Enter (LF or CR depending on terminal mode).
            if ch in (b"\r", b"\n"):
                return ordered[selected]
            # Arrow keys arrive as CSI sequences (ESC [ A/B/C/D).
            if ch.startswith(b"\x1b["):
                if ch.endswith(b"A"):  # up
                    selected = (selected - 1) % len(ordered)
                    render()
                elif ch.endswith(b"B"):  # down
                    selected = (selected + 1) % len(ordered)
                    render()
                continue
            # j/k vim-style nav, since folks already wired their muscle memory.
            if ch == b"k":
                selected = (selected - 1) % len(ordered)
                render()
            elif ch == b"j":
                selected = (selected + 1) % len(ordered)
                render()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        sys.stdout.write(_SHOW_CURSOR)
        sys.stdout.flush()


def _reorder_with_default(items: list[str], default: str | None) -> list[str]:
    if default is None or default not in items:
        return list(items)
    rest = [m for m in items if m != default]
    return [default, *rest]
