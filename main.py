import sys
from PyQt6.QtWidgets import QApplication

from ui.main_window import MainWindow
from core.logger import log_message


def exception_hook(exctype, value, traceback_obj):
    import traceback
    err_msg = "".join(traceback.format_exception(exctype, value, traceback_obj))
    sys.__excepthook__(exctype, value, traceback_obj)
    if hasattr(MainWindow, 'instance') and MainWindow.instance:
        try:
            log_message(MainWindow.instance.output_field, f"Ошибка выполнения:\n{err_msg}")
        except Exception:
            pass


sys.excepthook = exception_hook


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
