"""Точка входа для PyInstaller (launcher.py использует относительные импорты, поэтому как скрипт не собирается)."""

import multiprocessing

from neo.desktop.launcher import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
