from PyQt6.QtWidgets import QDateEdit, QToolButton
from PyQt6.QtCore import Qt, QSize


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
            "  padding: 4px;"
            "  font-family: 'Segoe UI'; font-size: 13px;"
            "}"
            "QDateEdit::drop-down {"
            "  subcontrol-origin: padding;"
            "  subcontrol-position: center right;"
            "  border: none;"
            "  width: 20px; padding-right: 2px;"
            "}"
            "QDateEdit::down-arrow { image: none; border: none; }"
        )

    def minimumSizeHint(self):
        return QSize(110, 30)

    def sizeHint(self):
        return QSize(110, 30)
