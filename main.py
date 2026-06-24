import sys
import time

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import QApplication, QSplashScreen, QProgressBar, QVBoxLayout, QLabel

MainWindow = None
_worker = None


def exception_hook(exctype, value, traceback_obj):
    import traceback
    err_msg = "".join(traceback.format_exception(exctype, value, traceback_obj))
    sys.__excepthook__(exctype, value, traceback_obj)
    if hasattr(MainWindow, 'instance') and MainWindow.instance:
        try:
            from core.logger import log_message
            log_message(MainWindow.instance.output_field, f"Ошибка выполнения:\n{err_msg}")
        except Exception:
            pass


sys.excepthook = exception_hook


class ImportWorker(QThread):
    progress = pyqtSignal(int, str)
    finished_import = pyqtSignal()

    def run(self):
        self.progress.emit(10, "Загрузка базовых компонентов...")
        time.sleep(0.1)
        
        self.progress.emit(30, "Загрузка модулей DICOM...")
        import pydicom
        
        self.progress.emit(50, "Загрузка сетевых компонентов PACS...")
        import pynetdicom
        
        self.progress.emit(70, "Загрузка модулей обработки изображений...")
        import numpy
        
        self.progress.emit(90, "Инициализация интерфейса...")
        import ui.main_window
        
        self.progress.emit(100, "Запуск...")
        time.sleep(0.1)
        self.finished_import.emit()


class LoadingSplash(QSplashScreen):
    def __init__(self):
        from PyQt6.QtGui import QPixmap, QColor
        pixmap = QPixmap(500, 200)
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
    global MainWindow, _worker
    app = QApplication(sys.argv)
    
    splash = LoadingSplash()
    splash.show()
    
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
