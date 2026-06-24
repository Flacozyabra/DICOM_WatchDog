import sys
import os

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap, QColor
from PyQt6.QtWidgets import QApplication, QSplashScreen, QProgressBar, QVBoxLayout, QLabel

MainWindow = None


def exception_hook(exctype, value, traceback_obj):
    import traceback
    err_msg = "".join(traceback.format_exception(exctype, value, traceback_obj))
    sys.__excepthook__(exctype, value, traceback_obj)
    if MainWindow and hasattr(MainWindow, 'instance') and MainWindow.instance:
        try:
            from core.logger import log_message
            log_message(MainWindow.instance.output_field, f"Ошибка выполнения:\n{err_msg}")
        except Exception:
            pass


sys.excepthook = exception_hook


class LoadingSplash(QSplashScreen):
    def __init__(self):
        pixmap = QPixmap(500, 220)
        pixmap.fill(QColor("#202020"))
        super().__init__(pixmap)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(15)
        layout.addStretch(1)
        
        self.title_label = QLabel("DICOM WatchDog")
        self.title_label.setStyleSheet("color: #ffffff; font-size: 24px; font-weight: bold; font-family: 'Segoe UI';")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title_label)
        
        self.status_label = QLabel("Инициализация...")
        self.status_label.setStyleSheet("color: #aaaaaa; font-size: 13px; font-family: 'Segoe UI';")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status_label)
        
        self.progress_bar = QProgressBar(self)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(10)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #3d3d3d;
                border-radius: 5px;
                background-color: #0f0f0f;
            }
            QProgressBar::chunk {
                background-color: #1f538d;
                border-radius: 4px;
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
    app = QApplication(sys.argv)
    
    splash = LoadingSplash()
    splash.show()
    
    splash.set_progress(10, "Загрузка базовых компонентов...")
    
    splash.set_progress(30, "Загрузка модулей DICOM...")
    import pydicom
    
    splash.set_progress(50, "Загрузка сетевых компонентов PACS...")
    import pynetdicom
    
    splash.set_progress(70, "Загрузка модулей обработки изображений...")
    import numpy as np
    
    splash.set_progress(90, "Инициализация интерфейса...")
    from ui.main_window import MainWindow as MW
    MainWindow = MW
    
    splash.set_progress(100, "Запуск...")
    window = MainWindow()
    window.show()
    
    splash.finish(window)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
