"""
main.py
-------
Entry point.   pip install PySide6   then   python main.py

PySide6 is imported inside main() on purpose: the scan starts helper processes (see
parallel.py); on Windows those re-import this file, and they must not load Qt.
"""

import multiprocessing
import sys


def main():
    from PySide6.QtWidgets import QApplication

    from gui import MainWindow

    sys.setswitchinterval(0.001)      # hand the GIL back to the window thread more often
    app = QApplication(sys.argv)
    app.setOrganizationName("rpg_asset_tools")
    app.setApplicationName("RPG Maker MZ Asset Tools")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
