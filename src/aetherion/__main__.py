"""Entry point for `python -m aetherion`. The console-script entry point in
pyproject.toml points at the same function, so both invocation paths share
a single implementation."""

import sys

from aetherion.cli import main

if __name__ == "__main__":
    sys.exit(main())
