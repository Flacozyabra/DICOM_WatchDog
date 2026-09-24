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
from ui.viewer import DicomViewerPanel
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
        self._last_pruned_archive_mtime = None
        self.pacs_data = {}
        self.tab_badges = {}
        self.previous_pacs_data = {}
        self.standby_new_patients = {}
        self.pacs_download_worker = None
        
        from ui.table_context_menus import TableContextMenuManager
        self.context_menu_mgr = TableContextMenuManager(self)
        
        from ui.patient_operations import PatientOperationsManager
        self.patient_ops = PatientOperationsManager(self)
        
        from ui.table_state import TableStateManager
        self.table_state_mgr = TableStateManager(self)
        
        # Инициализируем таймеры до создания UI во избежание AttributeError
        self.pacs_timer = QTimer(self)
        self.pacs_timer.timeout.connect(self.auto_update_pacs)
        
        self.net_retry_timer = QTimer(self)
        self.net_retry_timer.setInterval(5000)
        self.net_retry_timer.timeout.connect(self.check_network_folder_retry)
        self.net_retry_count = 0
        self.net_retry_max = 24
        
        self.is_scanning_active = False
        self.last_scan_finished_time = 0
        self.pending_folder_scan = False
        self.folder_scan_retry_pending = False

        # Инициализируем наблюдатель за файловой системой и таймеры сна
        from ui.watcher_coordinator import WatchdogCoordinator
        self.watcher_coordinator = WatchdogCoordinator(self)
        self.debounce_timer = self.watcher_coordinator.debounce_timer
        self.archive_debounce_timer = self.watcher_coordinator.archive_debounce_timer
        self.system_check_timer = self.watcher_coordinator.system_check_timer
        self.watcher_coordinator.start_system_check_timer()

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
        
        # Мгновенная предзагрузка КТ и Архива из сохраненного кэша для мгновенного старта
        ct_dir = self.config.get('ct_images_dir', '')
        if ct_dir and os.path.exists(ct_dir):
            try:
                from core.dicom_utils import load_ct_cache_as_patient_dict
                scan_ct_rtd = not self.images_table.isColumnHidden(8) if hasattr(self, 'images_table') and self.images_table.columnCount() > 8 else False
                scan_ct_rtp = not self.images_table.isColumnHidden(9) if hasattr(self, 'images_table') and self.images_table.columnCount() > 9 else False
                cached_images = load_ct_cache_as_patient_dict(ct_dir, scan_rtd=scan_ct_rtd, scan_rtp=scan_ct_rtp)
                if cached_images:
                    self.images_cache = cached_images
                    self.update_images_table_ui()
            except Exception:
                pass

        show_archive = self.config.get('show_tab_archive', 'True').lower() == 'true'
        archive_dir = self.config.get('archive_dir', '') if show_archive else ''
        if archive_dir and os.path.exists(archive_dir):
            try:
                from core.archive import load_archive_cache_as_patient_dict
                scan_arch_rtd = not self.archive_table.isColumnHidden(8) if hasattr(self, 'archive_table') and self.archive_table.columnCount() > 8 else False
                scan_arch_rtp = not self.archive_table.isColumnHidden(9) if hasattr(self, 'archive_table') and self.archive_table.columnCount() > 9 else False
                cached_archive = load_archive_cache_as_patient_dict(archive_dir, scan_rtd=scan_arch_rtd, scan_rtp=scan_arch_rtp)
                if cached_archive:
                    self.archive_cache = cached_archive
                    self.update_archive_table_ui()
            except Exception:
                pass

        self.update_tab_badges()

        # Запуск таймеров и мониторинга
        self.restart_timers()
        
        # Первоначальное заполнение / фоновое сканирование
        self.show_patient_list()
        if not getattr(self, 'scan_worker', None) or not self.scan_worker.isRunning():
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

    def on_background_action_progress(self, patient_id, progress):
        if patient_id in self.active_file_operations:
            self.active_file_operations[patient_id]['progress'] = progress
        else:
            for k in self.active_file_operations:
                if k == patient_id or patient_id.startswith(k + '/') or patient_id.startswith(k + '\\') or k.startswith(patient_id + '/') or k.startswith(patient_id + '\\'):
                    self.active_file_operations[k]['progress'] = progress
                    break
        self.images_table.viewport().update()
        self.archive_table.viewport().update()

    def on_study_auto_op_started(self, patient_key, op_type):
        self.active_file_operations[patient_key] = {'op': op_type, 'progress': None}
        if hasattr(self, 'images_cache') and self.images_cache is not None:
            has_match = (patient_key in self.images_cache) or any(
                k.startswith(patient_key + '/') or k.startswith(patient_key + '\\') or
                self.images_cache[k].get('folder_name') == patient_key
                for k in self.images_cache
            )
            if not has_match:
                now = datetime.now()
                self.images_cache[patient_key] = {
                    'patient_id': patient_key,
                    'patient_name': "Unknown",
                    'folder_name': patient_key,
                    'study_date': '',
                    'study_datetime': now,
                    'folder_datetime': now,
                    'modality': 'CT',
                    'rtd': 0,
                    'rtp': 0,
                    'str': 0,
                    'slices': 0,
                    'body_part': '',
                    'is_placeholder': True
                }
                self.update_images_table_ui()
        self.images_table.viewport().update()

    def on_study_auto_op_progress(self, patient_key, progress):
        if patient_key in self.active_file_operations:
            self.active_file_operations[patient_key]['progress'] = progress
        else:
            for k in self.active_file_operations:
                if k == patient_key or patient_key.startswith(k + '/') or patient_key.startswith(k + '\\'):
                    self.active_file_operations[k]['progress'] = progress
                    break
        self.images_table.viewport().update()

    def on_study_auto_op_finished(self, patient_key):
        if patient_key in self.active_file_operations:
            del self.active_file_operations[patient_key]
        if hasattr(self, 'images_cache') and self.images_cache and patient_key in self.images_cache:
            if self.images_cache[patient_key].get('is_placeholder'):
                del self.images_cache[patient_key]
                self.update_images_table_ui()
        self.images_table.viewport().update()

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
            
        import time
        self.last_scan_finished_time = time.time()
        if hasattr(self, 'debounce_timer') and self.debounce_timer:
            self.debounce_timer.stop()

        if op_type == 'archive':
            log_message(self.output_field, tr_log("log_patient_archived", result, self.get_archive_destination_name()))
            patient_entry = None
            if self.images_cache and patient_id in self.images_cache:
                patient_entry = self.images_cache.pop(patient_id, None)
            if self.archive_cache is not None and patient_entry:
                self.archive_cache[patient_id] = patient_entry
            self.sync_ct_cache_to_disk()
            self.sync_archive_cache_to_disk()
            self.update_images_table_ui()
            self.update_archive_table_ui()
            self.update_tab_badges()

        elif op_type in ('delete', 'delete_images'):
            log_message(self.output_field, tr_log("log_patient_deleted", result))
            if self.images_cache and patient_id in self.images_cache:
                self.images_cache.pop(patient_id, None)
            self.sync_ct_cache_to_disk()
            self.update_images_table_ui()
            self.update_tab_badges()

        elif op_type == 'delete_archive':
            log_message(self.output_field, tr_log("log_patient_deleted", result))
            if self.archive_cache is not None and patient_id in self.archive_cache:
                self.archive_cache.pop(patient_id, None)
            self.sync_archive_cache_to_disk()
            self.update_archive_table_ui()
            self.update_tab_badges()

        elif op_type == 'clean_str':
            deleted, folder_desc = result
            log_message(self.output_field, tr_log("log_cleaned_str_files", deleted, folder_desc))
            if self.images_cache and patient_id in self.images_cache:
                self.images_cache[patient_id]['str'] = False
            self.sync_ct_cache_to_disk()
            self.update_images_table_ui()

        elif op_type == 'restore':
            log_message(self.output_field, tr_log("log_patient_restored_from_archive", result, self.get_ct_destination_name(), self.get_archive_source_name()))
            patient_entry = None
            if self.archive_cache is not None and patient_id in self.archive_cache:
                patient_entry = self.archive_cache.pop(patient_id, None)
            if self.images_cache is not None and patient_entry:
                self.images_cache[patient_id] = patient_entry
            self.restored_patient_ids.add(patient_id)
            self.sync_archive_cache_to_disk()
            self.sync_ct_cache_to_disk()
            self.update_images_table_ui()
            self.update_archive_table_ui()
            self.update_tab_badges()

        elif op_type == 'change_id':
            is_archive = result.get('is_archive', False)
            old_id = result.get('old_id', '')
            new_id = result.get('new_id', '')
            p_name = result.get('patient_name', '')
            top_path = result.get('top_path', '')
            old_top_path = result.get('old_top_path', '')

            old_folder_name = os.path.basename(old_top_path)
            new_folder_name = os.path.basename(top_path)

            if new_id != old_id:
                log_message(self.output_field, tr_log("log_patient_id_changed", p_name, old_id, new_id))
            else:
                log_message(self.output_field, tr_log("log_patient_id_resynced", p_name, new_id))

            target_cache = self.archive_cache if is_archive else self.images_cache
            if target_cache is not None:
                keys_to_update = []
                for k in list(target_cache.keys()):
                    if k == patient_id or k == old_folder_name or k.startswith(old_folder_name + '/') or k.startswith(old_folder_name + '\\'):
                        keys_to_update.append(k)

                for old_key in keys_to_update:
                    entry = target_cache.pop(old_key)
                    entry['patient_id'] = new_id
                    if 'folder_name' in entry:
                        entry['folder_name'] = entry['folder_name'].replace(old_folder_name, new_folder_name, 1)
                    new_key = old_key.replace(old_folder_name, new_folder_name, 1) if old_folder_name in old_key else new_folder_name
                    target_cache[new_key] = entry

                if is_archive:
                    self.sync_archive_cache_to_disk()
                    self.update_archive_table_ui()
                else:
                    self.sync_ct_cache_to_disk()
                    self.update_images_table_ui()

                self.update_tab_badges()

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
        _err.setText(tr_ui("dlg_error_archive_msg", err_msg) if op_type in ('archive', 'delete_archive') else tr_ui("dlg_error_delete_msg", err_msg))
        apply_dark_title_bar(_err)
        _err.exec()
        
        if op_type == 'archive':
            log_message(self.output_field, tr_log("log_failed_archive_patient", patient_id, err_msg))
        elif op_type in ('delete', 'delete_images', 'delete_archive'):
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
        pass

    def update_watcher_path(self):
        self.watcher_coordinator.update_watcher_path()

    def stop_file_watcher(self):
        self.watcher_coordinator.stop_file_watcher()

    def check_system_status(self):
        self.watcher_coordinator.check_system_status()

    def trigger_debounce(self):
        self.watcher_coordinator.trigger_debounce()

    def on_watcher_timeout(self):
        self.watcher_coordinator.on_watcher_timeout()

    def trigger_archive_debounce(self):
        self.watcher_coordinator.trigger_archive_debounce()

    def on_archive_watcher_timeout(self):
        self.watcher_coordinator.on_archive_watcher_timeout()

    def _trigger_rescan_if_idle(self):
        self.watcher_coordinator.trigger_rescan_if_idle()

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
        self.table_state_mgr.setup_table_properties(table)

    def show_header_context_menu(self, pos, table):
        self.context_menu_mgr.show_header_context_menu(pos, table)

    def on_section_moved(self, logical, old, new, table):
        self.table_state_mgr.on_section_moved(logical, old, new, table)

    def save_table_state(self, table):
        self.table_state_mgr.save_table_state(table)

    def restore_table_state(self, table):
        self.table_state_mgr.restore_table_state(table)

    def on_tab_changed(self, index):
        # Защитная проверка на случай срабатывания сигнала до инициализации всех таблиц
        if not hasattr(self, 'images_tab') or not hasattr(self, 'archive_tab') or not hasattr(self, 'pacs_tab'):
            return

        # Сброс выделения строк во всех таблицах при переключении вкладок
        self.selected_images_items = set()
        self.selected_archive_items = set()
        self.selected_images_patient_id = None
        self.selected_archive_patient_id = None

        if hasattr(self, 'images_table') and self.images_table:
            self.images_table.clearSelection()
            self.images_table.setCurrentIndex(self.images_table.model().index(-1, -1))
        if hasattr(self, 'archive_table') and self.archive_table:
            self.archive_table.clearSelection()
            self.archive_table.setCurrentIndex(self.archive_table.model().index(-1, -1))
        if hasattr(self, 'pacs_table') and self.pacs_table:
            self.pacs_table.clearSelection()
            self.pacs_table.setCurrentIndex(self.pacs_table.model().index(-1, -1))

        if hasattr(self, 'move_to_archive_btn') and self.move_to_archive_btn:
            self.move_to_archive_btn.setEnabled(False)
        if hasattr(self, 'move_from_archive_btn') and self.move_from_archive_btn:
            self.move_from_archive_btn.setEnabled(False)
        if hasattr(self, 'send_to_ct_btn') and self.send_to_ct_btn:
            self.send_to_ct_btn.setEnabled(False)

        self.update_tab_badges()

        current_widget = self.tab_widget.widget(index)
        pacs_auto_scan_on = self.config.get('auto_update_is', 'off').lower() == 'on'
        
        if current_widget == self.images_tab:  # CT images
            if not pacs_auto_scan_on:
                self.pacs_timer.stop()
            if not hasattr(self, 'images_cache') or self.images_cache is None:
                if not self.scan_worker or not self.scan_worker.isRunning():
                    self.show_patient_list()
            else:
                self.update_images_table_ui()
            QTimer.singleShot(0, self.focus_ct_images_search)
        elif current_widget == self.archive_tab:  # CT archive
            if not pacs_auto_scan_on:
                self.pacs_timer.stop()
            archive_dir = self.config.get('archive_dir', '')

            # Немедленно отсекаем удаленные исследования перед показом
            pruned = self._prune_missing_archive_records()

            if not hasattr(self, 'archive_cache') or self.archive_cache is None:
                if not self.archive_worker or not self.archive_worker.isRunning():
                    self.fill_archive_list(silent=False)
            elif not pruned and self.archive_table.rowCount() == 0:
                self.update_archive_table_ui()
            QTimer.singleShot(0, self.focus_ct_archive_search)
        elif current_widget == self.pacs_tab:  # PACS
            self.fill_pacs_list(silent=True)
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

    def start_folder_scan(self, force=False, clear_table=True):
        if self.scan_worker and self.scan_worker.isRunning():
            if force:
                try:
                    self.scan_worker.finished.disconnect()
                except Exception:
                    pass
                self.scan_worker.requestInterruption()
                self.scan_worker.quit()
                self.scan_worker.wait(500)
                self.scan_worker = None
            else:
                return

        if force and clear_table:
            self.is_first_scan = True
            self.images_cache = None
            self.images_table.setRowCount(0)
            if hasattr(self, 'images_tab') and hasattr(self.images_tab, 'badge') and self.images_tab.badge:
                self.images_tab.badge.set_count(0)

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

        # Запоминаем выделенного пациента и тип строки (дочерняя/родитель)
        self.selected_images_patient_id = None
        self.selected_images_is_child = False
        selected_ranges = self.images_table.selectedRanges()
        if selected_ranges:
            row = selected_ranges[0].topRow()
            id_item = self.images_table.item(row, 0)
            name_item = self.images_table.item(row, 1)
            if id_item:
                self.selected_images_patient_id = id_item.data(Qt.ItemDataRole.UserRole)
                self.selected_images_is_child = bool(name_item and name_item.text().startswith("  ↳"))

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

        scan_rtd = not self.images_table.isColumnHidden(8) if hasattr(self, 'images_table') and self.images_table.columnCount() > 8 else False
        scan_rtp = not self.images_table.isColumnHidden(9) if hasattr(self, 'images_table') and self.images_table.columnCount() > 9 else False

        self.scan_worker = FolderScanWorker(
            ct_dir, cleanup_str_val, fix_id_val, prefixes_val,
            rename_folder_enabled, rename_folder_mode,
            archive_dir, archive_enabled, archive_days,
            archive_cleanup_enabled, archive_cleanup_days,
            scan_rtd=scan_rtd, scan_rtp=scan_rtp,
            archive_destination_name=self.get_archive_destination_name()
        )
        self.scan_worker.finished.connect(self.on_folder_scan_finished)
        self.scan_worker.archive_updated.connect(self.on_archive_updated_by_scan)
        self.scan_worker.log_emitted.connect(lambda msg: log_message(self.output_field, msg))
        self.scan_worker.status_changed.connect(self.on_scan_status_changed)
        self.scan_worker.progress.connect(self.on_scan_progress)
        self.scan_worker.count_updated.connect(self.on_images_scan_count_updated)
        self.scan_worker.study_auto_op_started.connect(self.on_study_auto_op_started)
        self.scan_worker.study_auto_op_progress.connect(self.on_study_auto_op_progress)
        self.scan_worker.study_auto_op_finished.connect(self.on_study_auto_op_finished)
        
        self.is_scanning_active = True
        if hasattr(self, 'debounce_timer') and self.debounce_timer:
            self.debounce_timer.stop()

        self.scan_worker.start()

    def cancel_folder_scan(self):
        self.is_scanning_active = False
        import time
        self.last_scan_finished_time = time.time()
        if hasattr(self, 'debounce_timer') and self.debounce_timer:
            self.debounce_timer.stop()
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
        self.is_scanning_active = False
        import time
        self.last_scan_finished_time = time.time()
        if hasattr(self, 'debounce_timer') and self.debounce_timer:
            self.debounce_timer.stop()

        # Собираем существующие ID пациентов из предыдущего кэша и таблицы (исключая временные заглушки)
        existing_ids = set()
        if hasattr(self, 'images_cache') and self.images_cache:
            existing_ids.update(k for k, v in self.images_cache.items() if not v.get('is_placeholder'))
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

        # Оповещения срабатывают для новых пациентов после первоначального сканирования
        can_notify = not self.is_first_scan
        for patient_id, data in patient_dict.items():
            if 'patient_name' in data and 'study_datetime' in data and 'folder_datetime' in data and 'str' in data:
                if can_notify and patient_id not in existing_ids and patient_id not in self.restored_patient_ids:
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

        # Очищаем статусы авто-обработки, завершившиеся в ходе сканирования
        auto_keys = [k for k, v in self.active_file_operations.items() if v.get('op') in ('auto_process', 'process')]
        for k in auto_keys:
            del self.active_file_operations[k]

        self.images_cache = patient_dict
        # Завершили первое сканирование
        self.is_first_scan = False
        self.restored_patient_ids.clear()

        self.update_images_table_ui()
        self.update_tab_badges()

        # Сохраняем снимок папок верхнего уровня для heartbeat-проверки
        try:
            ct_dir = self.config.get('ct_images_dir', '')
            if ct_dir and os.path.isdir(ct_dir):
                snapshot = {
                    d: os.path.getmtime(os.path.join(ct_dir, d))
                    for d in os.listdir(ct_dir)
                    if os.path.isdir(os.path.join(ct_dir, d))
                }
                self.last_scanned_folder_snapshot = snapshot
                if hasattr(self, 'watcher_coordinator') and self.watcher_coordinator:
                    self.watcher_coordinator.last_scanned_folder_snapshot = snapshot
        except Exception:
            pass

        # Проверяем, нужно ли отложенное повторное сканирование (были события во время сканирования или ошибки чтения из-за блокировок)
        had_errors = getattr(self.scan_worker, 'has_read_errors', False) if hasattr(self, 'scan_worker') and self.scan_worker else False
        pending = getattr(self, 'pending_folder_scan', False)
        if pending:
            self.pending_folder_scan = False
            self.folder_scan_retry_pending = False
            QTimer.singleShot(2000, self._trigger_rescan_if_idle)
        elif had_errors and not getattr(self, 'folder_scan_retry_pending', False):
            self.folder_scan_retry_pending = True
            QTimer.singleShot(3000, self._trigger_rescan_if_idle)
        else:
            self.folder_scan_retry_pending = False

        # Если включена вкладка архива и настроена папка архива, подгружаем если еще не загружен или если архив изменился
        if self.config.get('show_tab_archive', 'True').lower() == 'true':
            archive_dir = self.config.get('archive_dir', '')
            if archive_dir and os.path.exists(archive_dir):
                archive_modified = getattr(self.scan_worker, 'archived_count', 0) > 0 or getattr(self.scan_worker, 'archive_cleaned', False)
                if getattr(self, 'archive_cache', None) is None or archive_modified:
                    if not self.archive_worker or not self.archive_worker.isRunning():
                        self.fill_archive_list(silent=True)
                    else:
                        self._pending_archive_scan = True

    def on_archive_updated_by_scan(self):
        if self.config.get('show_tab_archive', 'True').lower() == 'true':
            archive_dir = self.config.get('archive_dir', '')
            if archive_dir and os.path.exists(archive_dir):
                if hasattr(self, 'archive_worker') and self.archive_worker and self.archive_worker.isRunning():
                    self._pending_archive_scan = True
                else:
                    self.fill_archive_list(silent=True)

    def update_images_table_ui(self):
        if hasattr(self, 'images_tab') and self.images_tab:
            self.images_tab.populate_table(self.images_cache)


    def search_patient_images(self):
        if not hasattr(self, 'images_cache') or self.images_cache is None:
            self.start_folder_scan()
        else:
            self.update_images_table_ui()

    def open_current_folder_cmd(self, row, column):
        self.patient_ops.open_current_folder_cmd(row, column)

    # ================= КОНТЕКСТНЫЕ МЕНЮ И ДЕЙСТВИЯ =================

    def show_tab_context_menu(self, pos):
        self.context_menu_mgr.show_tab_context_menu(pos)

    def get_archive_destination_name(self):
        custom = self.config.get('custom_tab_name_archive')
        if custom:
            return f'"{custom}"'
        from core.locale_utils import get_current_langs
        _, log_lang = get_current_langs()
        return "архив" if log_lang == 'ru' else "archive"

    def get_archive_source_name(self):
        custom = self.config.get('custom_tab_name_archive')
        if custom:
            return f'"{custom}"'
        from core.locale_utils import get_current_langs
        _, log_lang = get_current_langs()
        return "архива" if log_lang == 'ru' else "archive"

    def get_ct_destination_name(self):
        custom = self.config.get('custom_tab_name_ct')
        if custom:
            return f'"{custom}"'
        return "CT images"

    def get_move_to_archive_text(self, count=None):
        custom = self.config.get('custom_tab_name_archive')
        from core.locale_utils import get_current_langs
        lang, _ = get_current_langs()
        if count is not None:
            if custom:
                return f'Переместить выбранные в "{custom}" ({count})' if lang == 'ru' else f'Move selected to "{custom}" ({count})'
            return tr_ui("ctx_archive_mass", count)
        if custom:
            return f'Переместить в "{custom}"' if lang == 'ru' else f'Move to "{custom}"'
        return tr_ui("btn_move_to_archive")

    def get_restore_to_ct_text(self, count=None):
        custom_ct = self.config.get('custom_tab_name_ct')
        custom_archive = self.config.get('custom_tab_name_archive')
        from core.locale_utils import get_current_langs
        lang, _ = get_current_langs()
        if count is not None:
            if custom_ct:
                return f'Восстановить выбранные в "{custom_ct}" ({count})' if lang == 'ru' else f'Restore selected to "{custom_ct}" ({count})'
            if custom_archive:
                return f'Восстановить выбранные из "{custom_archive}" ({count})' if lang == 'ru' else f'Restore selected from "{custom_archive}" ({count})'
            return tr_ui("ctx_restore_mass", count)
        if custom_ct:
            return f'Восстановить в "{custom_ct}"' if lang == 'ru' else f'Restore to "{custom_ct}"'
        if custom_archive:
            return f'Восстановить из "{custom_archive}"' if lang == 'ru' else f'Restore from "{custom_archive}"'
        return tr_ui("btn_restore_from_archive")

    def get_send_to_ct_text(self):
        custom = self.config.get('custom_tab_name_ct')
        if custom:
            from core.locale_utils import get_current_langs
            lang, _ = get_current_langs()
            return f'Отправить в "{custom}"' if lang == 'ru' else f'Send to "{custom}"'
        return tr_ui("btn_send_to_ct")

    def rename_tab_dialog(self, index):
        self.context_menu_mgr.rename_tab_dialog(index)

    def show_images_context_menu(self, pos):
        self.context_menu_mgr.show_images_context_menu(pos)

    def delete_patient_action(self, patient_id=None, patient_name=None):
        self.patient_ops.delete_patient_action(patient_id, patient_name)

    def change_patient_id_action(self, patient_id, patient_name, is_archive=False):
        self.patient_ops.change_patient_id_action(patient_id, patient_name, is_archive)

    def archive_patient_action(self, patient_id, patient_name=None):
        self.patient_ops.archive_patient_action(patient_id, patient_name)

    def clean_str_action(self, patient_id):
        self.patient_ops.clean_str_action(patient_id)

    def on_images_selection_changed(self):
        has_selection = len(self.images_table.selectedRanges()) > 0
        self.move_to_archive_btn.setEnabled(has_selection)

    def on_archive_selection_changed(self):
        has_selection = len(self.archive_table.selectedRanges()) > 0
        self.move_from_archive_btn.setEnabled(has_selection)

    def move_to_archive_cmd(self):
        self.patient_ops.move_to_archive_cmd()

    # ================= ЛОГИКА ТАБЛИЦЫ CT ARCHIVE =================

    def start_archive_scan(self, force=False, clear_table=True):
        self.fill_archive_list(silent=False, force=force, clear_table=clear_table)

    def fill_archive_list(self, silent=False, force=False, clear_table=True):
        if self.archive_worker and self.archive_worker.isRunning():
            if force:
                try:
                    self.archive_worker.finished.disconnect()
                except Exception:
                    pass
                self.archive_worker.requestInterruption()
                self.archive_worker.quit()
                self.archive_worker.wait(500)
                self.archive_worker = None
            else:
                self._pending_archive_scan = True
                return

        if force and clear_table:
            self.archive_cache = None
            self.archive_table.setRowCount(0)
            if hasattr(self, 'archive_tab') and hasattr(self.archive_tab, 'badge') and self.archive_tab.badge:
                self.archive_tab.badge.set_count(0)

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

        # Запоминаем выделенного пациента и тип строки (дочерняя/родитель)
        self.selected_archive_patient_id = None
        self.selected_archive_is_child = False
        selected_ranges = self.archive_table.selectedRanges()
        if selected_ranges:
            row = selected_ranges[0].topRow()
            id_item = self.archive_table.item(row, 0)
            name_item = self.archive_table.item(row, 1)
            if id_item:
                self.selected_archive_patient_id = id_item.data(Qt.ItemDataRole.UserRole)
                self.selected_archive_is_child = bool(name_item and name_item.text().startswith("  ↳"))

        # Если таблица архива пуста, сразу отображаем статус сканирования
        if self.archive_table.rowCount() == 0:
            self.archive_table.set_placeholder_state(tr_ui("placeholder_scanning_folder"), show_button=False)
            self.archive_table.update_placeholder_visibility()

        cleanup_str_val = self.config.get('cleanup_structures_enabled', 'False')
        scan_rtd = not self.archive_table.isColumnHidden(8) if hasattr(self, 'archive_table') and self.archive_table.columnCount() > 8 else False
        scan_rtp = not self.archive_table.isColumnHidden(9) if hasattr(self, 'archive_table') and self.archive_table.columnCount() > 9 else False
        self.archive_worker = ArchiveScanWorker(archive_dir, cleanup_str_val, scan_rtd=scan_rtd, scan_rtp=scan_rtp)
        self.archive_worker.finished.connect(lambda ad, lm: self.on_archive_scan_finished(ad, lm, silent))
        self.archive_worker.progress.connect(self.on_archive_scan_progress)
        self.archive_worker.count_updated.connect(self.on_archive_scan_count_updated)
        if not silent:
            self.archive_worker.log_emitted.connect(lambda msg: log_message(self.output_field, msg))
        
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
        archive_dir = self.config.get('archive_dir', '')
        if archive_dir and os.path.exists(archive_dir):
            try:
                self._last_pruned_archive_mtime = os.path.getmtime(archive_dir)
            except OSError:
                self._last_pruned_archive_mtime = None
        self.update_archive_table_ui()
        self.update_tab_badges()
        if getattr(self, '_pending_archive_scan', False):
            self._pending_archive_scan = False
            self.fill_archive_list(silent=True)

    def _prune_missing_archive_records(self, force=False):
        if not hasattr(self, 'archive_cache') or not self.archive_cache:
            return False
        archive_dir = self.config.get('archive_dir', '')
        if not archive_dir or not os.path.exists(archive_dir):
            return False

        try:
            curr_mtime = os.path.getmtime(archive_dir)
        except OSError:
            curr_mtime = None

        if not force and curr_mtime is not None and getattr(self, '_last_pruned_archive_mtime', None) == curr_mtime:
            return False

        try:
            existing_dirs = {os.path.normcase(d) for d in os.listdir(archive_dir)}
        except OSError:
            return False

        missing_keys = []
        for key, item in list(self.archive_cache.items()):
            folder_name = item.get('folder_name', key)
            if not folder_name:
                continue
            norm_name = os.path.normcase(folder_name)
            if norm_name not in existing_dirs:
                missing_keys.append(key)
            else:
                full_path = os.path.join(archive_dir, folder_name)
                try:
                    with os.scandir(full_path) as it:
                        if not any(it):
                            missing_keys.append(key)
                except OSError:
                    missing_keys.append(key)

        self._last_pruned_archive_mtime = curr_mtime

        if missing_keys:
            from core.archive import load_cache, save_cache
            cache = load_cache()
            cache_changed = False

            for key in missing_keys:
                pat_info = self.archive_cache.pop(key, {})
                folder_name = pat_info.get('folder_name', key)
                full_path = os.path.normpath(os.path.join(archive_dir, folder_name))
                norm_full = os.path.normcase(full_path)
                keys_to_del = [k for k in cache.keys() if os.path.normcase(os.path.normpath(k)) == norm_full]
                for k in keys_to_del:
                    del cache[k]
                    cache_changed = True

            if cache_changed:
                save_cache(cache)

            self.update_archive_table_ui()
            self.update_tab_badges()
            return True
        return False

    def remove_missing_archive_patient(self, patient_key: str):
        if not hasattr(self, 'archive_cache') or not self.archive_cache:
            return

        pat_info = self.archive_cache.pop(patient_key, {})
        pat_name = pat_info.get('patient_name', 'Unknown')
        p_id = pat_info.get('patient_id', patient_key)
        
        log_message(self.output_field, tr_log("log_patient_not_found_in_archive", pat_name, p_id, self.get_archive_destination_name()))

        archive_dir = self.config.get('archive_dir', '')
        if archive_dir and os.path.exists(archive_dir):
            folder_name = pat_info.get('folder_name', patient_key)
            full_path = os.path.normpath(os.path.join(archive_dir, folder_name))
            
            from core.archive import load_cache, save_cache
            cache = load_cache()
            keys_to_del = [k for k in cache.keys() if os.path.normcase(os.path.normpath(k)) == os.path.normcase(full_path)]
            if keys_to_del:
                for k in keys_to_del:
                    del cache[k]
                save_cache(cache)

        self.update_archive_table_ui()
        self.update_tab_badges()

    def update_archive_table_ui(self):
        if hasattr(self, 'archive_tab') and self.archive_tab:
            self.archive_tab.populate_table(self.archive_cache)


    def search_patient_archive(self):
        if not hasattr(self, 'archive_cache') or self.archive_cache is None:
            self.fill_archive_list()
            return
        self.update_archive_table_ui()

    def show_archive_context_menu(self, pos):
        self.context_menu_mgr.show_archive_context_menu(pos)

    def delete_archive_patient_action(self, patient_id=None, patient_name=None):
        self.patient_ops.delete_archive_patient_action(patient_id, patient_name)

    def move_from_archive_cmd(self):
        self.patient_ops.move_from_archive_cmd()

    # ================= ЛОГИКА ТАБЛИЦЫ PACS =================

    def fill_pacs_list(self, silent=False):
        self.start_pacs_scan(silent=silent, is_auto=False)

    def auto_update_pacs(self):
        self.start_pacs_scan(silent=True, is_auto=True)

    def start_pacs_scan(self, silent=False, is_auto=False):
        if self.pacs_worker and self.pacs_worker.isRunning():
            if is_auto:
                return
            # Если пользователь вручную инициировал опрос (вкладка/фильтр), отключаем старый поток и запускаем новый
            try:
                self.pacs_worker.finished.disconnect()
            except (TypeError, RuntimeError):
                pass

        if not silent:
            log_message(self.output_field, tr_log("log_connecting_pacs"))
            self.pacs_table.setRowCount(0)
            self.pacs_table.set_placeholder_text(tr_ui("placeholder_scanning"))
            self.pacs_table.update_placeholder_visibility()
            self.previous_pacs_data = {}
        else:
            if self.pacs_table.rowCount() == 0:
                self.pacs_table.set_placeholder_text(tr_ui("placeholder_scanning"))
                self.pacs_table.update_placeholder_visibility()

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
                log_message(self.output_field, msg, replace_suffix=tr_log("log_connecting_pacs"))
                has_fail_msg = True
            else:
                if not silent:
                    log_message(self.output_field, msg)

        if not con and not has_fail_msg:
            log_message(self.output_field, tr_log("log_failed_connect_pacs"), replace_suffix=tr_log("log_connecting_pacs"))

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

            table_needs_render = (display_dict != getattr(self, 'previous_pacs_data', {})) or (self.pacs_table.rowCount() == 0 and len(display_dict) > 0)
            if table_needs_render:
                self.pacs_data = display_dict.copy()
                self.previous_pacs_data = display_dict.copy()
                self.render_pacs_table()
            else:
                self.pacs_data = display_dict.copy()
                self.pacs_table.update_placeholder_visibility()
            self.update_tab_badges()

        elif not con:
            if not silent and not has_fail_msg:
                log_message(self.output_field, tr_log("log_failed_connect_pacs"), replace_suffix=tr_log("log_connecting_pacs"))
            
            if getattr(self, 'previous_pacs_data', None):
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
        if hasattr(self, 'pacs_tab') and self.pacs_tab:
            self.pacs_tab.render_table(self.pacs_data)


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
            self.start_folder_scan(force=True)

    def browse_archive_dir(self):
        current_dir = self.config.get('archive_dir', '')
        dir_path = QFileDialog.getExistingDirectory(self, "Выберите папку архива", current_dir)
        if dir_path:
            norm_path = os.path.normpath(dir_path)
            if norm_path != current_dir:
                self.config['archive_dir'] = norm_path
                self.save_current_config()
                self.archive_cache = None
                self.update_watcher_path()
                current_widget = self.tab_widget.currentWidget()
                self.fill_archive_list(silent=(current_widget != self.archive_tab), force=True)

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
                self.update_watcher_path()
                if show_archive:
                    self.fill_archive_list(silent=(current_widget != self.archive_tab), force=True)

            if ct_changed:
                self.images_cache = None
                self.is_first_scan = True
                self.update_watcher_path()
                self.start_folder_scan(force=True)
            elif not archive_changed:
                if current_widget == self.images_tab:
                    self.start_folder_scan()
                elif current_widget == self.archive_tab:
                    if self.archive_cache is None:
                        self.fill_archive_list()
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
        self.patient_ops.open_patient_folder(patient_id, is_archive=is_archive)

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
            if is_archive:
                self.remove_missing_archive_patient(patient_id)
            else:
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

    def sync_ct_cache_to_disk(self):
        if not hasattr(self, 'images_cache') or self.images_cache is None:
            return
        ct_dir = self.config.get('ct_images_dir', '')
        if not ct_dir:
            return
        try:
            from core.dicom_utils import save_ct_cache
            cache_data = {}
            for rel_p, d in self.images_cache.items():
                full_p = os.path.normpath(os.path.join(ct_dir, rel_p))
                if os.path.exists(full_p):
                    try:
                        mtime = os.path.getmtime(full_p)
                    except Exception:
                        mtime = 0.0
                    cache_data[full_p] = {
                        'patient_id': d.get('patient_id', ''),
                        'patient_name': d.get('patient_name', ''),
                        'modality': d.get('modality', 'CT'),
                        'study_datetime': d.get('study_datetime'),
                        'body_part': d.get('body_part', 'Unknown'),
                        'folder_datetime': d.get('folder_datetime'),
                        'str': d.get('str', 0),
                        'rtd': d.get('rtd', 0),
                        'rtp': d.get('rtp', 0),
                        'slices': d.get('slices', 0),
                        'mtime': mtime
                    }
            save_ct_cache(cache_data)
        except Exception:
            pass

    def sync_archive_cache_to_disk(self):
        if not hasattr(self, 'archive_cache') or self.archive_cache is None:
            return
        archive_dir = self.config.get('archive_dir', '')
        if not archive_dir:
            return
        try:
            from core.archive import save_cache
            cache_data = {}
            for rel_p, d in self.archive_cache.items():
                full_p = os.path.normpath(os.path.join(archive_dir, rel_p))
                if os.path.exists(full_p):
                    try:
                        mtime = os.path.getmtime(full_p)
                    except Exception:
                        mtime = 0.0
                    cache_data[full_p] = {
                        'patient_id': d.get('patient_id', ''),
                        'patient_name': d.get('patient_name', ''),
                        'modality': d.get('modality', 'CT'),
                        'study_datetime': d.get('study_datetime'),
                        'body_part': d.get('body_part', 'Unknown'),
                        'folder_datetime': d.get('folder_datetime'),
                        'str': d.get('str', 0),
                        'rtd': d.get('rtd', 0),
                        'rtp': d.get('rtp', 0),
                        'slices': d.get('slices', 0),
                        'mtime': mtime
                    }
            save_cache(cache_data)
        except Exception:
            pass

    def closeEvent(self, event):
        # Сохраняем актуальные кэши на диск перед выходом
        try:
            self.sync_ct_cache_to_disk()
            self.sync_archive_cache_to_disk()
        except Exception:
            pass
        # Останавливаем наблюдатель перед выходом, чтобы не зависал фоновый поток
        self.stop_file_watcher()

        # Останавливаем активные фоновые воркеры сканирования и файловых операций
        try:
            self.cancel_folder_scan()
        except Exception:
            pass
        try:
            self.cancel_archive_scan()
        except Exception:
            pass
        if hasattr(self, 'pacs_worker') and self.pacs_worker and self.pacs_worker.isRunning():
            try:
                self.pacs_worker.requestInterruption()
                self.pacs_worker.wait(1000)
            except Exception:
                pass
        for attr_name in list(self.__dict__.keys()):
            if attr_name.startswith("worker_"):
                w = getattr(self, attr_name, None)
                if w and hasattr(w, "isRunning") and w.isRunning():
                    try:
                        w.requestInterruption()
                        w.wait(500)
                    except Exception:
                        pass

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

        if getattr(self, 'images_cache', None) is not None:
            ct_count = len(self.images_cache)
        elif hasattr(self, 'images_tab') and getattr(self.images_tab, 'badge', None):
            ct_count = self.images_tab.badge.count()
        else:
            ct_count = 0

        if getattr(self, 'archive_cache', None) is not None:
            archive_count = len(self.archive_cache)
        elif hasattr(self, 'archive_tab') and getattr(self.archive_tab, 'badge', None):
            archive_count = self.archive_tab.badge.count()
        else:
            archive_count = 0

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
                badge.set_count(count, force_update=False)
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
        if hasattr(self, 'viewer_panel') and self.viewer_panel:
            self.viewer_panel.retranslate_ui()


