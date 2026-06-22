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


def dict_create(ct_images_dir, output_field=None, fix_switch="off"):
    patient_data = defaultdict(dict)

    is_fix_on = False
    if hasattr(fix_switch, 'get'):
        is_fix_on = (fix_switch.get() == 'on')
    else:
        is_fix_on = (fix_switch == 'on' or fix_switch is True)

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

                    if is_fix_on and str_count > 1:
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


def rename_patient_folder(path, output_field):
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

    if patient_folder != ds.PatientID:
        new_patient_id = ds.PatientID[3:]
        new_folder = str(new_patient_id)
        new_path = os.path.join(os.path.dirname(path), new_folder)

        if os.path.exists(new_path):
            # Если папка уже существует, добавляем файлы в нее
            for dirpath, dirnames, filenames in os.walk(path):
                for filename in filenames:
                    if filename.endswith('.dcm'):
                        ds_file = pydicom.read_file(os.path.join(dirpath, filename))
                        ds_file.PatientID = new_patient_id
                        ds_file.save_as(os.path.join(new_path, filename))
            shutil.rmtree(path)
            log_message(output_field, f"Файлы успешно добавлены в существующую папку: {new_folder}, новый PatientID: {new_patient_id}, исходная папка {patient_folder} удалена")
        else:
            try:
                os.rename(path, new_path)
                for dirpath, dirnames, filenames in os.walk(new_path):
                    for filename in filenames:
                        if filename.endswith('.dcm'):
                            ds_file = pydicom.read_file(os.path.join(dirpath, filename))
                            ds_file.PatientID = new_patient_id
                            ds_file.save_as(os.path.join(new_path, filename))
                log_message(output_field, f"Переименовано: {patient_folder} -> {new_folder}, новый PatientID: {new_patient_id}")
            except Exception as e:
                log_message(output_field, f"Ошибка переименования {patient_folder}: {e}")

    # костыль для удаления точек и других символов
    elif not patient_folder.isdigit():
        new_patient_id = remove_non_digits(ds.PatientID)
        new_folder = str(new_patient_id)
        new_path = os.path.join(os.path.dirname(path), new_folder)

        if os.path.exists(new_path):
            # Если папка уже существует, добавляем файлы в нее
            for dirpath, dirnames, filenames in os.walk(path):
                for filename in filenames:
                    if filename.endswith('.dcm'):
                        ds_file = pydicom.read_file(os.path.join(dirpath, filename))
                        ds_file.PatientID = new_patient_id
                        ds_file.save_as(os.path.join(new_path, filename))
            shutil.rmtree(path)
            log_message(output_field, f"Файлы успешно добавлены в существующую папку: {new_folder}, новый PatientID: {new_patient_id}, исходная папка {patient_folder} удалена")
        else:
            try:
                os.rename(path, new_path)
                for dirpath, dirnames, filenames in os.walk(new_path):
                    for filename in filenames:
                        if filename.endswith('.dcm'):
                            ds_file = pydicom.read_file(os.path.join(dirpath, filename))
                            ds_file.PatientID = new_patient_id
                            ds_file.save_as(os.path.join(new_path, filename))
                log_message(output_field, f"Переименовано: {patient_folder} -> {new_folder}, новый PatientID: {new_patient_id}")
            except Exception as e:
                log_message(output_field, f"Ошибка переименования {patient_folder}: {e}")
