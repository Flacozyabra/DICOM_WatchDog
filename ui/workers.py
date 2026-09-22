# -*- coding: utf-8 -*-
"""Background Worker Threads for DICOM WatchDog."""

import os
import threading
from datetime import datetime
from watchdog.events import FileSystemEventHandler

try:
    from PyQt6.QtCore import QThread, pyqtSignal, QObject
except ImportError:
    from PyQt5.QtCore import QThread, pyqtSignal, QObject

from core.logger import log_message
from core.dicom_utils import dict_create, collect_patient_studies, load_ct_cache, save_ct_cache
from core.rename_utils import process_patient_folder, move_study_folder_hierarchical, get_folder_study_info
from core.pacs import pacs_dict_create, download_patient_from_pacs
from core.locale_utils import tr_log, tr_ui


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
    count_updated = pyqtSignal(int)
    status_changed = pyqtSignal(str) # (status_text)
    log_emitted = pyqtSignal(str)
    archive_updated = pyqtSignal()

    def __init__(self, ct_images_dir, cleanup_structures_enabled, fix_patient_id_enabled, id_prefixes,
                 rename_study_folder_enabled, rename_study_folder_mode,
                 archive_dir, archive_enabled, archive_days, archive_cleanup_enabled, archive_cleanup_days,
                 scan_rtd=False, scan_rtp=False):
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
        self.scan_rtd = scan_rtd
        self.scan_rtp = scan_rtp
        self.archived_count = 0
        self.archive_cleaned = False
        self.has_read_errors = False

    def run(self):
        collector = ThreadLogCollector(emit_callback=self.log_emitted.emit)
        is_cleanup_struct_on = str(self.cleanup_structures_enabled).lower() == 'true'
        is_fix_id_on = str(self.fix_patient_id_enabled).lower() == 'true'
        is_rename_folder_on = str(self.rename_study_folder_enabled).lower() == 'true'
        is_archive_on = str(self.archive_enabled).lower() == 'true'
        is_cleanup_on = str(self.archive_cleanup_enabled).lower() == 'true'
        
        prefixes_list = []
        if self.id_prefixes:
            prefixes_list = [p.strip() for p in self.id_prefixes.split(',') if p.strip()]

        if is_archive_on and not self.archive_dir:
            collector.appendPlainText(tr_log("log_warn_auto_archive_not_configured"))

        # 1. Быстрая автоочистка старых файлов архива (если включена)
        archive_cleaned = False
        if self.archive_dir and is_cleanup_on:
            if self.isInterruptionRequested():
                return
            from core.archive import cleanup_old_archive_folders
            deleted_cnt = cleanup_old_archive_folders(self.archive_dir, self.archive_cleanup_days, collector)
            if deleted_cnt and deleted_cnt > 0:
                archive_cleaned = True

        if self.isInterruptionRequested():
            return

        # 2. Единый проход: исправление ID, переименование, автоархивация и построение таблицы
        patient_folders = []
        if os.path.exists(self.ct_images_dir):
            try:
                patient_folders = [os.path.join(self.ct_images_dir, d) for d in os.listdir(self.ct_images_dir)
                                   if os.path.isdir(os.path.join(self.ct_images_dir, d))]
            except Exception:
                patient_folders = []

        total_folders = len(patient_folders)
        patient_dict = {}
        total_archived = 0

        ct_cache = load_ct_cache()
        cache_lock = threading.Lock()

        if total_folders > 0:
            self.status_changed.emit(tr_ui("loading_scanning_folders_status"))
            now = datetime.now()
            from core.rename_utils import move_study_folder_hierarchical, get_folder_study_info, is_folder_locked
            from concurrent.futures import ThreadPoolExecutor, as_completed
            
            def process_single(path):
                if not os.path.exists(path):
                    return {}, 0

                # Если папка заблокирована другим процессом (ПК 1 пишет файлы / меняет ID)
                if is_folder_locked(path):
                    with cache_lock:
                        cached_entry = ct_cache.get(path)
                    if cached_entry:
                        with cache_lock:
                            studies = collect_patient_studies(
                                path, self.ct_images_dir, collector,
                                cleanup_structures=False,
                                scan_rtd=self.scan_rtd,
                                scan_rtp=self.scan_rtp,
                                cache=ct_cache
                            )
                        return studies, 0
                    return {}, 0

                active_path = path
                archived_in_study = 0

                # 2a. Исправление ID и переименование (только если папка изменилась или не в кэше)
                folder_mtime = 0.0
                try:
                    folder_mtime = os.path.getmtime(path)
                except Exception:
                    pass

                with cache_lock:
                    cached_entry = ct_cache.get(path)
                is_unmodified = (cached_entry is not None and cached_entry.get('mtime') == folder_mtime)

                if (is_fix_id_on or is_rename_folder_on) and not is_unmodified:
                    res_path = process_patient_folder(
                        path, collector,
                        fix_patient_id=is_fix_id_on,
                        prefixes=prefixes_list,
                        rename_folder=is_rename_folder_on,
                        rename_mode=self.rename_study_folder_mode
                    )
                    if res_path and os.path.exists(res_path):
                        active_path = res_path

                # 2b. Автоархивация (если включена)
                is_fully_archived = False
                if self.archive_dir and is_archive_on and os.path.exists(active_path):
                    target_folder = active_path
                    try:
                        subdirs = [os.path.join(target_folder, s) for s in os.listdir(target_folder)
                                   if os.path.isdir(os.path.join(target_folder, s))]
                    except Exception:
                        subdirs = []

                    if subdirs:
                        for sub in subdirs:
                            try:
                                folder_date = datetime.fromtimestamp(os.path.getmtime(sub))
                            except Exception:
                                continue
                            if (now - folder_date).days >= self.archive_days:
                                try:
                                    patient_name = tr_log("log_patient_unknown")
                                    info = get_folder_study_info(sub)
                                    if info and info.get('patient_name'):
                                        patient_name = str(info['patient_name'])
                                    if move_study_folder_hierarchical(sub, self.archive_dir, collector):
                                        archived_in_study += 1
                                    log_message(collector, tr_log("log_patient_moved_to_archive", patient_name, os.path.basename(target_folder)))
                                except Exception as e:
                                    log_message(collector, tr_log("log_patient_move_to_archive_error", os.path.basename(target_folder), e))
                    else:
                        try:
                            folder_date = datetime.fromtimestamp(os.path.getmtime(target_folder))
                        except Exception:
                            folder_date = now
                        if (now - folder_date).days >= self.archive_days:
                            try:
                                patient_name = tr_log("log_patient_unknown")
                                info = get_folder_study_info(target_folder)
                                if info and info.get('patient_name'):
                                    patient_name = str(info['patient_name'])
                                if move_study_folder_hierarchical(target_folder, self.archive_dir, collector):
                                    archived_in_study += 1
                                log_message(collector, tr_log("log_patient_moved_to_archive", patient_name, os.path.basename(target_folder)))
                                is_fully_archived = True
                            except Exception as e:
                                log_message(collector, tr_log("log_patient_move_to_archive_error", os.path.basename(target_folder), e))

                # 2c. Считывание исследования сразу в patient_dict (с использованием кэша)
                if not is_fully_archived and os.path.exists(active_path):
                    with cache_lock:
                        studies = collect_patient_studies(
                            active_path, self.ct_images_dir, collector,
                            cleanup_structures=is_cleanup_struct_on,
                            scan_rtd=self.scan_rtd,
                            scan_rtp=self.scan_rtp,
                            cache=ct_cache
                        )
                    return studies, archived_in_study
                return {}, archived_in_study

            max_w = min(8, max(1, (os.cpu_count() or 4)))
            completed_count = 0
            with ThreadPoolExecutor(max_workers=max_w) as executor:
                future_map = {executor.submit(process_single, p): p for p in patient_folders}
                for future in as_completed(future_map):
                    if self.isInterruptionRequested():
                        executor.shutdown(wait=False, cancel_futures=True)
                        return
                    completed_count += 1
                    self.progress.emit(completed_count, total_folders)
                    try:
                        studies, num_archived = future.result()
                        total_archived += num_archived
                        if studies:
                            patient_dict.update(studies)
                            self.count_updated.emit(len(patient_dict))
                    except Exception as e:
                        self.has_read_errors = True
                        p_path = future_map.get(future, "unknown")
                        log_message(collector, f"Error scanning folder {p_path}: {e}")

            self.progress.emit(total_folders, total_folders)

        # Сохраняем актуальный кэш на диск
        try:
            abs_ct = os.path.abspath(self.ct_images_dir) if self.ct_images_dir else None
            cleaned_cache = {}
            for p, data in ct_cache.items():
                if not os.path.exists(p):
                    continue
                if abs_ct:
                    try:
                        abs_p = os.path.abspath(p)
                        if os.path.commonpath([abs_p, abs_ct]) != abs_ct or abs_p == abs_ct:
                            continue
                    except ValueError:
                        continue
                cleaned_cache[p] = data
            save_ct_cache(cleaned_cache)
        except Exception:
            pass

        self.archived_count = total_archived
        self.archive_cleaned = archive_cleaned
        if any(term in m for m in collector.messages for term in ("Ошибка чтения", "Error reading", "Error scanning folder", "PermissionError", "WinError")):
            self.has_read_errors = True

        if not self.isInterruptionRequested():
            if self.archived_count > 0 or self.archive_cleaned:
                self.archive_updated.emit()
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
        try:
            pacs_dict, con = pacs_dict_create(
                collector,
                pacs_ip=self.pacs_ip,
                pacs_port=self.pacs_port,
                called_aet=self.called_aet,
                calling_aet=self.calling_aet,
                study_date=self.study_date
            )
        except Exception:
            from collections import defaultdict
            pacs_dict, con = defaultdict(dict), False
        self.finished.emit(pacs_dict, con, collector.messages)


class ArchiveScanWorker(QThread):
    finished = pyqtSignal(dict, list)
    progress = pyqtSignal(int, int)  # (current, total)
    count_updated = pyqtSignal(int)
    log_emitted = pyqtSignal(str)

    def __init__(self, archive_dir, cleanup_structures_enabled, scan_rtd=False, scan_rtp=False):
        super().__init__()
        self.archive_dir = archive_dir
        self.cleanup_structures_enabled = cleanup_structures_enabled
        self.scan_rtd = scan_rtd
        self.scan_rtp = scan_rtp

    def run(self):
        collector = ThreadLogCollector(emit_callback=self.log_emitted.emit)
        is_cleanup_struct_on = str(self.cleanup_structures_enabled).lower() == 'true'
        from core.archive import archive_dict_create
        d = archive_dict_create(
            self.archive_dir, collector,
            cleanup_structures=is_cleanup_struct_on,
            progress_callback=self.progress.emit,
            count_callback=self.count_updated.emit,
            is_interrupted=self.isInterruptionRequested,
            scan_rtd=self.scan_rtd,
            scan_rtp=self.scan_rtp
        )
        if not self.isInterruptionRequested():
            self.finished.emit(d, collector.messages)


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


class PacsDownloadWorker(QThread):
    finished = pyqtSignal(bool, str)
    progress = pyqtSignal(int, int)

    def __init__(self, patient_id, target_dir, pacs_ip, pacs_port, called_aet, calling_aet, study_instance_uid=None):
        super().__init__()
        self.patient_id = patient_id
        self.target_dir = target_dir
        self.pacs_ip = pacs_ip
        self.pacs_port = pacs_port
        self.called_aet = called_aet
        self.calling_aet = calling_aet
        self.study_instance_uid = study_instance_uid
        self.is_cancelled = False

    def cancel(self):
        self.is_cancelled = True

    def run(self):
        success, msg = download_patient_from_pacs(
            self.patient_id, self.target_dir,
            self.pacs_ip, self.pacs_port,
            self.called_aet, self.calling_aet,
            progress_callback=self.progress.emit,
            is_cancelled_callback=lambda: self.is_cancelled,
            study_instance_uid=self.study_instance_uid
        )
        self.finished.emit(success, msg)
