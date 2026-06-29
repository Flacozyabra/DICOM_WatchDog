import sys
from PyQt6.QtCore import Qt, QCoreApplication
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QProgressBar
from core.locale_utils import tr_ui

class LoadingProgressDialog(QDialog):
    def __init__(self, parent=None, title=None):
        super().__init__(parent)
        if title is None:
            title = tr_ui("loading_title_data")
        self.setWindowTitle(title)
        self.setMinimumWidth(400)
        self.setModal(True)
        # Отключаем кнопку закрытия ("X"), чтобы пользователь не мог прервать заполнение
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowCloseButtonHint)
        
        # Темный режим заголовка для Windows
        if sys.platform == "win32":
            import ctypes
            try:
                hwnd = int(self.winId())
                # Immersive Dark Mode
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, 20, ctypes.byref(ctypes.c_int(1)), ctypes.sizeof(ctypes.c_int)
                )
                # Caption Color (#2b2b2b)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, 35, ctypes.byref(ctypes.c_int(0x002b2b2b)), ctypes.sizeof(ctypes.c_int)
                )
                # Text Color (White)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, 36, ctypes.byref(ctypes.c_int(0x00ffffff)), ctypes.sizeof(ctypes.c_int)
                )
            except Exception:
                pass
                
        # Макет
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)
        
        self.label = QLabel(tr_ui("loading_preparing_table"))
        self.label.setStyleSheet("color: #ffffff; font-size: 13px; font-family: 'Segoe UI';")
        layout.addWidget(self.label)
        
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFixedHeight(20)
        self.progress.setStyleSheet("""
            QProgressBar {
                border: 1px solid #3d3d3d;
                border-radius: 6px;
                background-color: #0f0f0f;
                text-align: center;
                color: #ffffff;
                font-weight: bold;
            }
            QProgressBar::chunk {
                background-color: #1f538d;
                border-radius: 5px;
            }
        """)
        layout.addWidget(self.progress)
        
        # Стили самого диалога (темный фон)
        self.setStyleSheet("QDialog { background-color: #202020; }")
        
    def set_progress(self, current, total):
        if total <= 0:
            return
        percent = int((current / total) * 100)
        self.progress.setValue(percent)
        self.label.setText(tr_ui("loading_rendering_table", current, total))
        QCoreApplication.processEvents()

    def set_scan_progress(self, current, total):
        """Update progress bar during folder scanning: shows percent + 'X из N папок'."""
        if total <= 0:
            return
        percent = int((current / total) * 100)
        self.progress.setValue(percent)
        self.label.setText(tr_ui("loading_scanning_folders", current, total, percent))
        QCoreApplication.processEvents()
