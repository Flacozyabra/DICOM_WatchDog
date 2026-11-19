from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QPointF, QRectF, QSize, QVariantAnimation, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QTabBar, QWidget


_RENDER_AA = QPainter.RenderHint.Antialiasing if hasattr(QPainter, "RenderHint") else QPainter.Antialiasing
_WA_TRANSPARENT = (
    Qt.WidgetAttribute.WA_TransparentForMouseEvents
    if hasattr(Qt, "WidgetAttribute")
    else Qt.WA_TransparentForMouseEvents
)
_FONT_DEMIBOLD = QFont.Weight.DemiBold if hasattr(QFont, "Weight") else 63
_FONT_ANTIALIAS = (
    QFont.StyleStrategy.PreferAntialias
    if hasattr(QFont, "StyleStrategy")
    else getattr(QFont, "PreferAntialias", 0x0080)
)
_EASE_OUT = QEasingCurve.Type.OutCubic if hasattr(QEasingCurve, "Type") else QEasingCurve.OutCubic


def _lerp_color(c0: QColor, c1: QColor, t: float) -> QColor:
    u = 0.0 if t < 0.0 else (1.0 if t > 1.0 else float(t))
    return QColor(
        int(round(c0.red() + (c1.red() - c0.red()) * u)),
        int(round(c0.green() + (c1.green() - c0.green()) * u)),
        int(round(c0.blue() + (c1.blue() - c0.blue()) * u)),
        int(round(c0.alpha() + (c1.alpha() - c0.alpha()) * u)),
    )


class TabBadge(QWidget):
    """Compact count pill displayed inside QTabBar tabs with smooth active/count transitions."""

    LEFT_MARGIN = 6
    RIGHT_MARGIN = 10
    BADGE_HEIGHT = 16
    WIDGET_HEIGHT = 18

    def __init__(self, tab_bar: QTabBar = None, tab_index: int = 0, parent=None) -> None:
        if isinstance(tab_bar, int):
            tab_index, tab_bar = tab_bar, tab_index
        super().__init__(parent or tab_bar)
        self.tab_index = tab_index
        self.tab_bar = tab_bar
        self.setAttribute(_WA_TRANSPARENT, True)
        self._count = 0
        self._text = "0"
        self._initialized_count = False
        self._pulse = 0.0

        self._font = QFont("Segoe UI")
        self._font.setPixelSize(10)
        self._font.setWeight(_FONT_DEMIBOLD)
        self._font.setStyleStrategy(_FONT_ANTIALIAS)

        self._pulse_anim = QVariantAnimation(self)
        self._pulse_anim.setDuration(180)
        self._pulse_anim.setEasingCurve(_EASE_OUT)
        self._pulse_anim.setStartValue(1.0)
        self._pulse_anim.setEndValue(0.0)
        self._pulse_anim.valueChanged.connect(self._on_pulse_step)

        self.setFixedHeight(self.WIDGET_HEIGHT)
        self.update_geometry()

    def _on_pulse_step(self, value) -> None:
        self._pulse = float(value)
        self.update()

    def set_count(self, count: int, force_update: bool = False) -> None:
        new_count = max(0, int(count)) if count is not None else 0
        changed = new_count != self._count
        self._count = new_count
        new_text = str(self._count)
        if force_update or new_text != self._text:
            old_w = self.width()
            self._text = new_text
            self.update_geometry()
            new_w = self.width()
            if (old_w != new_w or force_update) and self.tab_bar and 0 <= self.tab_index < self.tab_bar.count():
                try:
                    if hasattr(self.tab_bar, "refresh_tab_sizes"):
                        self.tab_bar.refresh_tab_sizes()
                    else:
                        self.tab_bar.updateGeometry()
                except Exception:
                    pass
        if changed and self._initialized_count and self.isVisible():
            self._pulse_anim.stop()
            self._pulse_anim.start()
        self._initialized_count = True
        self.update()

    def count(self) -> int:
        return self._count

    def _pill_width(self) -> int:
        path = QPainterPath()
        path.addText(QPointF(0, 0), self._font, self._text)
        ink_w = path.boundingRect().width()
        return self.BADGE_HEIGHT if len(self._text) <= 1 else int(ink_w + 10)

    def sizeHint(self) -> QSize:
        total_w = self.LEFT_MARGIN + self._pill_width() + self.RIGHT_MARGIN
        return QSize(total_w, self.WIDGET_HEIGHT)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def update_geometry(self) -> None:
        total_w = self.LEFT_MARGIN + self._pill_width() + self.RIGHT_MARGIN
        self.setFixedSize(total_w, self.WIDGET_HEIGHT)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.tab_bar and hasattr(self.tab_bar, "refresh_tab_sizes"):
            self.tab_bar.refresh_tab_sizes()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(_RENDER_AA, True)

        if self.tab_bar and hasattr(self.tab_bar, "active_ratio_for_index"):
            active_t = float(self.tab_bar.active_ratio_for_index(self.tab_index))
        elif self.tab_bar and 0 <= self.tab_index < self.tab_bar.count():
            active_t = 1.0 if self.tab_bar.currentIndex() == self.tab_index else 0.0
        else:
            active_t = 0.0

        pill_w = max(self.BADGE_HEIGHT, self.width() - self.LEFT_MARGIN - self.RIGHT_MARGIN)
        badge_y = (self.height() - self.BADGE_HEIGHT) * 0.5
        draw_rect = QRectF(self.LEFT_MARGIN + 0.5, badge_y + 0.5, pill_w - 1.0, self.BADGE_HEIGHT - 1.0)
        radius = (self.BADGE_HEIGHT - 1.0) * 0.5

        if self._count > 0:
            bg_inactive = QColor("#1b3552")
            border_inactive = QColor("#2c5482")
            text_inactive = QColor("#cde2f8")
        else:
            bg_inactive = QColor("#232731")
            border_inactive = QColor("#303643")
            text_inactive = QColor("#7d8590")

        bg_active = QColor(11, 20, 34, 155)
        border_active = QColor(147, 197, 253, 95)
        text_active = QColor("#ffffff")

        bg_color = _lerp_color(bg_inactive, bg_active, active_t)
        border_color = _lerp_color(border_inactive, border_active, active_t)
        text_color = _lerp_color(text_inactive, text_active, active_t)

        if self._pulse > 0.01:
            border_color = _lerp_color(border_color, QColor("#7dd3fc"), 0.7 * self._pulse)

        painter.setPen(QPen(border_color, 1.0))
        painter.setBrush(QBrush(bg_color))
        painter.drawRoundedRect(draw_rect, radius, radius)

        path = QPainterPath()
        path.addText(QPointF(0, 0), self._font, self._text)
        br = path.boundingRect()
        path.translate(draw_rect.center().x() - br.center().x(), draw_rect.center().y() - br.center().y())
        painter.fillPath(path, QBrush(text_color))
