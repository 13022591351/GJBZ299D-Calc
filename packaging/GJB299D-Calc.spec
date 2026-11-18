"""Single-file build specification; expects database/standard_reference.sqlite beside the executable."""

import sys
from pathlib import Path, PurePosixPath

from PyInstaller.utils.hooks import collect_submodules

root = Path(SPECPATH).parent  # noqa: F821
is_windows = sys.platform == "win32"
binary_name = "GJB299D-Calc.exe" if is_windows else "GJB299D-Calc.run"


def keep_qt_data(destination):
    path = PurePosixPath(destination.replace("\\", "/"))
    if "translations" in path.parts and "PyQt6" in path.parts:
        return path.name in {"qt_zh_CN.qm", "qtbase_zh_CN.qm"}
    return path.suffix.lower() not in {
        ".ttf", ".otf", ".ttc", ".woff", ".woff2", ".pfb", ".pfa", ".afm",
    }


a = Analysis(  # noqa: F821
    [str(root / "main.py")],
    pathex=[str(root)],
    binaries=[],
    # Bundle UI icons and checkbox/status SVG assets; database/standard_reference.sqlite stays external next to executable.
    datas=[(str(root / "src" / "assets"), "src/assets")],
    hiddenimports=collect_submodules("src") + ["PyQt6.QtSvg", "defusedxml", "openpyxl", "xlrd", "xlwt"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest", "pytestqt", "ruff", "debugpy", "setuptools", "pip",
        "tkinter", "PyQt5", "PySide6",
        "PyQt6.QtPdf", "PyQt6.QtPdfWidgets", "PyQt6.QtQuick", "PyQt6.QtQml",
        "PyQt6.QtWebEngineCore", "PyQt6.QtWebEngineWidgets", "PyQt6.QtWebEngineQuick",
        "PyQt6.QtNetwork", "PyQt6.QtTest", "PyQt6.QtMultimedia", "PyQt6.QtBluetooth",
    ],
    noarchive=False,
)
a.datas = [entry for entry in a.datas if keep_qt_data(entry[0])]
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=binary_name,
    icon=str(root / "src" / "assets" / "gjb299d-calc.ico"),
    debug=False,
    bootloader_ignore_signals=False,
    strip=not is_windows,
    upx=False,
    console=False,
)
