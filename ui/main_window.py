import os
import sys
import shutil
from datetime import datetime, timedelta

from PyQt6.QtCore import Qt, QTimer, QSize, QThread, pyqtSignal, QObject, QDate, QPoint
from PyQt6.QtGui import QColor, QAction, QIcon, QFont, QPainter, QPen, QBrush, QPolygon, QPalette, QLinearGradient
from PyQt6.QtWidgets import (QApplication, QMainWindow, QTabWidget, QWidget, 
                             QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem, 
                             QPlainTextEdit, QPushButton, QMessageBox, 
                             QHeaderView, QMenu, QAbstractItemView, QLineEdit, QLabel,
                             QDialog, QFileDialog, QDateEdit, QStackedWidget, QSplitter,
                             QSplitterHandle, QComboBox, QStyledItemDelegate, QStyleOptionViewItem, QStyle)

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from core.dicom_utils import dict_create, process_patient_folder, delete_redundant_str
from core.archive import move_old_folders_to_archive
from core.notifier import show_notification
from core.logger import log_message
from core.pacs import pacs_dict_create, download_patient_from_pacs
from core.config_utils import get_resource_path, VERSION
from core.locale_utils import tr_ui, tr_log, set_current_langs
from ui.settings_dialog import SettingsDialog, apply_dark_title_bar
from ui.toggle_switch import ToggleSwitch
from ui.centered_date_edit import CenteredDateEdit
from themes.theme_manager import load_theme
from ui.dicom_viewer import DicomViewerPanel


class ToggleTableWidget(QTableWidget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.placeholder_widget = None
        self.placeholder_label = None
        self.placeholder_btn = None

    def set_placeholder_state(self, text, show_button=False, button_callback=None):
        if not self.placeholder_widget:
            self.placeholder_widget = QWidget(self.viewport())
            layout = QVBoxLayout(self.placeholder_widget)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(10)
            layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            
            self.placeholder_label = QLabel(text, self.placeholder_widget)
            self.placeholder_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.placeholder_label.setStyleSheet("color: #666666; font-size: 15px; font-family: 'Segoe UI'; background: transparent;")
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

    def set_placeholder_text(self, text):
        self.set_placeholder_state(text, show_button=False)

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


class WatchdogHandler(QObject, FileSystemEventHandler):
    changed = pyqtSignal()

    def on_any_event(self, event):
        self.changed.emit()


class ThreadLogCollector:
    def __init__(self, emit_callback=None):
        self.messages = []
        self.emit_callback = emit_callback

    def appendPlainText(self, text):
        self.messages.append(text)
        if self.emit_callback:
            self.emit_callback(text)


class FolderScanWorker(QThread):
    finished = pyqtSignal(dict, list)
    progress = pyqtSignal(int, int)  # (current, total)
    status_changed = pyqtSignal(str) # (status_text)
    log_emitted = pyqtSignal(str)

    def __init__(self, ct_images_dir, cleanup_structures_enabled, fix_patient_id_enabled, id_prefixes,
                 rename_study_folder_enabled, rename_study_folder_mode,
                 archive_dir, archive_enabled, archive_days, archive_cleanup_enabled, archive_cleanup_days):
        super().__init__()
        self.ct_images_dir = ct_images_dir
        self.cleanup_structures_enabled = cleanup_structures_enabled
        self.fix_patient_id_enabled = fix_patient_id_enabled
        self.id_prefixes = id_prefixes
        self.rename_study_folder_enabled = rename_study_folder_enabled
        self.rename_study_folder_mode = rename_study_folder_mode
        self.archive_dir = archive_dir
        self.archive_enabled = archive_enabled
        self.archive_days = archive_days
        self.archive_cleanup_enabled = archive_cleanup_enabled
        self.archive_cleanup_days = archive_cleanup_days

    def run(self):
        collector = ThreadLogCollector(emit_callback=self.log_emitted.emit)
        is_cleanup_struct_on = self.cleanup_structures_enabled.lower() == 'true'
        is_fix_id_on = self.fix_patient_id_enabled.lower() == 'true'
        is_rename_folder_on = self.rename_study_folder_enabled.lower() == 'true'
        is_archive_on = self.archive_enabled.lower() == 'true'
        is_cleanup_on = self.archive_cleanup_enabled.lower() == 'true'
        
        prefixes_list = []
        if self.id_prefixes:
            prefixes_list = [p.strip() for p in self.id_prefixes.split(',') if p.strip()]

        patient_folders = []
        if os.path.exists(self.ct_images_dir):
            try:
                patient_folders = [os.path.join(self.ct_images_dir, d) for d in os.listdir(self.ct_images_dir)
                                   if os.path.isdir(os.path.join(self.ct_images_dir, d))]
            except Exception:
                pass

        total_folders = len(patient_folders)

        # 1. Фаза исправления ID и переименования папок
        if (is_fix_id_on or is_rename_folder_on) and total_folders > 0:
            self.status_changed.emit(tr_ui("loading_fixing_folders_status"))
            for i, path in enumerate(patient_folders):
                self.progress.emit(i, total_folders)
                process_patient_folder(
                    path, collector,
                    fix_patient_id=is_fix_id_on,
                    prefixes=prefixes_list,
                    rename_folder=is_rename_folder_on,
                    rename_mode=self.rename_study_folder_mode
                )
            self.progress.emit(total_folders, total_folders)
            
        # 2. Фаза архивации
        if is_archive_on and not self.archive_dir:
            collector.appendPlainText(tr_log("log_warn_auto_archive_not_configured"))

        if self.archive_dir and is_archive_on and os.path.exists(self.ct_images_dir):
            self.status_changed.emit(tr_ui("loading_archiving_status"))
            from core.archive import move_old_folders_to_archive
            move_old_folders_to_archive(self.ct_images_dir, self.archive_dir, self.archive_days, collector)

        # 3. Фаза очистки архива
        if self.archive_dir and is_cleanup_on:
            self.status_changed.emit(tr_ui("loading_cleanup_status"))
            from core.archive import cleanup_old_archive_folders
            cleanup_old_archive_folders(self.archive_dir, self.archive_cleanup_days, collector)

        # 4. Фаза сканирования папок для таблицы
        self.status_changed.emit(tr_ui("loading_scanning_folders_status"))
        patient_dict = dict_create(
            self.ct_images_dir, collector,
            cleanup_structures=is_cleanup_struct_on,
            progress_callback=self.progress.emit
        )
        self.finished.emit(patient_dict, collector.messages)


class PacsScanWorker(QThread):
    finished = pyqtSignal(dict, bool, list)

    def __init__(self, pacs_ip, pacs_port, called_aet, calling_aet, study_date=None):
        super().__init__()
        self.pacs_ip = pacs_ip
        self.pacs_port = pacs_port
        self.called_aet = called_aet
        self.calling_aet = calling_aet
        self.study_date = study_date

    def run(self):
        collector = ThreadLogCollector()
        pacs_dict, con = pacs_dict_create(
            collector,
            pacs_ip=self.pacs_ip,
            pacs_port=self.pacs_port,
            called_aet=self.called_aet,
            calling_aet=self.calling_aet,
            study_date=self.study_date
        )
        self.finished.emit(pacs_dict, con, collector.messages)


class ArchiveScanWorker(QThread):
    finished = pyqtSignal(dict, list)
    progress = pyqtSignal(int, int)  # (current, total)
    log_emitted = pyqtSignal(str)

    def __init__(self, archive_dir, cleanup_structures_enabled):
        super().__init__()
        self.archive_dir = archive_dir
        self.cleanup_structures_enabled = cleanup_structures_enabled

    def run(self):
        collector = ThreadLogCollector(emit_callback=self.log_emitted.emit)
        is_cleanup_struct_on = self.cleanup_structures_enabled.lower() == 'true'
        from core.archive import archive_dict_create
        d = archive_dict_create(
            self.archive_dir, collector,
            cleanup_structures=is_cleanup_struct_on,
            progress_callback=self.progress.emit
        )
        self.finished.emit(d, collector.messages)


def tr(ru_text, en_text):
    from core.locale_utils import get_current_langs
    lang, _ = get_current_langs()
    return ru_text if lang == 'ru' else en_text


class BackgroundFileWorker(QThread):
    finished = pyqtSignal(str, str, object)  # patient_id, op_type, result
    error = pyqtSignal(str, str, str, str)    # patient_id, op_type, err_msg, err_title

    def __init__(self, patient_id, op_type, func, *args):
        super().__init__()
        self.patient_id = patient_id
        self.op_type = op_type
        self.func = func
        self.args = args

    def run(self):
        try:
            res = self.func(*self.args)
            self.finished.emit(self.patient_id, self.op_type, res)
        except Exception as e:
            err_title = tr_ui("dlg_error_archive_title") if self.op_type == "archive" else tr_ui("dlg_error_delete_title")
            self.error.emit(self.patient_id, self.op_type, str(e), err_title)


class TaskProgressDelegate(QStyledItemDelegate):
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

            # Базовые цвета градиента для разных операций (активный / фоновый)
            color_map = {
                'archive': (QColor(40, 30, 15, 200), QColor(80, 50, 20, 200)),
                'delete': (QColor(50, 15, 15, 200), QColor(90, 25, 25, 200)),
                'restore': (QColor(15, 40, 50, 200), QColor(25, 70, 90, 200)),
                'clean_str': (QColor(35, 15, 50, 200), QColor(60, 25, 90, 200))
            }
            c1, c2 = color_map.get(op_type, (QColor(30, 30, 30, 200), QColor(50, 50, 50, 200)))

            table_widget = option.widget
            rect = option.rect
            
            # Вычисляем геометрические границы всей строки для плавного общего градиента
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
            
            # Обеспечиваем плавность на границах
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

            # Добавляем текстовый суффикс
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


class PacsDownloadWorker(QThread):
    finished = pyqtSignal(bool, str)
    progress = pyqtSignal(int, int)

    def __init__(self, patient_id, target_dir, pacs_ip, pacs_port, called_aet, calling_aet):
        super().__init__()
        self.patient_id = patient_id
        self.target_dir = target_dir
        self.pacs_ip = pacs_ip
        self.pacs_port = pacs_port
        self.called_aet = called_aet
        self.calling_aet = calling_aet

    def run(self):
        success, msg = download_patient_from_pacs(
            self.patient_id, self.target_dir,
            self.pacs_ip, self.pacs_port,
            self.called_aet, self.calling_aet,
            progress_callback=self.progress.emit
        )
        self.finished.emit(success, msg)


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


class MainWindow(QMainWindow):
    instance = None

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"DICOM WatchDog v{VERSION}")
        self.setWindowIcon(QIcon(get_resource_path("src/splashscreen_logo.png")))
        MainWindow.instance = self
        self.config = self.load_config()
        self.init_window_geometry()
        
        # Темная тема и цвет для рамки окна Windows (верхняя полоса)
        if sys.platform == "win32":
            import ctypes
            try:
                hwnd = int(self.winId())
                # Включение темного режима (Immersive Dark Mode)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, 20, ctypes.byref(ctypes.c_int(1)), ctypes.sizeof(ctypes.c_int)
                )
            except Exception:
                try:
                    ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        hwnd, 19, ctypes.byref(ctypes.c_int(1)), ctypes.sizeof(ctypes.c_int)
                    )
                except Exception:
                    pass
            
            # Установка точного серого цвета #242424 (BGR: 0x00242424) для Windows 11
            try:
                hwnd = int(self.winId())
                # DWMWA_CAPTION_COLOR = 35
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, 35, ctypes.byref(ctypes.c_int(0x00242424)), ctypes.sizeof(ctypes.c_int)
                )
                # DWMWA_TEXT_COLOR = 36 (белый текст заголовка)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, 36, ctypes.byref(ctypes.c_int(0x00ffffff)), ctypes.sizeof(ctypes.c_int)
                )
            except Exception:
                pass

        self.pacs_timer_id = None
        self.scan_worker = None
        self.pacs_worker = None
        self.archive_worker = None
        self.is_first_scan = True
        self.is_first_pacs_scan = True
        self.restored_patient_ids = set()
        self.known_pacs_patient_ids = set()
        self.images_cache = None
        self.previous_pacs_data = {}
        self.standby_new_patients = {}
        self.pacs_download_worker = None
        
        # Инициализируем таймеры до создания UI во избежание AttributeError
        self.pacs_timer = QTimer(self)
        self.pacs_timer.timeout.connect(self.auto_update_pacs)
        
        self.net_retry_timer = QTimer(self)
        self.net_retry_timer.setInterval(5000)
        self.net_retry_timer.timeout.connect(self.check_network_folder_retry)
        self.net_retry_count = 0
        self.net_retry_max = 24
        
        # Инициализируем таймер отслеживания сна и смены суток
        import time
        self.last_timer_timestamp = time.time()
        self.last_checked_date = datetime.now().date()
        self.system_check_timer = QTimer(self)
        self.system_check_timer.setInterval(10000)
        self.system_check_timer.timeout.connect(self.check_system_status)
        self.system_check_timer.start()
        
        # Инициализируем наблюдатель за файловой системой
        self.init_file_watcher()
        
        self.init_ui()
        self.apply_theme()
        
        # Инициализация фоновых операций и анимаций
        self.active_file_operations = {}
        self.animation_phase = [0.0]
        
        self.animation_timer = QTimer(self)
        self.animation_timer.setInterval(100)
        self.animation_timer.timeout.connect(self.update_animation_phase)
        self.animation_timer.start()

        self.table_delegate = TaskProgressDelegate(self, self.active_file_operations, self.animation_phase)
        self.images_table.setItemDelegate(self.table_delegate)
        self.archive_table.setItemDelegate(self.table_delegate)
        
        # Запуск таймеров и мониторинга
        self.restart_timers()
        
        # Первоначальное заполнение
        self.show_patient_list()
        
        # Проверка обновлений при запуске
        self.check_for_updates_on_startup()

    def update_animation_phase(self):
        if self.active_file_operations:
            self.animation_phase[0] += 0.05
            if self.animation_phase[0] >= 1.0:
                self.animation_phase[0] = 0.0
            self.images_table.viewport().update()
            self.archive_table.viewport().update()

    def on_background_action_finished(self, patient_id, op_type, result):
        if patient_id in self.active_file_operations:
            del self.active_file_operations[patient_id]
            
        op_key = f"worker_{patient_id}"
        if hasattr(self, op_key):
            delattr(self, op_key)
            
        self.images_table.viewport().update()
        self.archive_table.viewport().update()
            
        if op_type == 'archive':
            log_message(self.output_field, tr_log("log_patient_archived", result))
            self.show_patient_list()
        elif op_type == 'delete':
            log_message(self.output_field, tr_log("log_patient_deleted", result))
            self.show_patient_list()
            self.archive_cache = None
            self.fill_archive_list(silent=True)
        elif op_type == 'clean_str':
            deleted, folder_desc = result
            log_message(self.output_field, tr_log("log_cleaned_str_files", deleted, folder_desc))
            self.show_patient_list()
        elif op_type == 'restore':
            log_message(self.output_field, tr_log("log_patient_restored_from_archive", result))
            self.archive_cache = None
            self.fill_archive_list(silent=True)
            self.restored_patient_ids.add(patient_id)
            self.show_patient_list()

    def on_background_action_error(self, patient_id, op_type, err_msg, err_title):
        if patient_id in self.active_file_operations:
            del self.active_file_operations[patient_id]
            
        op_key = f"worker_{patient_id}"
        if hasattr(self, op_key):
            delattr(self, op_key)
            
        self.images_table.viewport().update()
        self.archive_table.viewport().update()
        
        _err = QMessageBox(self)
        _err.setIcon(QMessageBox.Icon.Critical)
        _err.setWindowTitle(err_title)
        _err.setText(tr_ui("dlg_error_archive_msg", err_msg) if op_type == 'archive' else tr_ui("dlg_error_delete_msg", err_msg))
        apply_dark_title_bar(_err)
        _err.exec()
        
        if op_type == 'archive':
            log_message(self.output_field, tr_log("log_failed_archive_patient", patient_id, err_msg))
        elif op_type == 'delete':
            log_message(self.output_field, tr_log("log_failed_delete_patient", patient_id, err_msg))
        elif op_type == 'restore':
            log_message(self.output_field, tr_log("log_failed_restore_patient", patient_id, err_msg))

    def get_folder_desc(self, folder_name, patient_name):
        if not patient_name:
            return folder_name
        clean_patient = patient_name.replace('^', ' ').strip().lower()
        if clean_patient in folder_name.lower():
            return folder_name
        return f"{folder_name} [{patient_name}]"

    def load_config(self):
        # Быстрый способ получить актуальные настройки из config.txt
        dialog = SettingsDialog(self)
        return dialog.config

    def init_window_geometry(self):
        width = max(self.config.get('x', 1100), 1100)
        height = self.config.get('y', 600)
        
        screen = QApplication.primaryScreen()
        if screen:
            screen_geometry = screen.geometry()
            dx = screen_geometry.x() + (screen_geometry.width() - width) // 2
            dy = screen_geometry.y() + (screen_geometry.height() - height) // 2
        else:
            dx = 350
            dy = 100
            
        self.setGeometry(dx, dy, width, height)

    def apply_theme(self):
        theme_content = load_theme("dark")
        if theme_content:
            self.setStyleSheet(theme_content)

    def apply_settings_dynamic(self, config):
        old_dir = self.config.get('ct_images_dir', '')
        new_dir = config.get('ct_images_dir', '')
        
        self.config = config.copy()
        set_current_langs(self.config.get('interface_lang', 'en'), self.config.get('log_lang', 'en'))
        
        # 1. Обновляем шрифты таблиц
        font_size = self.config.get('patient_font_size', 16)
        row_height = max(25, font_size + 12)
        
        weight_map = {
            "Regular": "400",
            "Semibold": "600",
            "Bold": "700"
        }
        weight_str = self.config.get('patient_weight', 'Semibold')
        weight = weight_map.get(weight_str, "400")
        table_style = f"font-size: {font_size}px; font-weight: {weight}; font-family: 'Segoe UI';"
        
        # Применяем ко всем трем таблицам
        for table in [self.images_table, self.archive_table, self.pacs_table]:
            table.verticalHeader().setDefaultSectionSize(row_height)
            table.setStyleSheet(table_style)
            table.viewport().update()
            
        # 2. Обновляем шрифт логов
        log_font_size = self.config.get('log_font_size', 12)
        font = QFont("Consolas", log_font_size)
        self.output_field.setFont(font)
        
        # 3. Синхронизируем чекбокс автообновления и перезапускаем таймеры
        self.pacs_auto_scan_cb.setChecked(self.config.get('auto_update_is', 'off').lower() == 'on')
        self.update_pacs_controls_state()
        self.restart_timers()
        
        # Обновляем локализацию интерфейса
        self.retranslate_ui()
        
        # 4. Обновляем путь наблюдателя, если он изменился
        if old_dir != new_dir:
            self.is_first_scan = True
            self.net_retry_count = 0
            self.update_watcher_path()

    def init_file_watcher(self):
        self.watcher_observer = None
        self.watcher_handler = None
        self.currently_watched_dir = None
        
        # Создаем таймер дебаунса (debounce)
        self.debounce_timer = QTimer(self)
        self.debounce_timer.setSingleShot(True)
        self.debounce_timer.timeout.connect(self.on_watcher_timeout)

    def update_watcher_path(self):
        ct_dir = self.config.get('ct_images_dir', '')
        if not ct_dir or not os.path.exists(ct_dir):
            self.stop_file_watcher()
            return
            
        ct_dir = self.config.get('ct_images_dir', '')
        if not ct_dir or not os.path.exists(ct_dir):
            self.stop_file_watcher()
            return
            
        # Если мониторинг уже запущен для этой же папки, ничего не делаем
        if hasattr(self, 'currently_watched_dir') and self.currently_watched_dir == ct_dir and self.watcher_observer and self.watcher_observer.is_alive():
            return
            
        self.stop_file_watcher()
            
        try:
            self.watcher_handler = WatchdogHandler()
            self.watcher_handler.changed.connect(self.trigger_debounce, Qt.ConnectionType.QueuedConnection)
            
            self.watcher_observer = Observer()
            self.watcher_observer.schedule(self.watcher_handler, ct_dir, recursive=True)
            self.watcher_observer.start()
            self.currently_watched_dir = ct_dir
            log_message(self.output_field, tr_log("log_watcher_started", ct_dir))
        except Exception as e:
            self.currently_watched_dir = None
            log_message(self.output_field, tr_log("log_watcher_failed", e))

    def stop_file_watcher(self):
        if hasattr(self, 'watcher_observer') and self.watcher_observer:
            try:
                self.watcher_observer.stop()
                self.watcher_observer.join(0.5)
            except Exception:
                pass
            self.watcher_observer = None
        self.watcher_handler = None
        self.currently_watched_dir = None

    def check_system_status(self):
        import time
        now_ts = time.time()
        today = datetime.now().date()
        
        # 1. Проверка пробуждения от сна (таймер был заморожен более чем на 25 секунд)
        elapsed = now_ts - self.last_timer_timestamp
        self.last_timer_timestamp = now_ts
        
        if elapsed > 25:
            log_message(self.output_field, tr_log("log_system_resumed_from_sleep"))
            self.last_checked_date = today
            self.update_images_table_ui()
            self.check_network_folder_retry()
            return
            
        # 2. Бесшумная проверка смены суток в полночь
        if self.last_checked_date != today:
            self.last_checked_date = today
            self.update_images_table_ui()

    def trigger_debounce(self):
        # 2 секунды задержки, чтобы дождаться окончания записи
        self.debounce_timer.start(2000)

    def on_watcher_timeout(self):
        self.start_folder_scan()

    def restart_timers(self):
        self.pacs_timer.stop()
        
        # Наблюдатель файлов в реальном времени работает всегда
        self.update_watcher_path()
            
        # Таймер PACS работает, если включено автообновление (Standby mode) или включены фоновые уведомления PACS
        master_notif = str(self.config.get('notifications_enabled', 'False')).lower() == 'true'
        pacs_toast = str(self.config.get('pacs_notification_toast_enabled', 'True')).lower() == 'true'
        pacs_sound = str(self.config.get('pacs_notification_sound_enabled', 'False')).lower() == 'true'
        pacs_notify_on = master_notif and (pacs_toast or pacs_sound)
        pacs_auto_scan_on = self.config.get('auto_update_is', 'off').lower() == 'on'
        if pacs_auto_scan_on or pacs_notify_on:
            self.pacs_timer.start(self.config.get('pacs_scan_time', 10000))

    def update_pacs_controls_state(self):
        auto_update_on = self.config.get('auto_update_is', 'off').lower() == 'on'
        
        # Если включен Standby mode (автообновление), выставляем принудительно Today
        if auto_update_on:
            self.pacs_date_from.blockSignals(True)
            self.pacs_date_to.blockSignals(True)
            self.pacs_date_from.setDate(QDate.currentDate())
            self.pacs_date_to.setDate(QDate.currentDate())
            self.pacs_date_from.blockSignals(False)
            self.pacs_date_to.blockSignals(False)
            
            # Устанавливаем серый цвет для подписей
            self.lbl_from.setStyleSheet("color: #666666; font-family: 'Segoe UI'; font-size: 13px;")
            self.lbl_to.setStyleSheet("color: #666666; font-family: 'Segoe UI'; font-size: 13px;")
        else:
            # Устанавливаем белый цвет для подписей
            self.lbl_from.setStyleSheet("color: #ffffff; font-family: 'Segoe UI'; font-size: 13px;")
            self.lbl_to.setStyleSheet("color: #ffffff; font-family: 'Segoe UI'; font-size: 13px;")

        # Блокируем или разблокируем виджеты дат и кнопок интервалов
        self.pacs_date_from.setEnabled(not auto_update_on)
        self.pacs_date_to.setEnabled(not auto_update_on)
        self.pacs_today_btn.setEnabled(not auto_update_on)
        self.pacs_3days_btn.setEnabled(not auto_update_on)

    def on_pacs_auto_scan_changed(self):
        is_checked = self.pacs_auto_scan_cb.isChecked()
        self.config['auto_update_is'] = 'on' if is_checked else 'off'
        self.save_current_config()
        self.update_pacs_controls_state()
        self.restart_timers()
        
        # Сбрасываем кэши и перерисовываем
        self.standby_new_patients = {}
        self.previous_pacs_data = {}
        
        if is_checked:
            self.pacs_table.set_placeholder_text(tr_ui("placeholder_standby"))
        else:
            self.pacs_table.set_placeholder_text(tr_ui("placeholder_not_configured"))

        self.pacs_table.setRowCount(0)
        self.pacs_table.update_placeholder_visibility()
        
        if is_checked:
            # При включении Standby mode сбрасываем флаг первого сканирования для предотвращения ложных уведомлений
            self.is_first_pacs_scan = True
        
        self.fill_pacs_list(silent=True)

    def init_ui(self):
        self.stacked_widget = QStackedWidget()
        self.setCentralWidget(self.stacked_widget)
        
        # Главный виджет (старый интерфейс)
        main_widget = QWidget()
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(10)
        
        # Вертикальный сплиттер для разделения вкладок и логов
        self.log_splitter = CustomSplitter(Qt.Orientation.Vertical)
        self.log_splitter.setObjectName("logSplitter")
        
        # Вкладки
        self.tab_widget = QTabWidget()
        self.tab_widget.tabBar().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tab_widget.tabBar().customContextMenuRequested.connect(self.show_tab_context_menu)
        self.log_splitter.addWidget(self.tab_widget)
        
        # Создание вкладок
        self.create_tab_ct_images()
        self.create_tab_ct_archive()
        self.create_tab_pacs()
        
        # Поле вывода логов в контейнере с верхним отступом от сплиттера
        self.output_container = QWidget()
        output_layout = QVBoxLayout(self.output_container)
        output_layout.setContentsMargins(0, 4, 0, 0)
        
        self.output_field = QPlainTextEdit()
        self.output_field.setReadOnly(True)
        # Установка размера шрифта из настроек
        font = QFont("Consolas", self.config.get('log_font_size', 12))
        self.output_field.setFont(font)
        output_layout.addWidget(self.output_field)
        
        self.log_splitter.addWidget(self.output_container)
        
        # Настройка пропорций и начальных размеров сплиттера
        self.log_splitter.setStretchFactor(0, 1)
        self.log_splitter.setStretchFactor(1, 0)
        
        window_height = self.geometry().height()
        log_height = 150
        tab_height = max(100, window_height - log_height - 30)
        self.log_splitter.setSizes([tab_height, log_height])
        
        main_layout.addWidget(self.log_splitter)
        
        # Подключаем сигнал изменения вкладок после полной инициализации виджетов
        self.tab_widget.currentChanged.connect(self.on_tab_changed)
        
        # Обновляем локализацию интерфейса перед отображением
        self.retranslate_ui()
        
        self.stacked_widget.addWidget(main_widget)
        
        # Панель вьюера DICOM
        self.viewer_panel = DicomViewerPanel(self)
        self.viewer_panel.close_requested.connect(self.close_viewer)
        self.stacked_widget.addWidget(self.viewer_panel)

    def create_tab_ct_images(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(4, 10, 4, 6)
        layout.setSpacing(10)
        
        # Таблица КТ-изображений
        self.images_table = ToggleTableWidget()
        self.images_table.setColumnCount(8)
        self.images_table.setHorizontalHeaderLabels([
            "Patient ID", "Patient Name", "Modality", "Slices", "Scanning Area", 
            "Study datetime", "Folder datetime", "STR"
        ])
        self.images_table.setColumnHidden(2, True)
        self.images_table.horizontalHeader().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.images_table.horizontalHeader().customContextMenuRequested.connect(
            lambda pos: self.show_header_context_menu(pos, self.images_table)
        )
        self.setup_table_properties(self.images_table)
        self.images_table.set_placeholder_text("В этой папке нет исследований")
        self.images_table.update_placeholder_visibility()
        self.restore_table_state(self.images_table)
        self.images_table.cellDoubleClicked.connect(self.on_images_double_clicked)
        self.images_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.images_table.customContextMenuRequested.connect(self.show_images_context_menu)
        self.images_table.itemSelectionChanged.connect(self.on_images_selection_changed)
        
        layout.addWidget(self.images_table)
        
        # Нижняя панель управления
        control_layout = QHBoxLayout()
        control_layout.setContentsMargins(5, 0, 5, 0)
        control_layout.setSpacing(10)
        
        # Поиск по КТ-изображениям
        self.search_images_entry = QLineEdit()
        self.search_images_entry.setPlaceholderText("Введите имя пациента для поиска")
        self.search_images_entry.textChanged.connect(self.search_patient_images)
        self.search_images_entry.setFixedHeight(30)
        control_layout.addWidget(self.search_images_entry, stretch=1, alignment=Qt.AlignmentFlag.AlignVCenter)
        
        self.search_images_btn = QPushButton("Search")
        self.search_images_btn.setFixedHeight(30)
        self.search_images_btn.clicked.connect(self.search_patient_images)
        control_layout.addWidget(self.search_images_btn, alignment=Qt.AlignmentFlag.AlignVCenter)
        
        # Кнопка перемещения в архив
        self.move_to_archive_btn = QPushButton("Move to Archive")
        self.move_to_archive_btn.clicked.connect(self.move_to_archive_cmd)
        self.move_to_archive_btn.setEnabled(False)  # Затененная по умолчанию
        self.move_to_archive_btn.setFixedHeight(30)
        self.move_to_archive_btn.setObjectName("moveToArchiveBtn")
        control_layout.addWidget(self.move_to_archive_btn, alignment=Qt.AlignmentFlag.AlignVCenter)
        
        # Кнопка настроек (шестеренка)
        self.settings_btn1 = QPushButton()
        self.settings_btn1.setIcon(QIcon(get_resource_path("themes/settings.svg")))
        self.settings_btn1.setIconSize(QSize(20, 20))
        self.settings_btn1.setFixedSize(35, 30)
        self.settings_btn1.setToolTip("Настройки папок и интервалов")
        self.settings_btn1.clicked.connect(self.open_settings_cmd)
        control_layout.addWidget(self.settings_btn1, alignment=Qt.AlignmentFlag.AlignVCenter)
        
        layout.addLayout(control_layout)
        
        self.tab_widget.addTab(tab, tr_ui("tab_ct_images"))

    def create_tab_ct_archive(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(4, 10, 4, 6)
        layout.setSpacing(10)
        
        # Таблица архива
        self.archive_table = ToggleTableWidget()
        self.archive_table.setColumnCount(8)
        self.archive_table.setHorizontalHeaderLabels([
            "Patient ID", "Patient Name", "Modality", "Slices", "Scanning Area", 
            "Study datetime", "Folder datetime", "STR"
        ])
        self.archive_table.setColumnHidden(2, True)
        self.archive_table.horizontalHeader().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.archive_table.horizontalHeader().customContextMenuRequested.connect(
            lambda pos: self.show_header_context_menu(pos, self.archive_table)
        )
        self.setup_table_properties(self.archive_table)
        self.archive_table.set_placeholder_text("В этой папке нет исследований")
        self.archive_table.update_placeholder_visibility()
        self.restore_table_state(self.archive_table)
        self.archive_table.cellDoubleClicked.connect(self.on_archive_double_clicked)
        self.archive_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.archive_table.customContextMenuRequested.connect(self.show_archive_context_menu)
        self.archive_table.itemSelectionChanged.connect(self.on_archive_selection_changed)
        layout.addWidget(self.archive_table)
        
        # Панель поиска и восстановления
        search_layout = QHBoxLayout()
        search_layout.setContentsMargins(5, 0, 5, 0)
        search_layout.setSpacing(10)
        
        self.search_entry = QLineEdit()
        self.search_entry.setPlaceholderText("Введите имя пациента для поиска")
        self.search_entry.textChanged.connect(self.search_patient_archive)
        self.search_entry.setFixedHeight(30)
        search_layout.addWidget(self.search_entry, stretch=1, alignment=Qt.AlignmentFlag.AlignVCenter)
        
        self.search_btn = QPushButton("Search")
        self.search_btn.setFixedHeight(30)
        self.search_btn.clicked.connect(self.search_patient_archive)
        search_layout.addWidget(self.search_btn, alignment=Qt.AlignmentFlag.AlignVCenter)
        
        self.move_from_archive_btn = QPushButton("Move to CT images")
        self.move_from_archive_btn.setFixedHeight(30)
        self.move_from_archive_btn.setEnabled(False)
        self.move_from_archive_btn.clicked.connect(self.move_from_archive_cmd)
        search_layout.addWidget(self.move_from_archive_btn, alignment=Qt.AlignmentFlag.AlignVCenter)
        
        # Кнопка настроек (шестеренка)
        self.settings_btn2 = QPushButton()
        self.settings_btn2.setIcon(QIcon(get_resource_path("themes/settings.svg")))
        self.settings_btn2.setIconSize(QSize(20, 20))
        self.settings_btn2.setFixedSize(35, 30)
        self.settings_btn2.setToolTip("Настройки папок и интервалов")
        self.settings_btn2.clicked.connect(self.open_settings_cmd)
        search_layout.addWidget(self.settings_btn2, alignment=Qt.AlignmentFlag.AlignVCenter)
        
        layout.addLayout(search_layout)
        
        self.tab_widget.addTab(tab, tr_ui("tab_ct_archive"))

    def create_tab_pacs(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(4, 10, 4, 6)
        layout.setSpacing(10)
        
        # Таблица PACS
        self.pacs_table = ToggleTableWidget()
        self.pacs_table.setColumnCount(6)
        self.pacs_table.setHorizontalHeaderLabels([
            "Patient ID", "Patient Name", "Modality", "Slices", "Scanning Area", "Study datetime"
        ])
        self.pacs_table.setColumnHidden(2, True)
        self.pacs_table.horizontalHeader().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.pacs_table.horizontalHeader().customContextMenuRequested.connect(
            lambda pos: self.show_header_context_menu(pos, self.pacs_table)
        )
        self.setup_table_properties(self.pacs_table)
        self.pacs_table.set_placeholder_text("Сканирование сервера PACS не настроено")
        self.pacs_table.update_placeholder_visibility()
        self.restore_table_state(self.pacs_table)
        self.pacs_table.itemSelectionChanged.connect(self.on_pacs_selection_changed)
        layout.addWidget(self.pacs_table)
        
        # Панель управления PACS
        pacs_control_layout = QHBoxLayout()
        pacs_control_layout.setContentsMargins(5, 0, 5, 0)
        pacs_control_layout.setSpacing(10)
        
        self.pacs_today_btn = QPushButton("Today")
        self.pacs_today_btn.setFixedHeight(30)
        self.pacs_today_btn.clicked.connect(self.pacs_set_today)
        
        self.pacs_3days_btn = QPushButton("Last 3 days")
        self.pacs_3days_btn.setFixedHeight(30)
        self.pacs_3days_btn.clicked.connect(self.pacs_set_3days)
        
        self.lbl_from = QLabel("Период с:")
        self.lbl_from.setStyleSheet("color: #ffffff; font-family: 'Segoe UI'; font-size: 13px;")
        
        self.pacs_date_from = CenteredDateEdit()
        self.pacs_date_from.setDisplayFormat("dd.MM.yyyy")
        self.pacs_date_from.setDate(QDate.currentDate())
        self.pacs_date_from.setFixedHeight(30)
        self.pacs_date_from.dateChanged.connect(lambda: self.fill_pacs_list(silent=True))
        
        self.lbl_to = QLabel("по:")
        self.lbl_to.setStyleSheet("color: #ffffff; font-family: 'Segoe UI'; font-size: 13px;")
        
        self.pacs_date_to = CenteredDateEdit()
        self.pacs_date_to.setDisplayFormat("dd.MM.yyyy")
        self.pacs_date_to.setDate(QDate.currentDate())
        self.pacs_date_to.setFixedHeight(30)
        self.pacs_date_to.dateChanged.connect(lambda: self.fill_pacs_list(silent=True))
        
        self.pacs_auto_scan_cb = ToggleSwitch(tr_ui("pacs_standby_mode"))
        self.pacs_auto_scan_cb.setChecked(self.config.get('auto_update_is', 'off').lower() == 'on')
        self.pacs_auto_scan_cb.setToolTip(tr_ui("tooltip_pacs_auto_scan"))
        self.pacs_auto_scan_cb.stateChanged.connect(self.on_pacs_auto_scan_changed)
        
        self.send_to_ct_btn = QPushButton(tr_ui("btn_send_to_ct"))
        self.send_to_ct_btn.setFixedHeight(30)
        self.send_to_ct_btn.setEnabled(False)
        self.send_to_ct_btn.clicked.connect(self.send_to_ct_images_cmd)
        
        # Кнопка настроек (шестеренка)
        self.settings_btn3 = QPushButton()
        self.settings_btn3.setIcon(QIcon(get_resource_path("themes/settings.svg")))
        self.settings_btn3.setIconSize(QSize(20, 20))
        self.settings_btn3.setFixedSize(35, 30)
        self.settings_btn3.setToolTip("Настройки папок и интервалов")
        self.settings_btn3.clicked.connect(self.open_settings_cmd)
        
        # Выпадающий список серверов PACS
        self.lbl_server = QLabel("Сервер:")
        self.lbl_server.setStyleSheet("color: #ffffff; font-family: 'Segoe UI'; font-size: 13px;")
        
        self.pacs_server_combo = QComboBox()
        self.pacs_server_combo.setFixedWidth(160)
        self.pacs_server_combo.setStyleSheet("""
            QComboBox {
                background-color: #2A2A2A;
                border: 1px solid #374151;
                border-radius: 4px;
                color: #FFFFFF;
                padding: 0px 8px;
                font-size: 12px;
                min-height: 28px;
                max-height: 28px;
            }
            QComboBox QAbstractItemView {
                background-color: #1A1A1A;
                border: 1px solid #374151;
                color: #FFFFFF;
                selection-background-color: #3B82F6;
            }
        """)
        self.populate_pacs_server_combo()
        self.pacs_server_combo.currentIndexChanged.connect(self.on_pacs_server_changed)
        
        pacs_control_layout.addWidget(self.pacs_today_btn, alignment=Qt.AlignmentFlag.AlignVCenter)
        pacs_control_layout.addWidget(self.pacs_3days_btn, alignment=Qt.AlignmentFlag.AlignVCenter)
        pacs_control_layout.addWidget(self.lbl_from, alignment=Qt.AlignmentFlag.AlignVCenter)
        pacs_control_layout.addWidget(self.pacs_date_from, alignment=Qt.AlignmentFlag.AlignVCenter)
        pacs_control_layout.addWidget(self.lbl_to, alignment=Qt.AlignmentFlag.AlignVCenter)
        pacs_control_layout.addWidget(self.pacs_date_to, alignment=Qt.AlignmentFlag.AlignVCenter)
        pacs_control_layout.addWidget(self.lbl_server, alignment=Qt.AlignmentFlag.AlignVCenter)
        pacs_control_layout.addWidget(self.pacs_server_combo, alignment=Qt.AlignmentFlag.AlignVCenter)
        pacs_control_layout.addWidget(self.pacs_auto_scan_cb, alignment=Qt.AlignmentFlag.AlignVCenter)
        pacs_control_layout.addStretch(1)
        pacs_control_layout.addWidget(self.send_to_ct_btn, alignment=Qt.AlignmentFlag.AlignVCenter)
        pacs_control_layout.addWidget(self.settings_btn3, alignment=Qt.AlignmentFlag.AlignVCenter)
        
        layout.addLayout(pacs_control_layout)
        
        self.update_pacs_controls_state()
        
        self.tab_widget.addTab(tab, tr_ui("tab_pacs"))

    def setup_table_properties(self, table):
        # Настройка поведения таблиц
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(False)  # Отключаем зебру
        table.setShowGrid(False)  # Отключаем сетку
        table.verticalHeader().setVisible(False)
        
        # Динамическая высота строки в зависимости от размера шрифта
        font_size = self.config.get('patient_font_size', 16)
        row_height = max(25, font_size + 12)
        table.verticalHeader().setDefaultSectionSize(row_height)
        
        # Установка шрифтов через styleSheet, так как глобальный QSS переопределяет setFont()
        weight_map = {
            "Regular": "400",
            "Semibold": "600",
            "Bold": "700"
        }
        weight_str = self.config.get('patient_weight', 'Semibold')
        weight = weight_map.get(weight_str, "400")
        table_style = f"font-size: {font_size}px; font-weight: {weight}; font-family: 'Segoe UI';"
        header_style = """
            QHeaderView::section {
                background-color: #1a1a1a;
                color: #ffffff;
                padding: 6px;
                border: none;
                border-left: 1px solid qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 transparent, stop:0.25 transparent, stop:0.3 #3d3d3d, stop:0.7 #3d3d3d, stop:0.75 transparent, stop:1 transparent);
                font-size: 14px;
                font-weight: normal;
                font-family: 'Segoe UI';
            }
            QHeaderView::section:first {
                border-left: none;
            }
            QHeaderView {
                background-color: #1a1a1a;
                border: none;
            }
        """
        table.setStyleSheet(table_style)
        table.horizontalHeader().setStyleSheet(header_style)
        
        table.horizontalHeader().setSectionsMovable(True)
        table.horizontalHeader().sectionMoved.connect(
            lambda logical, old, new, t=table: self.on_section_moved(logical, old, new, t)
        )
        
        # Растягивание колонок
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(False)
        
        # Установим пропорции ширины по умолчанию
        if table.columnCount() == 8:
            table.setColumnWidth(0, 110)  # ID
            table.setColumnWidth(1, 300)  # Name
            table.setColumnWidth(2, 65)   # Modality
            table.setColumnWidth(3, 65)   # Slices
            table.setColumnWidth(4, 120)  # Scanning Area
            table.setColumnWidth(5, 150)  # Study
            table.setColumnWidth(6, 150)  # Folder
            table.setColumnWidth(7, 45)   # STR
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)  # Имя тянется
        elif table.columnCount() == 6:
            table.setColumnWidth(0, 120)  # ID
            table.setColumnWidth(1, 300)  # Name
            table.setColumnWidth(2, 70)   # Modality
            table.setColumnWidth(3, 65)   # Slices
            table.setColumnWidth(4, 130)  # Scanning Area
            table.setColumnWidth(5, 150)  # Study
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

    def show_header_context_menu(self, pos, table):
        header = table.horizontalHeader()
        menu = QMenu(self)
        menu.setStyleSheet("QMenu { background-color: #1a1a1a; color: #ffffff; border: 1px solid #3d3d3d; } "
                           "QMenu::item:selected { background-color: #2b2b2b; }")
        
        column_count = table.columnCount()
        for i in range(column_count):
            label = table.horizontalHeaderItem(i).text()
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(not table.isColumnHidden(i))
            action.toggled.connect(lambda checked, idx=i, t=table: [t.setColumnHidden(idx, not checked), self.save_table_state(t)])
            
        menu.exec(header.mapToGlobal(pos))

    def on_section_moved(self, logical, old, new, table):
        self.save_table_state(table)

    def save_table_state(self, table):
        table_name = None
        if table == self.images_table:
            table_name = "images_table"
        elif table == self.archive_table:
            table_name = "archive_table"
        elif table == self.pacs_table:
            table_name = "pacs_table"
            
        if not table_name:
            return
            
        header = table.horizontalHeader()
        column_count = table.columnCount()
        
        visual_order = []
        for visual_idx in range(column_count):
            visual_order.append(header.logicalIndex(visual_idx))
            
        visibility = []
        for i in range(column_count):
            visibility.append(not table.isColumnHidden(i))
            
        if 'tables_state' not in self.config:
            self.config['tables_state'] = {}
            
        self.config['tables_state'][table_name] = {
            'visual_order': visual_order,
            'visibility': visibility
        }
        self.save_current_config()

    def restore_table_state(self, table):
        table_name = None
        if table == self.images_table:
            table_name = "images_table"
        elif table == self.archive_table:
            table_name = "archive_table"
        elif table == self.pacs_table:
            table_name = "pacs_table"
            
        if not table_name:
            return
            
        tables_state = self.config.get('tables_state', {})
        state = tables_state.get(table_name)
        if not state:
            return
            
        header = table.horizontalHeader()
        column_count = table.columnCount()
        
        header.blockSignals(True)
        
        # 1. Восстанавливаем порядок
        visual_order = state.get('visual_order')
        if visual_order and len(visual_order) == column_count:
            for visual_idx, logical_idx in enumerate(visual_order):
                current_visual_idx = header.visualIndex(logical_idx)
                if current_visual_idx != visual_idx:
                    header.moveSection(current_visual_idx, visual_idx)
                    
        # 2. Восстанавливаем видимость
        visibility = state.get('visibility')
        if visibility and len(visibility) == column_count:
            for i, visible in enumerate(visibility):
                table.setColumnHidden(i, not visible)
                
        header.blockSignals(False)

    def on_tab_changed(self, index):
        # Защитная проверка на случай срабатывания сигнала до инициализации всех таблиц
        if not hasattr(self, 'archive_table') or not hasattr(self, 'pacs_table') or not hasattr(self, 'images_table'):
            return
            
        master_notif = str(self.config.get('notifications_enabled', 'False')).lower() == 'true'
        pacs_toast = str(self.config.get('pacs_notification_toast_enabled', 'True')).lower() == 'true'
        pacs_sound = str(self.config.get('pacs_notification_sound_enabled', 'False')).lower() == 'true'
        pacs_notify_on = master_notif and (pacs_toast or pacs_sound)
        pacs_auto_scan_on = self.config.get('auto_update_is', 'off').lower() == 'on'
        if index == 0:  # CT images
            if not pacs_notify_on and not pacs_auto_scan_on:
                self.pacs_timer.stop()
            self.show_patient_list()
            QTimer.singleShot(0, self.focus_ct_images_search)
        elif index == 1:  # CT archive
            if not pacs_notify_on and not pacs_auto_scan_on:
                self.pacs_timer.stop()
            self.fill_archive_list()
            QTimer.singleShot(0, self.focus_ct_archive_search)
        elif index == 2:  # PACS
            self.fill_pacs_list()
            # Запускаем таймер PACS только если включено автообновление или фоновые уведомления
            if pacs_auto_scan_on or pacs_notify_on:
                self.pacs_timer.start(self.config.get('pacs_scan_time', 10000))

    def focus_ct_images_search(self):
        if hasattr(self, 'search_images_entry'):
            self.search_images_entry.setFocus()

    def focus_ct_archive_search(self):
        if hasattr(self, 'search_entry'):
            self.search_entry.setFocus()

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(100, self.focus_ct_images_search)

    # ================= ЛОГИКА ТАБЛИЦЫ CT IMAGES =================

    def show_patient_list(self):
        self.start_folder_scan()


    def check_network_folder_retry(self):
        ct_dir = self.config.get('ct_images_dir', '')
        if not ct_dir:
            self.net_retry_timer.stop()
            return
        if os.path.exists(ct_dir):
            self.start_folder_scan()
            self.update_watcher_path()
        else:
            self.start_folder_scan()

    def start_folder_scan(self, show_progress=False):
        if self.scan_worker and self.scan_worker.isRunning():
            return

        ct_dir = self.config.get('ct_images_dir', '')
        if not ct_dir:
            self.net_retry_timer.stop()
            log_message(self.output_field, tr_log("log_invalid_ct_path"))
            self.images_table.setRowCount(0)
            self.images_table.set_placeholder_state(
                tr_ui("placeholder_not_selected_ct"), 
                show_button=True, 
                button_callback=self.browse_ct_images_dir
            )
            self.images_table.update_placeholder_visibility()
            return

        if not os.path.exists(ct_dir):
            if self.net_retry_count < self.net_retry_max:
                self.net_retry_count += 1
                msg = tr_log("log_waiting_network_folder", ct_dir, self.net_retry_count, self.net_retry_max)
                if self.net_retry_count == 1:
                    log_message(self.output_field, msg)
                self.images_table.setRowCount(0)
                self.images_table.set_placeholder_state(
                    msg, 
                    show_button=True, 
                    button_callback=self.browse_ct_images_dir
                )
                self.images_table.update_placeholder_visibility()
                if not self.net_retry_timer.isActive():
                    self.net_retry_timer.start()
                return
            else:
                self.net_retry_timer.stop()
                log_message(self.output_field, tr_log("log_invalid_ct_path"))
                self.images_table.setRowCount(0)
                self.images_table.set_placeholder_state(
                    tr_ui("placeholder_not_selected_ct"), 
                    show_button=True, 
                    button_callback=self.browse_ct_images_dir
                )
                self.images_table.update_placeholder_visibility()
                return
        else:
            if self.net_retry_timer.isActive() or self.net_retry_count > 0:
                self.net_retry_timer.stop()
                self.net_retry_count = 0
                log_message(self.output_field, tr_log("log_network_folder_connected", ct_dir))

        # Запоминаем выделенного пациента
        self.selected_images_patient_id = None
        selected_ranges = self.images_table.selectedRanges()
        if selected_ranges:
            row = selected_ranges[0].topRow()
            id_item = self.images_table.item(row, 0)
            if id_item:
                self.selected_images_patient_id = id_item.data(Qt.ItemDataRole.UserRole)

        cleanup_str_val = self.config.get('cleanup_structures_enabled', 'False')
        fix_id_val = self.config.get('fix_patient_id_enabled', 'False')
        prefixes_val = self.config.get('id_prefixes', 'CT_')
        rename_folder_enabled = self.config.get('rename_study_folder_enabled', 'False')
        rename_folder_mode = self.config.get('rename_study_folder_mode', 'id')
        archive_dir = self.config.get('archive_dir', '')
        archive_enabled = self.config.get('archive_enabled', 'False')
        archive_days = int(self.config.get('archive_days', 3))
        archive_cleanup_enabled = self.config.get('archive_cleanup_enabled', 'False')
        archive_cleanup_days = int(self.config.get('archive_cleanup_days', 30))

        # Если таблица пуста, сразу отображаем статус сканирования
        if self.images_table.rowCount() == 0:
            self.images_table.set_placeholder_state(tr_ui("placeholder_scanning_folder"), show_button=False)
            self.images_table.update_placeholder_visibility()

        self.scan_worker = FolderScanWorker(
            ct_dir, cleanup_str_val, fix_id_val, prefixes_val,
            rename_folder_enabled, rename_folder_mode,
            archive_dir, archive_enabled, archive_days,
            archive_cleanup_enabled, archive_cleanup_days
        )
        self.scan_worker.finished.connect(self.on_folder_scan_finished)
        self.scan_worker.log_emitted.connect(lambda msg: log_message(self.output_field, msg))
        self.scan_worker.status_changed.connect(self.on_scan_status_changed)
        
        if show_progress:
            from ui.loading_dialog import LoadingProgressDialog
            self.scan_progress_dialog = LoadingProgressDialog(self, title="Сканирование")
            self.scan_progress_dialog.label.setText("Подготовка к сканированию DICOM-файлов...")
            self.scan_progress_dialog.progress.setRange(0, 100)
            self.scan_worker.progress.connect(self.scan_progress_dialog.set_scan_progress)
            self.scan_worker.status_changed.connect(self.scan_progress_dialog.label.setText)
            self.scan_worker.finished.connect(self.scan_progress_dialog.accept)
            
            self.scan_worker.start()
            self.scan_progress_dialog.exec()
        else:
            self.scan_worker.start()

    def on_scan_status_changed(self, status_text):
        if self.images_table.rowCount() == 0:
            self.images_table.set_placeholder_state(status_text, show_button=False)
            self.images_table.update_placeholder_visibility()

    def on_folder_scan_finished(self, patient_dict, log_messages):
        pass

        # Собираем существующие ID пациентов для сравнения
        existing_ids = set()
        for r in range(self.images_table.rowCount()):
            id_item = self.images_table.item(r, 0)
            if id_item:
                existing_ids.add(id_item.data(Qt.ItemDataRole.UserRole))

        master_enabled = str(self.config.get('notifications_enabled', 'False')).lower() == 'true'
        ct_toast_on = str(self.config.get('ct_notification_toast_enabled', 'True')).lower() == 'true'
        ct_sound_on = str(self.config.get('ct_notification_sound_enabled', 'False')).lower() == 'true'
        from core.config_utils import get_app_data_dir
        icon_path = os.path.join(get_app_data_dir(), "folder_notification.png")
        if not os.path.exists(icon_path):
            icon_path = get_resource_path("src/folder_notification.png")

        # Проверяем на появление новых файлов до фильтрации
        for patient_id, data in patient_dict.items():
            if 'patient_name' in data and 'study_datetime' in data and 'folder_datetime' in data and 'str' in data:
                if not self.is_first_scan and patient_id not in existing_ids and patient_id not in self.restored_patient_ids:
                    if master_enabled and (ct_toast_on or ct_sound_on):
                        show_notification(
                            str(data['patient_name']), 
                            'Новое КТ', 
                            'short', 
                            icon_path,
                            self.config.get('ct_notification_sound', 'default'),
                            show_toast=ct_toast_on,
                            play_sound=ct_sound_on,
                            duration_setting=self.config.get('ct_toast_duration', self.config.get('toast_duration', '5')),
                            position_setting=self.config.get('ct_toast_position', self.config.get('toast_position', 'bottom_right')),
                            custom_voice_text=self.config.get('ct_voice_text', '')
                        )

        self.images_cache = patient_dict
        # Завершили первое сканирование
        self.is_first_scan = False
        self.restored_patient_ids.clear()

        self.update_images_table_ui()

    def update_images_table_ui(self):
        if not hasattr(self, 'images_cache') or self.images_cache is None:
            return

        self.images_table.setUpdatesEnabled(False)
        self.images_table.blockSignals(True)

        # Запоминаем выделенного пациента
        self.selected_images_patient_id = None
        selected_ranges = self.images_table.selectedRanges()
        if selected_ranges:
            row = selected_ranges[0].topRow()
            id_item = self.images_table.item(row, 0)
            if id_item:
                self.selected_images_patient_id = id_item.data(Qt.ItemDataRole.UserRole)

        self.images_table.setRowCount(0)
        search_text = self.search_images_entry.text().lower()

        # Фильтруем пациентов с корректными DICOM данными и по имени
        valid_patients = {}
        for patient_id, data in self.images_cache.items():
            if 'patient_name' not in data or 'study_datetime' not in data or 'folder_datetime' not in data or 'str' not in data:
                log_message(self.output_field, tr_log("log_skipped_patient_incomplete", patient_id))
                continue
            
            patient_name = str(data.get('patient_name', '')).lower()
            if search_text:
                words = patient_name.replace('^', ' ').split()
                if not (words and words[0].startswith(search_text)):
                    continue
                
            valid_patients[patient_id] = data

        # Группируем исследования по (patient_name, patient_id)
        from collections import defaultdict
        grouped_patients = defaultdict(list)
        for key, data in valid_patients.items():
            p_name = str(data.get('patient_name', 'Unknown'))
            p_id = str(data.get('patient_id', 'Unknown'))
            grouped_patients[(p_name, p_id)].append((key, data))

        # Сортируем исследования внутри каждого пациента по study_datetime (по убыванию)
        for p_info in grouped_patients:
            grouped_patients[p_info].sort(key=lambda x: x[1]['study_datetime'], reverse=True)

        # Сортируем пациентов по дате их самого свежего исследования
        def get_patient_sort_key(item):
            p_info, studies = item
            latest_study = studies[0][1]
            folder_dt = latest_study['folder_datetime']
            patient_name = str(p_info[0]).lower()
            return (-folder_dt.timestamp(), patient_name)

        sorted_grouped_patients = sorted(grouped_patients.items(), key=get_patient_sort_key)

        # Заполняем таблицу
        row_idx = 0
        total_items = 0
        for p_info, studies in sorted_grouped_patients:
            if len(studies) == 1:
                total_items += 1
            else:
                total_items += 1 + len(studies)

        for p_info, studies in sorted_grouped_patients:
            p_name, p_id_val = p_info

            if len(studies) == 1:
                patient_key, data = studies[0]
                self.images_table.insertRow(row_idx)

                id_item = QTableWidgetItem(str(data.get('patient_id', p_id_val)))
                id_item.setData(Qt.ItemDataRole.UserRole, patient_key)
                name_item = QTableWidgetItem(str(data['patient_name']))
                modality_item = QTableWidgetItem(str(data.get('modality', 'CT')))
                slices_item = QTableWidgetItem(str(data.get('slices', 0)))
                area_item = QTableWidgetItem(str(data.get('body_part', '')))
                study_item = QTableWidgetItem(data['study_datetime'].strftime('%d.%m.%y - %H:%M'))
                folder_item = QTableWidgetItem(data['folder_datetime'].strftime('%d.%m.%y - %H:%M'))
                str_item = QTableWidgetItem(str(data['str']))

                for item in [id_item, name_item, modality_item, slices_item, area_item, study_item, folder_item, str_item]:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter)
                    if item in [modality_item, slices_item, area_item, study_item, folder_item, str_item]:
                        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

                color = QColor("#ffffff")
                highlighting_enabled = self.config.get('highlighting_enabled', 'False').lower() == 'true'
                if highlighting_enabled:
                    folder_dt = data['folder_datetime']
                    highlight_new = self.config.get('highlight_new_enabled', 'False').lower() == 'true'
                    highlight_today = self.config.get('highlight_today_enabled', 'False').lower() == 'true'
                    highlight_no_str = self.config.get('highlight_no_str_enabled', 'False').lower() == 'true'
                    highlight_no_slices = self.config.get('highlight_no_slices_enabled', 'False').lower() == 'true'

                    if highlight_new and (datetime.now() - folder_dt).total_seconds() / 3600 < 1:
                        color = QColor("lime")
                    elif highlight_today and folder_dt.date() == datetime.now().date():
                        color = QColor("mediumturquoise")

                    if highlight_no_str and (data['str'] == 0 or data['str'] > 1):
                        color = QColor("crimson")
                    if highlight_no_slices and data.get('slices', 0) == 0:
                        color = QColor("crimson")

                for item in [id_item, name_item, modality_item, slices_item, area_item, study_item, folder_item, str_item]:
                    item.setForeground(color)

                self.images_table.setItem(row_idx, 0, id_item)
                self.images_table.setItem(row_idx, 1, name_item)
                self.images_table.setItem(row_idx, 2, modality_item)
                self.images_table.setItem(row_idx, 3, slices_item)
                self.images_table.setItem(row_idx, 4, area_item)
                self.images_table.setItem(row_idx, 5, study_item)
                self.images_table.setItem(row_idx, 6, folder_item)
                self.images_table.setItem(row_idx, 7, str_item)

                row_idx += 1
            else:
                # Родительская строка
                self.images_table.insertRow(row_idx)

                # Родительская строка содержит имя и ID, остальные ячейки пустые
                id_item = QTableWidgetItem(p_id_val)
                # Сохраняем в UserRole родительскую папку
                parent_key = os.path.dirname(studies[0][0])
                id_item.setData(Qt.ItemDataRole.UserRole, parent_key)

                name_item = QTableWidgetItem(p_name)
                modality_item = QTableWidgetItem("")
                slices_item = QTableWidgetItem("")
                area_item = QTableWidgetItem("")
                study_item = QTableWidgetItem("")
                folder_item = QTableWidgetItem("")
                str_item = QTableWidgetItem("")

                for item in [id_item, name_item, modality_item, slices_item, area_item, study_item, folder_item, str_item]:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter)
                    if item in [modality_item, slices_item, area_item, study_item, folder_item, str_item]:
                        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    item.setForeground(QColor("#ffffff"))

                self.images_table.setItem(row_idx, 0, id_item)
                self.images_table.setItem(row_idx, 1, name_item)
                self.images_table.setItem(row_idx, 2, modality_item)
                self.images_table.setItem(row_idx, 3, slices_item)
                self.images_table.setItem(row_idx, 4, area_item)
                self.images_table.setItem(row_idx, 5, study_item)
                self.images_table.setItem(row_idx, 6, folder_item)
                self.images_table.setItem(row_idx, 7, str_item)

                row_idx += 1

                # Дочерние строки
                for patient_key, data in studies:
                    self.images_table.insertRow(row_idx)

                    id_child = QTableWidgetItem("")
                    id_child.setData(Qt.ItemDataRole.UserRole, patient_key)
                    name_child = QTableWidgetItem("  ↳")
                    modality_child = QTableWidgetItem(str(data.get('modality', 'CT')))
                    slices_child = QTableWidgetItem(str(data.get('slices', 0)))
                    area_child = QTableWidgetItem(str(data.get('body_part', '')))
                    study_child = QTableWidgetItem(data['study_datetime'].strftime('%d.%m.%y - %H:%M'))
                    folder_child = QTableWidgetItem(data['folder_datetime'].strftime('%d.%m.%y - %H:%M'))
                    str_child = QTableWidgetItem(str(data['str']))

                    for item in [id_child, name_child, modality_child, slices_child, area_child, study_child, folder_child, str_child]:
                        item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter)
                        if item in [modality_child, slices_child, area_child, study_child, folder_child, str_child]:
                            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

                    color = QColor("#ffffff")
                    highlighting_enabled = self.config.get('highlighting_enabled', 'False').lower() == 'true'
                    if highlighting_enabled:
                        folder_dt = data['folder_datetime']
                        highlight_new = self.config.get('highlight_new_enabled', 'False').lower() == 'true'
                        highlight_today = self.config.get('highlight_today_enabled', 'False').lower() == 'true'
                        highlight_no_str = self.config.get('highlight_no_str_enabled', 'False').lower() == 'true'
                        highlight_no_slices = self.config.get('highlight_no_slices_enabled', 'False').lower() == 'true'

                        if highlight_new and (datetime.now() - folder_dt).total_seconds() / 3600 < 1:
                            color = QColor("lime")
                        elif highlight_today and folder_dt.date() == datetime.now().date():
                            color = QColor("mediumturquoise")

                        if highlight_no_str and (data['str'] == 0 or data['str'] > 1):
                            color = QColor("crimson")
                        if highlight_no_slices and data.get('slices', 0) == 0:
                            color = QColor("crimson")

                    for item in [id_child, name_child, modality_child, slices_child, area_child, study_child, folder_child, str_child]:
                        item.setForeground(color)

                    self.images_table.setItem(row_idx, 0, id_child)
                    self.images_table.setItem(row_idx, 1, name_child)
                    self.images_table.setItem(row_idx, 2, modality_child)
                    self.images_table.setItem(row_idx, 3, slices_child)
                    self.images_table.setItem(row_idx, 4, area_child)
                    self.images_table.setItem(row_idx, 5, study_child)
                    self.images_table.setItem(row_idx, 6, folder_child)
                    self.images_table.setItem(row_idx, 7, str_child)

                    row_idx += 1

        # Восстанавливаем выделение
        if hasattr(self, 'selected_images_patient_id') and self.selected_images_patient_id:
            for r in range(self.images_table.rowCount()):
                id_item = self.images_table.item(r, 0)
                if id_item and id_item.data(Qt.ItemDataRole.UserRole) == self.selected_images_patient_id:
                    self.images_table.selectRow(r)
                    break

        self.images_table.set_placeholder_state("В этой папке нет исследований", show_button=False)
        self.images_table.update_placeholder_visibility()
        self.images_table.blockSignals(False)
        self.images_table.setUpdatesEnabled(True)

    def search_patient_images(self):
        if not hasattr(self, 'images_cache') or self.images_cache is None:
            self.start_folder_scan()
        else:
            self.update_images_table_ui()

    def open_current_folder_cmd(self, row, column):
        id_item = self.images_table.item(row, 0)
        patient_id = id_item.data(Qt.ItemDataRole.UserRole) if id_item else ""
        path = os.path.join(self.config.get('ct_images_dir', ''), patient_id)
        if os.path.exists(path):
            try:
                os.startfile(path)
            except Exception as e:
                log_message(self.output_field, tr_log("log_failed_open_folder", patient_id, e))

    # ================= КОНТЕКСТНЫЕ МЕНЮ И ДЕЙСТВИЯ =================

    def show_tab_context_menu(self, pos):
        index = self.tab_widget.tabBar().tabAt(pos)
        if index < 0:
            return
            
        menu = QMenu(self)
        
        # Только для вкладок КТ-снимки (0) и Архив (1) есть пункт "Открыть папку"
        if index in (0, 1):
            open_folder_action = QAction(tr_ui("ctx_open_folder"), self)
            path = self.config.get('ct_images_dir', '') if index == 0 else self.config.get('archive_dir', '')
            
            def open_dir():
                if path and os.path.exists(path):
                    try:
                        os.startfile(path)
                    except Exception as e:
                        log_message(self.output_field, tr_log("log_failed_open_folder", os.path.basename(path), e))
            
            open_folder_action.triggered.connect(open_dir)
            if not path or not os.path.exists(path):
                open_folder_action.setEnabled(False)
            menu.addAction(open_folder_action)
            
        # Для всех вкладок есть пункт "Переименовать"
        rename_action = QAction(tr_ui("ctx_rename"), self)
        rename_action.triggered.connect(lambda: self.rename_tab_dialog(index))
        menu.addAction(rename_action)
        
        menu.exec(self.tab_widget.tabBar().mapToGlobal(pos))

    def rename_tab_dialog(self, index):
        from PyQt6.QtWidgets import QInputDialog
        
        current_name = self.tab_widget.tabText(index)
        
        dialog = QInputDialog(self)
        dialog.setWindowTitle(tr_ui("dlg_rename_tab_title"))
        dialog.setLabelText(tr_ui("dlg_rename_tab_label", current_name))
        dialog.setTextValue(current_name)
        dialog.setInputMode(QInputDialog.InputMode.TextInput)
        dialog.setStyleSheet(self.styleSheet())
        apply_dark_title_bar(dialog)
        
        ok = dialog.exec()
        new_name = dialog.textValue()
        
        if ok and new_name.strip():
            new_name = new_name.strip()
            self.tab_widget.setTabText(index, new_name)
            
            # Сохраняем в конфиг
            config_keys = {0: 'custom_tab_name_ct', 1: 'custom_tab_name_archive', 2: 'custom_tab_name_pacs'}
            key = config_keys.get(index)
            if key:
                self.config[key] = new_name
                self.save_current_config()

    def show_images_context_menu(self, pos):
        # Получаем строку под курсором
        index = self.images_table.indexAt(pos)
        if not index.isValid():
            return
            
        row = index.row()
        id_item = self.images_table.item(row, 0)
        patient_id = id_item.data(Qt.ItemDataRole.UserRole) if id_item else ""
        patient_name = self.images_table.item(row, 1).text()
        
        if patient_id in self.active_file_operations:
            return
            
        menu = QMenu(self)
        
        open_folder_action = QAction(tr_ui("ctx_open_folder"), self)
        open_folder_action.triggered.connect(lambda: self.open_patient_folder(patient_id, is_archive=False))
        
        delete_action = QAction(tr_ui("ctx_delete_patient"), self)
        delete_action.triggered.connect(lambda: self.delete_patient_action(patient_id, patient_name))
        
        archive_action = QAction(tr_ui("ctx_move_to_archive"), self)
        archive_action.triggered.connect(lambda: self.archive_patient_action(patient_id, patient_name))
        
        clean_str_action = QAction(tr_ui("ctx_delete_str"), self)
        clean_str_action.triggered.connect(lambda: self.clean_str_action(patient_id))
        
        menu.addAction(open_folder_action)
        menu.addAction(delete_action)
        menu.addAction(archive_action)
        menu.addAction(clean_str_action)
        
        menu.exec(self.images_table.viewport().mapToGlobal(pos))

    def delete_patient_action(self, patient_id, patient_name):
        if patient_id in self.active_file_operations:
            return
            
        folder_name = self.images_cache[patient_id].get('folder_name', patient_id) if (self.images_cache and patient_id in self.images_cache) else patient_id
        path = os.path.join(self.config.get('ct_images_dir', ''), folder_name)
        if not os.path.exists(path):
            log_message(self.output_field, tr_log("log_path_not_exist", path))
            return

        _dlg = QMessageBox(self)
        _dlg.setIcon(QMessageBox.Icon.Question)
        _dlg.setWindowTitle(tr_ui("dlg_confirm_delete_title"))
        _dlg.setText(tr_ui("dlg_confirm_delete_msg", patient_name, patient_id))
        _dlg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        _dlg.setDefaultButton(QMessageBox.StandardButton.No)
        apply_dark_title_bar(_dlg)
        reply = _dlg.exec()
        
        if reply == QMessageBox.StandardButton.Yes:
            self.active_file_operations[patient_id] = {'op': 'delete'}
            self.images_table.viewport().update()
            
            def run_delete():
                shutil.rmtree(path)
                return self.get_folder_desc(patient_id, patient_name)
                
            worker = BackgroundFileWorker(patient_id, 'delete', run_delete)
            worker.finished.connect(self.on_background_action_finished)
            worker.error.connect(self.on_background_action_error)
            op_key = f"worker_{patient_id}"
            setattr(self, op_key, worker)
            worker.start()

    def archive_patient_action(self, patient_id, patient_name=None):
        if patient_id in self.active_file_operations:
            return
            
        folder_name = self.images_cache[patient_id].get('folder_name', patient_id) if (self.images_cache and patient_id in self.images_cache) else patient_id
        path = os.path.join(self.config.get('ct_images_dir', ''), folder_name)
        archive_dir = self.config.get('archive_dir', '')
        
        if not os.path.exists(path):
            log_message(self.output_field, tr_log("log_path_not_exist", path))
            return
            
        if not os.path.exists(archive_dir):
            os.makedirs(archive_dir, exist_ok=True)

        dest_path = os.path.join(archive_dir, folder_name)
        dest_parent = os.path.dirname(dest_path)
        if dest_parent:
            os.makedirs(dest_parent, exist_ok=True)

        self.active_file_operations[patient_id] = {'op': 'archive'}
        self.images_table.viewport().update()
        
        def run_archive():
            if os.path.exists(dest_path):
                shutil.rmtree(dest_path)
            shutil.move(path, dest_path)
            return self.get_folder_desc(patient_id, patient_name)
            
        worker = BackgroundFileWorker(patient_id, 'archive', run_archive)
        worker.finished.connect(self.on_background_action_finished)
        worker.error.connect(self.on_background_action_error)
        op_key = f"worker_{patient_id}"
        setattr(self, op_key, worker)
        worker.start()

    def clean_str_action(self, patient_id):
        if patient_id in self.active_file_operations:
            return
            
        folder_name = self.images_cache[patient_id].get('folder_name', patient_id) if (self.images_cache and patient_id in self.images_cache) else patient_id
        path = os.path.join(self.config.get('ct_images_dir', ''), folder_name)
        if os.path.exists(path):
            self.active_file_operations[patient_id] = {'op': 'clean_str'}
            self.images_table.viewport().update()
            
            def run_clean():
                deleted = delete_redundant_str(path, None)
                patient_name = ""
                if self.images_cache and patient_id in self.images_cache:
                    patient_name = self.images_cache[patient_id].get('patient_name', '')
                folder_desc = self.get_folder_desc(patient_id, patient_name)
                return deleted, folder_desc
                
            worker = BackgroundFileWorker(patient_id, 'clean_str', run_clean)
            worker.finished.connect(self.on_background_action_finished)
            worker.error.connect(self.on_background_action_error)
            op_key = f"worker_{patient_id}"
            setattr(self, op_key, worker)
            worker.start()

    def on_images_selection_changed(self):
        has_selection = len(self.images_table.selectedRanges()) > 0
        self.move_to_archive_btn.setEnabled(has_selection)

    def on_archive_selection_changed(self):
        has_selection = len(self.archive_table.selectedRanges()) > 0
        self.move_from_archive_btn.setEnabled(has_selection)

    def move_to_archive_cmd(self):
        selected_ranges = self.images_table.selectedRanges()
        if not selected_ranges:
            return
            
        row = selected_ranges[0].topRow()
        id_item = self.images_table.item(row, 0)
        patient_id = id_item.data(Qt.ItemDataRole.UserRole) if id_item else ""
        patient_name = self.images_table.item(row, 1).text()
        self.archive_patient_action(patient_id, patient_name)

    # ================= ЛОГИКА ТАБЛИЦЫ CT ARCHIVE =================

    def fill_archive_list(self, silent=False, show_progress=False):
        if self.archive_worker and self.archive_worker.isRunning():
            return

        archive_dir = self.config.get('archive_dir', '')
        if not archive_dir or not os.path.exists(archive_dir):
            if not silent:
                log_message(self.output_field, tr_log("log_archive_dir_not_exist"))
            self.archive_table.setRowCount(0)
            self.archive_table.set_placeholder_state(
                tr_ui("placeholder_not_selected_ct"),
                show_button=True,
                button_callback=self.browse_archive_dir
            )
            self.archive_table.update_placeholder_visibility()
            return
            
        if not silent:
            log_message(self.output_field, tr_log("log_loading_archive"))

        # Запоминаем выделенного пациента
        self.selected_archive_patient_id = None
        selected_ranges = self.archive_table.selectedRanges()
        if selected_ranges:
            row = selected_ranges[0].topRow()
            id_item = self.archive_table.item(row, 0)
            if id_item:
                self.selected_archive_patient_id = id_item.data(Qt.ItemDataRole.UserRole)

        cleanup_str_val = self.config.get('cleanup_structures_enabled', 'False')
        self.archive_worker = ArchiveScanWorker(archive_dir, cleanup_str_val)
        self.archive_worker.finished.connect(lambda ad, lm: self.on_archive_scan_finished(ad, lm, silent))
        if not silent:
            self.archive_worker.log_emitted.connect(lambda msg: log_message(self.output_field, msg))
        
        if show_progress:
            from ui.loading_dialog import LoadingProgressDialog
            self.archive_progress_dialog = LoadingProgressDialog(self, title="Сканирование")
            self.archive_progress_dialog.label.setText("Подготовка к сканированию файлов архива...")
            self.archive_progress_dialog.progress.setRange(0, 100)
            self.archive_worker.progress.connect(self.archive_progress_dialog.set_scan_progress)
            self.archive_worker.finished.connect(self.archive_progress_dialog.accept)
            
            self.archive_worker.start()
            self.archive_progress_dialog.exec()
        else:
            self.archive_worker.start()

    def on_archive_scan_finished(self, archive_dict, log_messages, silent=False):
        if not silent:
            log_message(self.output_field, tr_log("log_archive_loaded"), replace_suffix=tr_log("log_loading_archive"))
        self.archive_cache = archive_dict
        self.update_archive_table_ui()

    def update_archive_table_ui(self):
        if not hasattr(self, 'archive_cache') or self.archive_cache is None:
            return

        self.archive_table.setUpdatesEnabled(False)
        self.archive_table.blockSignals(True)

        # Запоминаем выделенного пациента
        self.selected_archive_patient_id = None
        selected_ranges = self.archive_table.selectedRanges()
        if selected_ranges:
            row = selected_ranges[0].topRow()
            id_item = self.archive_table.item(row, 0)
            if id_item:
                self.selected_archive_patient_id = id_item.data(Qt.ItemDataRole.UserRole)

        self.archive_table.setRowCount(0)
        search_text = self.search_entry.text().lower()
        slice_limit = self.config.get('archive_slice', 0)

        # Фильтруем пациентов с корректными DICOM данными и по имени
        valid_items = {}
        for patient_id, data in self.archive_cache.items():
            if 'patient_name' not in data or 'study_datetime' not in data or 'folder_datetime' not in data or 'str' not in data:
                log_message(self.output_field, tr_log("log_skipped_archive_patient", patient_id))
                continue
            
            patient_name = str(data.get('patient_name', '')).lower()
            if search_text:
                words = patient_name.replace('^', ' ').split()
                if not (words and words[0].startswith(search_text)):
                    continue
                
            valid_items[patient_id] = data

        # Сортируем и применяем лимит
        sorted_raw = sorted(valid_items.items(), key=lambda x: x[1]['folder_datetime'], reverse=True)
        if slice_limit > 0:
            sorted_raw = sorted_raw[:slice_limit]

        # Группируем по (patient_name, patient_id)
        from collections import defaultdict
        grouped_patients = defaultdict(list)
        for key, data in sorted_raw:
            p_name = str(data.get('patient_name', 'Unknown'))
            p_id = str(data.get('patient_id', 'Unknown'))
            grouped_patients[(p_name, p_id)].append((key, data))

        # Сортируем исследования внутри каждого пациента по study_datetime (по убыванию)
        for p_info in grouped_patients:
            grouped_patients[p_info].sort(key=lambda x: x[1]['study_datetime'], reverse=True)

        # Сортируем пациентов по дате их самого свежего исследования
        def get_patient_sort_key(item):
            p_info, studies = item
            latest_study = studies[0][1]
            folder_dt = latest_study['folder_datetime']
            patient_name = str(p_info[0]).lower()
            return (-folder_dt.timestamp(), patient_name)

        sorted_grouped_patients = sorted(grouped_patients.items(), key=get_patient_sort_key)

        # Заполняем таблицу
        row_idx = 0
        total_items = 0
        for p_info, studies in sorted_grouped_patients:
            if len(studies) == 1:
                total_items += 1
            else:
                total_items += 1 + len(studies)

        for p_info, studies in sorted_grouped_patients:
            p_name, p_id_val = p_info

            if len(studies) == 1:
                patient_key, data = studies[0]
                self.archive_table.insertRow(row_idx)

                id_item = QTableWidgetItem(str(data.get('patient_id', p_id_val)))
                id_item.setData(Qt.ItemDataRole.UserRole, patient_key)
                name_item = QTableWidgetItem(str(data['patient_name']))
                modality_item = QTableWidgetItem(str(data.get('modality', 'CT')))
                slices_item = QTableWidgetItem(str(data.get('slices', 0)))
                area_item = QTableWidgetItem(str(data.get('body_part', '')))
                study_item = QTableWidgetItem(data['study_datetime'].strftime('%d.%m.%y - %H:%M'))
                folder_item = QTableWidgetItem(data['folder_datetime'].strftime('%d.%m.%y - %H:%M'))
                str_item = QTableWidgetItem(str(data['str']))

                for item in [id_item, name_item, modality_item, slices_item, area_item, study_item, folder_item, str_item]:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter)
                    if item in [modality_item, slices_item, area_item, study_item, folder_item, str_item]:
                        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

                color = QColor("#ffffff")
                highlighting_enabled = self.config.get('highlighting_enabled', 'False').lower() == 'true'
                if highlighting_enabled:
                    folder_dt = data['folder_datetime']
                    highlight_new = self.config.get('highlight_new_enabled', 'False').lower() == 'true'
                    highlight_today = self.config.get('highlight_today_enabled', 'False').lower() == 'true'
                    highlight_no_str = self.config.get('highlight_no_str_enabled', 'False').lower() == 'true'
                    highlight_no_slices = self.config.get('highlight_no_slices_enabled', 'False').lower() == 'true'

                    if highlight_new and (datetime.now() - folder_dt).total_seconds() / 3600 < 1:
                        color = QColor("lime")
                    elif highlight_today and folder_dt.date() == datetime.now().date():
                        color = QColor("mediumturquoise")

                    if highlight_no_str and (data['str'] == 0 or data['str'] > 1):
                        color = QColor("crimson")
                    if highlight_no_slices and data.get('slices', 0) == 0:
                        color = QColor("crimson")

                for item in [id_item, name_item, modality_item, slices_item, area_item, study_item, folder_item, str_item]:
                    item.setForeground(color)

                self.archive_table.setItem(row_idx, 0, id_item)
                self.archive_table.setItem(row_idx, 1, name_item)
                self.archive_table.setItem(row_idx, 2, modality_item)
                self.archive_table.setItem(row_idx, 3, slices_item)
                self.archive_table.setItem(row_idx, 4, area_item)
                self.archive_table.setItem(row_idx, 5, study_item)
                self.archive_table.setItem(row_idx, 6, folder_item)
                self.archive_table.setItem(row_idx, 7, str_item)

                row_idx += 1
            else:
                # Родительская строка
                self.archive_table.insertRow(row_idx)

                id_item = QTableWidgetItem(p_id_val)
                parent_key = os.path.dirname(studies[0][0])
                id_item.setData(Qt.ItemDataRole.UserRole, parent_key)

                name_item = QTableWidgetItem(p_name)
                modality_item = QTableWidgetItem("")
                slices_item = QTableWidgetItem("")
                area_item = QTableWidgetItem("")
                study_item = QTableWidgetItem("")
                folder_item = QTableWidgetItem("")
                str_item = QTableWidgetItem("")

                for item in [id_item, name_item, modality_item, slices_item, area_item, study_item, folder_item, str_item]:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter)
                    if item in [modality_item, slices_item, area_item, study_item, folder_item, str_item]:
                        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    item.setForeground(QColor("#ffffff"))

                self.archive_table.setItem(row_idx, 0, id_item)
                self.archive_table.setItem(row_idx, 1, name_item)
                self.archive_table.setItem(row_idx, 2, modality_item)
                self.archive_table.setItem(row_idx, 3, slices_item)
                self.archive_table.setItem(row_idx, 4, area_item)
                self.archive_table.setItem(row_idx, 5, study_item)
                self.archive_table.setItem(row_idx, 6, folder_item)
                self.archive_table.setItem(row_idx, 7, str_item)

                row_idx += 1

                # Дочерние строки
                for patient_key, data in studies:
                    self.archive_table.insertRow(row_idx)

                    id_child = QTableWidgetItem("")
                    id_child.setData(Qt.ItemDataRole.UserRole, patient_key)
                    name_child = QTableWidgetItem("  ↳")
                    modality_child = QTableWidgetItem(str(data.get('modality', 'CT')))
                    slices_child = QTableWidgetItem(str(data.get('slices', 0)))
                    area_child = QTableWidgetItem(str(data.get('body_part', '')))
                    study_child = QTableWidgetItem(data['study_datetime'].strftime('%d.%m.%y - %H:%M'))
                    folder_child = QTableWidgetItem(data['folder_datetime'].strftime('%d.%m.%y - %H:%M'))
                    str_child = QTableWidgetItem(str(data['str']))

                    for item in [id_child, name_child, modality_child, slices_child, area_child, study_child, folder_child, str_child]:
                        item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter)
                        if item in [modality_child, slices_child, area_child, study_child, folder_child, str_child]:
                            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

                    color = QColor("#ffffff")
                    highlighting_enabled = self.config.get('highlighting_enabled', 'False').lower() == 'true'
                    if highlighting_enabled:
                        folder_dt = data['folder_datetime']
                        highlight_new = self.config.get('highlight_new_enabled', 'False').lower() == 'true'
                        highlight_today = self.config.get('highlight_today_enabled', 'False').lower() == 'true'
                        highlight_no_str = self.config.get('highlight_no_str_enabled', 'False').lower() == 'true'
                        highlight_no_slices = self.config.get('highlight_no_slices_enabled', 'False').lower() == 'true'

                        if highlight_new and (datetime.now() - folder_dt).total_seconds() / 3600 < 1:
                            color = QColor("lime")
                        elif highlight_today and folder_dt.date() == datetime.now().date():
                            color = QColor("mediumturquoise")

                        if highlight_no_str and (data['str'] == 0 or data['str'] > 1):
                            color = QColor("crimson")
                        if highlight_no_slices and data.get('slices', 0) == 0:
                            color = QColor("crimson")

                    for item in [id_child, name_child, modality_child, slices_child, area_child, study_child, folder_child, str_child]:
                        item.setForeground(color)

                    self.archive_table.setItem(row_idx, 0, id_child)
                    self.archive_table.setItem(row_idx, 1, name_child)
                    self.archive_table.setItem(row_idx, 2, modality_child)
                    self.archive_table.setItem(row_idx, 3, slices_child)
                    self.archive_table.setItem(row_idx, 4, area_child)
                    self.archive_table.setItem(row_idx, 5, study_child)
                    self.archive_table.setItem(row_idx, 6, folder_child)
                    self.archive_table.setItem(row_idx, 7, str_child)

                    row_idx += 1

        # Восстанавливаем выделение
        if hasattr(self, 'selected_archive_patient_id') and self.selected_archive_patient_id:
            for r in range(self.archive_table.rowCount()):
                id_item = self.archive_table.item(r, 0)
                if id_item and id_item.data(Qt.ItemDataRole.UserRole) == self.selected_archive_patient_id:
                    self.archive_table.selectRow(r)
                    break

        self.archive_table.set_placeholder_state(tr_ui("placeholder_no_studies_in_folder"), show_button=False)
        self.archive_table.update_placeholder_visibility()
        self.archive_table.blockSignals(False)
        self.archive_table.setUpdatesEnabled(True)

    def search_patient_archive(self):
        if not hasattr(self, 'archive_cache') or self.archive_cache is None:
            self.fill_archive_list()
            return
        self.update_archive_table_ui()

    def show_archive_context_menu(self, pos):
        index = self.archive_table.indexAt(pos)
        if not index.isValid():
            return
            
        row = index.row()
        id_item = self.archive_table.item(row, 0)
        patient_id = id_item.data(Qt.ItemDataRole.UserRole) if id_item else ""
        patient_name = self.archive_table.item(row, 1).text()
        
        if patient_id in self.active_file_operations:
            return
            
        menu = QMenu(self)
        
        open_folder_action = QAction(tr_ui("ctx_open_folder"), self)
        open_folder_action.triggered.connect(lambda: self.open_patient_folder(patient_id, is_archive=True))
        
        restore_action = QAction(tr_ui("ctx_restore_from_archive"), self)
        restore_action.triggered.connect(self.move_from_archive_cmd)
        
        delete_action = QAction(tr_ui("ctx_delete_archive_patient"), self)
        delete_action.triggered.connect(lambda: self.delete_archive_patient_action(patient_id, patient_name))
        
        menu.addAction(open_folder_action)
        menu.addAction(restore_action)
        menu.addAction(delete_action)
        
        menu.exec(self.archive_table.viewport().mapToGlobal(pos))

    def delete_archive_patient_action(self, patient_id, patient_name):
        if patient_id in self.active_file_operations:
            return
            
        folder_name = self.archive_cache[patient_id].get('folder_name', patient_id) if (self.archive_cache and patient_id in self.archive_cache) else patient_id
        path = os.path.join(self.config.get('archive_dir', ''), folder_name)
        if not os.path.exists(path):
            log_message(self.output_field, tr_log("log_path_not_exist", path))
            return

        _dlg = QMessageBox(self)
        _dlg.setIcon(QMessageBox.Icon.Question)
        _dlg.setWindowTitle(tr_ui("dlg_confirm_delete_title"))
        _dlg.setText(tr_ui("dlg_confirm_delete_archive_msg", patient_name, patient_id))
        _dlg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        _dlg.setDefaultButton(QMessageBox.StandardButton.No)
        apply_dark_title_bar(_dlg)
        reply = _dlg.exec()
        
        if reply == QMessageBox.StandardButton.Yes:
            self.active_file_operations[patient_id] = {'op': 'delete'}
            self.archive_table.viewport().update()
            
            def run_delete():
                shutil.rmtree(path)
                return self.get_folder_desc(patient_id, patient_name)
                
            worker = BackgroundFileWorker(patient_id, 'delete', run_delete)
            worker.finished.connect(self.on_background_action_finished)
            worker.error.connect(self.on_background_action_error)
            op_key = f"worker_{patient_id}"
            setattr(self, op_key, worker)
            worker.start()

    def move_from_archive_cmd(self):
        selected_ranges = self.archive_table.selectedRanges()
        if not selected_ranges:
            return
            
        row = selected_ranges[0].topRow()
        id_item = self.archive_table.item(row, 0)
        patient_id = id_item.data(Qt.ItemDataRole.UserRole) if id_item else ""
        patient_name = self.archive_table.item(row, 1).text()
        
        if patient_id in self.active_file_operations:
            return
            
        archive_dir = self.config.get('archive_dir', '')
        ct_images_dir = self.config.get('ct_images_dir', '')
        
        folder_name = self.archive_cache[patient_id].get('folder_name', patient_id) if (self.archive_cache and patient_id in self.archive_cache) else patient_id
        path = os.path.join(archive_dir, folder_name)
        if not os.path.exists(path):
            log_message(self.output_field, tr_log("log_patient_not_found_in_archive", patient_id, patient_name))
            return
            
        dest_path = os.path.join(ct_images_dir, folder_name)
        dest_parent = os.path.dirname(dest_path)
        if dest_parent:
            os.makedirs(dest_parent, exist_ok=True)
            
        self.active_file_operations[patient_id] = {'op': 'restore'}
        self.archive_table.viewport().update()
        
        def run_restore():
            if os.path.exists(dest_path):
                shutil.rmtree(dest_path)
            shutil.copytree(path, dest_path)
            shutil.rmtree(path)
            return self.get_folder_desc(patient_id, patient_name)
            
        worker = BackgroundFileWorker(patient_id, 'restore', run_restore)
        worker.finished.connect(self.on_background_action_finished)
        worker.error.connect(self.on_background_action_error)
        op_key = f"worker_{patient_id}"
        setattr(self, op_key, worker)
        worker.start()

    # ================= ЛОГИКА ТАБЛИЦЫ PACS =================

    def fill_pacs_list(self, silent=False):
        self.start_pacs_scan(silent=silent)

    def auto_update_pacs(self):
        self.start_pacs_scan(silent=True)

    def start_pacs_scan(self, silent=False):
        if self.pacs_worker and self.pacs_worker.isRunning():
            if not silent:
                # If we manually request a scan (non-silent), disconnect the previous worker's finished signal
                # so we can start a new scan immediately with the chosen date range without waiting for the background one.
                try:
                    self.pacs_worker.finished.disconnect()
                except TypeError:
                    pass
            else:
                return

        if not silent:
            log_message(self.output_field, tr_log("log_connecting_pacs"))
            self.pacs_table.setRowCount(0)
            self.pacs_table.set_placeholder_text(tr_ui("placeholder_scanning"))
            self.pacs_table.update_placeholder_visibility()
            self.previous_pacs_data = {}

        self.selected_pacs_patient_id = None
        selected_ranges = self.pacs_table.selectedRanges()
        if selected_ranges:
            row = selected_ranges[0].topRow()
            id_item = self.pacs_table.item(row, 0)
            if id_item:
                self.selected_pacs_patient_id = id_item.text()

        pacs_ip = self.config.get('pacs_ip', '127.0.0.1')
        pacs_port = int(self.config.get('pacs_port', 11112))
        called_aet = self.config.get('pacs_called_aet', 'ANY-SCP')
        calling_aet = self.config.get('pacs_calling_aet', 'ECHOSCU')

        auto_update_on = self.config.get('auto_update_is', 'off').lower() == 'on'
        if auto_update_on and hasattr(self, 'pacs_date_from') and hasattr(self, 'pacs_date_to'):
            self.pacs_date_from.blockSignals(True)
            self.pacs_date_to.blockSignals(True)
            self.pacs_date_from.setDate(QDate.currentDate())
            self.pacs_date_to.setDate(QDate.currentDate())
            self.pacs_date_from.blockSignals(False)
            self.pacs_date_to.blockSignals(False)

        study_date = None
        if hasattr(self, 'pacs_date_from') and hasattr(self, 'pacs_date_to'):
            date_from_str = self.pacs_date_from.date().toString("yyyyMMdd")
            date_to_str = self.pacs_date_to.date().toString("yyyyMMdd")
            if date_from_str == date_to_str:
                study_date = date_from_str
            else:
                study_date = f"{date_from_str}-{date_to_str}"

        self.pacs_worker = PacsScanWorker(pacs_ip, pacs_port, called_aet, calling_aet, study_date)
        self.pacs_worker.finished.connect(lambda pd, c, lm: self.on_pacs_scan_finished(pd, c, lm, silent))
        self.pacs_worker.start()

    def on_pacs_scan_finished(self, pacs_dict, con, log_messages, silent=False):
        auto_update_on = self.config.get('auto_update_is', 'off').lower() == 'on'
        if con:
            if auto_update_on:
                self.pacs_table.set_placeholder_text(tr_ui("placeholder_standby"))
            else:
                self.pacs_table.set_placeholder_text(tr_ui("placeholder_no_studies"))
        else:
            has_abort = any("сброшено сервером" in m or "aborted" in m for m in log_messages)
            if has_abort:
                self.pacs_table.set_placeholder_text(tr_ui("placeholder_pacs_access_denied"))
            else:
                self.pacs_table.set_placeholder_text(tr_ui("placeholder_not_configured"))
            self.pacs_table.setRowCount(0)
            self.pacs_table.update_placeholder_visibility()
            self.previous_pacs_data = {}
            self.standby_new_patients = {}
            
        has_fail_msg = False
        for msg in log_messages:
            if "подключиться к серверу PACS" in msg or "Failed to connect" in msg:
                if not silent:
                    log_message(self.output_field, msg, replace_suffix=tr_log("log_connecting_pacs"))
                has_fail_msg = True
            else:
                if not silent:
                    log_message(self.output_field, msg)

        if con:
            if not silent:
                log_message(self.output_field, tr_log("log_connected_pacs"), replace_suffix=tr_log("log_connecting_pacs"))
            
            # Фоновое уведомление о новых КТ в PACS
            master_enabled = str(self.config.get('notifications_enabled', 'False')).lower() == 'true'
            pacs_toast_on = str(self.config.get('pacs_notification_toast_enabled', 'True')).lower() == 'true'
            pacs_sound_on = str(self.config.get('pacs_notification_sound_enabled', 'False')).lower() == 'true'
            auto_update_on = self.config.get('auto_update_is', 'off').lower() == 'on'
            
            from core.config_utils import get_app_data_dir
            icon_blue_path = os.path.join(get_app_data_dir(), "pacs_notification.png")
            if not os.path.exists(icon_blue_path):
                icon_blue_path = get_resource_path("src/pacs_notification.png")

            if auto_update_on:
                today_date = datetime.now().date()
                today_pacs_dict = {}
                for patient_id, data in pacs_dict.items():
                    s_dt = data.get('study_datetime_obj')
                    if s_dt and s_dt.date() == today_date:
                        today_pacs_dict[patient_id] = data

                if self.is_first_pacs_scan:
                    self.is_first_pacs_scan = False
                    self.known_pacs_patient_ids = set(today_pacs_dict.keys())
                    display_dict = today_pacs_dict
                else:
                    new_patients = {}
                    for patient_id, data in today_pacs_dict.items():
                        if patient_id not in self.known_pacs_patient_ids:
                            new_patients[patient_id] = data
                            if master_enabled and (pacs_toast_on or pacs_sound_on):
                                show_notification(
                                    str(data['patient_name']),
                                    'Новое КТ (PACS)',
                                    'short',
                                    icon_blue_path,
                                    self.config.get('pacs_notification_sound', 'default'),
                                    show_toast=pacs_toast_on,
                                    play_sound=pacs_sound_on,
                                    duration_setting=self.config.get('pacs_toast_duration', self.config.get('toast_duration', '5')),
                                    position_setting=self.config.get('pacs_toast_position', self.config.get('toast_position', 'bottom_right')),
                                    custom_voice_text=self.config.get('pacs_voice_text', '')
                                )

                    if new_patients:
                        self.known_pacs_patient_ids.update(new_patients.keys())

                    display_dict = today_pacs_dict
            else:
                if self.is_first_pacs_scan:
                    self.is_first_pacs_scan = False
                self.known_pacs_patient_ids = set(pacs_dict.keys())
                display_dict = pacs_dict

            data_changed = (display_dict != self.previous_pacs_data)
            if data_changed and (auto_update_on or not silent):
                self.pacs_table.setUpdatesEnabled(False)
                self.pacs_table.blockSignals(True)

                self.pacs_table.setRowCount(0)
                row_idx = 0
                sorted_items = sorted(display_dict.items(), key=lambda x: x[1]['study_datetime_obj'], reverse=True)
                
                for patient_id, data in sorted_items:
                    self.pacs_table.insertRow(row_idx)
                    
                    id_item = QTableWidgetItem(str(patient_id))
                    name_item = QTableWidgetItem(str(data['patient_name']))
                    modality_item = QTableWidgetItem(str(data.get('modality', 'CT')))
                    slices_item = QTableWidgetItem(str(data.get('slices', '0')))
                    area_item = QTableWidgetItem(str(data.get('body_part', '')))
                    study_item = QTableWidgetItem(data['study_datetime_str'])
                    
                    id_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                    name_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                    modality_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    slices_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    area_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    study_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    
                    color = QColor("#ffffff")
                    highlighting_enabled = self.config.get('highlighting_enabled', 'False').lower() == 'true'
                    if highlighting_enabled:
                        highlight_new = self.config.get('highlight_new_enabled', 'False').lower() == 'true'
                        highlight_today = self.config.get('highlight_today_enabled', 'False').lower() == 'true'
                        d_time = data.get('study_datetime_obj')
                        if d_time:
                            if highlight_new and (datetime.now() - d_time).total_seconds() / 3600 < 1:
                                color = QColor("lime")
                            elif highlight_today and d_time.date() == datetime.now().date():
                                color = QColor("mediumturquoise")
                        
                    for item in [id_item, name_item, modality_item, slices_item, area_item, study_item]:
                        item.setForeground(color)
                        
                    self.pacs_table.setItem(row_idx, 0, id_item)
                    self.pacs_table.setItem(row_idx, 1, name_item)
                    self.pacs_table.setItem(row_idx, 2, modality_item)
                    self.pacs_table.setItem(row_idx, 3, slices_item)
                    self.pacs_table.setItem(row_idx, 4, area_item)
                    self.pacs_table.setItem(row_idx, 5, study_item)
                    
                    row_idx += 1

                if hasattr(self, 'selected_pacs_patient_id') and self.selected_pacs_patient_id:
                    for r in range(self.pacs_table.rowCount()):
                        id_item = self.pacs_table.item(r, 0)
                        if id_item and id_item.text() == self.selected_pacs_patient_id:
                            self.pacs_table.selectRow(r)
                            break

                self.pacs_table.update_placeholder_visibility()
                self.pacs_table.blockSignals(False)
                self.pacs_table.setUpdatesEnabled(True)

                self.previous_pacs_data = display_dict.copy()
            else:
                self.pacs_table.update_placeholder_visibility()

        elif not con and not has_fail_msg:
            if not silent:
                log_message(self.output_field, tr_log("log_failed_connect_pacs"), replace_suffix=tr_log("log_connecting_pacs"))
            
            if self.previous_pacs_data:
                self.pacs_table.setUpdatesEnabled(False)
                self.pacs_table.blockSignals(True)
                self.pacs_table.setRowCount(0)
                self.pacs_table.update_placeholder_visibility()
                self.pacs_table.blockSignals(False)
                self.pacs_table.setUpdatesEnabled(True)
                self.previous_pacs_data = {}
            else:
                self.pacs_table.update_placeholder_visibility()

    # ================= УПРАВЛЕНИЕ НАСТРОЙКАМИ =================

    def browse_ct_images_dir(self):
        current_dir = self.config.get('ct_images_dir', '')
        dir_path = QFileDialog.getExistingDirectory(self, "Выберите папку КТ-изображений", current_dir)
        if dir_path:
            norm_path = os.path.normpath(dir_path)
            self.config['ct_images_dir'] = norm_path
            self.save_current_config()
            self.update_watcher_path()
            self.is_first_scan = True
            self.start_folder_scan(show_progress=True)

    def browse_archive_dir(self):
        current_dir = self.config.get('archive_dir', '')
        dir_path = QFileDialog.getExistingDirectory(self, "Выберите папку архива", current_dir)
        if dir_path:
            norm_path = os.path.normpath(dir_path)
            self.config['archive_dir'] = norm_path
            self.save_current_config()
            self.fill_archive_list(show_progress=True)

    def open_settings_cmd(self):
        old_ct_dir = self.config.get('ct_images_dir', '')
        old_archive_dir = self.config.get('archive_dir', '')
        dialog = SettingsDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            # Перечитываем настройки
            self.config = dialog.config
            self.populate_pacs_server_combo()
            
            # Обновляем шрифт лога
            font = QFont("Consolas", self.config.get('log_font_size', 12))
            self.output_field.setFont(font)
            
            # Обновляем шрифты и высоту строк таблиц через styleSheet
            font_size = self.config.get('patient_font_size', 16)
            row_height = max(25, font_size + 12)
            weight_map = {
                "Regular": "400",
                "Semibold": "600",
                "Bold": "700"
            }
            weight_str = self.config.get('patient_weight', 'Semibold')
            weight = weight_map.get(weight_str, "400")
            table_style = f"font-size: {font_size}px; font-weight: {weight}; font-family: 'Segoe UI';"
            header_style = """
                QHeaderView::section {
                    background-color: #1a1a1a;
                    color: #ffffff;
                    padding: 6px;
                    border: none;
                    border-left: 1px solid qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 transparent, stop:0.25 transparent, stop:0.3 #3d3d3d, stop:0.7 #3d3d3d, stop:0.75 transparent, stop:1 transparent);
                    font-size: 14px;
                    font-weight: normal;
                    font-family: 'Segoe UI';
                }
                QHeaderView::section:first {
                    border-left: none;
                }
                QHeaderView {
                    background-color: #1a1a1a;
                    border: none;
                }
            """
            for table in [self.images_table, self.archive_table, self.pacs_table]:
                table.setStyleSheet(table_style)
                table.horizontalHeader().setStyleSheet(header_style)
                table.verticalHeader().setDefaultSectionSize(row_height)
            
            # Сброс и перезапуск таймеров
            self.restart_timers()
            
            log_message(self.output_field, tr_log("log_settings_saved"))
            
            new_ct_dir = self.config.get('ct_images_dir', '')
            new_archive_dir = self.config.get('archive_dir', '')
            
            ct_changed = (old_ct_dir != new_ct_dir)
            archive_changed = (old_archive_dir != new_archive_dir)
            
            # Обновляем текущую вкладку
            current_idx = self.tab_widget.currentIndex()
            if current_idx == 0:
                self.start_folder_scan(show_progress=ct_changed)
            elif current_idx == 1:
                self.fill_archive_list(show_progress=archive_changed)
            else:
                self.on_tab_changed(current_idx)

    def on_pacs_selection_changed(self):
        has_selection = len(self.pacs_table.selectedRanges()) > 0
        self.send_to_ct_btn.setEnabled(has_selection)

    def pacs_set_today(self):
        self.pacs_date_from.blockSignals(True)
        self.pacs_date_to.blockSignals(True)
        self.pacs_date_from.setDate(QDate.currentDate())
        self.pacs_date_to.setDate(QDate.currentDate())
        self.pacs_date_from.blockSignals(False)
        self.pacs_date_to.blockSignals(False)
        self.fill_pacs_list(silent=True)

    def pacs_set_3days(self):
        self.pacs_date_from.blockSignals(True)
        self.pacs_date_to.blockSignals(True)
        self.pacs_date_from.setDate(QDate.currentDate().addDays(-2))
        self.pacs_date_to.setDate(QDate.currentDate())
        self.pacs_date_from.blockSignals(False)
        self.pacs_date_to.blockSignals(False)
        self.fill_pacs_list(silent=True)

    def send_to_ct_images_cmd(self):
        selected_ranges = self.pacs_table.selectedRanges()
        if not selected_ranges:
            return
            
        row = selected_ranges[0].topRow()
        patient_id = self.pacs_table.item(row, 0).text()
        patient_name = self.pacs_table.item(row, 1).text()
        
        ct_images_dir = self.config.get('ct_images_dir', '')
        if not ct_images_dir or not os.path.exists(ct_images_dir):
            _warn = QMessageBox(self)
            _warn.setIcon(QMessageBox.Icon.Warning)
            _warn.setWindowTitle("Ошибка")
            _warn.setText("Неверно настроена рабочая папка CT Images.")
            apply_dark_title_bar(_warn)
            _warn.exec()
            return
            
        self.send_to_ct_btn.setEnabled(False)
        self.send_to_ct_btn.setText("Sending...")
        log_message(self.output_field, tr_log("log_pacs_download_started", patient_id, patient_name))
        
        pacs_ip = self.config.get('pacs_ip', '127.0.0.1')
        pacs_port = int(self.config.get('pacs_port', 11112))
        called_aet = self.config.get('pacs_called_aet', 'ANY-SCP')
        calling_aet = self.config.get('pacs_calling_aet', 'ECHOSCU')
        
        from ui.loading_dialog import LoadingProgressDialog
        self.download_progress_dialog = LoadingProgressDialog(self, title="Скачивание из PACS")
        self.download_progress_dialog.label.setText("Подключение к PACS и запуск скачивания...")
        self.download_progress_dialog.show()

        self.pacs_download_worker = PacsDownloadWorker(
            patient_id, ct_images_dir, pacs_ip, pacs_port, called_aet, calling_aet
        )
        self.pacs_download_worker.finished.connect(self.on_pacs_download_finished)
        self.pacs_download_worker.progress.connect(self.on_pacs_download_progress)
        self.pacs_download_worker.start()

    def on_pacs_download_progress(self, completed, total):
        if hasattr(self, 'download_progress_dialog') and self.download_progress_dialog:
            self.download_progress_dialog.progress.setValue(int((completed / total) * 100))
            self.download_progress_dialog.label.setText(f"Скачивание снимков: {completed} из {total}...")

    def on_pacs_download_finished(self, success, msg):
        if hasattr(self, 'download_progress_dialog') and self.download_progress_dialog:
            self.download_progress_dialog.close()
            self.download_progress_dialog = None

        self.send_to_ct_btn.setEnabled(True)
        self.send_to_ct_btn.setText(tr_ui("btn_send_to_ct"))
        log_message(self.output_field, msg)
        
        if success:
            self.start_folder_scan()
        else:
            msg_box = QMessageBox(self)
            msg_box.setIcon(QMessageBox.Icon.Warning)
            msg_box.setWindowTitle("Ошибка скачивания")
            msg_box.setText(msg)
            
            apply_dark_title_bar(msg_box)
            msg_box.exec()

    def save_current_config(self):
        dialog = SettingsDialog(self)
        dialog.config = self.config
        dialog.save_config()

    def open_patient_folder(self, patient_id, is_archive=False):
        dir_key = 'archive_dir' if is_archive else 'ct_images_dir'
        cache = self.archive_cache if is_archive else self.images_cache
        folder_name = cache[patient_id].get('folder_name', patient_id) if (cache and patient_id in cache) else patient_id
        path = os.path.join(self.config.get(dir_key, ''), folder_name)
        if os.path.exists(path):
            try:
                os.startfile(path)
            except Exception as e:
                log_message(self.output_field, tr_log("log_failed_open_folder", folder_name, e))
        else:
            log_message(self.output_field, tr_log("log_path_not_exist", path))

    def on_images_double_clicked(self, row, column):
        id_item = self.images_table.item(row, 0)
        patient_id = id_item.data(Qt.ItemDataRole.UserRole) if id_item else ""
        self.open_viewer(patient_id, is_archive=False)

    def on_archive_double_clicked(self, row, column):
        id_item = self.archive_table.item(row, 0)
        patient_id = id_item.data(Qt.ItemDataRole.UserRole) if id_item else ""
        self.open_viewer(patient_id, is_archive=True)

    def open_viewer(self, patient_id, is_archive=False):
        dir_key = 'archive_dir' if is_archive else 'ct_images_dir'
        cache = self.archive_cache if is_archive else self.images_cache
        folder_name = cache[patient_id].get('folder_name', patient_id) if (cache and patient_id in cache) else patient_id
        patient_dir = os.path.join(self.config.get(dir_key, ''), folder_name)
        
        if not os.path.exists(patient_dir):
            log_message(self.output_field, tr_log("log_path_not_exist", patient_dir))
            return
            
        try:
            files = []
            for root, dirs, filenames in os.walk(patient_dir):
                for filename in filenames:
                    files.append(os.path.join(root, filename))
                    
            if not files:
                log_message(self.output_field, tr_log("log_patient_folder_empty", patient_id))
                return
                
            self.viewer_panel.load_series(files)
            self.viewer_panel.apply_theme()
            self.stacked_widget.setCurrentIndex(1)
        except Exception as e:
            log_message(self.output_field, tr_log("log_failed_open_viewer", patient_id, e))

    def close_viewer(self):
        self.stacked_widget.setCurrentIndex(0)
        self.viewer_panel.viewer.clear_viewer()
        self.show_patient_list()
        self.fill_archive_list(silent=True)

    def closeEvent(self, event):
        # Останавливаем наблюдатель перед выходом, чтобы не зависал фоновый поток
        self.stop_file_watcher()
        super().closeEvent(event)

    def check_for_updates_on_startup(self):
        if self.config.get('check_updates_at_startup', 'on').lower() == 'on':
            from ui.updater import UpdateCheckWorker
            self.startup_update_worker = UpdateCheckWorker()
            self.startup_update_worker.finished.connect(self.on_startup_update_checked)
            self.startup_update_worker.start()

    def on_startup_update_checked(self, latest_version, html_url, assets):
        from ui.updater import is_newer_version
        if latest_version and is_newer_version(VERSION, latest_version):
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Icon.Information)
            msg.setWindowTitle("Доступно обновление")
            msg.setText(f"Доступна новая версия: {latest_version}.\n\nХотите запустить автоматическое обновление?")
            msg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            msg.setDefaultButton(QMessageBox.StandardButton.Yes)
            
            apply_dark_title_bar(msg)
            
            if msg.exec() == QMessageBox.StandardButton.Yes:
                from ui.updater import run_auto_update
                run_auto_update(self, latest_version, assets)

    def populate_pacs_server_combo(self):
        self.pacs_server_combo.blockSignals(True)
        self.pacs_server_combo.clear()
        
        servers = self.config.get('pacs_servers', [])
        current_name = self.config.get('pacs_current_server_name', '')
        
        active_idx = 0
        for i, s in enumerate(servers):
            self.pacs_server_combo.addItem(s['name'])
            if s['name'] == current_name:
                active_idx = i
                
        self.pacs_server_combo.setCurrentIndex(active_idx)
        self.pacs_server_combo.blockSignals(False)

    def on_pacs_server_changed(self, index):
        servers = self.config.get('pacs_servers', [])
        if 0 <= index < len(servers):
            s = servers[index]
            self.config['pacs_current_server_name'] = s['name']
            self.config['pacs_ip'] = s['pacs_ip']
            self.config['pacs_port'] = s['pacs_port']
            self.config['pacs_called_aet'] = s['pacs_called_aet']
            self.config['pacs_calling_aet'] = s['pacs_calling_aet']
            
            # Save configuration to file
            self.save_current_config()
            
            # Reset PACS states for the new server
            self.is_first_pacs_scan = True
            self.standby_new_patients = {}
            self.previous_pacs_data = {}
            self.pacs_table.setRowCount(0)
            
            # Trigger immediate scan
            self.fill_pacs_list()

    def retranslate_ui(self):
        # Названия вкладок
        self.tab_widget.setTabText(0, self.config.get('custom_tab_name_ct') or tr_ui("tab_ct_images"))
        self.tab_widget.setTabText(1, self.config.get('custom_tab_name_archive') or tr_ui("tab_ct_archive"))
        self.tab_widget.setTabText(2, self.config.get('custom_tab_name_pacs') or tr_ui("tab_pacs"))
        
        # Кнопки и плейсхолдеры
        self.search_images_entry.setPlaceholderText(tr_ui("placeholder_search_patient"))
        self.search_images_btn.setText(tr_ui("btn_search"))
        self.move_to_archive_btn.setText(tr_ui("btn_move_to_archive"))
        
        self.search_entry.setPlaceholderText(tr_ui("placeholder_search_patient"))
        self.search_btn.setText(tr_ui("btn_search"))
        self.move_from_archive_btn.setText(tr_ui("btn_restore_from_archive"))
        
        self.pacs_today_btn.setText(tr_ui("btn_today"))
        self.pacs_3days_btn.setText(tr_ui("btn_3days"))
        self.lbl_from.setText(tr_ui("lbl_from"))
        self.lbl_to.setText(tr_ui("lbl_to"))
        self.lbl_server.setText(tr_ui("lbl_server"))
        self.send_to_ct_btn.setText(tr_ui("btn_send_to_ct"))
        self.pacs_auto_scan_cb.setText(tr_ui("pacs_standby_mode"))
        
        self.images_table.set_placeholder_text(tr_ui("placeholder_no_studies_in_folder"))
        self.archive_table.set_placeholder_text(tr_ui("placeholder_no_studies_in_folder"))

        # Подсказки (tooltips)
        self.settings_btn1.setToolTip(tr_ui("tooltip_settings_btn"))
        self.settings_btn2.setToolTip(tr_ui("tooltip_settings_btn"))
        self.settings_btn3.setToolTip(tr_ui("tooltip_settings_btn"))
        self.search_images_entry.setToolTip(tr_ui("tooltip_search_images_entry"))
        self.search_images_btn.setToolTip(tr_ui("tooltip_search_images_btn"))
        self.move_to_archive_btn.setToolTip(tr_ui("tooltip_move_to_archive"))
        self.search_entry.setToolTip(tr_ui("tooltip_search_archive_entry"))
        self.search_btn.setToolTip(tr_ui("tooltip_search_archive_btn"))
        self.move_from_archive_btn.setToolTip(tr_ui("tooltip_restore_from_archive"))
        self.pacs_auto_scan_cb.setToolTip(tr_ui("tooltip_pacs_auto_scan"))
        self.pacs_today_btn.setToolTip(tr_ui("tooltip_pacs_today_btn"))
        self.pacs_3days_btn.setToolTip(tr_ui("tooltip_pacs_3days_btn"))
        self.pacs_date_from.setToolTip(tr_ui("tooltip_pacs_date_from"))
        self.pacs_date_to.setToolTip(tr_ui("tooltip_pacs_date_to"))
        self.pacs_server_combo.setToolTip(tr_ui("tooltip_pacs_server_combo"))
        self.send_to_ct_btn.setToolTip(tr_ui("tooltip_send_to_ct"))


