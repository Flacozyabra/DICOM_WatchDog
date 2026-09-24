import os
import sys
import shutil
import re
import time
import subprocess
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QMessageBox, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton
)

from core.logger import log_message
from core.locale_utils import tr_ui, tr_log
from core.dicom_utils import delete_redundant_str
from core.rename_utils import safe_update_patient_ids, get_folder_study_info, sanitize_folder_name
from ui.workers import BackgroundFileWorker
from ui.settings_tabs.settings_utils import apply_dark_title_bar


class ChangePatientIdDialog(QDialog):
    def __init__(self, parent, patient_name, current_id):
        super().__init__(parent)
        self.setWindowTitle(tr_ui("dlg_change_id_title"))
        self.setMinimumWidth(380)
        self.setStyleSheet("""
            QDialog {
                background-color: #1e1e1e;
                color: #ffffff;
                border: 1px solid #3d3d3d;
                border-radius: 6px;
                font-family: 'Segoe UI';
            }
            QLabel {
                color: #e0e0e0;
                font-family: 'Segoe UI';
            }
            QLineEdit {
                background-color: #2b2b2b;
                color: #ffffff;
                border: 1px solid #3d3d3d;
                border-radius: 4px;
                padding: 6px 10px;
                font-family: 'Segoe UI';
                font-size: 13px;
            }
            QLineEdit:focus {
                border: 1px solid #1f538d;
            }
            QPushButton {
                background-color: #2b2b2b;
                color: #ffffff;
                border: 1px solid #3d3d3d;
                border-radius: 4px;
                padding: 6px 18px;
                font-family: 'Segoe UI';
                font-size: 13px;
                min-width: 90px;
            }
            QPushButton:hover {
                background-color: #383838;
                border-color: #555555;
            }
            QPushButton#btn_apply {
                background-color: #1f538d;
                border-color: #2a6ab2;
                font-weight: bold;
            }
            QPushButton#btn_apply:hover {
                background-color: #2868ad;
                border-color: #3d7dc2;
            }
        """)
        apply_dark_title_bar(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        if patient_name:
            clean_name = patient_name.replace("  ↳", "").strip()
            lbl_name = QLabel(f"<b>{clean_name}</b>", self)
            lbl_name.setStyleSheet("font-size: 14px; color: #ffffff;")
            layout.addWidget(lbl_name)

        lbl_prompt = QLabel(tr_ui("dlg_change_id_label"), self)
        lbl_prompt.setStyleSheet("font-size: 12px; color: #aaaaaa;")
        layout.addWidget(lbl_prompt)

        self.edit_id = QLineEdit(self)
        self.edit_id.setText(str(current_id))
        self.edit_id.selectAll()
        layout.addWidget(self.edit_id)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)

        self.btn_apply = QPushButton(tr_ui("dlg_change_id_btn_apply"), self)
        self.btn_apply.setObjectName("btn_apply")
        self.btn_apply.clicked.connect(self.accept)

        self.btn_cancel = QPushButton(tr_ui("dlg_change_id_btn_cancel"), self)
        self.btn_cancel.clicked.connect(self.reject)

        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_apply)
        btn_layout.addWidget(self.btn_cancel)
        btn_layout.addStretch()

        layout.addLayout(btn_layout)

        self.edit_id.returnPressed.connect(self.accept)

    def get_new_id(self):
        return self.edit_id.text().strip()


def remove_folder_with_progress(p_path, progress_callback=None):
    files_to_del = []
    dirs_to_del = []
    for root, dirs, files in os.walk(p_path, topdown=False):
        for f in files:
            files_to_del.append(os.path.join(root, f))
        for d in dirs:
            dirs_to_del.append(os.path.join(root, d))
    total = len(files_to_del)
    for idx, f in enumerate(files_to_del, 1):
        try:
            os.remove(f)
        except Exception:
            pass
        if progress_callback and total > 0 and (idx % 10 == 0 or idx == total):
            progress_callback(idx / total)
    for d in dirs_to_del:
        try:
            os.rmdir(d)
        except Exception:
            pass
    try:
        if os.path.exists(p_path):
            os.rmdir(p_path)
    except Exception:
        pass
    if progress_callback:
        progress_callback(1.0)


class PatientOperationsManager:
    """Управляет операциями перемещения, архивации, удаления и открытия папок пациентов."""

    def __init__(self, main_window):
        self.mw = main_window

    def change_patient_id_action(self, patient_id, patient_name, is_archive=False):
        if patient_id in self.mw.active_file_operations:
            return

        dir_key = 'archive_dir' if is_archive else 'ct_images_dir'
        base_dir = self.mw.config.get(dir_key, '')
        if not base_dir or not os.path.exists(base_dir):
            return

        cache = self.mw.archive_cache if is_archive else self.mw.images_cache
        folder_name = str(patient_id)
        current_id = ""
        if cache and patient_id in cache:
            folder_name = cache[patient_id].get('folder_name', folder_name)
            current_id = cache[patient_id].get('patient_id', '')

        top_folder_name = folder_name.replace('\\', '/').split('/')[0] if ('/' in folder_name or '\\' in folder_name) else folder_name
        top_path = os.path.normpath(os.path.join(base_dir, top_folder_name))
        if not os.path.exists(top_path):
            if is_archive:
                self.mw.remove_missing_archive_patient(patient_id)
            else:
                log_message(self.mw.output_field, tr_log("log_path_not_exist", top_path))
            return

        if not current_id:
            info = get_folder_study_info(top_path)
            if info and info.get('patient_id'):
                current_id = str(info['patient_id'])
            else:
                current_id = top_folder_name

        dlg = ChangePatientIdDialog(self.mw, patient_name, current_id)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        new_id = dlg.get_new_id()
        if not new_id:
            return

        self.mw.active_file_operations[patient_id] = {'op': 'change_id', 'progress': None}
        if is_archive:
            self.mw.archive_table.viewport().update()
        else:
            self.mw.images_table.viewport().update()

        def run_change_id(progress_callback=None):
            # 1. Применяем ID ко ВСЕМ DICOM-файлам в папке и ее подпапках
            safe_update_patient_ids(top_path, new_id, self.mw.output_field, rt_only=False, progress_callback=progress_callback)

            # 2. Если ID изменился, переименовываем корневую папку пациента
            final_path = top_path
            if new_id != current_id:
                info = get_folder_study_info(top_path)
                p_name = info['patient_name'] if info else patient_name
                clean_p_name = str(p_name).replace('^', ' ').replace('_', ' ').replace('  ↳', '').strip()
                clean_p_name = re.sub(r'\s+', ' ', clean_p_name)
                name_part = sanitize_folder_name(clean_p_name)

                rename_mode = self.mw.config.get('rename_study_folder_mode', 'id')
                if rename_mode == 'name_id':
                    new_folder_name = f"{name_part} [{new_id}]" if name_part else str(new_id)
                elif rename_mode == 'id_name':
                    new_folder_name = f"[{new_id}] {name_part}" if name_part else str(new_id)
                elif rename_mode == 'name':
                    new_folder_name = name_part if name_part else str(new_id)
                else:
                    new_folder_name = str(new_id)

                new_top_path = os.path.normpath(os.path.join(base_dir, new_folder_name))
                if os.path.normcase(top_path) != os.path.normcase(new_top_path):
                    for attempt in range(5):
                        try:
                            os.rename(top_path, new_top_path)
                            final_path = new_top_path
                            break
                        except OSError:
                            time.sleep(0.2)

            return {
                'is_archive': is_archive,
                'old_id': current_id,
                'new_id': new_id,
                'patient_name': patient_name,
                'top_path': final_path,
                'old_top_path': top_path
            }

        worker = BackgroundFileWorker(patient_id, 'change_id', run_change_id)
        worker.progress.connect(self.mw.on_background_action_progress)
        worker.finished.connect(self.mw.on_background_action_finished)
        worker.error.connect(self.mw.on_background_action_error)
        op_key = f"worker_{patient_id}"
        setattr(self.mw, op_key, worker)
        worker.start()

    def get_selected_targets(self, table, cache, is_archive=False):
        """Extract unique target studies/patients from selected rows in the table."""
        selected_ranges = table.selectedRanges()
        if not selected_ranges:
            return []

        selected_rows = sorted({r for rng in selected_ranges for r in range(rng.topRow(), rng.bottomRow() + 1)})
        raw_targets = []
        for r in selected_rows:
            id_item = table.item(r, 0)
            name_item = table.item(r, 1)
            if not id_item:
                continue
            patient_id = id_item.data(Qt.ItemDataRole.UserRole)
            if not patient_id:
                continue
            patient_name = name_item.text() if name_item else ""
            is_child = bool(patient_name and patient_name.startswith("  ↳"))
            folder_name = patient_id
            if cache and patient_id in cache:
                folder_name = cache[patient_id].get('folder_name', folder_name)

            norm_folder = str(folder_name).replace('\\', '/')
            parent_folder = norm_folder.split('/')[0] if '/' in norm_folder else norm_folder

            raw_targets.append({
                'patient_id': patient_id,
                'patient_name': patient_name,
                'is_child': is_child,
                'folder_name': folder_name,
                'parent_folder': parent_folder,
                'row': r,
            })

        selected_parents = {t['parent_folder'] for t in raw_targets if not t['is_child']}
        filtered_targets = []
        for t in raw_targets:
            if t['is_child'] and t['parent_folder'] in selected_parents:
                continue
            filtered_targets.append(t)

        return filtered_targets

    def delete_patient_action(self, patient_id=None, patient_name=None):
        targets = []
        if patient_id is not None:
            sel_targets = self.get_selected_targets(self.mw.images_table, self.mw.images_cache, is_archive=False)
            if len(sel_targets) > 1 and any(t['patient_id'] == patient_id for t in sel_targets):
                targets = sel_targets
            else:
                folder_name = self.mw.images_cache[patient_id].get('folder_name', patient_id) if (self.mw.images_cache and patient_id in self.mw.images_cache) else patient_id
                is_child = bool(patient_name and str(patient_name).startswith("  ↳"))
                targets = [{
                    'patient_id': patient_id,
                    'patient_name': patient_name,
                    'is_child': is_child,
                    'folder_name': folder_name,
                }]
        else:
            targets = self.get_selected_targets(self.mw.images_table, self.mw.images_cache, is_archive=False)

        if not targets:
            return

        _dlg = QMessageBox(self.mw)
        _dlg.setIcon(QMessageBox.Icon.Question)
        _dlg.setWindowTitle(tr_ui("dlg_confirm_delete_title"))
        if len(targets) == 1:
            t = targets[0]
            _dlg.setText(tr_ui("dlg_confirm_delete_msg", t['patient_name'], t['patient_id']))
        else:
            _dlg.setText(tr_ui("dlg_confirm_mass_delete_msg", len(targets)))
        _dlg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        _dlg.setDefaultButton(QMessageBox.StandardButton.No)
        apply_dark_title_bar(_dlg)
        reply = _dlg.exec()

        if reply != QMessageBox.StandardButton.Yes:
            return

        ct_images_dir = self.mw.config.get('ct_images_dir', '')
        for t in targets:
            pid = t['patient_id']
            pname = t['patient_name']
            if pid in self.mw.active_file_operations:
                continue

            folder_name = t['folder_name']
            if not t.get('is_child') and ('/' in str(folder_name) or '\\' in str(folder_name)):
                folder_name = str(folder_name).replace('\\', '/').split('/')[0]

            path = os.path.join(ct_images_dir, folder_name)
            if not os.path.exists(path):
                log_message(self.mw.output_field, tr_log("log_path_not_exist", path))
                continue

            self.mw.active_file_operations[pid] = {'op': 'delete_images', 'progress': None}

            def make_run_delete(p_path, p_id, p_name):
                def run_delete(progress_callback=None):
                    remove_folder_with_progress(p_path, progress_callback=progress_callback)
                    parent_dir = os.path.dirname(p_path)
                    if parent_dir and os.path.exists(parent_dir) and not os.listdir(parent_dir):
                        try:
                            os.rmdir(parent_dir)
                        except Exception:
                            pass
                    return self.mw.get_folder_desc(p_id, p_name)
                return run_delete

            worker = BackgroundFileWorker(pid, 'delete_images', make_run_delete(path, pid, pname))
            worker.progress.connect(self.mw.on_background_action_progress)
            worker.finished.connect(self.mw.on_background_action_finished)
            worker.error.connect(self.mw.on_background_action_error)
            op_key = f"worker_{pid}"
            setattr(self.mw, op_key, worker)
            worker.start()

        self.mw.images_table.viewport().update()

    def archive_patient_action(self, patient_id, patient_name=None, is_child=None):
        if patient_id in self.mw.active_file_operations:
            return
            
        folder_name = self.mw.images_cache[patient_id].get('folder_name', patient_id) if (self.mw.images_cache and patient_id in self.mw.images_cache) else patient_id
        if is_child is None:
            is_child = bool(patient_name and str(patient_name).startswith("  ↳"))
        if not is_child and ('/' in str(folder_name) or '\\' in str(folder_name)):
            folder_name = str(folder_name).replace('\\', '/').split('/')[0]

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

        self.mw.active_file_operations[patient_id] = {'op': 'archive', 'progress': None}
        self.mw.images_table.viewport().update()
        
        def run_archive(progress_callback=None):
            from core.rename_utils import move_study_folder_hierarchical
            move_study_folder_hierarchical(path, archive_dir, self.mw.output_field, progress_callback=progress_callback)
            return self.mw.get_folder_desc(patient_id, patient_name)
            
        worker = BackgroundFileWorker(patient_id, 'archive', run_archive)
        worker.progress.connect(self.mw.on_background_action_progress)
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
            self.mw.active_file_operations[patient_id] = {'op': 'clean_str', 'progress': None}
            self.mw.images_table.viewport().update()
            
            def run_clean(progress_callback=None):
                deleted = delete_redundant_str(path, None, progress_callback=progress_callback)
                patient_name = ""
                if self.mw.images_cache and patient_id in self.mw.images_cache:
                    patient_name = self.mw.images_cache[patient_id].get('patient_name', '')
                folder_desc = self.mw.get_folder_desc(patient_id, patient_name)
                return deleted, folder_desc
                
            worker = BackgroundFileWorker(patient_id, 'clean_str', run_clean)
            worker.progress.connect(self.mw.on_background_action_progress)
            worker.finished.connect(self.mw.on_background_action_finished)
            worker.error.connect(self.mw.on_background_action_error)
            op_key = f"worker_{patient_id}"
            setattr(self.mw, op_key, worker)
            worker.start()

    def delete_archive_patient_action(self, patient_id=None, patient_name=None):
        targets = []
        if patient_id is not None:
            sel_targets = self.get_selected_targets(self.mw.archive_table, self.mw.archive_cache, is_archive=True)
            if len(sel_targets) > 1 and any(t['patient_id'] == patient_id for t in sel_targets):
                targets = sel_targets
            else:
                folder_name = self.mw.archive_cache[patient_id].get('folder_name', patient_id) if (self.mw.archive_cache and patient_id in self.mw.archive_cache) else patient_id
                is_child = bool(patient_name and str(patient_name).startswith("  ↳"))
                targets = [{
                    'patient_id': patient_id,
                    'patient_name': patient_name,
                    'is_child': is_child,
                    'folder_name': folder_name,
                }]
        else:
            targets = self.get_selected_targets(self.mw.archive_table, self.mw.archive_cache, is_archive=True)

        if not targets:
            return

        _dlg = QMessageBox(self.mw)
        _dlg.setIcon(QMessageBox.Icon.Question)
        _dlg.setWindowTitle(tr_ui("dlg_confirm_delete_title"))
        if len(targets) == 1:
            t = targets[0]
            _dlg.setText(tr_ui("dlg_confirm_delete_archive_msg", t['patient_name'], t['patient_id']))
        else:
            _dlg.setText(tr_ui("dlg_confirm_mass_delete_archive_msg", len(targets)))
        _dlg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        _dlg.setDefaultButton(QMessageBox.StandardButton.No)
        apply_dark_title_bar(_dlg)
        reply = _dlg.exec()

        if reply != QMessageBox.StandardButton.Yes:
            return

        archive_dir = self.mw.config.get('archive_dir', '')
        for t in targets:
            pid = t['patient_id']
            pname = t['patient_name']
            if pid in self.mw.active_file_operations:
                continue

            folder_name = t['folder_name']
            if not t.get('is_child') and ('/' in str(folder_name) or '\\' in str(folder_name)):
                folder_name = str(folder_name).replace('\\', '/').split('/')[0]

            path = os.path.join(archive_dir, folder_name)
            if not os.path.exists(path):
                self.mw.remove_missing_archive_patient(pid)
                continue

            self.mw.active_file_operations[pid] = {'op': 'delete_archive', 'progress': None}

            def make_run_delete(p_path, p_id, p_name):
                def run_delete(progress_callback=None):
                    remove_folder_with_progress(p_path, progress_callback=progress_callback)
                    parent_dir = os.path.dirname(p_path)
                    if parent_dir and os.path.exists(parent_dir) and not os.listdir(parent_dir):
                        try:
                            os.rmdir(parent_dir)
                        except Exception:
                            pass
                    return self.mw.get_folder_desc(p_id, p_name)
                return run_delete

            worker = BackgroundFileWorker(pid, 'delete_archive', make_run_delete(path, pid, pname))
            worker.progress.connect(self.mw.on_background_action_progress)
            worker.finished.connect(self.mw.on_background_action_finished)
            worker.error.connect(self.mw.on_background_action_error)
            op_key = f"worker_{pid}"
            setattr(self.mw, op_key, worker)
            worker.start()

        self.mw.archive_table.viewport().update()

    def move_to_archive_cmd(self):
        targets = self.get_selected_targets(self.mw.images_table, self.mw.images_cache, is_archive=False)
        if not targets:
            return
            
        self.mw.images_table.clearSelection()
        self.mw.move_to_archive_btn.setEnabled(False)
        for t in targets:
            self.archive_patient_action(t['patient_id'], t['patient_name'], is_child=t.get('is_child'))

    def move_from_archive_cmd(self):
        targets = self.get_selected_targets(self.mw.archive_table, self.mw.archive_cache, is_archive=True)
        if not targets:
            return
            
        self.mw.archive_table.clearSelection()
        self.mw.move_from_archive_btn.setEnabled(False)
        archive_dir = self.mw.config.get('archive_dir', '')
        ct_images_dir = self.mw.config.get('ct_images_dir', '')

        for t in targets:
            patient_id = t['patient_id']
            patient_name = t['patient_name']
            if patient_id in self.mw.active_file_operations:
                continue

            folder_name = t['folder_name']
            if not t.get('is_child') and ('/' in str(folder_name) or '\\' in str(folder_name)):
                folder_name = str(folder_name).replace('\\', '/').split('/')[0]

            path = os.path.join(archive_dir, folder_name)
            if not os.path.exists(path):
                self.mw.remove_missing_archive_patient(patient_id)
                continue

            dest_path = os.path.join(ct_images_dir, folder_name)
            dest_parent = os.path.dirname(dest_path)
            if dest_parent:
                os.makedirs(dest_parent, exist_ok=True)

            self.mw.active_file_operations[patient_id] = {'op': 'restore', 'progress': None}

            def make_run_restore(p_path, p_id, p_name):
                def run_restore(progress_callback=None):
                    from core.rename_utils import move_study_folder_hierarchical
                    move_study_folder_hierarchical(p_path, ct_images_dir, self.mw.output_field, progress_callback=progress_callback)
                    return self.mw.get_folder_desc(p_id, p_name)
                return run_restore

            worker = BackgroundFileWorker(patient_id, 'restore', make_run_restore(path, patient_id, patient_name))
            worker.progress.connect(self.mw.on_background_action_progress)
            worker.finished.connect(self.mw.on_background_action_finished)
            worker.error.connect(self.mw.on_background_action_error)
            op_key = f"worker_{patient_id}"
            setattr(self.mw, op_key, worker)
            worker.start()

        self.mw.archive_table.viewport().update()

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
            if is_archive:
                self.mw.remove_missing_archive_patient(patient_id)
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
