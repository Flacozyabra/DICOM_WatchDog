from PyQt6.QtWidgets import QDateEdit, QApplication
from PyQt6.QtCore import Qt, QSize, QEvent, QPointF
from PyQt6.QtGui import QMouseEvent


class CenteredDateEdit(QDateEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCalendarPopup(True)
        self.setFixedWidth(110)
        self.setMaximumWidth(110)
        self.lineEdit().setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.setStyleSheet(
            "QDateEdit {"
            "  background-color: #0f0f0f; color: #ffffff;"
            "  border: 1px solid #3d3d3d; border-radius: 6px;"
            "  padding: 4px 4px 4px 16px;"
            "  font-family: 'Segoe UI'; font-size: 13px;"
            "}"
            "QDateEdit:disabled {"
            "  background-color: #121212; color: #666666; border: 1px solid #2d2d2d;"
            "}"
            "QDateEdit::drop-down {"
            "  subcontrol-origin: padding;"
            "  subcontrol-position: center right;"
            "  border: none;"
            "  width: 12px;"
            "  background: transparent;"
            "}"
            "QDateEdit::down-arrow { image: none; border: none; }"
        )
        
        self.lineEdit().installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj == self.lineEdit() and event.type() == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
                click_point = QPointF(self.width() - 6.0, self.height() / 2.0)
                click_event = QMouseEvent(
                    QEvent.Type.MouseButtonPress,
                    click_point,
                    Qt.MouseButton.LeftButton,
                    Qt.MouseButton.LeftButton,
                    Qt.KeyboardModifier.NoModifier
                )
                QApplication.sendEvent(self, click_event)
                
                release_event = QMouseEvent(
                    QEvent.Type.MouseButtonRelease,
                    click_point,
                    Qt.MouseButton.LeftButton,
                    Qt.MouseButton.LeftButton,
                    Qt.KeyboardModifier.NoModifier
                )
                QApplication.sendEvent(self, release_event)
                return True
        return super().eventFilter(obj, event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            if event.position().x() > self.width() - 17:
                super().mousePressEvent(event)
            else:
                click_point = QPointF(self.width() - 6.0, self.height() / 2.0)
                forward_event = QMouseEvent(
                    event.type(),
                    click_point,
                    event.button(),
                    event.buttons(),
                    event.modifiers()
                )
                super().mousePressEvent(forward_event)
        else:
            super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.isEnabled():
            if event.position().x() > self.width() - 17:
                super().mouseReleaseEvent(event)
            else:
                click_point = QPointF(self.width() - 6.0, self.height() / 2.0)
                forward_event = QMouseEvent(
                    event.type(),
                    click_point,
                    event.button(),
                    event.buttons(),
                    event.modifiers()
                )
                super().mouseReleaseEvent(forward_event)
        else:
            super().mouseReleaseEvent(event)

    def minimumSizeHint(self):
        return QSize(110, 30)

    def sizeHint(self):
        return QSize(110, 30)
