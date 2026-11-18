import argparse
import os
import sys
import traceback
from pathlib import Path

from PyQt6.QtCore import QLibraryInfo, Qt, QTranslator
from PyQt6.QtWidgets import QApplication, QMessageBox

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "src"

from . import APP_VERSION, __version__  # noqa: E402
from .theme import app_icon, apply_theme, current_theme  # noqa: E402
from .window import MainWindow  # noqa: E402

_IN_EXCEPTHOOK = False


def install_excepthook():
    """Install a non-fatal global exception hook for the GUI event loop."""

    def _excepthook(exc_type, exc_value, exc_tb):
        global _IN_EXCEPTHOOK
        if issubclass(exc_type, (KeyboardInterrupt, SystemExit)):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        formatted = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        stream = sys.__stderr__ if sys.__stderr__ is not None else sys.stderr
        if stream is not None:
            try:
                stream.write(formatted)
                stream.flush()
            except Exception:
                pass
        if _IN_EXCEPTHOOK or QApplication.instance() is None:
            return
        _IN_EXCEPTHOOK = True
        try:
            parent = QApplication.activeWindow()
            QMessageBox.critical(
                parent,
                "未预期的内部错误",
                f"执行操作时发生未预期的异常，当前工程数据未受影响：\n\n{exc_type.__name__}: {exc_value}",
            )
        except Exception:
            pass
        finally:
            _IN_EXCEPTHOOK = False

    sys.excepthook = _excepthook
    return _excepthook


def create_application():
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setOrganizationName("DuoYuWang")
    app.setApplicationName("GJB299D")
    app.setApplicationVersion(__version__)
    app.setApplicationDisplayName(f"GJB299D 可靠性预计 {APP_VERSION}")
    app.setStyle("Fusion")
    app.setWindowIcon(app_icon())
    apply_theme(app, current_theme(), persist=False)
    app.translator = QTranslator(app)
    translations_path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    if (
        app.translator.load("qt_zh_CN", translations_path)
        or app.translator.load("qtbase_zh_CN", translations_path)
    ):
        app.installTranslator(app.translator)
    return app


def main():
    devnull_handles = []
    previous_excepthook = sys.excepthook
    try:
        if sys.stdout is None:
            handle = open(os.devnull, "w", encoding="utf-8")
            devnull_handles.append(handle)
            sys.stdout = handle
        if sys.stderr is None:
            handle = open(os.devnull, "w", encoding="utf-8")
            devnull_handles.append(handle)
            sys.stderr = handle
        parser = argparse.ArgumentParser(description="GJB299D 可靠性预计工作台")
        parser.add_argument("project", nargs="?", help="要打开的 .json 工程或表格文件")
        try:
            args = parser.parse_args()
        except SystemExit as error:
            return int(error.code) if isinstance(error.code, int) else 0
        app = create_application()
        install_excepthook()
        try:
            window = MainWindow()
        except Exception as error:
            QMessageBox.critical(None, "启动失败", str(error))
            return 1
        window.show()
        if args.project:
            window.open_dropped_files([args.project])
        else:
            window.recover_untitled_autosave(prompt=True)
        return app.exec()
    finally:
        sys.excepthook = previous_excepthook
        for handle in devnull_handles:
            handle.close()


if __name__ == "__main__":
    raise SystemExit(main())
