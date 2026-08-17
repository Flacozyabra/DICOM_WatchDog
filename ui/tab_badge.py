from PyQt6.QtCore import Qt, QRectF, QSize, QPointF
from PyQt6.QtGui import QPainter, QPen, QBrush, QColor, QFont, QPainterPath
from PyQt6.QtWidgets import QWidget, QTabBar


class TabBadge(QWidget):
    """
    A circular / capsule badge widget displaying study count in tab titles.
    """
    LEFT_MARGIN = 6
    RIGHT_MARGIN = 6
    BADGE_HEIGHT = 16
    WIDGET_HEIGHT = 18

    def __init__(self, tab_bar: QTabBar = None, tab_index: int = 0, parent=None):
        if isinstance(tab_bar, int):
            tab_index, tab_bar = tab_bar, tab_index
        super().__init__(parent or tab_bar)
        self.tab_index = tab_index
        self.tab_bar = tab_bar
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._count = 0
        self._text = "0"
        self._font = QFont("Segoe UI", 9, QFont.Weight.Bold)
        self._font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        self.setFixedHeight(self.WIDGET_HEIGHT)
        self.update_geometry()

    def set_count(self, count: int, force_update: bool = False):
        self._count = max(0, int(count)) if count is not None else 0
        new_text = str(self._count)
        if force_update or new_text != self._text:
            self._text = new_text
            self.update_geometry()
            if self.tab_bar and 0 <= self.tab_index < self.tab_bar.count():
                try:
                    self.tab_bar.setTabText(self.tab_index, self.tab_bar.tabText(self.tab_index))
                    self.tab_bar.updateGeometry()
                except Exception:
                    pass
        self.update()

    def count(self) -> int:
        return self._count

    def sizeHint(self):
        path = QPainterPath()
        path.addText(QPointF(0, 0), self._font, self._text)
        ink_w = path.boundingRect().width()
        pill_w = self.BADGE_HEIGHT if len(self._text) <= 1 else int(ink_w + 10)
        total_w = self.LEFT_MARGIN + pill_w + self.RIGHT_MARGIN
        return QSize(total_w, self.WIDGET_HEIGHT)

    def minimumSizeHint(self):
        return self.sizeHint()

    def update_geometry(self):
        path = QPainterPath()
        path.addText(QPointF(0, 0), self._font, self._text)
        ink_w = path.boundingRect().width()
        # Single digit circle (width == BADGE_HEIGHT = 16), multi-digit capsule
        pill_w = self.BADGE_HEIGHT if len(self._text) <= 1 else int(ink_w + 10)
        total_w = self.LEFT_MARGIN + pill_w + self.RIGHT_MARGIN
        self.setFixedSize(total_w, self.WIDGET_HEIGHT)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        is_selected = False
        if self.tab_bar and 0 <= self.tab_index < self.tab_bar.count():
            is_selected = (self.tab_bar.currentIndex() == self.tab_index)

        pill_w = max(self.BADGE_HEIGHT, self.width() - self.LEFT_MARGIN - self.RIGHT_MARGIN)
        badge_y = (self.height() - self.BADGE_HEIGHT) / 2.0

        draw_rect = QRectF(self.LEFT_MARGIN + 0.5, badge_y + 0.5, pill_w - 1.0, self.BADGE_HEIGHT - 1.0)
        radius = (self.BADGE_HEIGHT - 1.0) / 2.0

        if is_selected:
            # Active tab (background #1f538d): dark graphite pill with bright blue border
            bg_color = QColor("#181818")
            border_color = QColor("#569cd6")
            text_color = QColor("#ffffff")
        else:
            # Inactive tab (background #464646): button color #1f538d with subtle border
            bg_color = QColor("#1f538d")
            border_color = QColor("#3370b3")
            text_color = QColor("#ffffff")

        painter.setPen(QPen(border_color, 1))
        painter.setBrush(QBrush(bg_color))
        painter.drawRoundedRect(draw_rect, radius, radius)

        # Математически точное центрирование векторного контура видимых пикселей (QPainterPath)
        path = QPainterPath()
        path.addText(QPointF(0, 0), self._font, self._text)
        br = path.boundingRect()

        center_x = draw_rect.center().x()
        center_y = draw_rect.center().y()

        path.translate(center_x - br.center().x(), center_y - br.center().y())
        painter.fillPath(path, QBrush(text_color))
