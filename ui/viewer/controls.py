from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal, QPoint, QRect
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel,
    QPushButton, QDialog, QProgressBar
)
from PyQt6.QtGui import (
    QBrush, QColor, QPainter, QPen, QLinearGradient, QPolygon
)
from core.locale_utils import tr_ui


class DRRProgressDialog(QDialog):
    """Модальное окно прогресса генерации DRR с кнопкой отмены."""
    cancelled = pyqtSignal()

    def __init__(self, parent: QWidget = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setModal(True)
        self.setFixedSize(380, 160)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        card = QFrame(self)
        card.setStyleSheet("""
            QFrame {
                background-color: #0F172A;
                border: 1.5px solid #3B82F6;
                border-radius: 8px;
            }
        """)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 16, 20, 16)
        card_layout.setSpacing(10)

        self.lbl_title = QLabel(tr_ui("drr_title"), card)
        self.lbl_title.setStyleSheet("color: #FFFFFF; font-size: 13px; font-weight: bold; border: none;")
        self.lbl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(self.lbl_title)

        self.lbl_status = QLabel(tr_ui("drr_preparing_volume"), card)
        self.lbl_status.setStyleSheet("color: #94A3B8; font-size: 11px; border: none;")
        self.lbl_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_layout.addWidget(self.lbl_status)

        self.progress_bar = QProgressBar(card)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                background-color: #1E293B;
                color: #FFFFFF;
                border: 1px solid #334155;
                border-radius: 4px;
                font-size: 10px;
                font-weight: bold;
                text-align: center;
                height: 16px;
            }
            QProgressBar::chunk {
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #3B82F6, stop:1 #60A5FA);
                border-radius: 3px;
            }
        """)
        card_layout.addWidget(self.progress_bar)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.btn_cancel = QPushButton(tr_ui("drr_cancel"), card)
        self.btn_cancel.setFixedSize(90, 28)
        self.btn_cancel.setStyleSheet("""
            QPushButton {
                background-color: #334155;
                color: #F8FAFC;
                border: 1px solid #475569;
                border-radius: 4px;
                font-size: 11px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #EF4444;
                border-color: #DC2626;
                color: #FFFFFF;
            }
        """)
        self.btn_cancel.clicked.connect(self.on_cancel)
        btn_layout.addWidget(self.btn_cancel)
        btn_layout.addStretch()

        card_layout.addLayout(btn_layout)
        layout.addWidget(card)

    def on_cancel(self) -> None:
        self.cancelled.emit()
        self.reject()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.on_cancel()
        else:
            super().keyPressEvent(event)

    def set_progress(self, current: int, total: int, g_angle: float) -> None:
        if total > 0:
            self.progress_bar.setMaximum(total)
            self.progress_bar.setValue(current)
            self.lbl_status.setText(tr_ui("drr_progress", current, total, g_angle))


class HUVerticalSlider(QWidget):
    """Кастомный вертикальный слайдер с двумя ползунками для Window/Level (HU) в стиле Varian Eclipse."""
    values_changed = pyqtSignal(float, float)  # lower_val, upper_val

    def __init__(self, parent: QWidget = None) -> None:
        super().__init__(parent)
        self.min_hu = -1000.0
        self.max_hu = 3000.0
        self.lower_val = -160.0
        self.upper_val = 240.0
        
        self.pad = 12
        self.bar_width = 10
        self.slider_size = 14
        
        self.dragging = None  # None, 'lower', 'upper', 'both'
        self.drag_start_y = 0
        self.drag_start_lower = 0.0
        self.drag_start_upper = 0.0

        self.setMinimumWidth(60)
        self.setMouseTracking(True)

    def set_values(self, lower: float, upper: float) -> None:
        lower = max(self.min_hu, min(self.max_hu, lower))
        upper = max(self.min_hu, min(self.max_hu, upper))
        if lower > upper:
            lower, upper = upper, lower
        if upper - lower < 1.0:
            upper = lower + 1.0
        self.lower_val = lower
        self.upper_val = upper
        self.update()

    def _hu_to_y(self, hu: float) -> int:
        h = self.height()
        active_h = h - 2 * self.pad
        if active_h <= 0:
            return self.pad
        val_pct = (hu - self.min_hu) / (self.max_hu - self.min_hu)
        return int(self.pad + active_h * (1.0 - val_pct))

    def _y_to_hu(self, y: int) -> float:
        h = self.height()
        active_h = h - 2 * self.pad
        if active_h <= 0:
            return self.min_hu
        val_pct = 1.0 - (y - self.pad) / active_h
        val_pct = max(0.0, min(1.0, val_pct))
        return self.min_hu + val_pct * (self.max_hu - self.min_hu)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        h = self.height()
        w = self.width()
        cx = w // 2 - 12

        # 1. Рисуем фон шкалы
        bar_rect = QRect(cx - self.bar_width // 2, self.pad, self.bar_width, h - 2 * self.pad)
        painter.setPen(QPen(QColor("#374151"), 1))
        painter.setBrush(QBrush(QColor("#111827")))
        painter.drawRect(bar_rect)

        # 2. Рисуем градиент внутри шкалы (от lower_val до upper_val)
        y_lower = self._hu_to_y(self.lower_val)
        y_upper = self._hu_to_y(self.upper_val)
        
        # Заливка ниже нижнего порога (черный цвет)
        if y_lower < h - self.pad:
            rect_below = QRect(bar_rect.x(), y_lower, bar_rect.width(), h - self.pad - y_lower)
            painter.fillRect(rect_below, QColor("#000000"))

        # Заливка выше верхнего порога (белый цвет)
        if y_upper > self.pad:
            rect_above = QRect(bar_rect.x(), self.pad, bar_rect.width(), y_upper - self.pad)
            painter.fillRect(rect_above, QColor("#FFFFFF"))

        # Градиент между порогами (от черного снизу до белого сверху)
        if y_upper < y_lower:
            grad = QLinearGradient(cx, y_lower, cx, y_upper)
            grad.setColorAt(0.0, QColor("#000000"))
            grad.setColorAt(1.0, QColor("#FFFFFF"))
            rect_grad = QRect(bar_rect.x(), y_upper, bar_rect.width(), y_lower - y_upper)
            painter.fillRect(rect_grad, grad)

        # 3. Рисуем деления (риски)
        painter.setPen(QPen(QColor("#4B5563"), 1))
        font = painter.font()
        font.setPointSize(8)
        painter.setFont(font)
        
        for hu in range(int(self.min_hu), int(self.max_hu) + 1, 500):
            y_tick = self._hu_to_y(hu)
            painter.drawLine(cx - self.bar_width // 2 - 2, y_tick, cx - self.bar_width // 2, y_tick)
            
            # Подписи (каждые 1000 HU)
            if hu % 1000 == 0:
                painter.setPen(QPen(QColor("#9CA3AF"), 1))
                painter.drawText(cx + self.bar_width // 2 + 5, y_tick + 3, str(hu))
                painter.setPen(QPen(QColor("#4B5563"), 1))

        # 4. Рисуем ползунки
        y_u = self._hu_to_y(self.upper_val)
        y_l = self._hu_to_y(self.lower_val)

        # Рисуем ползунок Upper
        up_poly = [
            QPoint(cx - self.bar_width // 2 - 12, y_u - 5),
            QPoint(cx - self.bar_width // 2 - 2, y_u),
            QPoint(cx - self.bar_width // 2 - 12, y_u + 5)
        ]
        painter.setPen(QPen(QColor("#60A5FA") if self.dragging == "upper" else QColor("#D1D5DB"), 1.5))
        painter.setBrush(QBrush(QColor("#3B82F6") if self.dragging == "upper" else QColor("#4B5563")))
        painter.drawPolygon(QPolygon(up_poly))

        # Рисуем ползунок Lower
        low_poly = [
            QPoint(cx - self.bar_width // 2 - 12, y_l - 5),
            QPoint(cx - self.bar_width // 2 - 2, y_l),
            QPoint(cx - self.bar_width // 2 - 12, y_l + 5)
        ]
        painter.setPen(QPen(QColor("#60A5FA") if self.dragging == "lower" else QColor("#D1D5DB"), 1.5))
        painter.setBrush(QBrush(QColor("#3B82F6") if self.dragging == "lower" else QColor("#4B5563")))
        painter.drawPolygon(QPolygon(low_poly))

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            y = event.position().y()
            w = self.width()
            cx = w // 2 - 12
            
            y_u = self._hu_to_y(self.upper_val)
            y_l = self._hu_to_y(self.lower_val)
            
            click_x = event.position().x()
            in_slider_x = (cx - self.bar_width // 2 - 15 <= click_x <= cx - self.bar_width // 2)

            if in_slider_x and abs(y - y_u) < 8:
                self.dragging = 'upper'
            elif in_slider_x and abs(y - y_l) < 8:
                self.dragging = 'lower'
            elif click_x >= cx - self.bar_width // 2 - 4 and click_x <= cx + self.bar_width // 2 + 4 and y_u <= y <= y_l:
                self.dragging = 'both'
                self.drag_start_y = y
                self.drag_start_lower = self.lower_val
                self.drag_start_upper = self.upper_val
            else:
                new_hu = self._y_to_hu(y)
                if abs(new_hu - self.upper_val) < abs(new_hu - self.lower_val):
                    self.dragging = 'upper'
                    self.upper_val = max(self.lower_val + 10.0, new_hu)
                else:
                    self.dragging = 'lower'
                    self.lower_val = min(self.upper_val - 10.0, new_hu)
                self.values_changed.emit(self.lower_val, self.upper_val)
            self.update()

    def mouseMoveEvent(self, event) -> None:
        y = event.position().y()
        if self.dragging == 'upper':
            new_hu = self._y_to_hu(y)
            self.upper_val = max(self.lower_val + 10.0, min(self.max_hu, new_hu))
            self.values_changed.emit(self.lower_val, self.upper_val)
            self.update()
        elif self.dragging == 'lower':
            new_hu = self._y_to_hu(y)
            self.lower_val = min(self.upper_val - 10.0, max(self.min_hu, new_hu))
            self.values_changed.emit(self.lower_val, self.upper_val)
            self.update()
        elif self.dragging == 'both':
            hu_start = self._y_to_hu(self.drag_start_y)
            hu_current = self._y_to_hu(y)
            delta_hu = hu_current - hu_start
            
            new_lower = self.drag_start_lower + delta_hu
            new_upper = self.drag_start_upper + delta_hu
            
            if new_lower < self.min_hu:
                diff = self.min_hu - new_lower
                new_lower += diff
                new_upper += diff
            elif new_upper > self.max_hu:
                diff = new_upper - self.max_hu
                new_lower -= diff
                new_upper -= diff
                
            self.lower_val = max(self.min_hu, min(self.max_hu, new_lower))
            self.upper_val = max(self.min_hu, min(self.max_hu, new_upper))
            self.values_changed.emit(self.lower_val, self.upper_val)
            self.update()
        else:
            w = self.width()
            cx = w // 2 - 12
            y_u = self._hu_to_y(self.upper_val)
            y_l = self._hu_to_y(self.lower_val)
            click_x = event.position().x()
            in_slider_x = (cx - self.bar_width // 2 - 15 <= click_x <= cx - self.bar_width // 2)
            
            if in_slider_x and (abs(y - y_u) < 8 or abs(y - y_l) < 8):
                self.setCursor(Qt.CursorShape.SplitVCursor)
            elif click_x >= cx - self.bar_width // 2 and click_x <= cx + self.bar_width // 2 and y_u <= y <= y_l:
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, event) -> None:
        self.dragging = None
        self.update()
