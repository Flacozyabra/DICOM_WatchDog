# -*- coding: utf-8 -*-
"""Background Worker Threads for DICOM WatchDog."""

import os
from watchdog.events import FileSystemEventHandler

try:
    from PyQt6.QtCore import QThread, pyqtSignal, QObject
except ImportError:
    from PyQt5.QtCore import QThread, pyqtSignal, QObject

from core.dicom_utils import dict_create, process_patient_folder
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
        is_cleanup_struct_on = str(self.cleanup_structures_enabled).lower() == 'true'
        is_fix_id_on = str(self.fix_patient_id_enabled).lower() == 'true'
        is_rename_folder_on = str(self.rename_study_folder_enabled).lower() == 'true'
        is_archive_on = str(self.archive_enabled).lower() == 'true'
        is_cleanup_on = str(self.archive_cleanup_enabled).lower() == 'true'
        
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
            progress_callback=self.progress.emit,
            count_callback=self.count_updated.emit
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

    def __init__(self, archive_dir, cleanup_structures_enabled):
        super().__init__()
        self.archive_dir = archive_dir
        self.cleanup_structures_enabled = cleanup_structures_enabled

    def run(self):
        collector = ThreadLogCollector(emit_callback=self.log_emitted.emit)
        is_cleanup_struct_on = str(self.cleanup_structures_enabled).lower() == 'true'
        from core.archive import archive_dict_create
        d = archive_dict_create(
            self.archive_dir, collector,
            cleanup_structures=is_cleanup_struct_on,
            progress_callback=self.progress.emit,
            count_callback=self.count_updated.emit
        )
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
