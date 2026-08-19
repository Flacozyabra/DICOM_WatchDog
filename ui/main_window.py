import os
import sys
import shutil
from datetime import datetime, timedelta

from PyQt6.QtCore import Qt, QTimer, QSize, QThread, pyqtSignal, QObject, QDate, QPoint, QRectF
from PyQt6.QtGui import QColor, QAction, QIcon, QFont, QFontMetrics, QPainter, QPen, QBrush, QPolygon, QPalette, QLinearGradient
from PyQt6.QtWidgets import (QApplication, QMainWindow, QTabWidget, QTabBar, QWidget, 
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
from core.pacs import pacs_dict_create, download_patient_from_pacs, start_background_pacs_server
from core.config_utils import get_resource_path, VERSION
from core.locale_utils import tr_ui, tr_log, set_current_langs
from ui.settings_dialog import SettingsDialog, apply_dark_title_bar
from ui.toggle_switch import ToggleSwitch
from ui.centered_date_edit import CenteredDateEdit
from ui.tab_badge import TabBadge
from themes.theme_manager import load_theme
from ui.dicom_viewer import DicomViewerPanel
from ui.table_widgets import (
    ToggleTableWidget, TaskProgressDelegate, CustomSplitter, CustomSplitterHandle
)
from ui.workers import (
    WatchdogHandler, ThreadLogCollector, FolderScanWorker, PacsScanWorker,
    ArchiveScanWorker, BackgroundFileWorker, PacsDownloadWorker
)
from ui.tabs.images_tab import ImagesTab
from ui.tabs.archive_tab import ArchiveTab
from ui.tabs.pacs_tab import PacsTab


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
        self.archive_cache = None
        self.pacs_data = {}
        self.tab_badges = {}
        self.previous_pacs_data = {}
        self.standby_new_patients = {}
        self.pacs_download_worker = None
        
        from ui.table_context_menus import TableContextMenuManager
        self.context_menu_mgr = TableContextMenuManager(self)
        
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

        # Запускаем фоновый DICOM SCP сервер для ответа на опрос (C-ECHO) сервера PACS и приема C-STORE
        pacs_local_port = int(self.config.get('pacs_local_port', 11112))
        calling_aet = self.config.get('pacs_calling_aet', 'DW_GAMMA')
        ct_dir = self.config.get('ct_images_dir', '')
        start_background_pacs_server(port=pacs_local_port, ae_title=calling_aet, target_dir=ct_dir)
        
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
        self.fill_archive_list(silent=True)
        if self.config.get('auto_update_is', 'off').lower() == 'on' or self.tab_widget.currentIndex() == 2:
            self.fill_pacs_list(silent=True)
        
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
            worker = getattr(self, op_key)
            delattr(self, op_key)
            if worker:
                worker.deleteLater()
            
        self.images_table.viewport().update()
        self.archive_table.viewport().update()
            
        if op_type == 'archive':
            log_message(self.output_field, tr_log("log_patient_archived", result))
            self.show_patient_list()
            self.archive_cache = None
            self.fill_archive_list(silent=True)
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
            worker = getattr(self, op_key)
            delattr(self, op_key)
            if worker:
                worker.deleteLater()
            
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
        return SettingsDialog.load_config()

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
        self.pacs_auto_scan_cb.blockSignals(True)
        self.pacs_auto_scan_cb.setChecked(self.config.get('auto_update_is', 'off').lower() == 'on')
        self.pacs_auto_scan_cb.blockSignals(False)
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
        
        # 1. Проверка пробуждения от сна или глубокой задержки (>60 секунд)
        elapsed = now_ts - self.last_timer_timestamp
        self.last_timer_timestamp = now_ts
        
        if elapsed > 60:
            log_message(self.output_field, tr_log("log_system_resumed_from_sleep"))
            self.last_checked_date = today
            self.update_images_table_ui()
            # Сбрасываем счетчик повторов сети при выходе из сна
            self.net_retry_count = 0
            self.check_network_folder_retry()
            self.last_timer_timestamp = time.time()
            return

        # 2. Периодическая проверка восстановления сетевой папки, если соединение было ранее потеряно
        ct_dir = self.config.get('ct_images_dir', '')
        if ct_dir and not self.net_retry_timer.isActive() and not os.path.exists(ct_dir):
            if self.net_retry_count >= self.net_retry_max:
                self.net_retry_count = 0
                self.check_network_folder_retry()
            
        # 3. Бесшумная проверка смены суток в полночь
        if self.last_checked_date != today:
            self.last_checked_date = today
            self.update_images_table_ui()
            
        self.last_timer_timestamp = time.time()

    def trigger_debounce(self):
        # 2 секунды задержки, чтобы дождаться окончания записи
        self.debounce_timer.start(2000)

    def on_watcher_timeout(self):
        self.start_folder_scan()

    def restart_timers(self):
        self.pacs_timer.stop()
        
        # Наблюдатель файлов в реальном времени работает всегда
        self.update_watcher_path()
            
        # Таймер PACS работает только при включенном свиче автообновления (Standby mode)
        pacs_auto_scan_on = self.config.get('auto_update_is', 'off').lower() == 'on'
        if pacs_auto_scan_on:
            self.pacs_timer.start(self.config.get('pacs_scan_time', 10000))
        else:
            self.pacs_timer.stop()

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
        
        self.update_tab_badges()
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
        
        # Бейджи со счетчиками исследований на вкладках
        self.images_tab.badge = TabBadge(self.tab_widget.tabBar(), 0)
        self.archive_tab.badge = TabBadge(self.tab_widget.tabBar(), 1)
        self.pacs_tab.badge = TabBadge(self.tab_widget.tabBar(), 2)
        
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
        
        # Обновляем видимость и локализацию интерфейса перед отображением
        self.retranslate_ui()
        
        self.stacked_widget.addWidget(main_widget)
        
        # Панель вьюера DICOM
        self.viewer_panel = DicomViewerPanel(self)
        self.viewer_panel.close_requested.connect(self.close_viewer)
        self.stacked_widget.addWidget(self.viewer_panel)

    def create_tab_ct_images(self):
        self.images_tab = ImagesTab(self)
        self.images_table = self.images_tab.table
        self.search_images_entry = self.images_tab.search_entry
        self.search_images_btn = self.images_tab.search_btn
        self.move_to_archive_btn = self.images_tab.move_to_archive_btn
        self.settings_btn1 = self.images_tab.settings_btn

    def create_tab_ct_archive(self):
        self.archive_tab = ArchiveTab(self)
        self.archive_table = self.archive_tab.table
        self.search_entry = self.archive_tab.search_entry
        self.search_btn = self.archive_tab.search_btn
        self.move_from_archive_btn = self.archive_tab.move_from_archive_btn
        self.settings_btn2 = self.archive_tab.settings_btn

    def create_tab_pacs(self):
        self.pacs_tab = PacsTab(self)
        self.pacs_table = self.pacs_tab.table
        self.pacs_today_btn = self.pacs_tab.today_btn
        self.pacs_3days_btn = self.pacs_tab.last_3days_btn
        self.lbl_from = self.pacs_tab.lbl_from
        self.pacs_date_from = self.pacs_tab.date_from
        self.lbl_to = self.pacs_tab.lbl_to
        self.pacs_date_to = self.pacs_tab.date_to
        self.lbl_server = self.pacs_tab.lbl_server
        self.pacs_server_combo = self.pacs_tab.server_combo
        self.pacs_auto_scan_cb = self.pacs_tab.auto_scan_cb
        self.pacs_search_entry = self.pacs_tab.search_entry
        self.send_to_ct_btn = self.pacs_tab.send_to_ct_btn
        self.settings_btn3 = self.pacs_tab.settings_btn
        self.populate_pacs_server_combo()
        self.update_pacs_controls_state()

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
            table.setColumnWidth(0, 140)  # ID
            table.setColumnWidth(1, 300)  # Name
            table.setColumnWidth(2, 65)   # Modality
            table.setColumnWidth(3, 65)   # Slices
            table.setColumnWidth(4, 120)  # Scanning Area
            table.setColumnWidth(5, 150)  # Study
            table.setColumnWidth(6, 150)  # Folder
            table.setColumnWidth(7, 45)   # STR
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)  # Имя тянется
        elif table.columnCount() == 6:
            table.setColumnWidth(0, 140)  # ID
            table.setColumnWidth(1, 300)  # Name
            table.setColumnWidth(2, 70)   # Modality
            table.setColumnWidth(3, 65)   # Slices
            table.setColumnWidth(4, 130)  # Scanning Area
            table.setColumnWidth(5, 150)  # Study
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

    def show_header_context_menu(self, pos, table):
        self.context_menu_mgr.show_header_context_menu(pos, table)

    def on_section_moved(self, logical, old, new, table):
        self.save_table_state(table)

    def save_table_state(self, table):
        table_name = None
        if getattr(self, 'images_table', None) == table:
            table_name = "images_table"
        elif getattr(self, 'archive_table', None) == table:
            table_name = "archive_table"
        elif getattr(self, 'pacs_table', None) == table:
            table_name = "pacs_table"
        elif table.columnCount() == 8:
            if getattr(self, 'images_table', None) is None:
                table_name = "images_table"
            else:
                table_name = "archive_table"
        elif table.columnCount() == 6:
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
        if getattr(self, 'images_table', None) == table:
            table_name = "images_table"
        elif getattr(self, 'archive_table', None) == table:
            table_name = "archive_table"
        elif getattr(self, 'pacs_table', None) == table:
            table_name = "pacs_table"
        elif table.columnCount() == 8:
            if getattr(self, 'images_table', None) is None:
                table_name = "images_table"
            else:
                table_name = "archive_table"
        elif table.columnCount() == 6:
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
        if not hasattr(self, 'images_tab') or not hasattr(self, 'archive_tab') or not hasattr(self, 'pacs_tab'):
            return
            
        self.update_tab_badges()
        
        current_widget = self.tab_widget.widget(index)
        pacs_auto_scan_on = self.config.get('auto_update_is', 'off').lower() == 'on'
        
        if current_widget == self.images_tab:  # CT images
            if not pacs_auto_scan_on:
                self.pacs_timer.stop()
            self.show_patient_list()
            QTimer.singleShot(0, self.focus_ct_images_search)
        elif current_widget == self.archive_tab:  # CT archive
            if not pacs_auto_scan_on:
                self.pacs_timer.stop()
            if not hasattr(self, 'archive_cache') or self.archive_cache is None:
                self.fill_archive_list()
            else:
                self.update_archive_table_ui()
            QTimer.singleShot(0, self.focus_ct_archive_search)
        elif current_widget == self.pacs_tab:  # PACS
            self.fill_pacs_list()
            # Запускаем таймер PACS только если включено автообновление
            if pacs_auto_scan_on:
                self.pacs_timer.start(self.config.get('pacs_scan_time', 10000))
            else:
                self.pacs_timer.stop()
            QTimer.singleShot(0, self.focus_pacs_search)

    def focus_ct_images_search(self):
        if hasattr(self, 'search_images_entry'):
            self.search_images_entry.setFocus()

    def focus_ct_archive_search(self):
        if hasattr(self, 'search_entry'):
            self.search_entry.setFocus()

    def focus_pacs_search(self):
        if hasattr(self, 'pacs_search_entry'):
            self.pacs_search_entry.setFocus()

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
            self.net_retry_count = 0
            self.net_retry_timer.stop()
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
            self.images_cache = None
            self.update_tab_badges()
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
                self.images_cache = None
                self.update_tab_badges()
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
                self.images_cache = None
                self.update_tab_badges()
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
        
        show_archive = self.config.get('show_tab_archive', 'True').lower() == 'true'
        archive_dir = self.config.get('archive_dir', '') if show_archive else ''
        archive_enabled = self.config.get('archive_enabled', 'False') if show_archive else 'False'
        archive_days = int(self.config.get('archive_days', 3))
        archive_cleanup_enabled = self.config.get('archive_cleanup_enabled', 'False') if show_archive else 'False'
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
        self.scan_worker.progress.connect(self.on_scan_progress)
        self.scan_worker.count_updated.connect(self.on_images_scan_count_updated)
        
        if show_progress:
            from ui.loading_dialog import LoadingProgressDialog
            self.scan_progress_dialog = LoadingProgressDialog(
                self, 
                title=tr_ui("loading_title_data"), 
                show_cancel=True, 
                on_cancel=self.cancel_folder_scan
            )
            self.scan_progress_dialog.label.setText("Подготовка к сканированию DICOM-файлов...")
            self.scan_progress_dialog.progress.setRange(0, 100)
            self.scan_worker.progress.connect(self.scan_progress_dialog.set_scan_progress)
            self.scan_worker.status_changed.connect(self.scan_progress_dialog.label.setText)
            self.scan_worker.finished.connect(self.scan_progress_dialog.accept)
            
            self.scan_worker.start()
            self.scan_progress_dialog.exec()
        else:
            self.scan_worker.start()

    def cancel_folder_scan(self):
        if hasattr(self, 'scan_worker') and self.scan_worker and self.scan_worker.isRunning():
            self.scan_worker.requestInterruption()
            self.scan_worker.wait(1000)

    def on_scan_status_changed(self, status_text):
        self.current_scan_phase_text = status_text
        if self.images_table.rowCount() == 0:
            self.images_table.set_placeholder_state(status_text, show_button=False)
            self.images_table.update_placeholder_visibility()

    def on_images_scan_count_updated(self, current_count):
        if hasattr(self, 'images_tab') and hasattr(self.images_tab, 'badge') and self.images_tab.badge:
            show_badges = self.config.get('show_study_counts', 'True').lower() == 'true'
            if show_badges:
                if not hasattr(self, 'images_cache') or self.images_cache is None:
                    self.images_tab.badge.set_count(current_count)

    def on_scan_progress(self, current, total):
        if self.images_table.rowCount() == 0 and total > 0:
            curr_capped = min(current, total)
            percent = int((curr_capped / total) * 100)
            base_text = getattr(self, 'current_scan_phase_text', '') or tr_ui("placeholder_scanning_folder")
            is_ru = (tr_ui("placeholder_scanning_folder") == "Выполняется сканирование папки...")
            suffix = f"{percent}% ({curr_capped} из {total})" if is_ru else f"{percent}% ({curr_capped} of {total})"
            self.images_table.set_placeholder_state(f"{base_text} {suffix}", show_button=False)
            self.images_table.update_placeholder_visibility()

    def on_folder_scan_finished(self, patient_dict, log_messages):
        self.images_cache = patient_dict
        self.is_first_scan = False
        if hasattr(self, 'debounce_timer') and self.debounce_timer:
            self.debounce_timer.stop()
        self.update_tab_badges()

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
                        try:
                            show_notification(
                                title=str(data['patient_name']), 
                                message='Новое КТ', 
                                sound_setting=self.config.get('ct_notification_sound', 'default'),
                                volume=int(self.config.get('ct_notification_volume', 100)),
                                custom_voice_text=self.config.get('ct_voice_text', ''),
                                play_sound=ct_sound_on,
                                show_toast=ct_toast_on,
                                duration_setting=self.config.get('ct_toast_duration', self.config.get('toast_duration', '5')),
                                position_setting=self.config.get('ct_toast_position', self.config.get('toast_position', 'bottom_right')),
                                icon_path=icon_path
                            )
                        except Exception as e:
                            log_message(self.output_field, f"CT notification error: {e}")

        self.images_cache = patient_dict
        # Завершили первое сканирование
        self.is_first_scan = False
        self.restored_patient_ids.clear()

        self.update_images_table_ui()
        self.update_tab_badges()

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
            p_id = str(data.get('patient_id', patient_id)).lower()
            if search_text:
                words = patient_name.replace('^', ' ').split()
                name_match = bool(words and words[0].startswith(search_text))
                id_match = p_id.startswith(search_text)
                if not (name_match or id_match):
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
                # Для родительской строки сохраняем путь к самому свежему исследованию пациента
                id_item.setData(Qt.ItemDataRole.UserRole, studies[0][0])

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

        if search_text and self.images_table.rowCount() == 0 and bool(self.images_cache):
            self.images_table.set_placeholder_state(tr_ui("placeholder_no_filter_matches"), show_button=False, color="crimson")
        else:
            self.images_table.set_placeholder_state(tr_ui("placeholder_no_studies_in_folder"), show_button=False)
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
        if not patient_id or not str(patient_id).strip() or str(patient_id).strip() in ('.', '/', '\\'):
            return
        base_dir = self.config.get('ct_images_dir', '')
        if not base_dir:
            return
        path = os.path.normpath(os.path.join(base_dir, patient_id))
        if os.path.normcase(path) == os.path.normcase(os.path.normpath(base_dir)):
            return
        if os.path.exists(path):
            try:
                os.startfile(path)
            except Exception as e:
                log_message(self.output_field, tr_log("log_failed_open_folder", patient_id, e))

    # ================= КОНТЕКСТНЫЕ МЕНЮ И ДЕЙСТВИЯ =================

    def show_tab_context_menu(self, pos):
        self.context_menu_mgr.show_tab_context_menu(pos)

    def get_move_to_archive_text(self):
        custom = self.config.get('custom_tab_name_archive')
        if custom:
            from core.locale_utils import get_current_langs
            lang, _ = get_current_langs()
            return f"Переместить в {custom}" if lang == 'ru' else f"Move to {custom}"
        return tr_ui("btn_move_to_archive")

    def get_restore_to_ct_text(self):
        custom = self.config.get('custom_tab_name_ct')
        if custom:
            from core.locale_utils import get_current_langs
            lang, _ = get_current_langs()
            return f"Восстановить в {custom}" if lang == 'ru' else f"Restore to {custom}"
        return tr_ui("btn_restore_from_archive")

    def get_send_to_ct_text(self):
        custom = self.config.get('custom_tab_name_ct')
        if custom:
            from core.locale_utils import get_current_langs
            lang, _ = get_current_langs()
            return f"Отправить в {custom}" if lang == 'ru' else f"Send to {custom}"
        return tr_ui("btn_send_to_ct")

    def rename_tab_dialog(self, index):
        self.context_menu_mgr.rename_tab_dialog(index)

    def show_images_context_menu(self, pos):
        self.context_menu_mgr.show_images_context_menu(pos)

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
            from core.rename_utils import move_study_folder_hierarchical
            move_study_folder_hierarchical(path, archive_dir, self.output_field)
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
            self.archive_cache = None
            self.update_tab_badges()
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

        # Если таблица архива пуста, сразу отображаем статус сканирования
        if self.archive_table.rowCount() == 0:
            self.archive_table.set_placeholder_state(tr_ui("placeholder_scanning_folder"), show_button=False)
            self.archive_table.update_placeholder_visibility()

        cleanup_str_val = self.config.get('cleanup_structures_enabled', 'False')
        self.archive_worker = ArchiveScanWorker(archive_dir, cleanup_str_val)
        self.archive_worker.finished.connect(lambda ad, lm: self.on_archive_scan_finished(ad, lm, silent))
        self.archive_worker.progress.connect(self.on_archive_scan_progress)
        self.archive_worker.count_updated.connect(self.on_archive_scan_count_updated)
        if not silent:
            self.archive_worker.log_emitted.connect(lambda msg: log_message(self.output_field, msg))
        
        if show_progress:
            from ui.loading_dialog import LoadingProgressDialog
            self.archive_progress_dialog = LoadingProgressDialog(
                self, 
                title=tr_ui("loading_title_data"), 
                show_cancel=True, 
                on_cancel=self.cancel_archive_scan
            )
            self.archive_progress_dialog.label.setText("Подготовка к сканированию файлов архива...")
            self.archive_progress_dialog.progress.setRange(0, 100)
            self.archive_worker.progress.connect(self.archive_progress_dialog.set_scan_progress)
            self.archive_worker.finished.connect(self.archive_progress_dialog.accept)
            
            self.archive_worker.start()
            self.archive_progress_dialog.exec()
        else:
            self.archive_worker.start()

    def cancel_archive_scan(self):
        if hasattr(self, 'archive_worker') and self.archive_worker and self.archive_worker.isRunning():
            self.archive_worker.requestInterruption()
            self.archive_worker.wait(1000)

    def on_archive_scan_count_updated(self, current_count):
        if hasattr(self, 'archive_tab') and hasattr(self.archive_tab, 'badge') and self.archive_tab.badge:
            show_badges = self.config.get('show_study_counts', 'True').lower() == 'true'
            if show_badges:
                if not hasattr(self, 'archive_cache') or self.archive_cache is None:
                    self.archive_tab.badge.set_count(current_count)

    def on_archive_scan_progress(self, current, total):
        if self.archive_table.rowCount() == 0 and total > 0:
            percent = int((current / total) * 100)
            base_text = tr_ui("placeholder_scanning_folder")
            is_ru = (base_text == "Выполняется сканирование папки...")
            suffix = f"{percent}% ({current} из {total})" if is_ru else f"{percent}% ({current} of {total})"
            self.archive_table.set_placeholder_state(f"{base_text} {suffix}", show_button=False)
            self.archive_table.update_placeholder_visibility()

    def on_archive_scan_finished(self, archive_dict, log_messages, silent=False):
        if not silent:
            log_message(self.output_field, tr_log("log_archive_loaded"), replace_suffix=tr_log("log_loading_archive"))
        self.archive_cache = archive_dict
        self.update_archive_table_ui()
        self.update_tab_badges()

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
            p_id = str(data.get('patient_id', patient_id)).lower()
            if search_text:
                words = patient_name.replace('^', ' ').split()
                name_match = bool(words and words[0].startswith(search_text))
                id_match = p_id.startswith(search_text)
                if not (name_match or id_match):
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
                # Для родительской строки сохраняем путь к самому свежему исследованию пациента
                id_item.setData(Qt.ItemDataRole.UserRole, studies[0][0])

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

        if search_text and self.archive_table.rowCount() == 0 and bool(self.archive_cache):
            self.archive_table.set_placeholder_state(tr_ui("placeholder_no_filter_matches"), show_button=False, color="crimson")
        else:
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
        self.context_menu_mgr.show_archive_context_menu(pos)

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
            from core.rename_utils import move_study_folder_hierarchical
            move_study_folder_hierarchical(path, ct_images_dir, self.output_field)
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
            pacs_toast_on = str(self.config.get('pacs_notification_toast_enabled', 'False')).lower() == 'true'
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
                                try:
                                    show_notification(
                                        title=str(data['patient_name']),
                                        message='Новое КТ (PACS)',
                                        sound_setting=self.config.get('pacs_notification_sound', 'default'),
                                        volume=int(self.config.get('pacs_notification_volume', 100)),
                                        custom_voice_text=self.config.get('pacs_voice_text', ''),
                                        play_sound=pacs_sound_on,
                                        show_toast=pacs_toast_on,
                                        duration_setting=self.config.get('pacs_toast_duration', self.config.get('toast_duration', '5')),
                                        position_setting=self.config.get('pacs_toast_position', self.config.get('toast_position', 'bottom_right')),
                                        icon_path=icon_blue_path
                                    )
                                except Exception as e:
                                    log_message(self.output_field, f"PACS notification error: {e}")

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
                self.pacs_data = display_dict.copy()
                self.previous_pacs_data = display_dict.copy()
                self.render_pacs_table()
            else:
                self.pacs_data = display_dict.copy()
                self.pacs_table.update_placeholder_visibility()
            self.update_tab_badges()

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
            self.pacs_data = {}
            self.update_tab_badges()

    def search_patient_pacs(self):
        self.render_pacs_table()

    def render_pacs_table(self):
        if not hasattr(self, 'pacs_data') or self.pacs_data is None:
            return

        display_dict = self.pacs_data
        search_text = self.pacs_search_entry.text().lower().strip() if hasattr(self, 'pacs_search_entry') else ""

        filtered_items = {}
        for patient_id, data in display_dict.items():
            patient_name = str(data.get('patient_name', '')).lower()
            p_id = str(data.get('patient_id', patient_id)).lower()
            if search_text:
                words = patient_name.replace('^', ' ').split()
                name_match = bool(words and words[0].startswith(search_text))
                id_match = p_id.startswith(search_text)
                if not (name_match or id_match):
                    continue
            filtered_items[patient_id] = data

        self.pacs_table.setUpdatesEnabled(False)
        self.pacs_table.blockSignals(True)

        self.pacs_table.setRowCount(0)
        row_idx = 0
        sorted_items = sorted(filtered_items.items(), key=lambda x: x[1]['study_datetime_obj'], reverse=True)
        
        for patient_id, data in sorted_items:
            self.pacs_table.insertRow(row_idx)
            
            p_display_id = str(data.get('study_patient_id', data.get('patient_id', patient_id)))
            id_item = QTableWidgetItem(p_display_id)
            id_item.setData(Qt.ItemDataRole.UserRole, data.get('study_instance_uid', ''))
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

        if search_text and self.pacs_table.rowCount() == 0 and bool(self.pacs_data):
            self.pacs_table.set_placeholder_text(tr_ui("placeholder_no_filter_matches"), color="crimson")
        elif not search_text:
            auto_update_on = self.config.get('auto_update_is', 'off').lower() == 'on'
            if auto_update_on:
                self.pacs_table.set_placeholder_text(tr_ui("placeholder_standby"))
            else:
                self.pacs_table.set_placeholder_text(tr_ui("placeholder_no_studies"))

        self.pacs_table.update_placeholder_visibility()
        self.pacs_table.blockSignals(False)
        self.pacs_table.setUpdatesEnabled(True)

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
            if norm_path != current_dir:
                self.config['archive_dir'] = norm_path
                self.save_current_config()
                self.archive_cache = None
                current_widget = self.tab_widget.currentWidget()
                self.fill_archive_list(silent=(current_widget != self.archive_tab), show_progress=(current_widget == self.archive_tab))

    def open_settings_cmd(self):
        old_ct_dir = self.config.get('ct_images_dir', '')
        old_archive_dir = self.config.get('archive_dir', '')
        dialog = SettingsDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            # Перечитываем настройки
            self.config = dialog.config
            self.apply_settings_dynamic(self.config)
            self.populate_pacs_server_combo()
            
            # Перезапускаем фоновый DICOM сервер с новыми настройками
            pacs_local_port = int(self.config.get('pacs_local_port', 11112))
            calling_aet = self.config.get('pacs_calling_aet', 'DW_GAMMA')
            ct_dir = self.config.get('ct_images_dir', '')
            start_background_pacs_server(port=pacs_local_port, ae_title=calling_aet, target_dir=ct_dir)
            
            log_message(self.output_field, tr_log("log_settings_saved"))
            
            new_ct_dir = self.config.get('ct_images_dir', '')
            new_archive_dir = self.config.get('archive_dir', '')
            
            ct_changed = (old_ct_dir != new_ct_dir)
            archive_changed = (old_archive_dir != new_archive_dir)
            
            show_archive = self.config.get('show_tab_archive', 'True').lower() == 'true'
            current_widget = self.tab_widget.currentWidget()

            if archive_changed:
                self.archive_cache = None
                if show_archive:
                    self.fill_archive_list(silent=(current_widget != self.archive_tab), show_progress=(current_widget == self.archive_tab))

            if ct_changed:
                self.images_cache = None
                self.update_watcher_path()
                self.start_folder_scan(show_progress=(current_widget == self.images_tab))
            elif not archive_changed:
                if current_widget == self.images_tab:
                    self.start_folder_scan(show_progress=False)
                elif current_widget == self.archive_tab:
                    if self.archive_cache is None:
                        self.fill_archive_list(show_progress=False)
                    else:
                        self.update_archive_table_ui()
                else:
                    self.on_tab_changed(self.tab_widget.currentIndex())

    def on_pacs_selection_changed(self):
        has_selection = len(self.pacs_table.selectedRanges()) > 0
        self.send_to_ct_btn.setEnabled(has_selection)

    def show_pacs_context_menu(self, pos):
        self.context_menu_mgr.show_pacs_context_menu(pos)

    def pacs_set_today(self):
        self.pacs_date_from.blockSignals(True)
        self.pacs_date_to.blockSignals(True)
        self.pacs_date_from.setDate(QDate.currentDate())
        self.pacs_date_to.setDate(QDate.currentDate())
        self.pacs_date_from.blockSignals(False)
        self.pacs_date_to.blockSignals(False)
        self.fill_pacs_list(silent=False)

    def pacs_set_3days(self):
        self.pacs_date_from.blockSignals(True)
        self.pacs_date_to.blockSignals(True)
        self.pacs_date_from.setDate(QDate.currentDate().addDays(-2))
        self.pacs_date_to.setDate(QDate.currentDate())
        self.pacs_date_from.blockSignals(False)
        self.pacs_date_to.blockSignals(False)
        self.fill_pacs_list(silent=False)

    def send_to_ct_images_cmd(self):
        selected_ranges = self.pacs_table.selectedRanges()
        if not selected_ranges:
            return
            
        row = selected_ranges[0].topRow()
        id_item = self.pacs_table.item(row, 0)
        patient_id = id_item.text() if id_item else ""
        patient_name = self.pacs_table.item(row, 1).text() if self.pacs_table.item(row, 1) else ""
        
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
        
        study_instance_uid = id_item.data(Qt.ItemDataRole.UserRole) if id_item else None
        if not study_instance_uid:
            if hasattr(self, 'previous_pacs_data') and patient_id in self.previous_pacs_data:
                study_instance_uid = self.previous_pacs_data[patient_id].get('study_instance_uid')
            elif hasattr(self, 'pacs_data') and patient_id in self.pacs_data:
                study_instance_uid = self.pacs_data[patient_id].get('study_instance_uid')

        from ui.loading_dialog import LoadingProgressDialog
        self.download_progress_dialog = LoadingProgressDialog(
            self, title="Скачивание из PACS", show_cancel=True, on_cancel=self.cancel_pacs_download
        )
        self.download_progress_dialog.label.setText("Подключение к PACS и запуск скачивания...")
        self.download_progress_dialog.show()

        self.pacs_download_worker = PacsDownloadWorker(
            patient_id, ct_images_dir, pacs_ip, pacs_port, called_aet, calling_aet, study_instance_uid=study_instance_uid
        )
        self.pacs_download_worker.finished.connect(self.on_pacs_download_finished)
        self.pacs_download_worker.progress.connect(self.on_pacs_download_progress)
        self.pacs_download_worker.start()

    def cancel_pacs_download(self):
        if hasattr(self, 'pacs_download_worker') and self.pacs_download_worker:
            self.pacs_download_worker.cancel()
            log_message(self.output_field, "Запрос на отмену скачивания отправлен...")

    def on_pacs_download_progress(self, completed, total):
        if hasattr(self, 'download_progress_dialog') and self.download_progress_dialog:
            self.download_progress_dialog.progress.setValue(int((completed / total) * 100))
            self.download_progress_dialog.label.setText(f"Скачивание снимков: {completed} из {total}...")

    def on_pacs_download_finished(self, success, msg):
        if hasattr(self, 'download_progress_dialog') and self.download_progress_dialog:
            self.download_progress_dialog.close()
            self.download_progress_dialog = None

        self.send_to_ct_btn.setEnabled(True)
        self.send_to_ct_btn.setText(self.get_send_to_ct_text())
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
        from core.config_utils import save_config
        save_config(self.config)

    def open_patient_folder(self, patient_id, is_archive=False):
        dir_key = 'archive_dir' if is_archive else 'ct_images_dir'
        base_dir = self.config.get(dir_key, '')
        if not base_dir or not os.path.exists(base_dir):
            return
        if not patient_id or not str(patient_id).strip() or str(patient_id).strip() in ('.', '/', '\\'):
            return
        cache = self.archive_cache if is_archive else self.images_cache
        folder_name = cache[patient_id].get('folder_name', patient_id) if (cache and patient_id in cache) else patient_id
        if not folder_name or not str(folder_name).strip() or str(folder_name).strip() in ('.', '/', '\\'):
            return
        path = os.path.normpath(os.path.join(base_dir, folder_name))
        if os.path.normcase(path) == os.path.normcase(os.path.normpath(base_dir)):
            return
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
        base_dir = self.config.get(dir_key, '')
        if not base_dir or not os.path.exists(base_dir):
            log_message(self.output_field, tr_log("log_path_not_exist", base_dir))
            return

        if not patient_id or not str(patient_id).strip() or str(patient_id).strip() in ('.', '/', '\\'):
            return

        cache = self.archive_cache if is_archive else self.images_cache
        folder_name = cache[patient_id].get('folder_name', patient_id) if (cache and patient_id in cache) else patient_id
        if not folder_name or not str(folder_name).strip() or str(folder_name).strip() in ('.', '/', '\\'):
            return

        patient_dir = os.path.normpath(os.path.join(base_dir, folder_name))
        # Защита от открытия всего корневого каталога КТ/Архива
        if os.path.normcase(patient_dir) == os.path.normcase(os.path.normpath(base_dir)):
            return
            
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
                
            self.stacked_widget.setCurrentIndex(1)
            self.viewer_panel.apply_theme()
            self.viewer_panel.load_series(files)
        except Exception as e:
            log_message(self.output_field, tr_log("log_failed_open_viewer", patient_id, e))

    def close_viewer(self):
        self.stacked_widget.setCurrentIndex(0)
        self.viewer_panel.clear_panel()
        self.show_patient_list()
        self.fill_archive_list(silent=True)

    def closeEvent(self, event):
        # Останавливаем наблюдатель перед выходом, чтобы не зависал фоновый поток
        self.stop_file_watcher()
        
        # Отменяем активное скачивание из PACS, если запущено
        if hasattr(self, 'pacs_download_worker') and self.pacs_download_worker and self.pacs_download_worker.isRunning():
            try:
                self.pacs_download_worker.cancel()
            except Exception:
                pass

        # Останавливаем фоновые таймеры
        for timer_attr in ['pacs_timer', 'system_check_timer', 'net_retry_timer', 'animation_timer']:
            if hasattr(self, timer_attr):
                try:
                    getattr(self, timer_attr).stop()
                except Exception:
                    pass

        # Освобождаем входящий DICOM-порт и останавливаем сервер C-STORE
        try:
            from core.pacs import _global_dicom_server
            _global_dicom_server.stop()
        except Exception:
            pass

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

    def update_tabs_visibility(self):
        if not hasattr(self, 'images_tab') or not hasattr(self, 'archive_tab') or not hasattr(self, 'pacs_tab'):
            return

        show_archive = self.config.get('show_tab_archive', 'True').lower() == 'true'
        show_pacs = self.config.get('show_tab_pacs', 'True').lower() == 'true'

        current_widget = self.tab_widget.currentWidget()
        self.tab_widget.blockSignals(True)

        # 1. CT Images Tab (всегда index 0)
        ct_idx = self.tab_widget.indexOf(self.images_tab)
        if ct_idx == -1:
            self.tab_widget.insertTab(0, self.images_tab, self.config.get('custom_tab_name_ct') or tr_ui("tab_ct_images"))
        else:
            self.tab_widget.setTabText(ct_idx, self.config.get('custom_tab_name_ct') or tr_ui("tab_ct_images"))

        # 2. Archive Tab
        archive_idx = self.tab_widget.indexOf(self.archive_tab)
        if show_archive:
            if archive_idx == -1:
                pacs_idx = self.tab_widget.indexOf(self.pacs_tab)
                insert_pos = pacs_idx if pacs_idx != -1 else 1
                self.tab_widget.insertTab(insert_pos, self.archive_tab, self.config.get('custom_tab_name_archive') or tr_ui("tab_ct_archive"))
            else:
                self.tab_widget.setTabText(archive_idx, self.config.get('custom_tab_name_archive') or tr_ui("tab_ct_archive"))
            self.images_tab.move_to_archive_btn.setVisible(True)
        else:
            if archive_idx != -1:
                self.tab_widget.removeTab(archive_idx)
            self.archive_tab.setParent(None)
            self.archive_tab.hide()
            self.images_tab.move_to_archive_btn.setVisible(False)

        # 3. PACS Tab
        pacs_idx = self.tab_widget.indexOf(self.pacs_tab)
        if show_pacs:
            if pacs_idx == -1:
                self.tab_widget.addTab(self.pacs_tab, self.config.get('custom_tab_name_pacs') or tr_ui("tab_pacs"))
            else:
                self.tab_widget.setTabText(pacs_idx, self.config.get('custom_tab_name_pacs') or tr_ui("tab_pacs"))
        else:
            if pacs_idx != -1:
                self.tab_widget.removeTab(pacs_idx)
            self.pacs_tab.setParent(None)
            self.pacs_tab.hide()
            # При отключении вкладки PACS отключаем автообновление и останавливаем таймер
            self.config['auto_update_is'] = 'off'
            if hasattr(self, 'pacs_auto_scan_cb'):
                self.pacs_auto_scan_cb.blockSignals(True)
                self.pacs_auto_scan_cb.setChecked(False)
                self.pacs_auto_scan_cb.blockSignals(False)
            if hasattr(self, 'pacs_timer'):
                self.pacs_timer.stop()

        # Восстанавливаем активный виджет
        if current_widget and self.tab_widget.indexOf(current_widget) != -1:
            self.tab_widget.setCurrentWidget(current_widget)
        else:
            self.tab_widget.setCurrentIndex(0)

        self.tab_widget.blockSignals(False)
        
        # Если вкладка архива включена, но кэш еще не собран - собираем в фоне
        if show_archive and getattr(self, 'archive_cache', None) is None:
            self.fill_archive_list(silent=True)
            
        self.update_tab_badges()

    def update_tab_badges(self):
        if not hasattr(self, 'images_tab') or not hasattr(self, 'archive_tab') or not hasattr(self, 'pacs_tab') or not hasattr(self, 'tab_widget'):
            return

        show_badges = self.config.get('show_study_counts', 'True').lower() == 'true'
        auto_update_on = self.config.get('auto_update_is', 'off').lower() == 'on'
        
        current_widget = self.tab_widget.currentWidget()
        pacs_tab_active = (current_widget == self.pacs_tab)
        show_pacs_badge = show_badges and (pacs_tab_active or auto_update_on)

        ct_count = len(self.images_cache) if getattr(self, 'images_cache', None) else 0
        archive_count = len(self.archive_cache) if getattr(self, 'archive_cache', None) else 0
        pacs_count = len(self.pacs_data) if getattr(self, 'pacs_data', None) else 0

        tab_bar = self.tab_widget.tabBar()

        widget_info = [
            (self.images_tab, show_badges, ct_count),
            (self.archive_tab, show_badges, archive_count),
            (self.pacs_tab, show_pacs_badge, pacs_count)
        ]

        active_indices = set()
        for tab_widget, should_show, count in widget_info:
            if not tab_widget:
                continue
            idx = self.tab_widget.indexOf(tab_widget)
            if idx != -1 and should_show:
                active_indices.add(idx)
                existing_btn = tab_bar.tabButton(idx, QTabBar.ButtonPosition.RightSide)
                badge = getattr(tab_widget, 'badge', None)
                
                # Если бейдж отсутствует, откреплен или не совпадает с текущей кнопкой - создаем чистый новый
                if badge is None or existing_btn != badge:
                    if existing_btn:
                        tab_bar.setTabButton(idx, QTabBar.ButtonPosition.RightSide, None)
                    if badge:
                        try:
                            badge.deleteLater()
                        except Exception:
                            pass
                    badge = TabBadge(tab_bar, idx)
                    tab_widget.badge = badge
                    tab_bar.setTabButton(idx, QTabBar.ButtonPosition.RightSide, badge)
                
                badge.tab_bar = tab_bar
                badge.tab_index = idx
                badge.set_count(count, force_update=True)
                badge.show()
                badge.update()
            else:
                old_badge = getattr(tab_widget, 'badge', None)
                if old_badge:
                    try:
                        old_badge.deleteLater()
                    except Exception:
                        pass
                    tab_widget.badge = None
                if idx != -1:
                    tab_bar.setTabButton(idx, QTabBar.ButtonPosition.RightSide, None)

        # Очищаем кнопки на вкладках, где бейдж не должен отображаться
        for i in range(tab_bar.count()):
            if i not in active_indices:
                if tab_bar.tabButton(i, QTabBar.ButtonPosition.RightSide) is not None:
                    tab_bar.setTabButton(i, QTabBar.ButtonPosition.RightSide, None)

        tab_bar.updateGeometry()

    def retranslate_ui(self):
        self.update_tabs_visibility()
        
        # Делегируем перевод компонентам вкладок
        if hasattr(self, 'images_tab'):
            self.images_tab.retranslate_ui()
        if hasattr(self, 'archive_tab'):
            self.archive_tab.retranslate_ui()
        if hasattr(self, 'pacs_tab'):
            self.pacs_tab.retranslate_ui()


