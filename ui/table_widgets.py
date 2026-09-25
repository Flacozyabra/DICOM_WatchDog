# -*- coding: utf-8 -*-
"""Custom UI Widgets (Tables, Splitters, Delegates) for DICOM WatchDog."""

try:
    from PyQt6.QtCore import Qt, QRect, QSize, QPointF, QPoint, pyqtSignal
    from PyQt6.QtGui import (
        QColor, QPalette, QBrush, QPainter, QLinearGradient, QPen,
        QPainterPath, QMouseEvent, QPolygon, QKeySequence
    )
    from PyQt6.QtWidgets import (
        QTableWidget, QWidget, QVBoxLayout, QLabel, QPushButton,
        QStyledItemDelegate, QStyleOptionViewItem, QStyle,
        QSplitter, QSplitterHandle
    )
except ImportError:
    from PyQt5.QtCore import Qt, QRect, QSize, QPointF, QPoint, pyqtSignal
    from PyQt5.QtGui import (
        QColor, QPalette, QBrush, QPainter, QLinearGradient, QPen,
        QPainterPath, QMouseEvent, QPolygon, QKeySequence
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
    delete_requested = pyqtSignal()
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

        ctrl_mod = getattr(Qt.KeyboardModifier, 'ControlModifier', getattr(Qt, 'ControlModifier', 0x04000000))
        shift_mod = getattr(Qt.KeyboardModifier, 'ShiftModifier', getattr(Qt, 'ShiftModifier', 0x02000000))
        ctrl_or_shift = bool(event.modifiers() & (ctrl_mod | shift_mod))
        if ctrl_or_shift:
            super().mousePressEvent(event)
            return

        index = self.indexAt(event.pos())
        if not index.isValid():
            self.clearSelection()
            super().mousePressEvent(event)
            return

        row = index.row()
        selected_rows = {r for rng in self.selectedRanges() for r in range(rng.topRow(), rng.bottomRow() + 1)}
        if row in selected_rows:
            if len(selected_rows) == 1:
                self.clearSelection()
                self.setCurrentIndex(self.model().index(-1, -1))
                self.setFocus()
            else:
                self.clearSelection()
                self.selectRow(row)
                self.setCurrentIndex(self.model().index(row, index.column()))
                self.setFocus()
        else:
            super().mousePressEvent(event)

    def keyPressEvent(self, event):
        ctrl_mod = getattr(Qt.KeyboardModifier, 'ControlModifier', getattr(Qt, 'ControlModifier', 0x04000000))
        key_a = getattr(Qt.Key, 'Key_A', getattr(Qt, 'Key_A', 0x41))
        key_del = getattr(Qt.Key, 'Key_Delete', getattr(Qt, 'Key_Delete', 0x01000007))

        if event.matches(QKeySequence.StandardKey.SelectAll) or (
            event.key() == key_a and (event.modifiers() & ctrl_mod)
        ):
            self.selectAll()
            event.accept()
            return
        elif event.key() == key_del:
            self.delete_requested.emit()
            event.accept()
            return

        super().keyPressEvent(event)


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

        op_data = None
        if patient_id and patient_id in self.active_ops:
            op_data = self.active_ops[patient_id]
        elif patient_id:
            for k, v in self.active_ops.items():
                if k == patient_id or v.get('folder_name') == patient_id or str(patient_id).startswith(str(k) + "_"):
                    op_data = v
                    break

        if op_data:
            op_type = op_data.get('op', 'process')
            progress = op_data.get('progress')

            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

            color_map = {
                'archive': (QColor(45, 30, 15, 230), QColor(160, 95, 25, 240), QColor(255, 175, 55)),
                'delete': (QColor(50, 15, 20, 230), QColor(150, 30, 45, 240), QColor(255, 75, 95)),
                'delete_images': (QColor(50, 15, 20, 230), QColor(150, 30, 45, 240), QColor(255, 75, 95)),
                'delete_archive': (QColor(50, 15, 20, 230), QColor(150, 30, 45, 240), QColor(255, 75, 95)),
                'restore': (QColor(15, 35, 55, 230), QColor(25, 100, 160, 240), QColor(60, 190, 255)),
                'clean_str': (QColor(35, 15, 50, 230), QColor(110, 35, 160, 240), QColor(190, 80, 255)),
                'auto_process': (QColor(15, 45, 35, 230), QColor(25, 130, 95, 240), QColor(45, 220, 150)),
                'process': (QColor(15, 45, 35, 230), QColor(25, 130, 95, 240), QColor(45, 220, 150)),
            }
            c1, c2, edge_color = color_map.get(op_type, (QColor(30, 30, 35, 230), QColor(70, 75, 90, 240), QColor(160, 170, 190)))

            table_widget = option.widget
            rect = option.rect

            row_left = rect.left()
            row_right = rect.right()
            total_width = rect.width()
            if table_widget:
                total_width = 0
                for col in range(table_widget.columnCount()):
                    if not table_widget.isColumnHidden(col):
                        total_width += table_widget.columnWidth(col)

                cell_left_offset = 0
                for col in range(index.column()):
                    if not table_widget.isColumnHidden(col):
                        cell_left_offset += table_widget.columnWidth(col)

                row_left = rect.left() - cell_left_offset
                row_right = row_left + total_width

            phase = self.anim_phase[0]
            prog_val = min(1.0, max(0.0, float(progress))) if progress is not None else None

            # 1. Базовый темный фон строки + полупрозрачный трек цвета операции
            painter.fillRect(rect, QColor(22, 22, 25, 240))
            painter.fillRect(rect, QColor(c1.red(), c1.green(), c1.blue(), 35))

            if prog_val is not None:
                # Детерминированный прогресс-бар: заполнение слева направо
                prog_width = max(1.0, total_width * prog_val)
                fill_right = row_left + prog_width
                cell_filled_rect = rect.intersected(QRect(int(row_left), rect.top(), int(prog_width), rect.height()))

                if cell_filled_rect.isValid() and cell_filled_rect.width() > 0:
                    gradient = QLinearGradient(row_left, rect.top(), fill_right, rect.top())
                    stop1 = phase % 1.0
                    stop2 = (phase + 0.33) % 1.0
                    stop3 = (phase + 0.66) % 1.0
                    stops = sorted([(stop1, c1), (stop2, c2), (stop3, c1)], key=lambda x: x[0])

                    gradient.setColorAt(0.0, stops[0][1])
                    for stop, color in stops:
                        gradient.setColorAt(stop, color)
                    gradient.setColorAt(1.0, stops[-1][1])

                    painter.fillRect(cell_filled_rect, QBrush(gradient))

                # Светящаяся линия переднего края прогресса
                if rect.left() <= fill_right <= rect.right() and prog_val < 0.999:
                    painter.setPen(QPen(edge_color, 2))
                    painter.drawLine(int(fill_right), rect.top(), int(fill_right), rect.bottom())
            else:
                # Анимированная волна по всей ширине строки
                gradient = QLinearGradient(row_left, rect.top(), row_right, rect.top())
                stop1 = phase % 1.0
                stop2 = (phase + 0.33) % 1.0
                stop3 = (phase + 0.66) % 1.0
                stops = sorted([(stop1, c1), (stop2, c2), (stop3, c1)], key=lambda x: x[0])

                gradient.setColorAt(0.0, stops[0][1])
                for stop, color in stops:
                    gradient.setColorAt(stop, color)
                gradient.setColorAt(1.0, stops[-1][1])

                painter.fillRect(rect, QBrush(gradient))

            # 2. Выделение пользователем (полупрозрачная акцентная подсветка без затирания анимации)
            is_selected = bool(option.state & QStyle.StateFlag.State_Selected)
            if is_selected:
                painter.fillRect(rect, QColor(255, 94, 94, 45))
                painter.setPen(QPen(QColor(255, 94, 94, 180), 1))
                painter.drawLine(rect.left(), rect.top(), rect.right(), rect.top())
                painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.bottom())

            # 3. Прямой рендеринг текста белым цветом
            align_data = index.data(Qt.ItemDataRole.TextAlignmentRole)
            if align_data is not None:
                align = int(align_data)
            else:
                if index.column() in (0, 1):
                    align = int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
                else:
                    align = int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignCenter)

            orig_text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
            suffix = ""
            if index.column() in (0, 1):
                suffix_map = {
                    'archive': tr(" [Архивация...]", " [Archiving...]"),
                    'delete': tr(" [Удаление...]", " [Deleting...]"),
                    'delete_images': tr(" [Удаление...]", " [Deleting...]"),
                    'delete_archive': tr(" [Удаление...]", " [Deleting...]"),
                    'restore': tr(" [Восстановление...]", " [Restoring...]"),
                    'clean_str': tr(" [Очистка STR...]", " [Cleaning STR...]"),
                    'auto_process': tr(" [Обработка...]", " [Processing...]"),
                    'process': tr(" [Обработка...]", " [Processing...]")
                }
                suffix = suffix_map.get(op_type, tr(" [Выполнение...]", " [Processing...]"))

            clean_orig = orig_text.strip()
            if suffix:
                if index.column() == 0 and not clean_orig:
                    display_text = ""
                else:
                    clean_suffix_word = suffix.replace('[', '').replace(']', '').replace('.', '').strip().lower()
                    clean_orig_word = clean_orig.replace('[', '').replace(']', '').replace('.', '').strip().lower()
                    if clean_orig_word in (clean_suffix_word, 'processing', 'обработка', 'unknown', ''):
                        display_text = suffix.strip()
                    elif clean_orig_word.endswith(clean_suffix_word):
                        display_text = clean_orig
                    else:
                        display_text = orig_text + suffix
            else:
                display_text = orig_text
            painter.setFont(option.font)
            painter.setPen(QColor("#ffffff"))
            text_rect = rect.adjusted(6, 0, -6, 0)
            painter.drawText(text_rect, align, display_text)

            painter.restore()
            return

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

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        idx = self.get_handle_index()
        splitter = self.splitter()
        if splitter and idx != -1:
            sizes = splitter.sizes()
            if len(sizes) >= 2 and idx == 1:
                self.is_collapsed = (sizes[1] <= 5)
        self.update()

    def mouseMoveEvent(self, event) -> None:
        event.ignore()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            idx = self.get_handle_index()
            if idx != -1:
                self.toggle_collapse()
        else:
            event.ignore()

    def paintEvent(self, event) -> None:
        idx = self.get_handle_index()
        if idx == -1:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        line_color = QColor("#3F3F46")
        if self.underMouse():
            arrow_color = QColor("#1f538d")
        else:
            arrow_color = QColor("#71717A")

        w = self.width()
        h = self.height()
        cx = w // 2
        cy = h // 2

        poly = QPolygon()

        if self.orientation() == Qt.Orientation.Horizontal:
            painter.setPen(QPen(line_color, 1))
            painter.drawLine(cx, 0, cx, h)

            if not self.is_collapsed:
                poly.append(QPoint(cx - 2, cy))
                poly.append(QPoint(cx + 2, cy - 10))
                poly.append(QPoint(cx + 2, cy + 10))
            else:
                poly.append(QPoint(cx + 2, cy))
                poly.append(QPoint(cx - 2, cy - 10))
                poly.append(QPoint(cx - 2, cy + 10))
        else:
            painter.setPen(QPen(line_color, 1))
            painter.drawLine(15, cy, w - 15, cy)

            if not self.is_collapsed:
                poly.append(QPoint(cx, cy + 2))
                poly.append(QPoint(cx - 10, cy - 2))
                poly.append(QPoint(cx + 10, cy - 2))
            else:
                poly.append(QPoint(cx, cy - 2))
                poly.append(QPoint(cx - 10, cy + 2))
                poly.append(QPoint(cx + 10, cy + 2))

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(arrow_color))
        painter.drawPolygon(poly)

    def toggle_collapse(self) -> None:
        splitter = self.splitter()
        if not splitter:
            return

        sizes = splitter.sizes()
        idx = self.get_handle_index()

        if splitter.orientation() == Qt.Orientation.Horizontal:
            if len(sizes) < 2:
                return
            if idx == 1:
                if not self.is_collapsed:
                    self.saved_width = sizes[0] if sizes[0] > 5 else 385
                    new_sizes = [0, sizes[1] + sizes[0]]
                    splitter.setSizes(new_sizes)
                    self.is_collapsed = True
                else:
                    w = getattr(self, 'saved_width', 385)
                    new_sizes = [w, max(50, sizes[1] + sizes[0] - w)]
                    splitter.setSizes(new_sizes)
                    self.is_collapsed = False
        else:
            if len(sizes) < 2:
                return
            if idx == 1:
                if not self.is_collapsed:
                    self.saved_log_height = sizes[1] if sizes[1] > 5 else 150
                    new_sizes = [sizes[0] + sizes[1], 0]
                    splitter.setSizes(new_sizes)
                    self.is_collapsed = True
                else:
                    h = getattr(self, 'saved_log_height', 150)
                    new_sizes = [max(50, sizes[0] + sizes[1] - h), h]
                    splitter.setSizes(new_sizes)
                    self.is_collapsed = False

        self.update()


class CustomSplitter(QSplitter):
    def __init__(self, orientation: Qt.Orientation, parent: QWidget = None) -> None:
        super().__init__(orientation, parent)

    def createHandle(self) -> QSplitterHandle:
        return CustomSplitterHandle(self.orientation(), self)
