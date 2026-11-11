"""Entry script for the packaged Windows application (PyInstaller)."""

import sys

from nsw.ui.app import main

if __name__ == "__main__":
    sys.exit(main())
