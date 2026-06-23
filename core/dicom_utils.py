import os
import shutil
from datetime import datetime
from collections import defaultdict
import pydicom

from core.logger import log_message


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
                log_message(output_field, f"Был удален старый файл структур ({file})")
        except Exception as e:
            if output_field:
                log_message(output_field, f"Ошибка при удалении {file}: {e}")
                
    return deleted_count


def dict_create(ct_images_dir, output_field=None, cleanup_structures=False):
    patient_data = defaultdict(dict)

    is_cleanup_on = False
    if hasattr(cleanup_structures, 'get'):
        is_cleanup_on = (cleanup_structures.get() == 'on')
    else:
        is_cleanup_on = (cleanup_structures == 'on' or cleanup_structures is True)

    for root, dirs, files in os.walk(ct_images_dir):
        if files:
            file = files[0]
            if file.endswith('.dcm'):
                try:
                    ds = pydicom.read_file(os.path.join(root, file))
                    patient_data[ds.PatientID]['patient_id'] = ds.PatientID
                    patient_data[ds.PatientID]['patient_name'] = ds.PatientName

                    # учитываем два варианта записи времени исследования (с мкс и без)
                    date_time_string = ds.StudyDate + ds.StudyTime
                    format_string = '%Y%m%d%H%M%S' if '.' not in ds.StudyTime else '%Y%m%d%H%M%S.%f'
                    patient_data[ds.PatientID]['study_datetime'] = datetime.strptime(date_time_string, format_string)

                    # область сканирования (BodyPartExamined / StudyDescription / SeriesDescription)
                    body_part = ds.get('BodyPartExamined', '')
                    if not body_part:
                        body_part = ds.get('StudyDescription', '')
                    if not body_part:
                        body_part = ds.get('SeriesDescription', '')
                    
                    body_part_str = str(body_part).strip()
                    if not body_part_str:
                        body_part_str = "Unknown"
                    patient_data[ds.PatientID]['body_part'] = body_part_str

                    # время создания папки
                    patient_data[ds.PatientID]['folder_datetime'] = datetime.fromtimestamp(os.path.getctime(root))
                    # считаем количество срезов (файлов .dcm)
                    dcm_count = len([f for f in files if f.lower().endswith('.dcm')])
                    patient_data[ds.PatientID]['slices'] = dcm_count
                    # считаем количество файлов начинающихся с STR в папке пациента
                    files = [f for f in os.listdir(root) if f.startswith('STR')]
                    str_count = len(files)
                    patient_data[ds.PatientID]['str'] = str_count

                    if is_cleanup_on and str_count > 1:
                        delete_redundant_str(root, output_field)
                        # Пересчитываем количество файлов STR
                        files = [f for f in os.listdir(root) if f.startswith('STR')]
                        patient_data[ds.PatientID]['str'] = len(files)

                except Exception as e:
                    log_message(output_field, f"Ошибка чтения файла {os.path.join(root, file)}: {e}")

    return patient_data


def remove_non_digits(input_string):
    result = ''
    for char in input_string:
        if char.isdigit():
            result += char
    return result


def rename_patient_folder(path, output_field, prefixes=None):
    if not os.path.isdir(path):
        return

    patient_folder = os.path.basename(path)
    files = [f for f in os.listdir(path) if f.endswith('.dcm')]
    if not files:
        return

    try:
        ds = pydicom.read_file(os.path.join(path, files[0]))
    except Exception as e:
        log_message(output_field, f"Ошибка чтения DICOM в {patient_folder}: {e}")
        return

    new_patient_id = ds.PatientID
    if prefixes:
        for prefix in prefixes:
            prefix = prefix.strip()
            if prefix and new_patient_id.startswith(prefix):
                new_patient_id = new_patient_id[len(prefix):]
                break

    # Внутренняя функция для безопасного слияния папок без потери данных
    def safe_merge_folders(src, dest, new_id):
        for dirpath, dirnames, filenames in os.walk(src):
            for filename in filenames:
                src_file = os.path.join(dirpath, filename)
                dest_file = os.path.join(dest, filename)
                
                # Защита от перезаписи: генерируем уникальное имя, если файл уже существует
                if os.path.exists(dest_file):
                    base, ext = os.path.splitext(filename)
                    counter = 1
                    while os.path.exists(os.path.join(dest, f"{base}_{counter}{ext}")):
                        counter += 1
                    dest_file = os.path.join(dest, f"{base}_{counter}{ext}")
                
                # Если файл DICOM или структура, пытаемся обновить PatientID
                if filename.lower().endswith('.dcm') or filename.startswith('STR'):
                    try:
                        ds_file = pydicom.read_file(src_file)
                        ds_file.PatientID = new_id
                        ds_file.save_as(dest_file)
                    except Exception:
                        # Если не удалось обработать как DICOM, просто копируем файл
                        shutil.copy2(src_file, dest_file)
                else:
                    # Все остальные файлы копируем как есть
                    shutil.copy2(src_file, dest_file)
        # Удаляем исходную папку только после успешного копирования всего содержимого
        shutil.rmtree(src)

    # Внутренняя функция для обновления PatientID во всех DICOM-файлах после переименования папки
    def safe_update_patient_ids(folder_path, new_id):
        for dirpath, dirnames, filenames in os.walk(folder_path):
            for filename in filenames:
                if filename.lower().endswith('.dcm') or filename.startswith('STR'):
                    src_file = os.path.join(dirpath, filename)
                    try:
                        ds_file = pydicom.read_file(src_file)
                        ds_file.PatientID = new_id
                        ds_file.save_as(src_file)
                    except Exception as e:
                        log_message(output_field, f"Предупреждение: не удалось обновить PatientID в файле {filename}: {e}")

    if patient_folder != new_patient_id:
        new_folder = str(new_patient_id)
        new_path = os.path.join(os.path.dirname(path), new_folder)

        if os.path.exists(new_path):
            try:
                safe_merge_folders(path, new_path, new_patient_id)
                log_message(output_field, f"Файлы успешно объединены в существующую папку: {new_folder}, новый PatientID: {new_patient_id}, исходная папка {patient_folder} удалена")
            except Exception as e:
                log_message(output_field, f"Ошибка при слиянии папок {patient_folder} -> {new_folder}: {e}")
        else:
            try:
                os.rename(path, new_path)
                safe_update_patient_ids(new_path, new_patient_id)
                log_message(output_field, f"Переименовано: {patient_folder} -> {new_folder}, новый PatientID: {new_patient_id}")
            except Exception as e:
                log_message(output_field, f"Ошибка переименования {patient_folder}: {e}")

    # костыль для удаления точек и других символов
    elif not patient_folder.isdigit():
        new_patient_id = remove_non_digits(ds.PatientID)
        new_folder = str(new_patient_id)
        new_path = os.path.join(os.path.dirname(path), new_folder)

        if os.path.exists(new_path):
            try:
                safe_merge_folders(path, new_path, new_patient_id)
                log_message(output_field, f"Файлы успешно объединены в существующую папку: {new_folder}, новый PatientID: {new_patient_id}, исходная папка {patient_folder} удалена")
            except Exception as e:
                log_message(output_field, f"Ошибка при слиянии папок {patient_folder} -> {new_folder}: {e}")
        else:
            try:
                os.rename(path, new_path)
                safe_update_patient_ids(new_path, new_patient_id)
                log_message(output_field, f"Переименовано: {patient_folder} -> {new_folder}, новый PatientID: {new_patient_id}")
            except Exception as e:
                log_message(output_field, f"Ошибка переименования {patient_folder}: {e}")
