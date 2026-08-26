import os
import shutil
from datetime import datetime
from collections import defaultdict
import pydicom

from core.logger import log_message
from core.locale_utils import tr_log


def classify_dicom_file(filename: str) -> str:
    """
    Быстро определяет тип DICOM файла по имени/расширению без обращения к диску.
    Возвращает: 'RTSTRUCT', 'RTDOSE', 'RTPLAN', 'CT', или 'IGNORE' (для DICOMDIR).
    """
    fn = filename.upper()
    if fn == 'DICOMDIR':
        return 'IGNORE'
    if fn.endswith('.STR') or fn.startswith(('STR', 'RS', 'RTSTRUCT', 'RT_STRUCT', 'STRUCTURES')):
        return 'RTSTRUCT'
    if fn.endswith(('.RTD', '.DOSE')) or fn.startswith(('RD', 'RTDOSE', 'RT_DOSE', 'DOSE')):
        return 'RTDOSE'
    if fn.endswith(('.RTP', '.PLAN')) or fn.startswith(('RP', 'RTPLAN', 'RT_PLAN', 'PLAN')):
        return 'RTPLAN'
    return 'CT'


def is_structure_file(file_path):
    """
    Определяет, является ли файл файлом структур (RTSTRUCT).
    Поддерживает расширение .str, префиксы STR, RS, RTSTRUCT, 
    а также быструю проверку Modality в DICOM-файлах без ограничения по размеру.
    """
    if not os.path.exists(file_path):
        return False
    bname = os.path.basename(file_path)
    cls = classify_dicom_file(bname)
    if cls == 'RTSTRUCT':
        return True
    if cls in ('RTDOSE', 'RTPLAN', 'IGNORE'):
        return False
    if file_path.lower().endswith('.dcm') or not os.path.splitext(file_path)[1]:
        try:
            ds = pydicom.dcmread(file_path, stop_before_pixels=True, force=True, specific_tags=['Modality', 'SOPClassUID'])
            mod = str(getattr(ds, 'Modality', ''))
            sop = str(getattr(ds, 'SOPClassUID', ''))
            if mod == 'RTSTRUCT' or sop == '1.2.840.10008.5.1.4.1.1.481.3':
                return True
        except Exception:
            pass
    return False


def is_dose_file(file_path):
    """
    Определяет, является ли файл файлом дозы (RTDOSE).
    Поддерживает расширения .rtd, префиксы RD, RTDOSE, DOSE,
    а также быструю проверку Modality / SOPClassUID.
    """
    if not os.path.exists(file_path):
        return False
    bname = os.path.basename(file_path)
    cls = classify_dicom_file(bname)
    if cls == 'RTDOSE':
        return True
    if cls in ('RTSTRUCT', 'RTPLAN', 'IGNORE'):
        return False
    if file_path.lower().endswith('.dcm') or not os.path.splitext(file_path)[1]:
        try:
            ds = pydicom.dcmread(file_path, stop_before_pixels=True, force=True, specific_tags=['Modality', 'SOPClassUID'])
            mod = str(getattr(ds, 'Modality', ''))
            sop = str(getattr(ds, 'SOPClassUID', ''))
            if mod == 'RTDOSE' or sop == '1.2.840.10008.5.1.4.1.1.481.2':
                return True
        except Exception:
            pass
    return False


def is_plan_file(file_path):
    """
    Определяет, является ли файл файлом плана (RTPLAN).
    Поддерживает расширения .rtp, префиксы RP, RTPLAN, PLAN,
    а также быструю проверку Modality / SOPClassUID.
    """
    if not os.path.exists(file_path):
        return False
    bname = os.path.basename(file_path)
    cls = classify_dicom_file(bname)
    if cls == 'RTPLAN':
        return True
    if cls in ('RTSTRUCT', 'RTDOSE', 'IGNORE'):
        return False
    if file_path.lower().endswith('.dcm') or not os.path.splitext(file_path)[1]:
        try:
            ds = pydicom.dcmread(file_path, stop_before_pixels=True, force=True, specific_tags=['Modality', 'SOPClassUID'])
            mod = str(getattr(ds, 'Modality', ''))
            sop = str(getattr(ds, 'SOPClassUID', ''))
            if mod == 'RTPLAN' or sop == '1.2.840.10008.5.1.4.1.1.481.5':
                return True
        except Exception:
            pass
    return False


def is_dicom_file(file_path):
    """
    Определяет, является ли файл валидным DICOM файлом.
    Поддерживает как файлы с расширением .dcm, так и файлы без расширения (IM000001 и т.д.).
    Игнорирует индексные файлы DICOMDIR.
    """
    if not os.path.isfile(file_path):
        return False
    bname = os.path.basename(file_path)
    cls = classify_dicom_file(bname)
    if cls == 'IGNORE':
        return False
    if cls in ('RTSTRUCT', 'RTDOSE', 'RTPLAN'):
        return True
    if file_path.lower().endswith('.dcm'):
        return True
    try:
        if os.path.getsize(file_path) < 132:
            return False
        with open(file_path, 'rb') as f:
            f.seek(128)
            return f.read(4) == b'DICM'
    except Exception:
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


def collect_patient_studies(patient_dir, ct_images_dir, output_field=None, cleanup_structures=False, scan_rtd=False, scan_rtp=False):
    """
    Сканирует одну конкретную папку пациента (включая возможные подпапки исследований)
    и возвращает словарь исследований для таблицы.
    Выполняется в один быстрый проход без повторных обращений к диску.
    """
    patient_data = {}
    if not os.path.exists(patient_dir):
        return patient_data

    is_cleanup_on = False
    if hasattr(cleanup_structures, 'get'):
        is_cleanup_on = (cleanup_structures.get() == 'on')
    else:
        is_cleanup_on = (cleanup_structures == 'on' or cleanup_structures is True)

    for root, dirs, files in os.walk(patient_dir):
        if not files:
            continue

        ct_files = []
        str_files = []
        rtd_files = []
        rtp_files = []

        for f in files:
            t = classify_dicom_file(f)
            if t == 'IGNORE':
                continue
            elif t == 'RTSTRUCT':
                str_files.append(f)
            elif t == 'RTDOSE':
                rtd_files.append(f)
            elif t == 'RTPLAN':
                rtp_files.append(f)
            else:
                ct_files.append(f)

        if not (ct_files or str_files or rtd_files or rtp_files):
            continue

        # Выбираем репрезентативный файл для считывания общих метаданных исследования (в приоритете КТ)
        rep_file = ct_files[0] if ct_files else (str_files[0] if str_files else (rtd_files[0] if rtd_files else files[0]))
        fp = os.path.join(root, rep_file)
        try:
            ds = pydicom.dcmread(
                fp, stop_before_pixels=True, force=True,
                specific_tags=['PatientID', 'PatientName', 'Modality', 'StudyDate', 'StudyTime', 'BodyPartExamined', 'StudyDescription', 'SeriesDescription']
            )
            rel_path = os.path.relpath(root, ct_images_dir).replace('\\', '/')
            
            patient_id = getattr(ds, 'PatientID', '') or str(ds.get('PatientID', ''))
            patient_name = getattr(ds, 'PatientName', '') or str(ds.get('PatientName', ''))
            if not patient_id:
                patient_id = os.path.basename(root)
            if not patient_name:
                patient_name = os.path.basename(root)

            study_entry = {
                'patient_id': str(patient_id),
                'patient_name': str(patient_name),
                'modality': str(ds.get('Modality', 'CT')),
                'folder_name': rel_path
            }

            # Безопасный разбор времени исследования
            study_date = str(ds.get('StudyDate', '')).strip()
            study_time = str(ds.get('StudyTime', '')).strip()
            folder_ctime = datetime.fromtimestamp(max(os.path.getctime(root), os.path.getmtime(root)))

            study_dt = folder_ctime
            if study_date:
                try:
                    date_time_string = study_date + study_time
                    study_dt = datetime.strptime(date_time_string[:14], '%Y%m%d%H%M%S')
                except Exception:
                    try:
                        study_dt = datetime.strptime(study_date, '%Y%m%d')
                    except Exception:
                        pass

            study_entry['study_datetime'] = study_dt

            # область сканирования (BodyPartExamined / StudyDescription / SeriesDescription)
            body_part = ds.get('BodyPartExamined', '')
            if not body_part:
                body_part = ds.get('StudyDescription', '')
            if not body_part:
                body_part = ds.get('SeriesDescription', '')
            
            body_part_str = str(body_part).strip()
            if not body_part_str:
                body_part_str = "Unknown"
            study_entry['body_part'] = body_part_str

            # время создания папки
            study_entry['folder_datetime'] = folder_ctime
            
            # Количество файлов структур, доз, планов и срезов
            study_entry['str'] = len(str_files)
            study_entry['rtd'] = len(rtd_files) if scan_rtd else 0
            study_entry['rtp'] = len(rtp_files) if scan_rtp else 0
            study_entry['slices'] = len(ct_files)

            if is_cleanup_on and len(str_files) > 1:
                delete_redundant_str(root, output_field)
                str_files = [f for f in os.listdir(root) if classify_dicom_file(f) == 'RTSTRUCT']
                study_entry['str'] = len(str_files)

            patient_data[rel_path] = study_entry

        except Exception as e:
            log_message(output_field, tr_log("log_dcm_read_error", fp, e))

    return patient_data


def dict_create(ct_images_dir, output_field=None, cleanup_structures=False, progress_callback=None, count_callback=None, scan_rtd=False, scan_rtp=False):
    patient_data = defaultdict(dict)
    if not os.path.exists(ct_images_dir):
        return patient_data

    try:
        top_dirs = [os.path.join(ct_images_dir, d) for d in os.listdir(ct_images_dir)
                    if os.path.isdir(os.path.join(ct_images_dir, d))]
        total_dirs = len(top_dirs)
    except Exception:
        total_dirs = 0

    for i, p_dir in enumerate(top_dirs):
        if progress_callback and total_dirs > 0:
            progress_callback(i + 1, total_dirs)
        studies = collect_patient_studies(p_dir, ct_images_dir, output_field, cleanup_structures, scan_rtd=scan_rtd, scan_rtp=scan_rtp)
        patient_data.update(studies)
        if count_callback:
            count_callback(len(patient_data))

    return patient_data


def process_patient_folder(*args, **kwargs):
    from core.rename_utils import process_patient_folder as _process
    return _process(*args, **kwargs)
