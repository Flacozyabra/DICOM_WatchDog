import os
import shutil
import re
from datetime import datetime
import pydicom

from core.logger import log_message
from core.locale_utils import tr_log
from core.dicom_utils import is_structure_file

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
    if os.path.normcase(os.path.abspath(src)) == os.path.normcase(os.path.abspath(dest)):
        return
    for dirpath, dirnames, filenames in os.walk(src):
        for filename in filenames:
            src_file = os.path.join(dirpath, filename)
            rel_path = os.path.relpath(dirpath, src)
            dest_dir = os.path.join(dest, rel_path)
            os.makedirs(dest_dir, exist_ok=True)
            dest_file = os.path.join(dest_dir, filename)
            
            if filename.lower().endswith('.dcm') or is_structure_file(src_file):
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
            if filename.lower().endswith('.dcm') or is_structure_file(os.path.join(dirpath, filename)):
                src_file = os.path.join(dirpath, filename)
                try:
                    ds_file = pydicom.dcmread(src_file)
                    if ds_file.PatientID != new_id:
                        ds_file.PatientID = new_id
                        ds_file.save_as(src_file)
                except Exception as e:
                    if output_field:
                        log_message(output_field, tr_log("log_dcm_update_id_warning", filename, e))

def get_folder_study_info(folder_path):
    """
    Извлекает метаданные исследования из папки (PatientID, PatientName, StudyDate, StudyTime).
    Приоритет отдается файлам КТ-срезов (Modality != 'RTSTRUCT' и с кадрами/пикселями),
    чтобы дата и время исследования всегда определялись по сканам КТ, а не по созданным позже контурам RTSTRUCT.
    """
    if not os.path.isdir(folder_path):
        return None

    dcm_files = []
    try:
        for root, dirs, files in os.walk(folder_path):
            for f in files:
                f_path = os.path.join(root, f)
                if f.lower().endswith('.dcm') or is_structure_file(f_path):
                    dcm_files.append(f_path)
    except Exception:
        pass

    if not dcm_files:
        return None

    target_file = None
    # 1. Ищем файл КТ-среза (не RTSTRUCT)
    for fpath in dcm_files:
        if not is_structure_file(fpath):
            try:
                ds = pydicom.dcmread(fpath, stop_before_pixels=True)
                mod = str(getattr(ds, 'Modality', 'CT'))
                if mod not in ('RTSTRUCT', 'RTPLAN', 'RTDOSE') and hasattr(ds, 'Rows'):
                    target_file = fpath
                    break
            except Exception:
                pass

    # 2. Если файл КТ-среза не найден, берем первый попавшийся DICOM файл
    if not target_file:
        target_file = dcm_files[0]

    try:
        ds = pydicom.dcmread(target_file, stop_before_pixels=True)
        raw_id = getattr(ds, 'PatientID', '')
        raw_name = getattr(ds, 'PatientName', '')
        study_date = str(getattr(ds, 'StudyDate', ''))
        study_time = str(getattr(ds, 'StudyTime', '000000'))
        
        date_time_string = study_date + study_time
        format_string = '%Y%m%d%H%M%S' if '.' not in study_time else '%Y%m%d%H%M%S.%f'
        study_dt = datetime.strptime(date_time_string, format_string)
        study_date_str = study_dt.strftime('%d.%m.%y - %H-%M')
        date_only_str = study_dt.strftime('%d.%m.%y')
        
        return {
            'ds': ds,
            'patient_id': raw_id,
            'patient_name': raw_name,
            'study_date_str': study_date_str,
            'date_only_str': date_only_str,
            'target_file': target_file
        }
    except Exception:
        return None

def find_matching_study_subfolder(parent_path, date_only_str, study_date_str):
    """
    Ищет существующую подпапку исследования в parent_path: сначала точное совпадение времени, 
    затем подпапку с совпадающей календарной датой исследования.
    """
    if not os.path.isdir(parent_path):
        return None
        
    exact_subfolder = os.path.join(parent_path, f"[{study_date_str}]")
    if os.path.exists(exact_subfolder):
        return exact_subfolder
        
    prefix = f"[{date_only_str}"
    try:
        for item in os.listdir(parent_path):
            item_path = os.path.join(parent_path, item)
            if os.path.isdir(item_path) and item.startswith(prefix) and item.endswith("]"):
                return item_path
    except Exception:
        pass
        
    return None

def make_folder_hierarchical(parent_path, output_field=None):
    """
    Преобразует плоскую папку пациента (где файлы лежат в корне)
    в иерархическую структуру, перенося файлы в подпапку с датой исследования.
    """
    info = get_folder_study_info(parent_path)
    if not info:
        return True # Пустая или файлы уже во вложенных папках
        
    study_date_str = info['study_date_str']
    study_subdir = os.path.join(parent_path, f"[{study_date_str}]")
    
    # Проверяем, не лежит ли файл уже во вложенной папке
    if os.path.dirname(os.path.abspath(info['target_file'])) != os.path.abspath(parent_path):
        return True

    os.makedirs(study_subdir, exist_ok=True)
    
    try:
        files_to_move = os.listdir(parent_path)
    except Exception:
        return False

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
    info = get_folder_study_info(path)
    if not info:
        return

    ds = info['ds']
    raw_patient_id = str(info['patient_id'])
    new_patient_id = raw_patient_id

    # 1. Если включено исправление ID (fix_patient_id)
    if fix_patient_id:
        if prefixes:
            for prefix in prefixes:
                prefix = prefix.strip()
                if prefix and new_patient_id.startswith(prefix):
                    new_patient_id = new_patient_id[len(prefix):]
                    break
        if not new_patient_id.isdigit():
            new_patient_id = remove_non_digits(new_patient_id)

    # 2. Если ID изменился в процессе фиксации, обновляем его во всех DICOM-файлах
    if fix_patient_id and new_patient_id != raw_patient_id:
        safe_update_patient_ids(path, new_patient_id, output_field)

    # 3. Если включено переименование папки исследования (rename_folder)
    if rename_folder:
        raw_name = str(info['patient_name'])
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

        study_date_str = info['study_date_str']
        date_only_str = info['date_only_str']

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
            if os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(parent_path)):
                safe_update_patient_ids(path, new_patient_id, output_field)
                return

            # Проверим, лежит ли существующее исследование в корне parent_path (плоская структура)
            exist_info = get_folder_study_info(parent_path)
            if exist_info and exist_info['date_only_str'] == date_only_str:
                # Совпадает по дате, сливаем в корень parent_path
                try:
                    safe_merge_folders(path, parent_path, new_patient_id)
                    if id_changed:
                        log_message(output_field, tr_log("log_files_merged_success_with_id", os.path.basename(parent_path), new_patient_id, patient_folder))
                    else:
                        log_message(output_field, tr_log("log_files_merged_success", os.path.basename(parent_path), patient_folder))
                except Exception as e:
                    log_message(output_field, tr_log("log_folders_merge_error", patient_folder, os.path.basename(parent_path), e))
                return

            # Если целевая папка плоская, переведем её в иерархическую структуру
            if not make_folder_hierarchical(parent_path, output_field):
                return
                
            # Ищем подпапку исследования для совпадения по дате (или создаем новую с подпапкой)
            matching_sub = find_matching_study_subfolder(parent_path, date_only_str, study_date_str)
            target_sub = matching_sub if matching_sub else os.path.join(parent_path, f"[{study_date_str}]")
            
            if os.path.exists(target_sub):
                try:
                    safe_merge_folders(path, target_sub, new_patient_id)
                    if id_changed:
                        log_message(output_field, tr_log("log_files_merged_success_with_id", os.path.basename(target_sub), new_patient_id, patient_folder))
                    else:
                        log_message(output_field, tr_log("log_files_merged_success", os.path.basename(target_sub), patient_folder))
                except Exception as e:
                    log_message(output_field, tr_log("log_folders_merge_error", patient_folder, os.path.basename(target_sub), e))
            else:
                success = False
                last_error = None
                for attempt in range(5):
                    try:
                        os.rename(path, target_sub)
                        success = True
                        break
                    except OSError as e:
                        last_error = e
                        import time
                        time.sleep(0.2)
                
                if success:
                    safe_update_patient_ids(target_sub, new_patient_id, output_field)
                    if id_changed:
                        log_message(output_field, tr_log("log_folder_renamed_success_with_id", patient_folder, f"{target_folder_name}/{os.path.basename(target_sub)}", new_patient_id))
                    else:
                        log_message(output_field, tr_log("log_folder_renamed_success", patient_folder, f"{target_folder_name}/{os.path.basename(target_sub)}"))
                else:
                    log_message(output_field, tr_log("log_folder_rename_error", patient_folder, last_error))
