from PyQt6.QtCore import Qt, QRect, QPoint, QPropertyAnimation, pyqtProperty
from PyQt6.QtGui import QColor, QPainter, QBrush, QPen, QFontMetrics
from PyQt6.QtWidgets import QCheckBox


class ToggleSwitch(QCheckBox):
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._bg_color = QColor("#555555")
        self._active_color = QColor("#1f538d")
        self._knob_color = QColor("#ffffff")
        self._knob_position = 18 if self.isChecked() else 2
        self.setFixedHeight(22)

    def sizeHint(self):
        fm = QFontMetrics(self.font())
        text_width = fm.horizontalAdvance(self.text()) if self.text() else 0
        return QRect(0, 0, 38 + text_width, 22).size()

    @pyqtProperty(int)
    def knob_position(self):
        return self._knob_position

    @knob_position.setter
    def knob_position(self, pos):
        self._knob_position = pos
        self.update()

    def hitButton(self, pos: QPoint) -> bool:
        return self.rect().contains(pos)

    def nextCheckState(self):
        super().nextCheckState()
        end_value = 18 if self.isChecked() else 2
        self.anim = QPropertyAnimation(self, b"knob_position")
        self.anim.setDuration(120)
        self.anim.setEndValue(end_value)
        self.anim.start()

    def setChecked(self, state):
        super().setChecked(state)
        self._knob_position = 18 if state else 2
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Рисуем дорожку слайдера
        track_rect = QRect(0, 3, 32, 16)
        if self.isChecked():
            p.setBrush(QBrush(self._active_color))
        else:
            p.setBrush(QBrush(self._bg_color))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(track_rect, 8, 8)
        
        # Рисуем бегунок (круг)
        p.setBrush(QBrush(self._knob_color))
        p.drawEllipse(self._knob_position, 5, 12, 12)
        
        # Рисуем текст метки
        if self.text():
            p.setPen(QPen(QColor("#ffffff")))
            font_metrics = p.fontMetrics()
            y_offset = (self.height() - font_metrics.height()) // 2 + font_metrics.ascent()
            p.drawText(38, y_offset, self.text())
            
        p.end()
