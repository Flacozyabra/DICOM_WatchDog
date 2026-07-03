import sys
import os
import time

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
                "Ошибка запуска приложения:\n\n"
                "Не удалось загрузить компоненты PyQt6 или PyQt5. Обычно это связано с тем, что на компьютере не установлен пакет Microsoft Visual C++ Redistributable (MSVC++).\n\n"
                "Пожалуйста, скачайте и установите распространяемый пакет Visual C++ (версии 2015-2022) с официального сайта Microsoft и запустите программу снова.\n\n"
                f"Детали ошибки (PyQt6): Не удалось загрузить DLL\n"
                f"Детали ошибки (PyQt5): {e_pyqt5}"
            )
            ctypes.windll.user32.MessageBoxW(0, error_msg, "Критическая ошибка - DICOM WatchDog", 0x10) # 0x10 = MB_ICONERROR
        else:
            print(f"Error: failed to load PyQt6/PyQt5. Details: {e_pyqt5}")
        sys.exit(1)

from PyQt6.QtWidgets import QApplication, QSplashScreen, QVBoxLayout, QLabel, QProgressBar
from PyQt6.QtCore import Qt

from core.locale_utils import tr_ui, tr_log

MainWindow = None


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
    global MainWindow
    
    # Set AppUserModelID so Windows taskbar correctly groups windows under the custom icon
    if sys.platform == "win32":
        try:
            import winreg
            from core.config_utils import get_resource_path
            # Register AppUserModelID in HKCU registry for correct Windows Toasts behavior
            key_path = r"Software\Classes\AppUserModelId\DICOM WatchDog"
            key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path)
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, "DICOM WatchDog")
            
            from core.config_utils import get_app_data_dir
            icon_path = os.path.abspath(os.path.join(get_app_data_dir(), "splashscreen_logo.png"))
            if not os.path.exists(icon_path):
                icon_path = os.path.abspath(get_resource_path("src/splashscreen_logo.png"))
            if os.path.exists(icon_path):
                winreg.SetValueEx(key, "IconUri", 0, winreg.REG_SZ, icon_path)
            winreg.CloseKey(key)
        except Exception:
            pass

        import ctypes
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("DICOM WatchDog")
        except Exception:
            pass

    app = QApplication(sys.argv)
    
    # Set application-wide default window icon
    from PyQt6.QtGui import QIcon
    from core.config_utils import get_resource_path
    app.setWindowIcon(QIcon(get_resource_path("src/splashscreen_logo.png")))
    
    splash = LoadingSplash()
    splash.show()
    splash.set_progress(10, tr_ui("main_progress_base"))
    time.sleep(0.05)
    
    # Close PyInstaller bootloader splash screen if it was shown
    try:
        import pyi_splash
        pyi_splash.close()
    except ImportError:
        pass

    # Шаг 1: pydicom
    splash.set_progress(30, tr_ui("main_progress_dicom"))
    import pydicom
    time.sleep(0.05)
    
    # Шаг 2: pynetdicom
    splash.set_progress(50, tr_ui("main_progress_pacs"))
    import pynetdicom
    time.sleep(0.05)
    
    # Шаг 3: numpy
    splash.set_progress(70, tr_ui("main_progress_image"))
    import numpy
    time.sleep(0.05)
    
    # Шаг 4: ui.main_window
    splash.set_progress(90, tr_ui("main_progress_ui"))
    from ui.main_window import MainWindow as MW
    MainWindow = MW
    time.sleep(0.05)
    
    splash.set_progress(100, tr_ui("main_progress_launch"))
    time.sleep(0.05)
    
    window = MainWindow()
    window.show()
    splash.finish(window)
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
