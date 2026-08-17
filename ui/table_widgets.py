# -*- coding: utf-8 -*-
"""Custom UI Widgets (Tables, Splitters, Delegates) for DICOM WatchDog."""

try:
    from PyQt6.QtCore import Qt, QRect, QSize, QPointF
    from PyQt6.QtGui import (
        QColor, QPalette, QBrush, QPainter, QLinearGradient, QPen,
        QPainterPath, QMouseEvent
    )
    from PyQt6.QtWidgets import (
        QTableWidget, QWidget, QVBoxLayout, QLabel, QPushButton,
        QStyledItemDelegate, QStyleOptionViewItem, QStyle,
        QSplitter, QSplitterHandle
    )
except ImportError:
    from PyQt5.QtCore import Qt, QRect, QSize, QPointF
    from PyQt5.QtGui import (
        QColor, QPalette, QBrush, QPainter, QLinearGradient, QPen,
        QPainterPath, QMouseEvent
    )
    from PyQt5.QtWidgets import (
        QTableWidget, QWidget, QVBoxLayout, QLabel, QPushButton,
        QStyledItemDelegate, QStyleOptionViewItem, QStyle,
        QSplitter, QSplitterHandle
    )

from core.locale_utils import tr_ui


def tr(ru_text, en_text):
    try:
        from core.locale_utils import get_current_langs
        lang, _ = get_current_langs()
        return ru_text if lang == 'ru' else en_text
    except Exception:
        return ru_text


class ToggleTableWidget(QTableWidget):
    """Таблица со встроенным плейсхолдером и улучшенным выделением строк."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.placeholder_widget = None
        self.placeholder_label = None
        self.placeholder_btn = None

    def set_placeholder_state(self, text, show_button=False, button_callback=None, color=None):
        if not self.placeholder_widget:
            self.placeholder_widget = QWidget(self.viewport())
            layout = QVBoxLayout(self.placeholder_widget)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(10)
            layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            
            self.placeholder_label = QLabel(text, self.placeholder_widget)
            self.placeholder_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(self.placeholder_label)
            
            self.placeholder_btn = QPushButton(tr_ui("btn_browse"), self.placeholder_widget)
            self.placeholder_btn.setFixedSize(120, 30)
            self.placeholder_btn.setStyleSheet("""
                QPushButton {
                    background-color: #2b2b2b;
                    color: #ffffff;
                    border: 1px solid #3d3d3d;
                    border-radius: 4px;
                    font-family: 'Segoe UI';
                    font-size: 13px;
                }
                QPushButton:hover {
                    background-color: #3d3d3d;
                }
                QPushButton:pressed {
                    background-color: #1a1a1a;
                }
            """)
            layout.addWidget(self.placeholder_btn, alignment=Qt.AlignmentFlag.AlignCenter)
            self.placeholder_widget.hide()
            
        label_color = color if color else "#666666"
        self.placeholder_label.setStyleSheet(f"color: {label_color}; font-size: 15px; font-family: 'Segoe UI'; background: transparent;")
        self.placeholder_label.setText(text)
        self.placeholder_btn.setText(tr_ui("btn_browse"))
        self.placeholder_btn.setVisible(show_button)
        
        try:
            self.placeholder_btn.clicked.disconnect()
        except TypeError:
            pass
            
        if button_callback:
            self.placeholder_btn.clicked.connect(button_callback)
            
        self.update_placeholder_visibility()

    def set_placeholder_text(self, text, color=None):
        self.set_placeholder_state(text, show_button=False, color=color)

    def update_placeholder_visibility(self):
        if self.placeholder_widget:
            if self.rowCount() == 0:
                self.placeholder_widget.setGeometry(self.viewport().rect())
                self.placeholder_widget.show()
            else:
                self.placeholder_widget.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.placeholder_widget:
            self.placeholder_widget.setGeometry(self.viewport().rect())

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
            
        index = self.indexAt(event.pos())
        if not index.isValid():
            self.clearSelection()
            super().mousePressEvent(event)
            return

        row = index.row()
        is_selected = False
        selected_ranges = self.selectedRanges()
        for r in selected_ranges:
            if r.topRow() <= row <= r.bottomRow():
                is_selected = True
                break

        if is_selected:
            self.clearSelection()
            self.setCurrentIndex(self.model().index(-1, -1))
            self.setFocus()
        else:
            super().mousePressEvent(event)


class TaskProgressDelegate(QStyledItemDelegate):
    """Делегат для отрисовки анимированного градиента при операциях над строками."""
    def __init__(self, parent, active_ops, anim_phase):
        super().__init__(parent)
        self.main_window = parent
        self.active_ops = active_ops
        self.anim_phase = anim_phase

    def paint(self, painter, option, index):
        id_index = index.sibling(index.row(), 0)
        patient_id = id_index.data(Qt.ItemDataRole.UserRole)

        if patient_id in self.active_ops:
            op_data = self.active_ops[patient_id]
            op_type = op_data.get('op')

            painter.save()

            color_map = {
                'archive': (QColor(40, 30, 15, 200), QColor(80, 50, 20, 200)),
                'delete': (QColor(50, 15, 15, 200), QColor(90, 25, 25, 200)),
                'restore': (QColor(15, 40, 50, 200), QColor(25, 70, 90, 200)),
                'clean_str': (QColor(35, 15, 50, 200), QColor(60, 25, 90, 200))
            }
            c1, c2 = color_map.get(op_type, (QColor(30, 30, 30, 200), QColor(50, 50, 50, 200)))

            table_widget = option.widget
            rect = option.rect
            
            row_left = rect.left()
            row_right = rect.right()
            if table_widget:
                total_width = 0
                for col in range(table_widget.columnCount()):
                    total_width += table_widget.columnWidth(col)
                
                cell_left_offset = 0
                for col in range(index.column()):
                    cell_left_offset += table_widget.columnWidth(col)
                
                row_left = rect.left() - cell_left_offset
                row_right = row_left + total_width

            gradient = QLinearGradient(row_left, rect.top(), row_right, rect.top())

            phase = self.anim_phase[0]
            stop1 = phase % 1.0
            stop2 = (phase + 0.33) % 1.0
            stop3 = (phase + 0.66) % 1.0

            stops = sorted([(stop1, c1), (stop2, c2), (stop3, c1)], key=lambda x: x[0])
            
            gradient.setColorAt(0.0, stops[0][1])
            for stop, color in stops:
                gradient.setColorAt(stop, color)
            gradient.setColorAt(1.0, stops[-1][1])

            painter.fillRect(rect, QBrush(gradient))
            painter.restore()

            new_option = QStyleOptionViewItem(option)
            if new_option.state & QStyle.StateFlag.State_Selected:
                new_option.state &= ~QStyle.StateFlag.State_Selected
            new_option.palette.setColor(QPalette.ColorGroup.All, QPalette.ColorRole.Text, QColor("#ffffff"))
            new_option.palette.setColor(QPalette.ColorGroup.All, QPalette.ColorRole.HighlightedText, QColor("#ffffff"))

            if index.column() in (0, 1):
                suffix_map = {
                    'archive': tr(" [Архивация...]", " [Archiving...]"),
                    'delete': tr(" [Удаление...]", " [Deleting...]"),
                    'restore': tr(" [Восстановление...]", " [Restoring...]"),
                    'clean_str': tr(" [Очистка STR...]", " [Cleaning STR...]")
                }
                suffix = suffix_map.get(op_type, tr(" [Выполнение...]", " [Processing...]"))
                orig_text = index.data(Qt.ItemDataRole.DisplayRole)
                new_option.text = str(orig_text) + suffix

            super().paint(painter, new_option, index)
        else:
            super().paint(painter, option, index)


class CustomSplitterHandle(QSplitterHandle):
    def __init__(self, orientation: Qt.Orientation, parent) -> None:
        super().__init__(orientation, parent)
        self.is_collapsed = False
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        if orientation == Qt.Orientation.Horizontal:
            self.setFixedWidth(8)
        else:
            self.setFixedHeight(8)

    def get_handle_index(self) -> int:
        splitter = self.splitter()
        if not splitter:
            return -1
        for i in range(1, splitter.count()):
            if splitter.handle(i) is self:
                return i
        return -1

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = self.rect()
        is_hovered = self.underMouse()

        bg_color = QColor("#222222") if not is_hovered else QColor("#2a2a2a")
        painter.fillRect(rect, bg_color)

        dots_color = QColor("#555555") if not is_hovered else QColor("#999999")
        painter.setBrush(QBrush(dots_color))
        painter.setPen(Qt.PenStyle.NoPen)

        if self.orientation() == Qt.Orientation.Horizontal:
            cx = rect.center().x()
            cy = rect.center().y()
            for dy in [-10, -5, 0, 5, 10]:
                painter.drawEllipse(QPointF(cx, cy + dy), 1.5, 1.5)
        else:
            cx = rect.center().x()
            cy = rect.center().y()
            for dx in [-10, -5, 0, 5, 10]:
                painter.drawEllipse(QPointF(cx + dx, cy), 1.5, 1.5)


class CustomSplitter(QSplitter):
    def createHandle(self) -> QSplitterHandle:
        return CustomSplitterHandle(self.orientation(), self)
