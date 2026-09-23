# -*- coding: utf-8 -*-
"""Watchdog and file system monitoring coordinator for DICOM WatchDog."""

import os
import time
from datetime import datetime

try:
    from PyQt6.QtCore import QObject, QTimer, Qt
except ImportError:
    from PyQt5.QtCore import QObject, QTimer, Qt

try:
    from watchdog.observers import Observer
except ImportError:
    Observer = None

from ui.workers import WatchdogHandler
from core.logger import log_message
from core.locale_utils import tr_log


class WatchdogCoordinator(QObject):
    """Координатор файлового наблюдателя (Watchdog), таймеров дебаунса и отслеживания состояния системы."""

    def __init__(self, main_window):
        super().__init__(main_window)
        self.main_window = main_window

        self.watcher_observer = None
        self.watcher_handler = None
        self.archive_watcher_handler = None
        self.currently_watched_dir = None
        self.currently_watched_archive_dir = None

        self.last_folder_heartbeat_time = 0
        self.last_scanned_folder_snapshot = None
        self.last_timer_timestamp = time.time()
        self.last_checked_date = datetime.now().date()

        # Таймер дебаунса для входящих КТ-снимков
        self.debounce_timer = QTimer(self)
        self.debounce_timer.setSingleShot(True)
        self.debounce_timer.timeout.connect(self.on_watcher_timeout)

        # Таймер дебаунса для архива КТ
        self.archive_debounce_timer = QTimer(self)
        self.archive_debounce_timer.setSingleShot(True)
        self.archive_debounce_timer.timeout.connect(self.on_archive_watcher_timeout)

        # Таймер отслеживания сна ПК, сети и смены суток (каждые 10 секунд)
        self.system_check_timer = QTimer(self)
        self.system_check_timer.setInterval(10000)
        self.system_check_timer.timeout.connect(self.check_system_status)

    @property
    def config(self):
        return getattr(self.main_window, 'config', {})

    @property
    def output_field(self):
        return getattr(self.main_window, 'output_field', None)

    def start_system_check_timer(self):
        self.last_timer_timestamp = time.time()
        self.last_checked_date = datetime.now().date()
        self.system_check_timer.start()

    def update_watcher_path(self):
        ct_dir = self.config.get('ct_images_dir', '')
        archive_dir = self.config.get('archive_dir', '')

        valid_ct_dir = ct_dir if (ct_dir and os.path.exists(ct_dir)) else None
        valid_archive_dir = archive_dir if (archive_dir and os.path.exists(archive_dir)) else None

        # Если ни одна папка не настроена или не существует — останавливаем наблюдатель
        if not valid_ct_dir and not valid_archive_dir:
            self.stop_file_watcher()
            return

        # Если пути не изменились и наблюдатель уже активен — ничего не делаем
        if (self.currently_watched_dir == valid_ct_dir and
            self.currently_watched_archive_dir == valid_archive_dir and
            self.watcher_observer and self.watcher_observer.is_alive()):
            return

        self.stop_file_watcher()

        if Observer is None:
            return

        try:
            self.watcher_observer = Observer()
            queued_conn = getattr(getattr(Qt, 'ConnectionType', Qt), 'QueuedConnection', getattr(Qt, 'QueuedConnection', 2))

            # 1. Мониторинг входящей папки КТ-исследований
            if valid_ct_dir:
                self.watcher_handler = WatchdogHandler()
                self.watcher_handler.changed.connect(self.trigger_debounce, queued_conn)
                self.watcher_observer.schedule(self.watcher_handler, valid_ct_dir, recursive=True)
                self.currently_watched_dir = valid_ct_dir
                log_message(self.output_field, tr_log("log_watcher_started", valid_ct_dir))
            else:
                self.currently_watched_dir = None

            # 2. Мониторинг папки архива КТ
            if valid_archive_dir and valid_archive_dir != valid_ct_dir:
                self.archive_watcher_handler = WatchdogHandler()
                self.archive_watcher_handler.changed.connect(self.trigger_archive_debounce, queued_conn)
                self.watcher_observer.schedule(self.archive_watcher_handler, valid_archive_dir, recursive=True)
                self.currently_watched_archive_dir = valid_archive_dir
            else:
                self.currently_watched_archive_dir = None

            self.watcher_observer.start()
        except Exception as e:
            self.currently_watched_dir = None
            self.currently_watched_archive_dir = None
            log_message(self.output_field, tr_log("log_watcher_failed", e))

    def stop_file_watcher(self):
        if self.watcher_observer:
            try:
                self.watcher_observer.stop()
                self.watcher_observer.join(0.5)
            except Exception:
                pass
            self.watcher_observer = None
        self.watcher_handler = None
        self.archive_watcher_handler = None
        self.currently_watched_dir = None
        self.currently_watched_archive_dir = None

    def trigger_debounce(self):
        # Если сканирование уже идет или действует кулдаун после него (1.5 сек),
        # откладываем повторное сканирование на момент после завершения
        mw = self.main_window
        if getattr(mw, 'is_scanning_active', False) or (hasattr(mw, 'scan_worker') and mw.scan_worker and mw.scan_worker.isRunning()):
            mw.pending_folder_scan = True
            return
        if time.time() - getattr(mw, 'last_scan_finished_time', 0) < 1.5:
            mw.pending_folder_scan = True
            return
        # 2 секунды задержки, чтобы дождаться окончания записи
        self.debounce_timer.start(2000)

    def on_watcher_timeout(self):
        if hasattr(self.main_window, 'start_folder_scan'):
            self.main_window.start_folder_scan()

    def trigger_archive_debounce(self):
        if hasattr(self, 'archive_debounce_timer'):
            self.archive_debounce_timer.start(1000)

    def on_archive_watcher_timeout(self):
        mw = self.main_window
        # 1. Немедленно удаляем из кэша, таблицы и бейджа отсутствующие папки
        if hasattr(mw, '_prune_missing_archive_records'):
            mw._prune_missing_archive_records()

        # 2. Если появились новые исследования на диске — синхронизируем в тихом режиме
        if not hasattr(mw, 'archive_worker') or not mw.archive_worker or not mw.archive_worker.isRunning():
            archive_dir = self.config.get('archive_dir', '')
            if archive_dir and os.path.exists(archive_dir):
                if hasattr(mw, 'fill_archive_list'):
                    mw.fill_archive_list(silent=True)
        else:
            mw._pending_archive_scan = True

    def trigger_rescan_if_idle(self):
        if self.debounce_timer.isActive():
            return
        mw = self.main_window
        if not getattr(mw, 'is_scanning_active', False):
            if not hasattr(mw, 'scan_worker') or not mw.scan_worker or not mw.scan_worker.isRunning():
                if hasattr(mw, 'start_folder_scan'):
                    mw.start_folder_scan()

    def check_system_status(self):
        now_ts = time.time()
        today = datetime.now().date()
        mw = self.main_window

        # 1. Проверка пробуждения от сна или глубокой задержки (>60 секунд)
        elapsed = now_ts - self.last_timer_timestamp
        self.last_timer_timestamp = now_ts

        if elapsed > 60:
            log_message(self.output_field, tr_log("log_system_resumed_from_sleep"))
            self.last_checked_date = today
            if hasattr(mw, 'update_images_table_ui'):
                mw.update_images_table_ui()
            # Сбрасываем счетчик повторов сети при выходе из сна
            mw.net_retry_count = 0
            if hasattr(mw, 'check_network_folder_retry'):
                mw.check_network_folder_retry()
            self.last_timer_timestamp = time.time()
            return

        # 2. Периодическая проверка восстановления сетевой папки, если соединение было ранее потеряно
        ct_dir = self.config.get('ct_images_dir', '')
        if ct_dir and hasattr(mw, 'net_retry_timer') and not mw.net_retry_timer.isActive() and not os.path.exists(ct_dir):
            if mw.net_retry_count >= mw.net_retry_max:
                mw.net_retry_count = 0
                if hasattr(mw, 'check_network_folder_retry'):
                    mw.check_network_folder_retry()

        # 3. Бесшумная проверка смены суток в полночь
        if self.last_checked_date != today:
            self.last_checked_date = today
            if hasattr(mw, 'update_images_table_ui'):
                mw.update_images_table_ui()

        # 4. Фоновая проверка расхождения общей/сетевой папки (heartbeat каждые 30 сек)
        if now_ts - self.last_folder_heartbeat_time >= 30:
            self.last_folder_heartbeat_time = now_ts
            if ct_dir and os.path.isdir(ct_dir) and not getattr(mw, 'is_scanning_active', False):
                try:
                    current_snapshot = {
                        d: os.path.getmtime(os.path.join(ct_dir, d))
                        for d in os.listdir(ct_dir)
                        if os.path.isdir(os.path.join(ct_dir, d))
                    }
                    if self.last_scanned_folder_snapshot is not None:
                        if current_snapshot != self.last_scanned_folder_snapshot:
                            self.last_scanned_folder_snapshot = current_snapshot
                            if hasattr(mw, 'start_folder_scan'):
                                mw.start_folder_scan()
                    else:
                        self.last_scanned_folder_snapshot = current_snapshot
                except Exception:
                    pass

        self.last_timer_timestamp = time.time()
