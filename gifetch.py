#!/usr/bin/env python3
"""Entry point. Symlinked as ~/.local/bin/gifetch by install.sh."""
import os
import sys

# Resolve the symlink so the package next to the real file is found.
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

from gifetch.cli import main  # noqa: E402

main()
