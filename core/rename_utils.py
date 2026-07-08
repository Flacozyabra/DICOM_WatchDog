import os
import shutil
import re
from datetime import datetime
import pydicom

from core.logger import log_message
from core.locale_utils import tr_log

def remove_non_digits(input_string):
    result = ''
    for char in input_string:
        if char.isdigit():
            result += char
    return result

def sanitize_folder_name(name):
    name_str = str(name)
    sanitized = re.sub(r'[\\/*?:"<>|]', '_', name_str)
    return sanitized.strip()

def safe_merge_folders(src, dest, new_id):
    if os.path.abspath(src) == os.path.abspath(dest):
        return
    for dirpath, dirnames, filenames in os.walk(src):
        for filename in filenames:
            src_file = os.path.join(dirpath, filename)
            rel_path = os.path.relpath(dirpath, src)
            dest_dir = os.path.join(dest, rel_path)
            os.makedirs(dest_dir, exist_ok=True)
            dest_file = os.path.join(dest_dir, filename)
            
            if filename.lower().endswith('.dcm') or filename.startswith('STR'):
                try:
                    ds_file = pydicom.dcmread(src_file)
                    ds_file.PatientID = new_id
                    ds_file.save_as(dest_file)
                except Exception:
                    shutil.copy2(src_file, dest_file)
            else:
                shutil.copy2(src_file, dest_file)
    shutil.rmtree(src)

def safe_update_patient_ids(folder_path, new_id, output_field=None):
    for dirpath, dirnames, filenames in os.walk(folder_path):
        for filename in filenames:
            if filename.lower().endswith('.dcm') or filename.startswith('STR'):
                src_file = os.path.join(dirpath, filename)
                try:
                    ds_file = pydicom.dcmread(src_file)
                    if ds_file.PatientID != new_id:
                        ds_file.PatientID = new_id
                        ds_file.save_as(src_file)
                except Exception as e:
                    if output_field:
                        log_message(output_field, tr_log("log_dcm_update_id_warning", filename, e))

def process_patient_folder(path, output_field, fix_patient_id=False, prefixes=None, rename_folder=False, rename_mode='id'):
    if not os.path.isdir(path):
        return

    patient_folder = os.path.basename(path)
    files = [f for f in os.listdir(path) if f.endswith('.dcm')]
    if not files:
        return

    try:
        ds = pydicom.dcmread(os.path.join(path, files[0]), stop_before_pixels=True)
    except Exception as e:
        log_message(output_field, tr_log("log_dcm_read_patient_error", patient_folder, e))
        return

    raw_patient_id = ds.PatientID
    new_patient_id = raw_patient_id

    # 1. Если включено исправление ID (fix_patient_id)
    if fix_patient_id:
        if prefixes:
            for prefix in prefixes:
                prefix = prefix.strip()
                if prefix and new_patient_id.startswith(prefix):
                    new_patient_id = new_patient_id[len(prefix):]
                    break
        # костыль для удаления точек и других символов
        if not new_patient_id.isdigit():
            new_patient_id = remove_non_digits(new_patient_id)

    # 2. Если ID изменился в процессе фиксации, обновляем его во всех DICOM-файлах
    if fix_patient_id and new_patient_id != raw_patient_id:
        safe_update_patient_ids(path, new_patient_id, output_field)

    # 3. Если включено переименование папки исследования (rename_folder)
    if rename_folder:
        raw_name = str(getattr(ds, "PatientName", ""))
        clean_name = raw_name.replace('^', ' ').replace('_', ' ').strip()
        clean_name = re.sub(r'\s+', ' ', clean_name)
        name_part = sanitize_folder_name(clean_name)
        if rename_mode == 'id':
            target_folder_name = str(new_patient_id)
        elif rename_mode == 'name':
            target_folder_name = name_part if name_part else str(new_patient_id)
        elif rename_mode == 'name_id':
            target_folder_name = f"{name_part} [{new_patient_id}]" if name_part else str(new_patient_id)
        elif rename_mode == 'id_name':
            target_folder_name = f"[{new_patient_id}] {name_part}" if name_part else str(new_patient_id)
        else:
            target_folder_name = patient_folder

        # Пытаемся получить дату исследования
        try:
            date_time_string = ds.StudyDate + ds.StudyTime
            format_string = '%Y%m%d%H%M%S' if '.' not in ds.StudyTime else '%Y%m%d%H%M%S.%f'
            study_dt = datetime.strptime(date_time_string, format_string)
            study_date_str = study_dt.strftime('%d.%m.%y - %H-%M')
        except Exception:
            study_date_str = "unknown_date"

        target_folder_name_with_date = f"{target_folder_name} [{study_date_str}]"

        if patient_folder not in [target_folder_name, target_folder_name_with_date] and target_folder_name:
            new_path = os.path.join(os.path.dirname(path), target_folder_name)
            id_changed = (new_patient_id != raw_patient_id)

            if os.path.exists(new_path):
                # Если целевая папка (без даты) уже существует, мы переименовываем ее, добавляя ее дату исследования.
                files_exist = [f for f in os.listdir(new_path) if f.endswith('.dcm')]
                if files_exist:
                    try:
                        ds_exist = pydicom.dcmread(os.path.join(new_path, files_exist[0]), stop_before_pixels=True)
                        date_time_string_exist = ds_exist.StudyDate + ds_exist.StudyTime
                        format_string_exist = '%Y%m%d%H%M%S' if '.' not in ds_exist.StudyTime else '%Y%m%d%H%M%S.%f'
                        study_dt_exist = datetime.strptime(date_time_string_exist, format_string_exist)
                        study_date_str_exist = study_dt_exist.strftime('%d.%m.%y - %H-%M')
                    except Exception:
                        study_date_str_exist = "unknown_date"
                    
                    target_folder_name_exist = f"{target_folder_name} [{study_date_str_exist}]"
                    new_path_exist = os.path.join(os.path.dirname(path), target_folder_name_exist)
                    
                    if new_path != new_path_exist and not os.path.exists(new_path_exist):
                        try:
                            os.rename(new_path, new_path_exist)
                            log_message(output_field, tr_log("log_folder_renamed_success", os.path.basename(new_path), target_folder_name_exist))
                        except Exception as e:
                            log_message(output_field, f"Error renaming existing folder {new_path} to {target_folder_name_exist}: {e}")

                # Теперь вычисляем имя для текущей папки с добавлением даты
                target_folder_name_current = f"{target_folder_name} [{study_date_str}]"
                new_path_current = os.path.join(os.path.dirname(path), target_folder_name_current)

                # Если папка с такой же датой уже существует, делаем слияние (дубликаты исследования)
                if os.path.exists(new_path_current):
                    try:
                        safe_merge_folders(path, new_path_current, new_patient_id)
                        if id_changed:
                            log_message(output_field, tr_log("log_files_merged_success_with_id", os.path.basename(new_path_current), new_patient_id, patient_folder))
                        else:
                            log_message(output_field, tr_log("log_files_merged_success", os.path.basename(new_path_current), patient_folder))
                    except Exception as e:
                        log_message(output_field, tr_log("log_folders_merge_error", patient_folder, os.path.basename(new_path_current), e))
                else:
                    success = False
                    last_error = None
                    for attempt in range(5):
                        try:
                            os.rename(path, new_path_current)
                            success = True
                            break
                        except OSError as e:
                            last_error = e
                            import time
                            time.sleep(0.2)
                    
                    if success:
                        safe_update_patient_ids(new_path_current, new_patient_id, output_field)
                        if id_changed:
                            log_message(output_field, tr_log("log_folder_renamed_success_with_id", patient_folder, target_folder_name_current, new_patient_id))
                        else:
                            log_message(output_field, tr_log("log_folder_renamed_success", patient_folder, target_folder_name_current))
                    else:
                        log_message(output_field, tr_log("log_folder_rename_error", patient_folder, last_error))
            else:
                # Если целевой папки нет, переименовываем в нее обычным образом
                success = False
                last_error = None
                for attempt in range(5):
                    try:
                        os.rename(path, new_path)
                        success = True
                        break
                    except OSError as e:
                        last_error = e
                        import time
                        time.sleep(0.2)
                
                if success:
                    safe_update_patient_ids(new_path, new_patient_id, output_field)
                    if id_changed:
                        log_message(output_field, tr_log("log_folder_renamed_success_with_id", patient_folder, target_folder_name, new_patient_id))
                    else:
                        log_message(output_field, tr_log("log_folder_renamed_success", patient_folder, target_folder_name))
                else:
                    log_message(output_field, tr_log("log_folder_rename_error", patient_folder, last_error))
