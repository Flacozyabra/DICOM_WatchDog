import sys
import os
import time
import random
import math

from PyQt6.QtCore import Qt, QTimer, QRect, QPoint, QThread, pyqtSignal
from PyQt6.QtGui import QPixmap, QColor, QPainter, QPen, QBrush
from PyQt6.QtWidgets import QApplication, QSplashScreen, QProgressBar, QHBoxLayout, QVBoxLayout, QLabel

MainWindow = None
_worker = None


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
        pixmap = QPixmap(500, 220)
        pixmap.fill(QColor("#202020"))
        super().__init__(pixmap)
        
        # Загрузка логотипа
        icon_path = "src/icon.png"
        self.logo_pixmap = QPixmap(icon_path)
        if not self.logo_pixmap.isNull():
            self.logo_pixmap = self.logo_pixmap.scaled(
                110, 110, 
                Qt.AspectRatioMode.KeepAspectRatio, 
                Qt.TransformationMode.SmoothTransformation
            )
            
        # Параметры анимации зрачка
        self.pupil_x = 0.0
        self.pupil_y = 0.0
        self.target_x = 0.0
        self.target_y = 0.0
        self.next_move_time = 0.0
        
        # Таймер анимации (~60 FPS)
        self.animation_timer = QTimer(self)
        self.animation_timer.timeout.connect(self.update_animation)
        self.animation_timer.start(16)
        
        # Горизонтальная разметка
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(25, 25, 25, 25)
        main_layout.setSpacing(20)
        
        # Место под логотип слева (ширина 110 + отступы)
        main_layout.addSpacing(120)
        
        # Правая колонка с информацией
        right_layout = QVBoxLayout()
        right_layout.setSpacing(12)
        right_layout.addStretch(1)
        
        self.title_label = QLabel("DICOM WatchDog")
        self.title_label.setStyleSheet("color: #ffffff; font-size: 22px; font-weight: bold; font-family: 'Segoe UI';")
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        right_layout.addWidget(self.title_label)
        
        self.status_label = QLabel("Инициализация...")
        self.status_label.setStyleSheet("color: #aaaaaa; font-size: 13px; font-family: 'Segoe UI';")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        right_layout.addWidget(self.status_label)
        
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
        right_layout.addWidget(self.progress_bar)
        right_layout.addStretch(1)
        
        main_layout.addLayout(right_layout)
        self.setWindowFlags(Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.FramelessWindowHint)

    def update_animation(self):
        current_time = time.time()
        if current_time >= self.next_move_time:
            # Выбираем новую точку для взгляда
            # 25% шанс посмотреть по центру
            if random.random() < 0.25:
                self.target_x = 0.0
                self.target_y = 0.0
            else:
                # Случайный угол и амплитуда движения зрачка (макс 6 пикселей)
                angle = random.uniform(0, 2 * math.pi)
                r = random.uniform(2.0, 6.0)
                self.target_x = r * math.cos(angle)
                self.target_y = r * math.sin(angle)
            
            # Задержка до следующего изменения взгляда (1.0 - 2.5 сек)
            self.next_move_time = current_time + random.uniform(1.0, 2.5)
            
        # Плавное перемещение зрачка к целевой точке (lerp)
        self.pupil_x += (self.target_x - self.pupil_x) * 0.12
        self.pupil_y += (self.target_y - self.pupil_y) * 0.12
        
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Рисуем фон заставки
        painter.fillRect(self.rect(), QColor("#202020"))
        
        # Рисуем логотип и анимацию зрачка
        logo_w, logo_h = 110, 110
        logo_x = 25
        logo_y = (self.height() - logo_h) // 2
        logo_rect = QRect(logo_x, logo_y, logo_w, logo_h)
        
        if not self.logo_pixmap.isNull():
            # 1. Отрисовка оригинального логотипа
            painter.drawPixmap(logo_rect, self.logo_pixmap)
            
            # 2. Закрашиваем оригинальный зрачок белым кругом (центр глаза на логотипе: 55, 55)
            cx = logo_rect.left() + 55
            cy = logo_rect.top() + 55
            r_eye = 18
            
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor("#ffffff")))
            painter.drawEllipse(QPoint(cx, cy), r_eye, r_eye)
            
            # 3. Рисуем анимированный зрачок темно-серого цвета
            r_pupil = 8
            px = cx + int(self.pupil_x)
            py = cy + int(self.pupil_y)
            
            # Цвет зрачка, идеально совпадающий с цветом квадрата
            painter.setBrush(QBrush(QColor("#2b303c")))
            painter.drawEllipse(QPoint(px, py), r_pupil, r_pupil)
            
            # 4. Рисуем блик
            r_glare = 2
            gx = px + 2
            gy = py - 2
            painter.setBrush(QBrush(QColor("#ffffff")))
            painter.drawEllipse(QPoint(gx, gy), r_glare, r_glare)
            
        # Внешняя тонкая граница
        painter.setPen(QPen(QColor("#3d3d3d"), 1))
        painter.drawRect(0, 0, self.width() - 1, self.height() - 1)

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
