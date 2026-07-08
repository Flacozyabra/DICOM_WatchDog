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

def make_folder_hierarchical(parent_path, output_field=None):
    """
    Преобразует плоскую папку пациента (где файлы лежат в корне)
    в иерархическую структуру, перенося файлы в подпапку с датой исследования.
    """
    dcm_files = [f for f in os.listdir(parent_path) if f.lower().endswith('.dcm')]
    if not dcm_files:
        return True # Уже пустая или файлы уже во вложенных папках
        
    try:
        first_file = os.path.join(parent_path, dcm_files[0])
        ds = pydicom.dcmread(first_file, stop_before_pixels=True)
        date_time_string = ds.StudyDate + ds.StudyTime
        format_string = '%Y%m%d%H%M%S' if '.' not in ds.StudyTime else '%Y%m%d%H%M%S.%f'
        study_dt = datetime.strptime(date_time_string, format_string)
        study_date_str = study_dt.strftime('%d.%m.%y - %H-%M')
    except Exception as e:
        study_date_str = "unknown_date"
        
    study_subdir = os.path.join(parent_path, f"[{study_date_str}]")
    os.makedirs(study_subdir, exist_ok=True)
    
    files_to_move = os.listdir(parent_path)
    moved_files = []
    
    try:
        for item in files_to_move:
            item_path = os.path.join(parent_path, item)
            if os.path.isdir(item_path):
                continue
                
            dest_item_path = os.path.join(study_subdir, item)
            shutil.move(item_path, dest_item_path)
            moved_files.append((dest_item_path, item_path))
        return True
    except Exception as e:
        if output_field:
            log_message(output_field, tr_log("log_failed_make_hierarchical", os.path.basename(parent_path), e))
        for dest, src in moved_files:
            try:
                shutil.move(dest, src)
            except Exception:
                pass
        try:
            os.rmdir(study_subdir)
        except Exception:
            pass
        return False

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

        parent_path = os.path.join(os.path.dirname(path), target_folder_name)
        id_changed = (new_patient_id != raw_patient_id)

        if not os.path.exists(parent_path):
            # Первое исследование пациента, переименовываем в базовую папку без вложенности
            success = False
            last_error = None
            for attempt in range(5):
                try:
                    os.rename(path, parent_path)
                    success = True
                    break
                except OSError as e:
                    last_error = e
                    import time
                    time.sleep(0.2)
            
            if success:
                safe_update_patient_ids(parent_path, new_patient_id, output_field)
                if id_changed:
                    log_message(output_field, tr_log("log_folder_renamed_success_with_id", patient_folder, target_folder_name, new_patient_id))
                else:
                    log_message(output_field, tr_log("log_folder_renamed_success", patient_folder, target_folder_name))
            else:
                log_message(output_field, tr_log("log_folder_rename_error", patient_folder, last_error))

        else:
            # Папка пациента уже существует.
            # Если текущая папка и есть целевая папка пациента
            if os.path.abspath(path) == os.path.abspath(parent_path):
                # Просто структурируем файлы внутри нее (переносим файлы из корня в подпапку с датой)
                if make_folder_hierarchical(parent_path, output_field):
                    new_study_subdir = os.path.join(parent_path, f"[{study_date_str}]")
                    safe_update_patient_ids(new_study_subdir, new_patient_id, output_field)
                    if id_changed:
                        log_message(output_field, tr_log("log_folder_renamed_success_with_id", patient_folder, f"{target_folder_name}/[{study_date_str}]", new_patient_id))
                    else:
                        log_message(output_field, tr_log("log_folder_renamed_success", patient_folder, f"{target_folder_name}/[{study_date_str}]"))
                return

            # Если это другая папка, то работаем по стандартной схеме переноса/слияния
            # 1. Сначала делаем целевую папку иерархической, если в ее корне еще есть файлы
            if not make_folder_hierarchical(parent_path, output_field):
                # Если произошла блокировка файлов первого исследования, выходим
                return
                
            # 2. Вычисляем путь к подпапке для текущего (нового) исследования
            new_study_subdir = os.path.join(parent_path, f"[{study_date_str}]")
            
            if os.path.exists(new_study_subdir):
                # Такое же исследование уже существует (дубликат), выполняем слияние
                try:
                    safe_merge_folders(path, new_study_subdir, new_patient_id)
                    if id_changed:
                        log_message(output_field, tr_log("log_files_merged_success_with_id", os.path.basename(new_study_subdir), new_patient_id, patient_folder))
                    else:
                        log_message(output_field, tr_log("log_files_merged_success", os.path.basename(new_study_subdir), patient_folder))
                except Exception as e:
                    log_message(output_field, tr_log("log_folders_merge_error", patient_folder, os.path.basename(new_study_subdir), e))
            else:
                # Новое исследование для этого пациента, переименовываем в подпапку
                success = False
                last_error = None
                for attempt in range(5):
                    try:
                        os.rename(path, new_study_subdir)
                        success = True
                        break
                    except OSError as e:
                        last_error = e
                        import time
                        time.sleep(0.2)
                
                if success:
                    safe_update_patient_ids(new_study_subdir, new_patient_id, output_field)
                    if id_changed:
                        log_message(output_field, tr_log("log_folder_renamed_success_with_id", patient_folder, f"{target_folder_name}/[{study_date_str}]", new_patient_id))
                    else:
                        log_message(output_field, tr_log("log_folder_renamed_success", patient_folder, f"{target_folder_name}/[{study_date_str}]"))
                else:
                    log_message(output_field, tr_log("log_folder_rename_error", patient_folder, last_error))
