import sys
from pathlib import Path

# Ensure the package is importable when running from a checkout.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import harness.core  # noqa: F401  # fuerza la inclusión del submódulo en PyInstaller
from harness.tui import main

if __name__ == "__main__":
    main()
