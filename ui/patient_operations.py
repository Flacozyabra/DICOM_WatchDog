# -*- coding: utf-8 -*-
"""Patient File Operations Controller for DICOM WatchDog."""

import os
import sys
import shutil
import subprocess
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox

from core.logger import log_message
from core.locale_utils import tr_ui, tr_log
from core.dicom_utils import delete_redundant_str
from ui.workers import BackgroundFileWorker
from ui.settings_tabs.settings_utils import apply_dark_title_bar


class PatientOperationsManager:
    """Управляет операциями перемещения, архивации, удаления и открытия папок пациентов."""

    def __init__(self, main_window):
        self.mw = main_window

    def delete_patient_action(self, patient_id, patient_name):
        if patient_id in self.mw.active_file_operations:
            return
            
        folder_name = self.mw.images_cache[patient_id].get('folder_name', patient_id) if (self.mw.images_cache and patient_id in self.mw.images_cache) else patient_id
        path = os.path.join(self.mw.config.get('ct_images_dir', ''), folder_name)
        if not os.path.exists(path):
            log_message(self.mw.output_field, tr_log("log_path_not_exist", path))
            return

        _dlg = QMessageBox(self.mw)
        _dlg.setIcon(QMessageBox.Icon.Question)
        _dlg.setWindowTitle(tr_ui("dlg_confirm_delete_title"))
        _dlg.setText(tr_ui("dlg_confirm_delete_msg", patient_name, patient_id))
        _dlg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        _dlg.setDefaultButton(QMessageBox.StandardButton.No)
        apply_dark_title_bar(_dlg)
        reply = _dlg.exec()
        
        if reply == QMessageBox.StandardButton.Yes:
            self.mw.active_file_operations[patient_id] = {'op': 'delete'}
            self.mw.images_table.viewport().update()
            
            def run_delete():
                shutil.rmtree(path, ignore_errors=True)
                return self.mw.get_folder_desc(patient_id, patient_name)
                
            worker = BackgroundFileWorker(patient_id, 'delete', run_delete)
            worker.finished.connect(self.mw.on_background_action_finished)
            worker.error.connect(self.mw.on_background_action_error)
            op_key = f"worker_{patient_id}"
            setattr(self.mw, op_key, worker)
            worker.start()

    def archive_patient_action(self, patient_id, patient_name=None):
        if patient_id in self.mw.active_file_operations:
            return
            
        folder_name = self.mw.images_cache[patient_id].get('folder_name', patient_id) if (self.mw.images_cache and patient_id in self.mw.images_cache) else patient_id
        path = os.path.join(self.mw.config.get('ct_images_dir', ''), folder_name)
        archive_dir = self.mw.config.get('archive_dir', '')
        
        if not os.path.exists(path):
            log_message(self.mw.output_field, tr_log("log_path_not_exist", path))
            return
            
        if not os.path.exists(archive_dir):
            os.makedirs(archive_dir, exist_ok=True)

        dest_path = os.path.join(archive_dir, folder_name)
        dest_parent = os.path.dirname(dest_path)
        if dest_parent:
            os.makedirs(dest_parent, exist_ok=True)

        self.mw.active_file_operations[patient_id] = {'op': 'archive'}
        self.mw.images_table.viewport().update()
        
        def run_archive():
            from core.rename_utils import move_study_folder_hierarchical
            move_study_folder_hierarchical(path, archive_dir, self.mw.output_field)
            return self.mw.get_folder_desc(patient_id, patient_name)
            
        worker = BackgroundFileWorker(patient_id, 'archive', run_archive)
        worker.finished.connect(self.mw.on_background_action_finished)
        worker.error.connect(self.mw.on_background_action_error)
        op_key = f"worker_{patient_id}"
        setattr(self.mw, op_key, worker)
        worker.start()

    def clean_str_action(self, patient_id):
        if patient_id in self.mw.active_file_operations:
            return
            
        folder_name = self.mw.images_cache[patient_id].get('folder_name', patient_id) if (self.mw.images_cache and patient_id in self.mw.images_cache) else patient_id
        path = os.path.join(self.mw.config.get('ct_images_dir', ''), folder_name)
        if os.path.exists(path):
            self.mw.active_file_operations[patient_id] = {'op': 'clean_str'}
            self.mw.images_table.viewport().update()
            
            def run_clean():
                deleted = delete_redundant_str(path, None)
                patient_name = ""
                if self.mw.images_cache and patient_id in self.mw.images_cache:
                    patient_name = self.mw.images_cache[patient_id].get('patient_name', '')
                folder_desc = self.mw.get_folder_desc(patient_id, patient_name)
                return deleted, folder_desc
                
            worker = BackgroundFileWorker(patient_id, 'clean_str', run_clean)
            worker.finished.connect(self.mw.on_background_action_finished)
            worker.error.connect(self.mw.on_background_action_error)
            op_key = f"worker_{patient_id}"
            setattr(self.mw, op_key, worker)
            worker.start()

    def delete_archive_patient_action(self, patient_id, patient_name):
        if patient_id in self.mw.active_file_operations:
            return
            
        folder_name = self.mw.archive_cache[patient_id].get('folder_name', patient_id) if (self.mw.archive_cache and patient_id in self.mw.archive_cache) else patient_id
        path = os.path.join(self.mw.config.get('archive_dir', ''), folder_name)
        if not os.path.exists(path):
            log_message(self.mw.output_field, tr_log("log_path_not_exist", path))
            return

        _dlg = QMessageBox(self.mw)
        _dlg.setIcon(QMessageBox.Icon.Question)
        _dlg.setWindowTitle(tr_ui("dlg_confirm_delete_title"))
        _dlg.setText(tr_ui("dlg_confirm_delete_archive_msg", patient_name, patient_id))
        _dlg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        _dlg.setDefaultButton(QMessageBox.StandardButton.No)
        apply_dark_title_bar(_dlg)
        reply = _dlg.exec()
        
        if reply == QMessageBox.StandardButton.Yes:
            self.mw.active_file_operations[patient_id] = {'op': 'delete'}
            self.mw.archive_table.viewport().update()
            
            def run_delete():
                shutil.rmtree(path, ignore_errors=True)
                return self.mw.get_folder_desc(patient_id, patient_name)
                
            worker = BackgroundFileWorker(patient_id, 'delete', run_delete)
            worker.finished.connect(self.mw.on_background_action_finished)
            worker.error.connect(self.mw.on_background_action_error)
            op_key = f"worker_{patient_id}"
            setattr(self.mw, op_key, worker)
            worker.start()

    def move_to_archive_cmd(self):
        selected_ranges = self.mw.images_table.selectedRanges()
        if not selected_ranges:
            return
            
        row = selected_ranges[0].topRow()
        id_item = self.mw.images_table.item(row, 0)
        patient_id = id_item.data(Qt.ItemDataRole.UserRole) if id_item else ""
        patient_name = self.mw.images_table.item(row, 1).text()
        self.mw.images_table.clearSelection()
        self.mw.move_to_archive_btn.setEnabled(False)
        self.archive_patient_action(patient_id, patient_name)

    def move_from_archive_cmd(self):
        selected_ranges = self.mw.archive_table.selectedRanges()
        if not selected_ranges:
            return
            
        row = selected_ranges[0].topRow()
        id_item = self.mw.archive_table.item(row, 0)
        patient_id = id_item.data(Qt.ItemDataRole.UserRole) if id_item else ""
        patient_name = self.mw.archive_table.item(row, 1).text()
        
        if patient_id in self.mw.active_file_operations:
            return
            
        archive_dir = self.mw.config.get('archive_dir', '')
        ct_images_dir = self.mw.config.get('ct_images_dir', '')
        
        folder_name = self.mw.archive_cache[patient_id].get('folder_name', patient_id) if (self.mw.archive_cache and patient_id in self.mw.archive_cache) else patient_id
        path = os.path.join(archive_dir, folder_name)
        if not os.path.exists(path):
            log_message(self.mw.output_field, tr_log("log_patient_not_found_in_archive", patient_id, patient_name))
            return
            
        dest_path = os.path.join(ct_images_dir, folder_name)
        dest_parent = os.path.dirname(dest_path)
        if dest_parent:
            os.makedirs(dest_parent, exist_ok=True)
            
        self.mw.archive_table.clearSelection()
        self.mw.move_from_archive_btn.setEnabled(False)
        self.mw.active_file_operations[patient_id] = {'op': 'restore'}
        self.mw.archive_table.viewport().update()
        
        def run_restore():
            from core.rename_utils import move_study_folder_hierarchical
            move_study_folder_hierarchical(path, ct_images_dir, self.mw.output_field)
            return self.mw.get_folder_desc(patient_id, patient_name)
            
        worker = BackgroundFileWorker(patient_id, 'restore', run_restore)
        worker.finished.connect(self.mw.on_background_action_finished)
        worker.error.connect(self.mw.on_background_action_error)
        op_key = f"worker_{patient_id}"
        setattr(self.mw, op_key, worker)
        worker.start()

    def open_patient_folder(self, patient_id, is_archive=False):
        dir_key = 'archive_dir' if is_archive else 'ct_images_dir'
        base_dir = self.mw.config.get(dir_key, '')
        if not base_dir or not os.path.exists(base_dir):
            return
        if not patient_id or not str(patient_id).strip() or str(patient_id).strip() in ('.', '/', '\\'):
            return
        folder_name = str(patient_id)
        path = os.path.normpath(os.path.join(base_dir, folder_name))
        if not os.path.exists(path):
            cache = self.mw.archive_cache if is_archive else self.mw.images_cache
            if cache and patient_id in cache:
                folder_name = cache[patient_id].get('folder_name', folder_name)
                path = os.path.normpath(os.path.join(base_dir, folder_name))
        if os.path.normcase(path) == os.path.normcase(os.path.normpath(base_dir)):
            return
        if os.path.exists(path):
            try:
                if sys.platform == "win32":
                    os.startfile(path)
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", path])
                else:
                    subprocess.Popen(["xdg-open", path])
            except Exception as e:
                log_message(self.mw.output_field, tr_log("log_failed_open_folder", folder_name, e))
        else:
            log_message(self.mw.output_field, tr_log("log_path_not_exist", path))

    def open_current_folder_cmd(self, row, column):
        id_item = self.mw.images_table.item(row, 0)
        name_item = self.mw.images_table.item(row, 1)
        patient_id = id_item.data(Qt.ItemDataRole.UserRole) if id_item else ""
        if not patient_id or not str(patient_id).strip() or str(patient_id).strip() in ('.', '/', '\\'):
            return
        is_child_row = bool(name_item and name_item.text().startswith("  ↳"))
        folder_to_open = str(patient_id)
        if not is_child_row and ('/' in folder_to_open or '\\' in folder_to_open):
            folder_to_open = folder_to_open.replace('\\', '/').split('/')[0]
        self.open_patient_folder(folder_to_open, is_archive=False)
