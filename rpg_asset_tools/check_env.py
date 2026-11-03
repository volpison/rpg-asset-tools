"""
check_env.py
------------
Checks that this machine can run the app. Called by check_dependencies.bat,
but also fine to run by hand:   python check_env.py

Exit codes (the .bat reads them):
    0   everything needed is there
    10  Python is too old
    11  PySide6 is missing or does not load  (the .bat offers to install it)
    12  files of the app itself are missing or fail to import
"""

import importlib
import os
import sys

MIN_PYTHON = (3, 9)
HERE = os.path.dirname(os.path.abspath(__file__))

# Pure-Python modules (no Qt needed)
CORE_MODULES = [
    "asset_paths", "system_defaults", "refs", "plugin_refs", "jsonio", "scanner",
    "annotations", "history", "index", "rename_engine", "duplicates", "lint",
    "deploy", "health", "batch_tools",
]
# Modules that import PySide6
GUI_MODULES = ["image_preview", "audio_preview", "health_gui", "gui"]
APP_FILES = [m + ".py" for m in CORE_MODULES + GUI_MODULES] + ["main.py", "requirements.txt"]


def line(tag: str, text: str) -> None:
    print(f"  [{tag:^4}] {text}")


def main() -> int:
    code = 0
    print("Python")
    line("OK", f"{sys.version.split()[0]}  ({sys.executable})") \
        if sys.version_info >= MIN_PYTHON else \
        line("FAIL", f"{sys.version.split()[0]} - need {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer")
    if sys.version_info < MIN_PYTHON:
        return 10
    if sys.maxsize <= 2 ** 32:
        line("WARN", "32-bit Python: PySide6 needs a 64-bit Python")

    print("\nApp files")
    missing = [f for f in APP_FILES if not os.path.isfile(os.path.join(HERE, f))]
    if missing:
        line("FAIL", "missing: " + ", ".join(missing))
        line("INFO", "run this script from the folder that contains main.py")
        return 12
    line("OK", f"{len(APP_FILES)} files found")

    print("\nPySide6 (the only package the app needs)")
    qt_ok = True
    try:
        import PySide6
        line("OK", f"PySide6 {PySide6.__version__}")
        from PySide6 import QtCore, QtGui, QtWidgets  # noqa: F401
        line("OK", "QtCore, QtGui, QtWidgets load")
    except Exception as e:  # noqa: BLE001 - report whatever went wrong
        qt_ok = False
        line("FAIL", f"{type(e).__name__}: {e}")
        line("INFO", "install with:  python -m pip install -r requirements.txt")
        code = 11
    if qt_ok:
        try:
            from PySide6 import QtMultimedia  # noqa: F401
            line("OK", "QtMultimedia (audio preview inside the app)")
        except Exception:  # noqa: BLE001
            line("WARN", "QtMultimedia not available - audio preview will offer your "
                         "default player instead. Everything else works.")

    print("\nApp modules")
    sys.path.insert(0, HERE)
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")   # importing must not need a display
    bad = []
    for name in CORE_MODULES + (GUI_MODULES if qt_ok else []):
        try:
            importlib.import_module(name)
        except Exception as e:  # noqa: BLE001
            bad.append((name, f"{type(e).__name__}: {e}"))
    if bad:
        for name, err in bad:
            line("FAIL", f"{name}: {err}")
        code = code or 12
    else:
        line("OK", f"{len(CORE_MODULES)} core modules import cleanly"
                   + (f", {len(GUI_MODULES)} window modules too" if qt_ok else
                      " (window modules skipped: PySide6 missing)"))

    print()
    if code == 0:
        print("  Everything needed is installed.")
    return code


if __name__ == "__main__":
    sys.exit(main())
