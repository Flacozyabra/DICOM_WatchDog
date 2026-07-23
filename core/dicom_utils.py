import os
import shutil
from datetime import datetime
from collections import defaultdict
import pydicom

from core.logger import log_message
from core.locale_utils import tr_log

def is_structure_file(file_path):
    """
    Определяет, является ли файл файлом структур (RTSTRUCT).
    Поддерживает расширение .str, префиксы STR, RS, RTSTRUCT, 
    а также проверку Modality в DICOM-файлах небольшого размера.
    """
    if not os.path.exists(file_path):
        return False
    filename = os.path.basename(file_path).upper()
    if filename.endswith('.STR'):
        return True
    if filename.startswith('STR') or filename.startswith('RS') or filename.startswith('RTSTRUCT'):
        return True
    if file_path.lower().endswith('.dcm'):
        try:
            # Файлы RTSTRUCT обычно не имеют пиксельных данных и весят мало (< 250 КБ)
            if os.path.getsize(file_path) < 250 * 1024 or filename.startswith('RT'):
                ds = pydicom.dcmread(file_path, stop_before_pixels=True)
                if ds.get('Modality') == 'RTSTRUCT':
                    return True
        except Exception:
            pass
    return False

def delete_redundant_str(patient_dir, output_field=None):
    """
    Удаляет все файлы структур (RTSTRUCT) в папке пациента, кроме самого свежего.
    """
    if not os.path.exists(patient_dir):
        return 0
        
    try:
        all_files = os.listdir(patient_dir)
    except Exception:
        return 0

    files = [f for f in all_files if is_structure_file(os.path.join(patient_dir, f))]
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
                    rel_path = os.path.relpath(root, ct_images_dir).replace('\\', '/')
                    patient_data[rel_path]['patient_id'] = ds.PatientID
                    patient_data[rel_path]['patient_name'] = ds.PatientName
                    patient_data[rel_path]['modality'] = str(ds.get('Modality', 'CT'))
                    patient_data[rel_path]['folder_name'] = rel_path

                    # учитываем два варианта записи времени исследования (с мкс и без)
                    date_time_string = ds.StudyDate + ds.StudyTime
                    format_string = '%Y%m%d%H%M%S' if '.' not in ds.StudyTime else '%Y%m%d%H%M%S.%f'
                    patient_data[rel_path]['study_datetime'] = datetime.strptime(date_time_string, format_string)

                    # область сканирования (BodyPartExamined / StudyDescription / SeriesDescription)
                    body_part = ds.get('BodyPartExamined', '')
                    if not body_part:
                        body_part = ds.get('StudyDescription', '')
                    if not body_part:
                        body_part = ds.get('SeriesDescription', '')
                    
                    body_part_str = str(body_part).strip()
                    if not body_part_str:
                        body_part_str = "Unknown"
                    patient_data[rel_path]['body_part'] = body_part_str

                    # время создания папки
                    patient_data[rel_path]['folder_datetime'] = datetime.fromtimestamp(os.path.getctime(root))
                    # считаем количество файлов структур
                    str_files = [f for f in os.listdir(root) if is_structure_file(os.path.join(root, f))]
                    str_count = len(str_files)
                    patient_data[rel_path]['str'] = str_count

                    # считаем количество файлов срезов (файлов .dcm, исключая файлы структур)
                    slice_files = [f for f in files if f.lower().endswith('.dcm') and not is_structure_file(os.path.join(root, f))]
                    patient_data[rel_path]['slices'] = len(slice_files)

                    if is_cleanup_on and str_count > 1:
                        delete_redundant_str(root, output_field)
                        # Пересчитываем количество файлов структур
                        str_files = [f for f in os.listdir(root) if is_structure_file(os.path.join(root, f))]
                        patient_data[rel_path]['str'] = len(str_files)

                except Exception as e:
                    log_message(output_field, tr_log("log_dcm_read_error", os.path.join(root, file), e))

    return patient_data

# Re-export process_patient_folder for backward compatibility
from core.rename_utils import process_patient_folder
