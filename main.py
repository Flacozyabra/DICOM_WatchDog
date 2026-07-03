import sys
import time

import os
from core.locale_utils import tr_ui, tr_log

# --- ШИМ ДЛЯ СОВМЕСТИМОСТИ PYQT6 -> PYQT5 ---
USE_PYQT5 = os.environ.get('FORCE_PYQT5') == '1'
if not USE_PYQT5:
    try:
        import PyQt6.QtCore
    except ImportError:
        USE_PYQT5 = True

if USE_PYQT5:
    try:
        import PyQt5.QtCore
        import PyQt5.QtGui
        import PyQt5.QtWidgets

        # Патчим оригинальные классы PyQt5 для трансляции вложенных перечислений PyQt6
        for name in [
            'AlignmentFlag', 'CheckState', 'ContextMenuPolicy', 'Corner',
            'CursorShape', 'ItemDataRole', 'ItemFlag', 'KeyboardModifier',
            'Orientation', 'ScrollBarPolicy', 'SortOrder', 'TextInteractionFlag',
            'WindowModality'
        ]:
            if not hasattr(PyQt5.QtCore.Qt, name):
                setattr(PyQt5.QtCore.Qt, name, PyQt5.QtCore.Qt)

        for name in ['EditTrigger', 'SelectionBehavior', 'SelectionMode']:
            if not hasattr(PyQt5.QtWidgets.QAbstractItemView, name):
                setattr(PyQt5.QtWidgets.QAbstractItemView, name, PyQt5.QtWidgets.QAbstractItemView)

        if not hasattr(PyQt5.QtGui.QFont, 'Weight'):
            setattr(PyQt5.QtGui.QFont, 'Weight', PyQt5.QtGui.QFont)
            
        if not hasattr(PyQt5.QtWidgets.QHeaderView, 'ResizeMode'):
            setattr(PyQt5.QtWidgets.QHeaderView, 'ResizeMode', PyQt5.QtWidgets.QHeaderView)
            
        if not hasattr(PyQt5.QtWidgets.QLineEdit, 'EchoMode'):
            setattr(PyQt5.QtWidgets.QLineEdit, 'EchoMode', PyQt5.QtWidgets.QLineEdit)

        for name in ['ButtonRole', 'Icon', 'StandardButton']:
            if not hasattr(PyQt5.QtWidgets.QMessageBox, name):
                setattr(PyQt5.QtWidgets.QMessageBox, name, PyQt5.QtWidgets.QMessageBox)

        for name in ['ColorGroup', 'ColorRole']:
            if not hasattr(PyQt5.QtGui.QPalette, name):
                setattr(PyQt5.QtGui.QPalette, name, PyQt5.QtGui.QPalette)

        if not hasattr(PyQt5.QtCore.QSettings, 'Format'):
            setattr(PyQt5.QtCore.QSettings, 'Format', PyQt5.QtCore.QSettings)
            
        if not hasattr(PyQt5.QtWidgets.QSizePolicy, 'Policy'):
            setattr(PyQt5.QtWidgets.QSizePolicy, 'Policy', PyQt5.QtWidgets.QSizePolicy)
            
        if not hasattr(PyQt5.QtGui.QTextCursor, 'MoveOperation'):
            setattr(PyQt5.QtGui.QTextCursor, 'MoveOperation', PyQt5.QtGui.QTextCursor)

        if not hasattr(PyQt5.QtWidgets.QFrame, 'Shape'):
            setattr(PyQt5.QtWidgets.QFrame, 'Shape', PyQt5.QtWidgets.QFrame)
            
        if not hasattr(PyQt5.QtWidgets.QFrame, 'Shadow'):
            setattr(PyQt5.QtWidgets.QFrame, 'Shadow', PyQt5.QtWidgets.QFrame)

        # Перенаправляем QAction и QActionGroup, которые переехали в QtGui в PyQt6
        PyQt5.QtGui.QAction = PyQt5.QtWidgets.QAction
        if hasattr(PyQt5.QtWidgets, 'QActionGroup'):
            PyQt5.QtGui.QActionGroup = PyQt5.QtWidgets.QActionGroup

        # Патчим QMouseEvent для поддержки .position(), возвращающего QPointF как в PyQt6
        PyQt5.QtGui.QMouseEvent.position = lambda self: self.localPos()

        # Включаем поддержку High DPI масштабирования для PyQt5
        PyQt5.QtCore.QCoreApplication.setAttribute(PyQt5.QtCore.Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)
        PyQt5.QtCore.QCoreApplication.setAttribute(PyQt5.QtCore.Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)
        os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "1"
        os.environ["QT_SCALE_FACTOR_ROUNDING_POLICY"] = "PassThrough"

        # Подменяем модули в sys.modules
        sys.modules['PyQt6'] = sys.modules.get('PyQt5')
        sys.modules['PyQt6.QtCore'] = PyQt5.QtCore
        sys.modules['PyQt6.QtGui'] = PyQt5.QtGui
        sys.modules['PyQt6.QtWidgets'] = PyQt5.QtWidgets
    except ImportError as e_pyqt5:
        if sys.platform == "win32":
            import ctypes
            error_msg = (
                tr_ui("main_err_startup") + ":\n\n" +
                tr_ui("main_err_missing_qt") + "\n\n" +
                tr_ui("main_err_details_qt6") + "\n" +
                tr_ui("main_err_details_qt5", str(e_pyqt5))
            )
            ctypes.windll.user32.MessageBoxW(0, error_msg, tr_ui("main_critical_error_title"), 0x10) # 0x10 = MB_ICONERROR
        else:
            print(f"Error: failed to load PyQt6/PyQt5. Details: {e_pyqt5}")
        sys.exit(1)

try:
    from PyQt6.QtCore import Qt, QThread, pyqtSignal
    from PyQt6.QtWidgets import QApplication, QSplashScreen, QProgressBar, QVBoxLayout, QLabel
except ImportError as e:
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, tr_ui("main_err_import_msg", str(e)), tr_ui("main_err_import_title"), 0x10)
    else:
        print(f"Critical import error: {e}")
    sys.exit(1)

MainWindow = None
_worker = None


def exception_hook(exctype, value, traceback_obj):
    import traceback
    err_msg = "".join(traceback.format_exception(exctype, value, traceback_obj))
    sys.__excepthook__(exctype, value, traceback_obj)
    if hasattr(MainWindow, 'instance') and MainWindow.instance:
        try:
            from core.logger import log_message
            log_message(MainWindow.instance.output_field, tr_log("log_runtime_error", err_msg))
        except Exception:
            pass


sys.excepthook = exception_hook


class ImportWorker(QThread):
    progress = pyqtSignal(int, str)
    finished_import = pyqtSignal()

    def run(self):
        self.progress.emit(10, tr_ui("main_progress_base"))
        time.sleep(0.1)
        
        self.progress.emit(30, tr_ui("main_progress_dicom"))
        import pydicom
        
        self.progress.emit(50, tr_ui("main_progress_pacs"))
        import pynetdicom
        
        self.progress.emit(70, tr_ui("main_progress_image"))
        import numpy
        
        self.progress.emit(90, tr_ui("main_progress_ui"))
        import ui.main_window
        
        self.progress.emit(100, tr_ui("main_progress_launch"))
        time.sleep(0.1)
        self.finished_import.emit()


class LoadingSplash(QSplashScreen):
    def __init__(self):
        from PyQt6.QtGui import QPixmap, QColor
        from PyQt6.QtWidgets import QVBoxLayout, QLabel, QProgressBar
        from PyQt6.QtCore import Qt
        from core.config_utils import get_resource_path
        import os
        
        pixmap = QPixmap(450, 500)
        pixmap.fill(QColor("#202020"))
        
        super().__init__(pixmap)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 20, 40, 30)
        layout.setSpacing(10)
        
        self.logo_label = QLabel()
        logo_path = get_resource_path("src/splashscreen_logo.png")
        if os.path.exists(logo_path):
            logo_pix = QPixmap(logo_path)
            logo_pix = logo_pix.scaled(350, 350, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self.logo_label.setPixmap(logo_pix)
        self.logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.logo_label)
        
        layout.addStretch(1)
        
        self.status_label = QLabel(tr_ui("main_status_init"))
        self.status_label.setStyleSheet("color: #aaaaaa; font-size: 12px; font-family: 'Segoe UI';")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status_label)
        
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #3d3d3d;
                border-radius: 3px;
                background-color: #0f0f0f;
            }
            QProgressBar::chunk {
                background-color: #1f538d;
                border-radius: 2px;
            }
        """)
        layout.addWidget(self.progress_bar)
        
        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.FramelessWindowHint)

    def set_progress(self, value, text):
        self.progress_bar.setValue(value)
        self.status_label.setText(text)
        QApplication.processEvents()


def main():
    global MainWindow, _worker
    
    # Set AppUserModelID so Windows taskbar correctly groups windows under the custom icon
    if sys.platform == "win32":
        import ctypes
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("dicom.watchdog.app.v1")
        except Exception:
            pass

    app = QApplication(sys.argv)
    
    # Set application-wide default window icon
    from PyQt6.QtGui import QIcon
    from core.config_utils import get_resource_path
    app.setWindowIcon(QIcon(get_resource_path("src/splashscreen_logo.png")))
    
    splash = LoadingSplash()
    splash.show()
    
    # Close PyInstaller bootloader splash screen if it was shown
    try:
        import pyi_splash
        pyi_splash.close()
    except ImportError:
        pass
    
    _worker = ImportWorker()
    _worker.progress.connect(splash.set_progress)
    
    def on_finished():
        global MainWindow
        from ui.main_window import MainWindow as MW
        MainWindow = MW
        
        window = MainWindow()
        window.show()
        splash.finish(window)
        
    _worker.finished_import.connect(on_finished)
    _worker.start()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
