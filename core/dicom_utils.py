import os
import shutil
from datetime import datetime
from collections import defaultdict
import pydicom

from core.logger import log_message
from core.locale_utils import tr_log


def delete_redundant_str(patient_dir, output_field=None):
    """
    Deletes all files starting with 'STR' in the patient directory except the latest one.
    """
    if not os.path.exists(patient_dir):
        return 0
        
    files = [f for f in os.listdir(patient_dir) if f.startswith('STR')]
    if len(files) <= 1:
        return 0
        
    # Сортировка списка файлов по времени создания
    sorted_files = sorted(files, key=lambda x: os.path.getctime(os.path.join(patient_dir, x)))
    files_to_delete = sorted_files[:-1]  # Сохраняем только последний
    
    deleted_count = 0
    for file in files_to_delete:
        try:
            os.remove(os.path.join(patient_dir, file))
            deleted_count += 1
            if output_field:
                patient_id = os.path.basename(patient_dir)
                log_message(output_field, tr_log("log_str_deleted", patient_id, file))
        except Exception as e:
            if output_field:
                log_message(output_field, tr_log("log_str_delete_error", file, e))
                
    return deleted_count


def dict_create(ct_images_dir, output_field=None, cleanup_structures=False, progress_callback=None):
    patient_data = defaultdict(dict)

    is_cleanup_on = False
    if hasattr(cleanup_structures, 'get'):
        is_cleanup_on = (cleanup_structures.get() == 'on')
    else:
        is_cleanup_on = (cleanup_structures == 'on' or cleanup_structures is True)

    # Pre-count top-level subdirectories for accurate progress reporting
    try:
        top_dirs = [d for d in os.listdir(ct_images_dir) if os.path.isdir(os.path.join(ct_images_dir, d))]
        total_dirs = len(top_dirs)
    except Exception:
        total_dirs = 0
    processed = 0

    for root, dirs, files in os.walk(ct_images_dir):
        # Track progress at the top level only
        if os.path.dirname(root) == ct_images_dir or root == ct_images_dir:
            processed += 1
            if progress_callback and total_dirs > 0:
                progress_callback(processed, total_dirs)

        if files:
            file = files[0]
            if file.endswith('.dcm'):
                try:
                    ds = pydicom.dcmread(os.path.join(root, file), stop_before_pixels=True)
                    folder_name = os.path.basename(root)
                    patient_data[folder_name]['patient_id'] = ds.PatientID
                    patient_data[folder_name]['patient_name'] = ds.PatientName
                    patient_data[folder_name]['modality'] = str(ds.get('Modality', 'CT'))
                    patient_data[folder_name]['folder_name'] = folder_name

                    # учитываем два варианта записи времени исследования (с мкс и без)
                    date_time_string = ds.StudyDate + ds.StudyTime
                    format_string = '%Y%m%d%H%M%S' if '.' not in ds.StudyTime else '%Y%m%d%H%M%S.%f'
                    patient_data[folder_name]['study_datetime'] = datetime.strptime(date_time_string, format_string)

                    # область сканирования (BodyPartExamined / StudyDescription / SeriesDescription)
                    body_part = ds.get('BodyPartExamined', '')
                    if not body_part:
                        body_part = ds.get('StudyDescription', '')
                    if not body_part:
                        body_part = ds.get('SeriesDescription', '')
                    
                    body_part_str = str(body_part).strip()
                    if not body_part_str:
                        body_part_str = "Unknown"
                    patient_data[folder_name]['body_part'] = body_part_str

                    # время создания папки
                    patient_data[folder_name]['folder_datetime'] = datetime.fromtimestamp(os.path.getctime(root))
                    # считаем количество срезов (файлов .dcm)
                    dcm_count = len([f for f in files if f.lower().endswith('.dcm')])
                    patient_data[folder_name]['slices'] = dcm_count
                    # считаем количество файлов начинающихся с STR в папке пациента
                    str_files = [f for f in os.listdir(root) if f.startswith('STR')]
                    str_count = len(str_files)
                    patient_data[folder_name]['str'] = str_count

                    if is_cleanup_on and str_count > 1:
                        delete_redundant_str(root, output_field)
                        # Пересчитываем количество файлов STR
                        str_files = [f for f in os.listdir(root) if f.startswith('STR')]
                        patient_data[folder_name]['str'] = len(str_files)

                except Exception as e:
                    log_message(output_field, tr_log("log_dcm_read_error", os.path.join(root, file), e))

    return patient_data


def remove_non_digits(input_string):
    result = ''
    for char in input_string:
        if char.isdigit():
            result += char
    return result


def sanitize_folder_name(name):
    import re
    name_str = str(name)
    sanitized = re.sub(r'[\\/*?:"<>|]', '_', name_str)
    return sanitized.strip()

def safe_merge_folders(src, dest, new_id):
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
        import re
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

        if patient_folder != target_folder_name and target_folder_name:
            # Пытаемся получить дату исследования
            try:
                date_time_string = ds.StudyDate + ds.StudyTime
                format_string = '%Y%m%d%H%M%S' if '.' not in ds.StudyTime else '%Y%m%d%H%M%S.%f'
                study_dt = datetime.strptime(date_time_string, format_string)
                study_date_str = study_dt.strftime('%d.%m.%y - %H-%M')
            except Exception:
                study_date_str = "unknown_date"

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
                    import time
                    success = False
                    last_error = None
                    for attempt in range(5):
                        try:
                            os.rename(path, new_path_current)
                            success = True
                            break
                        except OSError as e:
                            last_error = e
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
                import time
                success = False
                last_error = None
                for attempt in range(5):
                    try:
                        os.rename(path, new_path)
                        success = True
                        break
                    except OSError as e:
                        last_error = e
                        time.sleep(0.2)
                
                if success:
                    safe_update_patient_ids(new_path, new_patient_id, output_field)
                    if id_changed:
                        log_message(output_field, tr_log("log_folder_renamed_success_with_id", patient_folder, target_folder_name, new_patient_id))
                    else:
                        log_message(output_field, tr_log("log_folder_renamed_success", patient_folder, target_folder_name))
                else:
                    log_message(output_field, tr_log("log_folder_rename_error", patient_folder, last_error))
