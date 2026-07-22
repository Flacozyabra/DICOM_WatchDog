import sys
import os
from typing import List, Optional

try:
    from PyQt6.QtCore import (
        Qt, QTimer, QPropertyAnimation, QEasingCurve, QCoreApplication, QThread
    )
    from PyQt6.QtWidgets import (
        QWidget, QLabel, QHBoxLayout, QVBoxLayout, QPushButton,
        QFrame, QApplication, QGraphicsDropShadowEffect
    )
    from PyQt6.QtGui import QPixmap, QColor, QFont, QMouseEvent, QCursor
except ImportError:
    from PyQt5.QtCore import (  # type: ignore
        Qt, QTimer, QPropertyAnimation, QEasingCurve, QCoreApplication, QThread
    )
    from PyQt5.QtWidgets import (  # type: ignore
        QWidget, QLabel, QHBoxLayout, QVBoxLayout, QPushButton,
        QFrame, QApplication, QGraphicsDropShadowEffect
    )
    from PyQt5.QtGui import QPixmap, QColor, QFont, QMouseEvent, QCursor  # type: ignore


_active_toasts: List['ToastNotification'] = []


class ToastNotification(QWidget):
    """Custom frameless toast notification widget rendered natively in Qt."""

    TOAST_WIDTH = 380
    TOAST_HEIGHT = 84
    MARGIN = 16
    SPACING = 10

    def __init__(
        self,
        title: str,
        message: str,
        icon_path: str = "",
        duration_ms: int = 5000,
        position: str = "bottom_right",
        parent: Optional[QWidget] = None
    ):
        super().__init__(parent)
        self.duration_ms = duration_ms
        self.position = position
        self._is_closing = False

        # Set window flags for frameless floating popup
        flags = (
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool |
            Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        self.setFixedSize(self.TOAST_WIDTH, self.TOAST_HEIGHT)

        # Main Container
        container = QFrame(self)
        container.setGeometry(0, 0, self.TOAST_WIDTH - 8, self.TOAST_HEIGHT - 8)
        container.setStyleSheet("""
            QFrame {
                background-color: #202020;
                border: 1px solid #3d3d3d;
                border-radius: 8px;
            }
        """)

        # Drop Shadow Effect
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(16)
        shadow.setColor(QColor(0, 0, 0, 180))
        shadow.setOffset(0, 4)
        container.setGraphicsEffect(shadow)

        # Content Layout
        layout = QHBoxLayout(container)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(12)

        # Icon Label
        self.icon_label = QLabel(container)
        self.icon_label.setFixedSize(48, 48)
        self.icon_label.setStyleSheet("background: transparent; border: none;")
        clean_icon_path = icon_path.replace("file:///", "") if icon_path else ""
        if clean_icon_path and os.path.exists(clean_icon_path):
            pixmap = QPixmap(clean_icon_path)
            if not pixmap.isNull():
                self.icon_label.setPixmap(
                    pixmap.scaled(48, 48, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                )
        layout.addWidget(self.icon_label)

        # Text Layout (Title & Message)
        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)
        text_layout.setContentsMargins(0, 0, 0, 0)

        self.title_label = QLabel(title, container)
        title_font = QFont("Segoe UI", 11, QFont.Weight.Bold)
        self.title_label.setFont(title_font)
        self.title_label.setStyleSheet("color: #ffffff; background: transparent; border: none;")
        self.title_label.setTextFormat(Qt.TextFormat.PlainText)

        self.msg_label = QLabel(message, container)
        msg_font = QFont("Segoe UI", 9, QFont.Weight.Normal)
        self.msg_label.setFont(msg_font)
        self.msg_label.setStyleSheet("color: #cccccc; background: transparent; border: none;")
        self.msg_label.setTextFormat(Qt.TextFormat.PlainText)

        text_layout.addStretch()
        text_layout.addWidget(self.title_label)
        text_layout.addWidget(self.msg_label)
        text_layout.addStretch()

        layout.addLayout(text_layout, stretch=1)

        # Close Button
        close_btn = QPushButton("✕", container)
        close_btn.setFixedSize(22, 22)
        close_btn.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        close_btn.setStyleSheet("""
            QPushButton {
                color: #888888;
                background: transparent;
                border: none;
                font-size: 13px;
                font-weight: bold;
                border-radius: 11px;
            }
            QPushButton:hover {
                color: #ffffff;
                background-color: #383838;
            }
        """)
        close_btn.clicked.connect(self.close_toast)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignTop)

        # Auto-close timer
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.close_toast)

        # Opacity Animation
        self.setWindowOpacity(0.0)
        self.fade_anim = QPropertyAnimation(self, b"windowOpacity")
        self.fade_anim.setDuration(250)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self.close_toast()
        super().mousePressEvent(event)

    def show_toast(self):
        global _active_toasts
        _active_toasts.append(self)
        self._reposition_all()

        self.show()

        # Fade in animation
        self.fade_anim.stop()
        self.fade_anim.setStartValue(0.0)
        self.fade_anim.setEndValue(0.95)
        self.fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.fade_anim.start()

        if self.duration_ms > 0:
            self.timer.start(self.duration_ms)

    def close_toast(self):
        if self._is_closing:
            return
        self._is_closing = True
        self.timer.stop()

        global _active_toasts
        if self in _active_toasts:
            _active_toasts.remove(self)

        self._reposition_all()

        # Fade out animation
        self.fade_anim.stop()
        self.fade_anim.setStartValue(self.windowOpacity())
        self.fade_anim.setEndValue(0.0)
        self.fade_anim.setEasingCurve(QEasingCurve.Type.InCubic)
        self.fade_anim.finished.connect(self.close)
        self.fade_anim.start()

    @classmethod
    def _reposition_all(cls):
        app = QApplication.instance()
        if not app:
            return

        screen = app.primaryScreen()
        if not screen:
            return

        geom = screen.availableGeometry()

        for idx, toast in enumerate(reversed(_active_toasts)):
            pos = getattr(toast, 'position', 'bottom_right')
            if pos == 'bottom_left':
                target_x = geom.left() + cls.MARGIN
                target_y = geom.bottom() - cls.TOAST_HEIGHT - cls.MARGIN - idx * (cls.TOAST_HEIGHT + cls.SPACING)
            elif pos == 'top_right':
                target_x = geom.right() - cls.TOAST_WIDTH - cls.MARGIN
                target_y = geom.top() + cls.MARGIN + idx * (cls.TOAST_HEIGHT + cls.SPACING)
            elif pos == 'top_left':
                target_x = geom.left() + cls.MARGIN
                target_y = geom.top() + cls.MARGIN + idx * (cls.TOAST_HEIGHT + cls.SPACING)
            else:  # bottom_right
                target_x = geom.right() - cls.TOAST_WIDTH - cls.MARGIN
                target_y = geom.bottom() - cls.TOAST_HEIGHT - cls.MARGIN - idx * (cls.TOAST_HEIGHT + cls.SPACING)
            toast.move(target_x, target_y)


def show_qt_toast(
    title: str,
    msg: str,
    durations: str,
    ico_path: str,
    duration_ms: int = 5000,
    position: str = "bottom_right"
) -> None:
    """Helper to instantiate and show a ToastNotification on the main Qt thread."""
    app = QApplication.instance()
    if app is None:
        return

    def _create():
        toast = ToastNotification(
            title=title,
            message=msg,
            icon_path=ico_path,
            duration_ms=duration_ms,
            position=position
        )
        toast.show_toast()

    main_thread = app.thread()
    if QThread.currentThread() == main_thread:
        _create()
    else:
        QTimer.singleShot(0, app, _create)
