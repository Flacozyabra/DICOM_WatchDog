import sys
import os
import time

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
