"""Double-click entry point for the desktop GUI -- no terminal required.

Run via run_gui.bat (uses the project's virtual environment) rather than
double-clicking this file directly, unless you've installed the
requirements into your system Python.
"""

from src.gui import main

if __name__ == "__main__":
    main()
