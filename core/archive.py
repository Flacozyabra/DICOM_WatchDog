import os
import shutil
import json
import pydicom
from datetime import datetime
from collections import defaultdict

from core.logger import log_message
from core.config_utils import get_cache_path
from core.locale_utils import tr_log
from core.dicom_utils import is_structure_file


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
    except Exception as e:
        try:
            from core.config_utils import get_log_path
            with open(get_log_path(), "a", encoding="utf-8") as log_f:
                log_f.write(f"[{datetime.now()}] Failed to save archive cache: {e}\n")
        except Exception:
            pass


def archive_dict_create(archive_dir, output_field=None, cleanup_structures=False, progress_callback=None, count_callback=None, is_interrupted=None):
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

    # Pre-count top-level directories for accurate progress reporting
    try:
        top_dirs = [d for d in os.listdir(archive_dir) if os.path.isdir(os.path.join(archive_dir, d))]
        total_dirs = len(top_dirs)
    except Exception:
        total_dirs = 0
    processed = 0

    for root, dirs, files in os.walk(archive_dir):
        if is_interrupted and is_interrupted():
            return patient_data

        # Track progress at the top level only
        if os.path.dirname(root) == archive_dir or root == archive_dir:
            processed += 1
            if progress_callback and total_dirs > 0:
                progress_callback(processed, total_dirs)

        if files:
            dcm_files = [f for f in files if f.lower().endswith('.dcm')]
            if dcm_files:
                scanned_paths.add(root)
                rel_path = os.path.relpath(root, archive_dir).replace('\\', '/')
                try:
                    mtime = os.path.getmtime(root)
                except Exception:
                    mtime = 0.0
                
                cached_item = cache.get(root)
                if cached_item and cached_item.get('mtime') == mtime:
                    p_id = cached_item['patient_id']
                    patient_data[rel_path] = {
                        'patient_id': p_id,
                        'patient_name': cached_item['patient_name'],
                        'modality': cached_item.get('modality', 'CT'),
                        'study_datetime': datetime.fromisoformat(cached_item['study_datetime']),
                        'body_part': cached_item['body_part'],
                        'folder_datetime': datetime.fromisoformat(cached_item['folder_datetime']),
                        'str': cached_item['str'],
                        'slices': cached_item.get('slices', len([f for f in dcm_files if not is_structure_file(os.path.join(root, f))])),
                        'folder_name': rel_path
                    }
                    if count_callback:
                        count_callback(len(patient_data))
                    
                    # Если Fix Switch включен, проверяем/удаляем лишние STR
                    if is_cleanup_on and cached_item['str'] > 1:
                        from core.dicom_utils import delete_redundant_str
                        deleted = delete_redundant_str(root, output_field)
                        if deleted > 0:
                            try:
                                str_count = len([f for f in os.listdir(root) if is_structure_file(os.path.join(root, f))])
                                patient_data[rel_path]['str'] = str_count
                                cached_item['str'] = str_count
                                cached_item['mtime'] = os.path.getmtime(root)
                            except Exception:
                                pass
                else:
                    file = dcm_files[0]
                    for f in dcm_files:
                        if not is_structure_file(os.path.join(root, f)):
                            file = f
                            break
                    file_path = os.path.join(root, file)
                    try:
                        ds = pydicom.dcmread(file_path, stop_before_pixels=True)
                        p_id = getattr(ds, 'PatientID', '') or str(ds.get('PatientID', ''))
                        p_name = getattr(ds, 'PatientName', '') or str(ds.get('PatientName', ''))
                        if not p_id:
                            p_id = os.path.basename(root)
                        if not p_name:
                            p_name = os.path.basename(root)
                        p_modality = str(ds.get('Modality', 'CT'))
                        
                        folder_dt = datetime.fromtimestamp(max(os.path.getctime(root), os.path.getmtime(root)))
                        
                        study_date = str(ds.get('StudyDate', '')).strip()
                        study_time = str(ds.get('StudyTime', '')).strip()
                        study_dt = folder_dt
                        if study_date:
                            try:
                                date_time_string = study_date + study_time
                                study_dt = datetime.strptime(date_time_string[:14], '%Y%m%d%H%M%S')
                            except Exception:
                                try:
                                    study_dt = datetime.strptime(study_date, '%Y%m%d')
                                except Exception:
                                    pass
                        
                        body_part = ds.get('BodyPartExamined', '')
                        if not body_part:
                            body_part = ds.get('StudyDescription', '')
                        if not body_part:
                            body_part = ds.get('SeriesDescription', '')
                        body_part_str = str(body_part).strip() or "Unknown"
                        
                        str_files = [f for f in os.listdir(root) if is_structure_file(os.path.join(root, f))]
                        str_count = len(str_files)
                        
                        if is_cleanup_on and str_count > 1:
                            from core.dicom_utils import delete_redundant_str
                            delete_redundant_str(root, output_field)
                            try:
                                str_count = len([f for f in os.listdir(root) if is_structure_file(os.path.join(root, f))])
                            except Exception:
                                pass
                        
                        slice_files = [f for f in dcm_files if not is_structure_file(os.path.join(root, f))]
                        slices_cnt = len(slice_files)

                        patient_data[rel_path] = {
                            'patient_id': str(p_id),
                            'patient_name': str(p_name),
                            'modality': p_modality,
                            'study_datetime': study_dt,
                            'body_part': body_part_str,
                            'folder_datetime': folder_dt,
                            'str': str_count,
                            'slices': slices_cnt,
                            'folder_name': rel_path
                        }
                        if count_callback:
                            count_callback(len(patient_data))
                        
                        cache[root] = {
                            'mtime': os.path.getmtime(root),
                            'patient_id': str(p_id),
                            'patient_name': str(p_name),
                            'modality': p_modality,
                            'study_datetime': study_dt.isoformat(),
                            'body_part': body_part_str,
                            'folder_datetime': folder_dt.isoformat(),
                            'str': str_count,
                            'slices': slices_cnt
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
        
    try:
        top_dirs = [d for d in os.listdir(ct_images_dir) if os.path.isdir(os.path.join(ct_images_dir, d))]
    except Exception:
        return

    from core.rename_utils import move_study_folder_hierarchical, get_folder_study_info

    now = datetime.now()
    for dir_name in top_dirs:
        patient_folder = os.path.join(ct_images_dir, dir_name)
        try:
            subdirs = [os.path.join(patient_folder, s) for s in os.listdir(patient_folder)
                       if os.path.isdir(os.path.join(patient_folder, s))]
        except Exception:
            subdirs = []

        if subdirs:
            for sub in subdirs:
                try:
                    folder_date = datetime.fromtimestamp(max(os.path.getctime(sub), os.path.getmtime(sub)))
                except Exception:
                    continue
                if (now - folder_date).days >= archive_days:
                    try:
                        patient_name = tr_log("log_patient_unknown")
                        info = get_folder_study_info(sub)
                        if info and info.get('patient_name'):
                            patient_name = str(info['patient_name'])
                        move_study_folder_hierarchical(sub, archive_dir, output_field)
                        log_message(output_field, tr_log("log_patient_moved_to_archive", patient_name, dir_name))
                    except Exception as e:
                        log_message(output_field, tr_log("log_patient_move_to_archive_error", dir_name, e))
        else:
            try:
                folder_date = datetime.fromtimestamp(max(os.path.getctime(patient_folder), os.path.getmtime(patient_folder)))
            except Exception:
                continue
            if (now - folder_date).days >= archive_days:
                try:
                    patient_name = tr_log("log_patient_unknown")
                    info = get_folder_study_info(patient_folder)
                    if info and info.get('patient_name'):
                        patient_name = str(info['patient_name'])
                    move_study_folder_hierarchical(patient_folder, archive_dir, output_field)
                    log_message(output_field, tr_log("log_patient_moved_to_archive", patient_name, dir_name))
                except Exception as e:
                    log_message(output_field, tr_log("log_patient_move_to_archive_error", dir_name, e))


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
                        dcm_files = [f for f in os.listdir(path) if f.lower().endswith('.dcm')]
                        if dcm_files:
                            ds = pydicom.dcmread(os.path.join(path, dcm_files[0]), specific_tags=['PatientName'], stop_before_pixels=True)
                            patient_name = str(ds.get('PatientName', tr_log("log_patient_unknown")))
                    except Exception:
                        pass

                    shutil.rmtree(path)
                    deleted_count += 1
                    log_message(output_field, tr_log("log_archive_cleanup_success", patient_name, item, days_old))
                except Exception as e:
                    log_message(output_field, tr_log("log_archive_cleanup_error", item, e))
