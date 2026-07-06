import os
import shutil
import json
import pydicom
from datetime import datetime
from collections import defaultdict

from core.logger import log_message
from core.config_utils import get_cache_path
from core.locale_utils import tr_log


def load_cache():
    cache_path = get_cache_path()
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_cache(cache_data):
    try:
        with open(get_cache_path(), "w", encoding="utf-8") as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=4)
    except Exception:
        pass


def archive_dict_create(archive_dir, output_field=None, cleanup_structures=False, progress_callback=None):
    """
    Создает словарь пациентов для архива, используя кэширование метаданных в файл JSON.
    Это предотвращает повторное чтение DICOM-файлов при больших архивах.
    """
    patient_data = defaultdict(dict)
    
    is_cleanup_on = False
    if hasattr(cleanup_structures, 'get'):
        is_cleanup_on = (cleanup_structures.get() == 'on')
    else:
        is_cleanup_on = (cleanup_structures == 'on' or cleanup_structures is True)
        
    cache = load_cache()
    scanned_paths = set()
    
    if not os.path.exists(archive_dir):
        return patient_data

    # Сканируем папки первого уровня в archive_dir
    try:
        items = os.listdir(archive_dir)
    except Exception as e:
        if output_field:
            log_message(output_field, tr_log("log_archive_access_error", e))
        return patient_data

    total_items = len(items)
    processed = 0

    for item in items:
        root = os.path.join(archive_dir, item)
        processed += 1
        if progress_callback and total_items > 0:
            progress_callback(processed, total_items)
        if not os.path.isdir(root):
            continue
            
        # Ищем DICOM файлы в этой папке
        try:
            files = os.listdir(root)
        except Exception:
            continue
            
        dcm_files = [f for f in files if f.endswith('.dcm')]
        if dcm_files:
            scanned_paths.add(root)
            try:
                mtime = os.path.getmtime(root)
            except Exception:
                mtime = 0.0
            
            cached_item = cache.get(root)
            if cached_item and cached_item.get('mtime') == mtime:
                p_id = cached_item['patient_id']
                patient_data[item] = {
                    'patient_id': p_id,
                    'patient_name': cached_item['patient_name'],
                    'modality': cached_item.get('modality', 'CT'),
                    'study_datetime': datetime.fromisoformat(cached_item['study_datetime']),
                    'body_part': cached_item['body_part'],
                    'folder_datetime': datetime.fromisoformat(cached_item['folder_datetime']),
                    'str': cached_item['str'],
                    'slices': cached_item.get('slices', len(dcm_files)),
                    'folder_name': item
                }
                
                # Если Fix Switch включен, проверяем/удаляем лишние STR
                if is_cleanup_on and cached_item['str'] > 1:
                    from core.dicom_utils import delete_redundant_str
                    deleted = delete_redundant_str(root, output_field)
                    if deleted > 0:
                        try:
                            str_count = len([f for f in os.listdir(root) if f.startswith('STR')])
                            patient_data[item]['str'] = str_count
                            cached_item['str'] = str_count
                            cached_item['mtime'] = os.path.getmtime(root)
                        except Exception:
                            pass
            else:
                file = dcm_files[0]
                file_path = os.path.join(root, file)
                try:
                    ds = pydicom.dcmread(file_path)
                    p_id = ds.PatientID
                    p_name = str(ds.PatientName)
                    p_modality = str(ds.get('Modality', 'CT'))
                    
                    date_time_string = ds.StudyDate + ds.StudyTime
                    format_string = '%Y%m%d%H%M%S' if '.' not in ds.StudyTime else '%Y%m%d%H%M%S.%f'
                    study_dt = datetime.strptime(date_time_string, format_string)
                    
                    body_part = ds.get('BodyPartExamined', '')
                    if not body_part:
                        body_part = ds.get('StudyDescription', '')
                    if not body_part:
                        body_part = ds.get('SeriesDescription', '')
                    body_part_str = str(body_part).strip() or "Unknown"
                    
                    folder_dt = datetime.fromtimestamp(os.path.getctime(root))
                    
                    str_files = [f for f in os.listdir(root) if f.startswith('STR')]
                    str_count = len(str_files)
                    
                    if is_cleanup_on and str_count > 1:
                        from core.dicom_utils import delete_redundant_str
                        delete_redundant_str(root, output_field)
                        try:
                            str_count = len([f for f in os.listdir(root) if f.startswith('STR')])
                        except Exception:
                            pass
                    
                    patient_data[item] = {
                        'patient_id': p_id,
                        'patient_name': p_name,
                        'modality': p_modality,
                        'study_datetime': study_dt,
                        'body_part': body_part_str,
                        'folder_datetime': folder_dt,
                        'str': str_count,
                        'slices': len(dcm_files),
                        'folder_name': item
                    }
                    
                    cache[root] = {
                        'mtime': os.path.getmtime(root),
                        'patient_id': p_id,
                        'patient_name': p_name,
                        'modality': p_modality,
                        'study_datetime': study_dt.isoformat(),
                        'body_part': body_part_str,
                        'folder_datetime': folder_dt.isoformat(),
                        'str': str_count,
                        'slices': len(dcm_files)
                    }
                except Exception as e:
                    if output_field:
                        log_message(output_field, tr_log("log_dcm_read_error", file_path, e))
                        
    # Удаляем из кэша папки, которых больше нет
    cleaned_cache = {path: data for path, data in cache.items() if path in scanned_paths}
    save_cache(cleaned_cache)
    
    return patient_data


def move_old_folders_to_archive(ct_images_dir, archive_dir, archive_days, output_field):
    """
    Переносит папки исследований старше archive_days дней из рабочей директории в архивную.
    """
    if not os.path.exists(ct_images_dir) or not archive_dir or archive_days <= 0:
        return
        
    for root, dirs, files in os.walk(ct_images_dir):
        for dir in dirs:
            folder_path = os.path.join(root, dir)
            try:
                folder_date = datetime.fromtimestamp(os.path.getctime(folder_path))
            except Exception:
                continue

            if (datetime.now() - folder_date).days >= archive_days:
                if not os.path.exists(archive_dir):
                    try:
                        os.makedirs(archive_dir)
                    except Exception as e:
                        log_message(output_field, tr_log("log_archive_create_error", e))
                        continue

                archive_path = os.path.join(archive_dir, dir)

                if os.path.exists(archive_path):
                    try:
                        shutil.rmtree(archive_path)
                    except Exception as e:
                        log_message(output_field, tr_log("log_archive_delete_existing_error", e))
                        continue

                try:
                    patient_name = tr_log("log_patient_unknown")
                    try:
                        dcm_files = [f for f in os.listdir(folder_path) if f.endswith('.dcm')]
                        if dcm_files:
                            ds = pydicom.dcmread(os.path.join(folder_path, dcm_files[0]), specific_tags=['PatientName'])
                            patient_name = str(ds.get('PatientName', tr_log("log_patient_unknown")))
                    except Exception:
                        pass

                    shutil.move(folder_path, archive_path)
                    log_message(output_field, tr_log("log_patient_moved_to_archive", patient_name, dir))
                except Exception as e:
                    log_message(output_field, tr_log("log_patient_move_to_archive_error", dir, e))


def cleanup_old_archive_folders(archive_dir, cleanup_days, output_field):
    """
    Удаляет из архива папки исследований, которые были изменены более cleanup_days дней назад.
    """
    if not os.path.exists(archive_dir) or cleanup_days <= 0:
        return

    now = datetime.now()
    deleted_count = 0
    
    try:
        items = os.listdir(archive_dir)
    except Exception as e:
        log_message(output_field, tr_log("log_archive_cleanup_access_error", e))
        return

    for item in items:
        path = os.path.join(archive_dir, item)
        if os.path.isdir(path):
            try:
                mtime = os.path.getmtime(path)
                folder_date = datetime.fromtimestamp(mtime)
            except Exception:
                continue

            days_old = (now - folder_date).days
            if days_old >= cleanup_days:
                try:
                    patient_name = tr_log("log_patient_unknown")
                    try:
                        dcm_files = [f for f in os.listdir(path) if f.endswith('.dcm')]
                        if dcm_files:
                            ds = pydicom.dcmread(os.path.join(path, dcm_files[0]), specific_tags=['PatientName'])
                            patient_name = str(ds.get('PatientName', tr_log("log_patient_unknown")))
                    except Exception:
                        pass

                    shutil.rmtree(path)
                    deleted_count += 1
                    log_message(output_field, tr_log("log_archive_cleanup_success", patient_name, item, days_old))
                except Exception as e:
                    log_message(output_field, tr_log("log_archive_cleanup_error", item, e))
