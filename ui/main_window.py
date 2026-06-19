import os
import sys
import shutil
from datetime import datetime

from PyQt6.QtCore import Qt, QTimer, QSize, QThread, pyqtSignal, QObject
from PyQt6.QtGui import QColor, QAction, QIcon, QFont
from PyQt6.QtWidgets import (QApplication, QMainWindow, QTabWidget, QWidget, 
                             QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem, 
                             QPlainTextEdit, QPushButton, QMessageBox, 
                             QHeaderView, QMenu, QAbstractItemView, QLineEdit, QLabel,
                             QDialog, QFileDialog)

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from core.dicom_utils import dict_create, rename_patient_folder, delete_redundant_str
from core.archive import move_old_folders_to_archive
from core.notifier import show_notification
from core.logger import log_message
from core.pacs import pacs_dict_create
from ui.settings_dialog import SettingsDialog
from themes.theme_manager import load_theme


class WatchdogHandler(QObject, FileSystemEventHandler):
    changed = pyqtSignal()

    def on_any_event(self, event):
        self.changed.emit()


class ThreadLogCollector:
    def __init__(self):
        self.messages = []

    def appendPlainText(self, text):
        self.messages.append(text)


class FolderScanWorker(QThread):
    finished = pyqtSignal(dict, list)

    def __init__(self, ct_images_dir, fix_switch_value, archive_dir, archive_enabled, archive_days, archive_cleanup_enabled, archive_cleanup_days):
        super().__init__()
        self.ct_images_dir = ct_images_dir
        self.fix_switch_value = fix_switch_value
        self.archive_dir = archive_dir
        self.archive_enabled = archive_enabled
        self.archive_days = archive_days
        self.archive_cleanup_enabled = archive_cleanup_enabled
        self.archive_cleanup_days = archive_cleanup_days

    def run(self):
        collector = ThreadLogCollector()
        is_fix_on = self.fix_switch_value.lower() == 'true'
        is_archive_on = self.archive_enabled.lower() == 'true'
        is_cleanup_on = self.archive_cleanup_enabled.lower() == 'true'
        
        if is_fix_on and os.path.exists(self.ct_images_dir):
            for root, dirs, files in os.walk(self.ct_images_dir):
                for dir_name in dirs:
                    rename_patient_folder(os.path.join(root, dir_name), collector)
            
            if self.archive_dir and is_archive_on:
                from core.archive import move_old_folders_to_archive
                move_old_folders_to_archive(self.ct_images_dir, self.archive_dir, self.archive_days, collector)

        if self.archive_dir and is_cleanup_on:
            from core.archive import cleanup_old_archive_folders
            cleanup_old_archive_folders(self.archive_dir, self.archive_cleanup_days, collector)

        patient_dict = dict_create(self.ct_images_dir, collector, fix_switch=is_fix_on)
        self.finished.emit(patient_dict, collector.messages)


class PacsScanWorker(QThread):
    finished = pyqtSignal(dict, bool, list)

    def __init__(self, pacs_ip, pacs_port, called_aet, calling_aet):
        super().__init__()
        self.pacs_ip = pacs_ip
        self.pacs_port = pacs_port
        self.called_aet = called_aet
        self.calling_aet = calling_aet

    def run(self):
        collector = ThreadLogCollector()
        pacs_dict, con = pacs_dict_create(
            collector,
            pacs_ip=self.pacs_ip,
            pacs_port=self.pacs_port,
            called_aet=self.called_aet,
            calling_aet=self.calling_aet
        )
        self.finished.emit(pacs_dict, con, collector.messages)


class ArchiveScanWorker(QThread):
    finished = pyqtSignal(dict, list)

    def __init__(self, archive_dir, fix_switch_value):
        super().__init__()
        self.archive_dir = archive_dir
        self.fix_switch_value = fix_switch_value

    def run(self):
        collector = ThreadLogCollector()
        is_fix_on = self.fix_switch_value.lower() == 'true'
        from core.archive import archive_dict_create
        d = archive_dict_create(self.archive_dir, collector, fix_switch=is_fix_on)
        self.finished.emit(d, collector.messages)


class MainWindow(QMainWindow):
    instance = None

    def __init__(self):
        super().__init__()
        self.setWindowTitle("DICOM Explorer")
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
        
        # Инициализируем таймеры до создания UI во избежание AttributeError
        self.scan_timer = QTimer(self)
        self.scan_timer.timeout.connect(self.update_patient_list)
        self.pacs_timer = QTimer(self)
        self.pacs_timer.timeout.connect(self.auto_update_pacs)
        
        # Инициализируем наблюдатель за файловой системой
        self.init_file_watcher()
        
        self.init_ui()
        self.apply_theme()
        
        # Запуск таймеров и мониторинга
        self.restart_timers()
        
        # Первоначальное заполнение
        self.show_patient_list()

    def load_config(self):
        # Быстрый способ получить актуальные настройки из config.txt
        dialog = SettingsDialog(self)
        return dialog.config

    def init_window_geometry(self):
        x = self.config.get('x', 1000)
        y = self.config.get('y', 600)
        dx = self.config.get('dx', 350)
        dy = self.config.get('dy', 100)
        self.setGeometry(dx, dy, x, y)

    def apply_theme(self):
        theme_content = load_theme("dark")
        if theme_content:
            self.setStyleSheet(theme_content)

    def apply_settings_dynamic(self, config):
        old_dir = self.config.get('ct_images_dir', '')
        new_dir = config.get('ct_images_dir', '')
        
        self.config = config.copy()
        
        # 1. Обновляем шрифты таблиц
        font_size = self.config.get('patient_font_size', 14)
        row_height = max(25, font_size + 12)
        
        weight_map = {
            "Regular": "400",
            "Semibold": "600",
            "Bold": "700"
        }
        weight_str = self.config.get('patient_weight', 'Regular')
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
        
        # 3. Перезапускаем таймеры и watcher
        self.restart_timers()
        
        # 4. Обновляем путь наблюдателя, если он изменился
        if old_dir != new_dir:
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
        is_auto_update = self.config.get('auto_update_is', 'on').lower() == 'on'
        if not is_auto_update:
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
            self.watcher_handler.changed.connect(self.trigger_debounce)
            
            self.watcher_observer = Observer()
            self.watcher_observer.schedule(self.watcher_handler, ct_dir, recursive=True)
            self.watcher_observer.start()
            self.currently_watched_dir = ct_dir
            log_message(self.output_field, f"Запущен мониторинг папки в реальном времени: {ct_dir}")
        except Exception as e:
            self.currently_watched_dir = None
            log_message(self.output_field, f"Не удалось запустить мониторинг папки: {e}")

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

    def trigger_debounce(self):
        # 2 секунды задержки, чтобы дождаться окончания записи
        self.debounce_timer.start(2000)

    def on_watcher_timeout(self):
        log_message(self.output_field, "Обнаружены изменения файлов. Запускаю обновление списка...")
        self.start_folder_scan()

    def restart_timers(self):
        self.scan_timer.stop()
        self.pacs_timer.stop()
        
        is_auto_update = self.config.get('auto_update_is', 'on').lower() == 'on'
        
        # Управляем наблюдателем файлов в реальном времени
        if is_auto_update:
            self.update_watcher_path()
        else:
            self.stop_file_watcher()
            
        # Таймер PACS работает только когда активна вкладка PACS
        if self.tab_widget.currentIndex() == 2:  # Вкладка PACS
            if is_auto_update:
                self.pacs_timer.start(self.config.get('pacs_scan_time', 10000))

    def init_ui(self):
        # Главный виджет
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        
        # Основной вертикальный макет (просторные отступы как на макете)
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(15, 10, 15, 10)
        main_layout.setSpacing(10)
        
        # Вкладки
        self.tab_widget = QTabWidget()
        main_layout.addWidget(self.tab_widget)
        
        # Создание вкладок
        self.create_tab_ct_images()
        self.create_tab_ct_archive()
        self.create_tab_pacs()
        
        # Поле вывода логов
        self.output_field = QPlainTextEdit()
        self.output_field.setReadOnly(True)
        # Установка размера шрифта из настроек
        font = QFont("Consolas", self.config.get('log_font_size', 12))
        self.output_field.setFont(font)
        self.output_field.setFixedHeight(150)
        main_layout.addWidget(self.output_field)
        
        # Подключаем сигнал изменения вкладок после полной инициализации виджетов
        self.tab_widget.currentChanged.connect(self.on_tab_changed)

    def create_tab_ct_images(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 24, 10, 10)
        layout.setSpacing(10)
        
        # Таблица КТ-изображений
        self.images_table = QTableWidget()
        self.images_table.setColumnCount(6)
        self.images_table.setHorizontalHeaderLabels([
            "Patient ID", "Patient Name", "Scanning Area", 
            "Study datetime", "Folder datetime", "STR"
        ])
        self.setup_table_properties(self.images_table)
        self.images_table.cellDoubleClicked.connect(self.open_current_folder_cmd)
        self.images_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.images_table.customContextMenuRequested.connect(self.show_images_context_menu)
        self.images_table.itemSelectionChanged.connect(self.on_images_selection_changed)
        
        layout.addWidget(self.images_table)
        
        # Нижняя панель управления
        control_layout = QHBoxLayout()
        control_layout.setContentsMargins(5, 0, 5, 0)
        control_layout.setSpacing(10)
        
        # Поле выбора папки сканирования
        scan_label = QLabel("Scan Folder:")
        scan_label.setStyleSheet("color: #ffffff; font-family: 'Segoe UI'; font-size: 13px; font-weight: normal;")
        
        self.scan_dir_edit = QLineEdit(self.config.get('ct_images_dir', ''))
        self.scan_dir_edit.setReadOnly(True)
        self.scan_dir_edit.setFixedHeight(30)
        self.scan_dir_edit.setStyleSheet(
            "background-color: #0f0f0f; color: #ffffff; border: 1px solid #3d3d3d; "
            "border-radius: 4px; padding: 4px; font-family: 'Segoe UI'; font-size: 13px;"
        )
        
        scan_browse_btn = QPushButton("Browse...")
        scan_browse_btn.setFixedHeight(30)
        scan_browse_btn.setStyleSheet(
            "QPushButton { background-color: #1f538d; color: white; border: none; border-radius: 4px; padding: 5px 12px; font-family: 'Segoe UI'; font-size: 13px; }"
            "QPushButton:hover { background-color: #2a6db7; }"
            "QPushButton:pressed { background-color: #153e6b; }"
        )
        scan_browse_btn.clicked.connect(self.browse_scan_folder)
        
        control_layout.addWidget(scan_label, alignment=Qt.AlignmentFlag.AlignVCenter)
        control_layout.addWidget(self.scan_dir_edit, stretch=1, alignment=Qt.AlignmentFlag.AlignVCenter)
        control_layout.addWidget(scan_browse_btn, alignment=Qt.AlignmentFlag.AlignVCenter)
        
        # Кнопка перемещения в архив
        self.move_to_archive_btn = QPushButton("Move to Archive")
        self.move_to_archive_btn.clicked.connect(self.move_to_archive_cmd)
        self.move_to_archive_btn.setEnabled(False)  # Затененная по умолчанию
        self.move_to_archive_btn.setFixedHeight(30)
        self.move_to_archive_btn.setObjectName("moveToArchiveBtn")
        control_layout.addWidget(self.move_to_archive_btn, alignment=Qt.AlignmentFlag.AlignVCenter)
        
        # Кнопка настроек (шестеренка)
        self.settings_btn = QPushButton()
        self.settings_btn.setIcon(QIcon("themes/settings.svg"))
        self.settings_btn.setIconSize(QSize(20, 20))
        self.settings_btn.setFixedSize(35, 30)
        self.settings_btn.setToolTip("Настройки папок и интервалов")
        self.settings_btn.clicked.connect(self.open_settings_cmd)
        control_layout.addWidget(self.settings_btn, alignment=Qt.AlignmentFlag.AlignVCenter)
        
        layout.addLayout(control_layout)
        
        self.tab_widget.addTab(tab, "CT images")

    def create_tab_ct_archive(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 24, 10, 10)
        layout.setSpacing(10)
        
        # Таблица архива
        self.archive_table = QTableWidget()
        self.archive_table.setColumnCount(6)
        self.archive_table.setHorizontalHeaderLabels([
            "Patient ID", "Patient Name", "Scanning Area", 
            "Study datetime", "Folder datetime", "STR"
        ])
        self.setup_table_properties(self.archive_table)
        self.archive_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.archive_table.customContextMenuRequested.connect(self.show_archive_context_menu)
        self.archive_table.itemSelectionChanged.connect(self.on_archive_selection_changed)
        layout.addWidget(self.archive_table)
        
        # Панель поиска и восстановления
        search_layout = QHBoxLayout()
        
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
        
        layout.addLayout(search_layout)
        
        self.tab_widget.addTab(tab, "CT archive")

    def create_tab_pacs(self):
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(10, 24, 10, 10)
        layout.setSpacing(10)
        
        # Таблица PACS
        self.pacs_table = QTableWidget()
        self.pacs_table.setColumnCount(4)
        self.pacs_table.setHorizontalHeaderLabels([
            "Patient ID", "Patient Name", "Scanning Area", "Study datetime"
        ])
        self.setup_table_properties(self.pacs_table)
        layout.addWidget(self.pacs_table)
        
        self.tab_widget.addTab(tab, "PACS")

    def setup_table_properties(self, table):
        # Настройка поведения таблиц
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(False)  # Отключаем зебру
        table.setShowGrid(False)  # Отключаем сетку
        table.verticalHeader().setVisible(False)
        
        # Динамическая высота строки в зависимости от размера шрифта
        font_size = self.config.get('patient_font_size', 14)
        row_height = max(25, font_size + 12)
        table.verticalHeader().setDefaultSectionSize(row_height)
        
        # Установка шрифтов через styleSheet, так как глобальный QSS переопределяет setFont()
        weight_map = {
            "Regular": "400",
            "Semibold": "600",
            "Bold": "700"
        }
        weight_str = self.config.get('patient_weight', 'Regular')
        weight = weight_map.get(weight_str, "400")
        table_style = f"font-size: {font_size}px; font-weight: {weight}; font-family: 'Segoe UI';"
        header_style = "font-size: 14px; font-weight: normal; font-family: 'Segoe UI';"
        table.setStyleSheet(table_style)
        table.horizontalHeader().setStyleSheet(header_style)
        
        # Растягивание колонок
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(False)
        
        # Установим пропорции ширины по умолчанию
        if table.columnCount() == 6:
            table.setColumnWidth(0, 130)  # ID
            table.setColumnWidth(1, 200)  # Name
            table.setColumnWidth(2, 150)  # Scanning Area
            table.setColumnWidth(3, 150)  # Study
            table.setColumnWidth(4, 150)  # Folder
            table.setColumnWidth(5, 50)   # STR
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)  # Имя тянется
        else:
            table.setColumnWidth(0, 180)  # ID
            table.setColumnWidth(1, 250)  # Name
            table.setColumnWidth(2, 180)  # Scanning Area
            table.setColumnWidth(3, 200)  # Study
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

    def on_tab_changed(self, index):
        # Защитная проверка на случай срабатывания сигнала до инициализации всех таблиц
        if not hasattr(self, 'archive_table') or not hasattr(self, 'pacs_table') or not hasattr(self, 'images_table'):
            return
            
        # Сброс списков PACS и архива при переходе
        if index == 0:  # CT images
            self.archive_table.setRowCount(0)
            self.archive_cache = None
            self.pacs_table.setRowCount(0)
            self.pacs_timer.stop()
        elif index == 1:  # CT archive
            self.images_table.setRowCount(0)
            self.pacs_table.setRowCount(0)
            self.pacs_timer.stop()
            self.fill_archive_list()
        elif index == 2:  # PACS
            self.images_table.setRowCount(0)
            self.archive_table.setRowCount(0)
            self.archive_cache = None
            self.fill_pacs_list()
            # Запускаем таймер PACS
            is_auto_update = self.config.get('auto_update_is', 'on').lower() == 'on'
            if is_auto_update:
                self.pacs_timer.start(self.config.get('pacs_scan_time', 10000))

    # ================= ЛОГИКА ТАБЛИЦЫ CT IMAGES =================

    def show_patient_list(self):
        self.start_folder_scan()

    def update_patient_list(self):
        is_auto_update = self.config.get('auto_update_is', 'on').lower() == 'on'
        if is_auto_update:
            self.start_folder_scan()

    def start_folder_scan(self):
        if self.scan_worker and self.scan_worker.isRunning():
            return

        ct_dir = self.config.get('ct_images_dir', '')
        if not os.path.exists(ct_dir):
            log_message(self.output_field, "Неверный путь к папке CT Images")
            return

        # Запоминаем выделенного пациента
        self.selected_images_patient_id = None
        selected_ranges = self.images_table.selectedRanges()
        if selected_ranges:
            row = selected_ranges[0].topRow()
            id_item = self.images_table.item(row, 0)
            if id_item:
                self.selected_images_patient_id = id_item.text()

        fix_val = self.config.get('fix_switch_value', 'True')
        archive_dir = self.config.get('archive_dir', '')
        archive_enabled = self.config.get('archive_enabled', 'True')
        archive_days = int(self.config.get('archive_days', 3))
        archive_cleanup_enabled = self.config.get('archive_cleanup_enabled', 'False')
        archive_cleanup_days = int(self.config.get('archive_cleanup_days', 30))

        self.scan_worker = FolderScanWorker(
            ct_dir, fix_val, archive_dir,
            archive_enabled, archive_days,
            archive_cleanup_enabled, archive_cleanup_days
        )
        self.scan_worker.finished.connect(self.on_folder_scan_finished)
        self.scan_worker.start()

    def on_folder_scan_finished(self, patient_dict, log_messages):
        for msg in log_messages:
            log_message(self.output_field, msg)

        self.images_table.setRowCount(0)

        scan_time_sec = self.config.get('folder_scan_time', 10000) / 1000
        notification_on = self.config.get('notification_is', 'on').upper() == 'ON'
        
        # Определение абсолютного пути к иконке в папке src
        icon_path = self.config.get('icon_path', '')
        if not icon_path or os.path.isdir(icon_path):
            base_dir = icon_path if icon_path else os.getcwd()
            potential_icon = os.path.abspath(os.path.join(base_dir, "src", "icon.png"))
            if os.path.exists(potential_icon):
                icon_path = potential_icon
            else:
                potential_root_icon = os.path.abspath(os.path.join(base_dir, "icon.png"))
                if os.path.exists(potential_root_icon):
                    icon_path = potential_root_icon
        else:
            icon_path = os.path.abspath(icon_path)

        # Заполняем таблицу
        row_idx = 0
        total_items = len(patient_dict)
        progress_dialog = None
        if total_items > 100:
            from ui.loading_dialog import LoadingProgressDialog
            progress_dialog = LoadingProgressDialog(self, title="Заполнение таблицы КТ")
            progress_dialog.show()

        for patient_id, data in sorted(patient_dict.items(), key=lambda x: str(x[1].get('patient_name', ''))):
            if 'patient_name' not in data or 'study_datetime' not in data or 'folder_datetime' not in data or 'str' not in data:
                log_message(self.output_field, f"Пропущен пациент {patient_id} из-за неполных данных DICOM")
                continue
            
            # Уведомление о новых файлах
            folder_age_sec = (datetime.now() - data['folder_datetime']).total_seconds()
            if 0 < folder_age_sec < scan_time_sec:
                if notification_on:
                    show_notification(
                        str(data['patient_name']), 
                        'Новое КТ', 
                        'short', 
                        icon_path
                    )
            
            self.images_table.insertRow(row_idx)
            
            id_item = QTableWidgetItem(str(patient_id))
            name_item = QTableWidgetItem(str(data['patient_name']))
            area_item = QTableWidgetItem(str(data.get('body_part', '')))
            study_item = QTableWidgetItem(data['study_datetime'].strftime('%d.%m.%y - %H:%M'))
            folder_item = QTableWidgetItem(data['folder_datetime'].strftime('%d.%m.%y - %H:%M'))
            str_item = QTableWidgetItem(str(data['str']))
            
            id_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            name_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            area_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            study_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            folder_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            str_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            
            color = QColor("#ffffff")
            if data['str'] == 0 or data['str'] > 1:
                color = QColor("crimson")
                
            for item in [id_item, name_item, area_item, study_item, folder_item, str_item]:
                item.setForeground(color)
                
            self.images_table.setItem(row_idx, 0, id_item)
            self.images_table.setItem(row_idx, 1, name_item)
            self.images_table.setItem(row_idx, 2, area_item)
            self.images_table.setItem(row_idx, 3, study_item)
            self.images_table.setItem(row_idx, 4, folder_item)
            self.images_table.setItem(row_idx, 5, str_item)
            
            row_idx += 1
            if progress_dialog:
                progress_dialog.set_progress(row_idx, total_items)

        if progress_dialog:
            progress_dialog.close()

        # Восстанавливаем выделение
        if hasattr(self, 'selected_images_patient_id') and self.selected_images_patient_id:
            for r in range(self.images_table.rowCount()):
                id_item = self.images_table.item(r, 0)
                if id_item and id_item.text() == self.selected_images_patient_id:
                    self.images_table.selectRow(r)
                    break

    def open_current_folder_cmd(self, row, column):
        patient_id = self.images_table.item(row, 0).text()
        path = os.path.join(self.config.get('ct_images_dir', ''), patient_id)
        if os.path.exists(path):
            try:
                os.startfile(path)
            except Exception as e:
                log_message(self.output_field, f"Не удалось открыть папку {patient_id}: {e}")

    # ================= КОНТЕКСТНЫЕ МЕНЮ И ДЕЙСТВИЯ =================

    def show_images_context_menu(self, pos):
        # Получаем строку под курсором
        index = self.images_table.indexAt(pos)
        if not index.isValid():
            return
            
        row = index.row()
        patient_id = self.images_table.item(row, 0).text()
        patient_name = self.images_table.item(row, 1).text()
        
        menu = QMenu(self)
        
        delete_action = QAction("Удалить пациента", self)
        delete_action.triggered.connect(lambda: self.delete_patient_action(patient_id, patient_name))
        
        archive_action = QAction("Переместить в архив", self)
        archive_action.triggered.connect(lambda: self.archive_patient_action(patient_id))
        
        clean_str_action = QAction("Удалить лишние STR", self)
        clean_str_action.triggered.connect(lambda: self.clean_str_action(patient_id))
        
        menu.addAction(delete_action)
        menu.addAction(archive_action)
        menu.addAction(clean_str_action)
        
        menu.exec(self.images_table.viewport().mapToGlobal(pos))

    def delete_patient_action(self, patient_id, patient_name):
        path = os.path.join(self.config.get('ct_images_dir', ''), patient_id)
        if not os.path.exists(path):
            log_message(self.output_field, f"Путь {path} не существует")
            return

        reply = QMessageBox.question(
            self, 
            'Подтверждение удаления',
            f'Вы действительно хотите безвозвратно удалить пациента\n"{patient_name}" ({patient_id}) с диска?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, 
            QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            try:
                shutil.rmtree(path)
                log_message(self.output_field, f"Папка пациента {patient_name} ({patient_id}) полностью удалена")
                self.show_patient_list()
            except Exception as e:
                QMessageBox.critical(self, "Ошибка удаления", f"Не удалось удалить папку: {e}")
                log_message(self.output_field, f"Ошибка удаления папки {patient_id}: {e}")

    def archive_patient_action(self, patient_id):
        path = os.path.join(self.config.get('ct_images_dir', ''), patient_id)
        archive_dir = self.config.get('archive_dir', '')
        
        if not os.path.exists(path):
            log_message(self.output_field, f"Путь {path} не существует")
            return
            
        if not os.path.exists(archive_dir):
            os.makedirs(archive_dir)

        dest_path = os.path.join(archive_dir, patient_id)
        try:
            if os.path.exists(dest_path):
                shutil.rmtree(dest_path)
            shutil.move(path, archive_dir)
            log_message(self.output_field, f"Папка {patient_id} перемещена в архив")
            self.show_patient_list()
        except Exception as e:
            QMessageBox.critical(self, "Ошибка архивации", f"Не удалось переместить в архив: {e}")

    def clean_str_action(self, patient_id):
        path = os.path.join(self.config.get('ct_images_dir', ''), patient_id)
        if os.path.exists(path):
            deleted = delete_redundant_str(path, self.output_field)
            log_message(self.output_field, f"Очищено {deleted} лишних файлов STR для {patient_id}")
            self.show_patient_list()

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
        patient_id = self.images_table.item(row, 0).text()
        self.archive_patient_action(patient_id)

    # ================= ЛОГИКА ТАБЛИЦЫ CT ARCHIVE =================

    def fill_archive_list(self):
        if self.archive_worker and self.archive_worker.isRunning():
            return

        archive_dir = self.config.get('archive_dir', '')
        if not os.path.exists(archive_dir):
            log_message(self.output_field, "Папка архива не существует")
            return
            
        log_message(self.output_field, "Загрузка списка архивных пациентов...")

        # Запоминаем выделенного пациента
        self.selected_archive_patient_id = None
        selected_ranges = self.archive_table.selectedRanges()
        if selected_ranges:
            row = selected_ranges[0].topRow()
            id_item = self.archive_table.item(row, 0)
            if id_item:
                self.selected_archive_patient_id = id_item.text()

        fix_val = self.config.get('fix_switch_value', 'True')
        self.archive_worker = ArchiveScanWorker(archive_dir, fix_val)
        self.archive_worker.finished.connect(self.on_archive_scan_finished)
        self.archive_worker.start()

    def on_archive_scan_finished(self, archive_dict, log_messages):
        for msg in log_messages:
            log_message(self.output_field, msg)

        log_message(self.output_field, "Список архивных пациентов загружен", replace_suffix="Загрузка списка архивных пациентов...")
        self.archive_cache = archive_dict
        
        search_text = self.search_entry.text().lower()
        if search_text:
            self.search_patient_archive()
            return

        self.archive_table.setRowCount(0)
        slice_limit = self.config.get('archive_slice', 2)

        valid_items = {}
        for k, v in archive_dict.items():
            if 'patient_name' in v and 'study_datetime' in v and 'folder_datetime' in v and 'str' in v:
                valid_items[k] = v
            else:
                log_message(self.output_field, f"Пропущен пациент {k} в архиве из-за неполных данных DICOM")

        row_idx = 0
        sorted_items = sorted(valid_items.items(), key=lambda x: x[1]['folder_datetime'], reverse=True)[:slice_limit]
        
        total_items = len(sorted_items)
        progress_dialog = None
        if total_items > 100:
            from ui.loading_dialog import LoadingProgressDialog
            progress_dialog = LoadingProgressDialog(self, title="Заполнение таблицы архива")
            progress_dialog.show()

        for patient_id, data in sorted_items:
            self.archive_table.insertRow(row_idx)
            
            id_item = QTableWidgetItem(str(patient_id))
            name_item = QTableWidgetItem(str(data['patient_name']))
            area_item = QTableWidgetItem(str(data.get('body_part', '')))
            study_item = QTableWidgetItem(data['study_datetime'].strftime('%d.%m.%y - %H:%M'))
            folder_item = QTableWidgetItem(data['folder_datetime'].strftime('%d.%m.%y - %H:%M'))
            str_item = QTableWidgetItem(str(data['str']))
            
            id_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            name_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            area_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            study_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            folder_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            str_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            
            color = QColor("#ffffff")
            if data['str'] == 0 or data['str'] > 1:
                color = QColor("crimson")
                
            for item in [id_item, name_item, area_item, study_item, folder_item, str_item]:
                item.setForeground(color)
                
            self.archive_table.setItem(row_idx, 0, id_item)
            self.archive_table.setItem(row_idx, 1, name_item)
            self.archive_table.setItem(row_idx, 2, area_item)
            self.archive_table.setItem(row_idx, 3, study_item)
            self.archive_table.setItem(row_idx, 4, folder_item)
            self.archive_table.setItem(row_idx, 5, str_item)
            
            row_idx += 1
            if progress_dialog:
                progress_dialog.set_progress(row_idx, total_items)

        if progress_dialog:
            progress_dialog.close()

        if hasattr(self, 'selected_archive_patient_id') and self.selected_archive_patient_id:
            for r in range(self.archive_table.rowCount()):
                id_item = self.archive_table.item(r, 0)
                if id_item and id_item.text() == self.selected_archive_patient_id:
                    self.archive_table.selectRow(r)
                    break

    def show_archive_context_menu(self, pos):
        index = self.archive_table.indexAt(pos)
        if not index.isValid():
            return
            
        row = index.row()
        patient_id = self.archive_table.item(row, 0).text()
        patient_name = self.archive_table.item(row, 1).text()
        
        menu = QMenu(self)
        
        restore_action = QAction("Восстановить в CT images", self)
        restore_action.triggered.connect(self.move_from_archive_cmd)
        
        delete_action = QAction("Удалить пациента навсегда", self)
        delete_action.triggered.connect(lambda: self.delete_archive_patient_action(patient_id, patient_name))
        
        menu.addAction(restore_action)
        menu.addAction(delete_action)
        
        menu.exec(self.archive_table.viewport().mapToGlobal(pos))

    def delete_archive_patient_action(self, patient_id, patient_name):
        path = os.path.join(self.config.get('archive_dir', ''), patient_id)
        if not os.path.exists(path):
            log_message(self.output_field, f"Путь {path} не найден")
            return

        reply = QMessageBox.question(
            self, 
            'Подтверждение удаления',
            f'Вы действительно хотите безвозвратно удалить архивного пациента\n"{patient_name}" ({patient_id}) с диска?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, 
            QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            try:
                shutil.rmtree(path)
                log_message(self.output_field, f"Архивный пациент {patient_name} ({patient_id}) полностью удален с диска")
                # Сбрасываем кэш, чтобы принудительно обновить список
                self.archive_cache = None
                self.fill_archive_list()
            except Exception as e:
                QMessageBox.critical(self, "Ошибка удаления", f"Не удалось удалить: {e}")

    def move_from_archive_cmd(self):
        selected_ranges = self.archive_table.selectedRanges()
        if not selected_ranges:
            return
            
        row = selected_ranges[0].topRow()
        patient_id = self.archive_table.item(row, 0).text()
        
        archive_dir = self.config.get('archive_dir', '')
        ct_images_dir = self.config.get('ct_images_dir', '')
        
        path = os.path.join(archive_dir, patient_id)
        if not os.path.exists(path):
            log_message(self.output_field, f"Папка {patient_id} не найдена в архиве")
            return
            
        dest_path = os.path.join(ct_images_dir, patient_id)
        try:
            if os.path.exists(dest_path):
                shutil.rmtree(dest_path)
                
            shutil.copytree(path, dest_path)
            shutil.rmtree(path)
            
            log_message(self.output_field, f"Папка {patient_id} перемещена в CT images и удалена из архива")
            self.archive_cache = None
            self.fill_archive_list()
        except Exception as e:
            log_message(self.output_field, f"Ошибка восстановления {patient_id}: {e}")

    def search_patient_archive(self):
        search_text = self.search_entry.text().lower()
        
        if not hasattr(self, 'archive_cache') or self.archive_cache is None:
            self.fill_archive_list()
            return

        self.archive_table.setRowCount(0)
        
        row_idx = 0
        for patient_id, data in self.archive_cache.items():
            if 'patient_name' not in data or 'study_datetime' not in data or 'folder_datetime' not in data or 'str' not in data:
                continue
                
            name_lower = str(data['patient_name']).lower()
            if search_text in name_lower:
                self.archive_table.insertRow(row_idx)
                
                id_item = QTableWidgetItem(str(patient_id))
                name_item = QTableWidgetItem(str(data['patient_name']))
                area_item = QTableWidgetItem(str(data.get('body_part', '')))
                study_item = QTableWidgetItem(data['study_datetime'].strftime('%d.%m.%y - %H:%M'))
                folder_item = QTableWidgetItem(data['folder_datetime'].strftime('%d.%m.%y - %H:%M'))
                str_item = QTableWidgetItem(str(data['str']))
                
                id_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                name_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                area_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                study_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                folder_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                str_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                
                color = QColor("#ffffff")
                if data['str'] == 0 or data['str'] > 1:
                    color = QColor("crimson")
                    
                for item in [id_item, name_item, area_item, study_item, folder_item, str_item]:
                    item.setForeground(color)
                    
                self.archive_table.setItem(row_idx, 0, id_item)
                self.archive_table.setItem(row_idx, 1, name_item)
                self.archive_table.setItem(row_idx, 2, area_item)
                self.archive_table.setItem(row_idx, 3, study_item)
                self.archive_table.setItem(row_idx, 4, folder_item)
                self.archive_table.setItem(row_idx, 5, str_item)
                
                row_idx += 1

    # ================= ЛОГИКА ТАБЛИЦЫ PACS =================

    def fill_pacs_list(self):
        self.start_pacs_scan()

    def auto_update_pacs(self):
        self.start_pacs_scan()

    def start_pacs_scan(self):
        if self.pacs_worker and self.pacs_worker.isRunning():
            return

        log_message(self.output_field, "Пытаюсь подключиться к серверу PACS")
        self.pacs_table.setRowCount(0)

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

        self.pacs_worker = PacsScanWorker(pacs_ip, pacs_port, called_aet, calling_aet)
        self.pacs_worker.finished.connect(self.on_pacs_scan_finished)
        self.pacs_worker.start()

    def on_pacs_scan_finished(self, pacs_dict, con, log_messages):
        has_fail_msg = False
        for msg in log_messages:
            if "подключиться к серверу PACS" in msg:
                log_message(self.output_field, msg, replace_suffix="Пытаюсь подключиться к серверу PACS")
                has_fail_msg = True
            else:
                log_message(self.output_field, msg)

        if con:
            log_message(self.output_field, "Установлено подключение к серверу PACS", replace_suffix="Пытаюсь подключиться к серверу PACS")
        elif not con and not has_fail_msg:
            log_message(self.output_field, "Не удалось подключиться к серверу PACS", replace_suffix="Пытаюсь подключиться к серверу PACS")
            
            self.pacs_table.setRowCount(0)
            row_idx = 0
            sorted_items = sorted(pacs_dict.items(), key=lambda x: x[1]['study_datetime_obj'], reverse=True)
            
            for patient_id, data in sorted_items:
                self.pacs_table.insertRow(row_idx)
                
                id_item = QTableWidgetItem(str(patient_id))
                name_item = QTableWidgetItem(str(data['patient_name']))
                area_item = QTableWidgetItem(str(data.get('body_part', '')))
                study_item = QTableWidgetItem(data['study_datetime_str'])
                
                id_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                name_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                area_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                study_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                
                color = QColor("#ffffff")
                d_time = datetime.strptime(data['study_datetime_str'], "%d.%m.%y - %H:%M")
                if (datetime.now() - d_time).total_seconds() / 3600 < 1:
                    color = QColor("lime")
                elif d_time.date() == datetime.now().date():
                    color = QColor("mediumturquoise")
                    
                for item in [id_item, name_item, area_item, study_item]:
                    item.setForeground(color)
                    
                self.pacs_table.setItem(row_idx, 0, id_item)
                self.pacs_table.setItem(row_idx, 1, name_item)
                self.pacs_table.setItem(row_idx, 2, area_item)
                self.pacs_table.setItem(row_idx, 3, study_item)
                
                row_idx += 1

            if hasattr(self, 'selected_pacs_patient_id') and self.selected_pacs_patient_id:
                for r in range(self.pacs_table.rowCount()):
                    id_item = self.pacs_table.item(r, 0)
                    if id_item and id_item.text() == self.selected_pacs_patient_id:
                        self.pacs_table.selectRow(r)
                        break

    # ================= УПРАВЛЕНИЕ НАСТРОЙКАМИ =================

    def open_settings_cmd(self):
        dialog = SettingsDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            # Перечитываем настройки
            self.config = dialog.config
            
            # Обновляем шрифт лога
            font = QFont("Consolas", self.config.get('log_font_size', 12))
            self.output_field.setFont(font)
            
            # Обновляем шрифты и высоту строк таблиц через styleSheet
            font_size = self.config.get('patient_font_size', 14)
            row_height = max(25, font_size + 12)
            weight_map = {
                "Regular": "400",
                "Semibold": "600",
                "Bold": "700"
            }
            weight_str = self.config.get('patient_weight', 'Regular')
            weight = weight_map.get(weight_str, "400")
            table_style = f"font-size: {font_size}px; font-weight: {weight}; font-family: 'Segoe UI';"
            header_style = "font-size: 14px; font-weight: normal; font-family: 'Segoe UI';"
            for table in [self.images_table, self.archive_table, self.pacs_table]:
                table.setStyleSheet(table_style)
                table.horizontalHeader().setStyleSheet(header_style)
                table.verticalHeader().setDefaultSectionSize(row_height)
            
            # Сброс и перезапуск таймеров
            self.restart_timers()
            
            # Обновляем поле папки сканирования, если оно изменилось
            if hasattr(self, 'scan_dir_edit'):
                self.scan_dir_edit.setText(self.config.get('ct_images_dir', ''))
                
            log_message(self.output_field, "Настройки сохранены и применены")
            
            # Обновляем текущую вкладку
            self.on_tab_changed(self.tab_widget.currentIndex())
            if self.tab_widget.currentIndex() == 0:
                self.show_patient_list()

    def browse_scan_folder(self):
        dir_path = QFileDialog.getExistingDirectory(self, "Выберите папку КТ-изображений", self.scan_dir_edit.text())
        if dir_path:
            norm_path = os.path.normpath(dir_path)
            self.scan_dir_edit.setText(norm_path)
            self.config['ct_images_dir'] = norm_path
            self.save_current_config()
            self.update_watcher_path()  # Перезапускаем наблюдатель на новый путь
            self.show_patient_list()

    def save_current_config(self):
        dialog = SettingsDialog(self)
        dialog.config = self.config
        dialog.save_config()

    def closeEvent(self, event):
        # Останавливаем наблюдатель перед выходом, чтобы не зависал фоновый поток
        self.stop_file_watcher()
        super().closeEvent(event)

