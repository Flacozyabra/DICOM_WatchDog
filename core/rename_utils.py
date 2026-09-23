import os
import shutil
import re
import time
from datetime import datetime
import pydicom

from core.logger import log_message
from core.locale_utils import tr_log
from core.dicom_utils import is_structure_file, is_dicom_file

def touch_folder_tree(path: str):
    """
    Обновляет время модификации (mtime) и доступа (atime) для папки и всех ее вложенных файлов и папок на текущее время.
    """
    try:
        now_ts = time.time()
        if os.path.exists(path):
            os.utime(path, (now_ts, now_ts))
            for root, dirs, files in os.walk(path):
                for d in dirs:
                    try:
                        os.utime(os.path.join(root, d), (now_ts, now_ts))
                    except Exception:
                        pass
                for f in files:
                    try:
                        os.utime(os.path.join(root, f), (now_ts, now_ts))
                    except Exception:
                        pass
    except Exception:
        pass

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

LOCK_FILE_NAME = ".dw_processing.lock"
LOCK_TIMEOUT_SECONDS = 60.0

def is_folder_locked(folder_path: str) -> bool:
    """
    Проверяет, заблокирована ли папка другим процессом (например, на сетевом диске SMB).
    Если лок-файл старше LOCK_TIMEOUT_SECONDS, считается устаревшим (deadlock prevention).
    """
    if not os.path.isdir(folder_path):
        return False
    lock_path = os.path.join(folder_path, LOCK_FILE_NAME)
    if not os.path.exists(lock_path):
        return False
    try:
        mtime = os.path.getmtime(lock_path)
        if (time.time() - mtime) > LOCK_TIMEOUT_SECONDS:
            try:
                os.remove(lock_path)
            except Exception:
                pass
            return False
        return True
    except Exception:
        return False

class FolderLock:
    """
    Контекстный менеджер для блокировки папки пациента на время изменения ID,
    слияния файлов, очистки структур и переименования папки.
    Позволяет другим процессам (ПК 2) не читать файлы в момент перезаписи.
    """
    def __init__(self, folder_path: str):
        self.folder_path = folder_path
        self.lock_path = os.path.join(folder_path, LOCK_FILE_NAME)
        self.acquired = False

    def __enter__(self):
        if not os.path.isdir(self.folder_path):
            return self
        if is_folder_locked(self.folder_path):
            return self
        try:
            import socket
            hostname = socket.gethostname()
            pid = os.getpid()
            with open(self.lock_path, 'w', encoding='utf-8') as f:
                f.write(f"host={hostname};pid={pid};time={time.time()}\n")
            self.acquired = True
        except Exception:
            self.acquired = False
        return self

    def update_path(self, new_folder_path: str):
        """Если папка была переименована, обновляем путь к лок-файлу."""
        self.folder_path = new_folder_path
        self.lock_path = os.path.join(new_folder_path, LOCK_FILE_NAME)

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.acquired:
            try:
                if os.path.exists(self.lock_path):
                    os.remove(self.lock_path)
            except Exception:
                pass
            self.acquired = False

def safe_merge_folders(src, dest, new_id):
    if os.path.normcase(os.path.abspath(src)) == os.path.normcase(os.path.abspath(dest)):
        return
    for dirpath, dirnames, filenames in os.walk(src):
        for filename in filenames:
            if filename == LOCK_FILE_NAME or filename.lower().endswith('.lock'):
                continue
            src_file = os.path.join(dirpath, filename)
            rel_path = os.path.relpath(dirpath, src)
            dest_dir = os.path.join(dest, rel_path)
            os.makedirs(dest_dir, exist_ok=True)
            dest_file = os.path.join(dest_dir, filename)
            
            if is_dicom_file(src_file) or is_structure_file(src_file):
                try:
                    if new_id:
                        ds_header = pydicom.dcmread(src_file, stop_before_pixels=True, force=True, specific_tags=['PatientID'])
                        curr_id = str(getattr(ds_header, 'PatientID', '') or ds_header.get('PatientID', '')).strip()
                        if curr_id != str(new_id):
                            ds_file = pydicom.dcmread(src_file, force=True)
                            ds_file.PatientID = str(new_id)
                            ds_file.save_as(dest_file)
                        else:
                            shutil.copy2(src_file, dest_file)
                    else:
                        shutil.copy2(src_file, dest_file)
                except Exception:
                    shutil.copy2(src_file, dest_file)
            else:
                shutil.copy2(src_file, dest_file)
    for attempt in range(5):
        try:
            shutil.rmtree(src)
            break
        except Exception:
            time.sleep(0.15)
    else:
        try:
            shutil.rmtree(src, ignore_errors=True)
        except Exception:
            pass

def safe_update_patient_ids(folder_path, new_id, output_field=None, rt_only=False):
    if not new_id:
        return 0, False
    updated_count = 0
    has_errors = False
    for dirpath, dirnames, filenames in os.walk(folder_path):
        for filename in filenames:
            fn_lower = filename.lower()
            if fn_lower in ('dicomdir', LOCK_FILE_NAME.lower()) or fn_lower.endswith('.lock'):
                continue

            # Если включен режим rt_only, проверяем только файлы структур/доз/планов, пропуская явные КТ-срезы
            if rt_only and (fn_lower.startswith(('ct', 'img')) or '_ct' in fn_lower or 'ct_' in fn_lower or '_image' in fn_lower):
                continue

            if not (fn_lower.endswith('.dcm') or fn_lower.endswith(('.str', '.rtd', '.rtp', '.dose', '.plan')) or '.' not in filename):
                continue

            src_file = os.path.join(dirpath, filename)
            try:
                # Сначала читаем только тег PatientID без пикселей для сверхбыстрой проверки
                ds_header = pydicom.dcmread(src_file, stop_before_pixels=True, force=True, specific_tags=['PatientID'])
                current_id = str(getattr(ds_header, 'PatientID', '') or ds_header.get('PatientID', '')).strip()
                if not current_id or current_id == str(new_id):
                    continue
                
                # Только если ID действительно отличается - загружаем и перезаписываем с Retry механизмом
                ds_file = pydicom.dcmread(src_file, force=True)
                ds_file.PatientID = str(new_id)

                file_saved = False
                last_err = None
                for attempt in range(5):
                    try:
                        ds_file.save_as(src_file)
                        file_saved = True
                        break
                    except (PermissionError, OSError) as pe:
                        last_err = pe
                        time.sleep(0.15 * (attempt + 1))
                    except Exception as e:
                        last_err = e
                        break

                if file_saved:
                    updated_count += 1
                else:
                    has_errors = True
                    if output_field:
                        log_message(output_field, tr_log("log_dcm_update_id_warning", filename, last_err))
            except Exception as e:
                has_errors = True
                if output_field:
                    log_message(output_field, tr_log("log_dcm_update_id_warning", filename, e))

    return updated_count, has_errors

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
                if is_dicom_file(f_path) or is_structure_file(f_path):
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
                ds = pydicom.dcmread(fpath, stop_before_pixels=True, force=True)
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
        ds = pydicom.dcmread(target_file, stop_before_pixels=True, force=True)
        raw_id = getattr(ds, 'PatientID', '')
        raw_name = getattr(ds, 'PatientName', '')
        study_date = str(getattr(ds, 'StudyDate', ''))
        study_time = str(getattr(ds, 'StudyTime', '000000'))
        study_instance_uid = str(getattr(ds, 'StudyInstanceUID', '') or ds.get('StudyInstanceUID', '')).strip()

        # Если в RTSTRUCT нет StudyInstanceUID в корне, проверим ссылки
        if not study_instance_uid and hasattr(ds, 'ReferencedFrameOfReferenceSequence'):
            try:
                for rfor in ds.ReferencedFrameOfReferenceSequence:
                    if hasattr(rfor, 'RTReferencedStudySequence'):
                        for rstudy in rfor.RTReferencedStudySequence:
                            ref_uid = str(getattr(rstudy, 'ReferencedSOPInstanceUID', '')).strip()
                            if ref_uid:
                                study_instance_uid = ref_uid
                                break
            except Exception:
                pass

        date_time_string = study_date + study_time
        format_string = '%Y%m%d%H%M%S' if '.' not in study_time else '%Y%m%d%H%M%S.%f'
        try:
            study_dt = datetime.strptime(date_time_string, format_string)
        except Exception:
            try:
                study_dt = datetime.strptime(study_date, '%Y%m%d')
            except Exception:
                study_dt = datetime.fromtimestamp(os.path.getctime(folder_path))

        study_date_str = study_dt.strftime('%d.%m.%y - %H-%M')
        date_only_str = study_dt.strftime('%d.%m.%y')
        
        return {
            'ds': ds,
            'patient_id': raw_id,
            'patient_name': raw_name,
            'study_date_str': study_date_str,
            'date_only_str': date_only_str,
            'study_instance_uid': study_instance_uid,
            'target_file': target_file
        }
    except Exception:
        return None

def is_structure_only_folder(folder_path):
    """Возвращает True, если в папке есть файлы структур и нет КТ-среза."""
    if not os.path.isdir(folder_path):
        return False
    has_struct = False
    has_slices = False
    for root, dirs, files in os.walk(folder_path):
        for f in files:
            fpath = os.path.join(root, f)
            if is_structure_file(fpath):
                has_struct = True
            elif is_dicom_file(fpath):
                has_slices = True
    return has_struct and not has_slices

def find_matching_study_subfolder(parent_path, study_instance_uid, date_only_str, study_date_str):
    """
    Ищет существующую подпапку исследования в parent_path:
    1. По совпадению StudyInstanceUID (100% разделение исследований).
    2. По совпадению имени папки [study_date_str] или [date_only_str...].
    """
    if not os.path.isdir(parent_path):
        return None
        
    try:
        subfolders = [os.path.join(parent_path, d) for d in os.listdir(parent_path)
                      if os.path.isdir(os.path.join(parent_path, d))]
    except Exception:
        return None

    # 1. Поиск по StudyInstanceUID
    if study_instance_uid:
        for sub in subfolders:
            sub_info = get_folder_study_info(sub)
            if sub_info and sub_info.get('study_instance_uid') and sub_info['study_instance_uid'] == study_instance_uid:
                return sub

    # 2. Поиск по точному имени [study_date_str]
    exact_subfolder = os.path.join(parent_path, f"[{study_date_str}]")
    if os.path.exists(exact_subfolder):
        return exact_subfolder

    # 3. Поиск по префиксу даты
    prefix = f"[{date_only_str}"
    for sub in subfolders:
        bname = os.path.basename(sub)
        if bname.startswith(prefix) and bname.endswith("]"):
            return sub

    return None

def auto_heal_split_patient_folders(parent_path, new_patient_id=None, output_field=None):
    """
    Если в папке пациента образовались несколько подпапок:
    1. Подпапки с одинаковым StudyInstanceUID сливаются в одну.
    2. Подпапка с одними структурами (0 срезов) сливается в подпапку со срезами того же UID (или в единственную КТ-подпапку).
    """
    if not os.path.isdir(parent_path):
        return
    try:
        subfolders = [os.path.join(parent_path, d) for d in os.listdir(parent_path)
                      if os.path.isdir(os.path.join(parent_path, d))]
    except Exception:
        return

    if len(subfolders) <= 1:
        return

    sub_infos = {}
    for sub in subfolders:
        info = get_folder_study_info(sub)
        is_struct_only = is_structure_only_folder(sub)
        sub_infos[sub] = (info, is_struct_only)

    # 1. Слияние одинаковых StudyInstanceUID
    uid_map = {}
    for sub, (info, is_struct_only) in list(sub_infos.items()):
        if not info:
            continue
        uid = info.get('study_instance_uid')
        if not uid:
            continue
        if uid in uid_map:
            dest_sub = uid_map[uid]
            try:
                safe_merge_folders(sub, dest_sub, new_patient_id or info['patient_id'])
                if output_field:
                    log_message(output_field, tr_log("log_files_merged_success", os.path.basename(dest_sub), os.path.basename(sub)))
                del sub_infos[sub]
            except Exception:
                pass
        else:
            uid_map[uid] = sub

    # 2. Если осталась структура-сирота (0 срезов), сливаем ее в первую доступную КТ-подпапку
    remaining_subs = [os.path.join(parent_path, d) for d in os.listdir(parent_path) if os.path.isdir(os.path.join(parent_path, d))]
    if len(remaining_subs) > 1:
        slice_subs = []
        struct_only_subs = []
        for s in remaining_subs:
            if is_structure_only_folder(s):
                struct_only_subs.append(s)
            else:
                slice_subs.append(s)
        if slice_subs and struct_only_subs:
            for str_sub in struct_only_subs:
                target_sub = slice_subs[0]
                try:
                    safe_merge_folders(str_sub, target_sub, new_patient_id or "")
                    if output_field:
                        log_message(output_field, tr_log("log_files_merged_success", os.path.basename(target_sub), os.path.basename(str_sub)))
                except Exception:
                    pass

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
        return path

    with FolderLock(path) as lock:
        if not lock.acquired:
            # Папка заблокирована другим процессом или недоступна
            return path

        patient_folder = os.path.basename(path)
        info = get_folder_study_info(path)
        if not info:
            return path

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

        id_changed = fix_patient_id and (new_patient_id != raw_patient_id)

        # 2. Если включен fix_patient_id:
        # Всегда проверяем все файлы папки (rt_only=False), потому что предварительное чтение
        # только тега PatientID (без пикселей) занимает доли секунды на всю папку,
        # но надежно выявляет и исправляет любые файлы с отставшим/неисправленным ID.
        had_update_errors = False
        if fix_patient_id:
            updated_count, had_update_errors = safe_update_patient_ids(path, new_patient_id, output_field, rt_only=False)
            if updated_count > 0:
                id_changed = True

        # Если при обновлении файлов возникли ошибки блокировки, отложим переименование папки до следующего цикла
        if had_update_errors:
            return path

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

            if not os.path.exists(parent_path):
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
                    lock.update_path(parent_path)
                    if id_changed:
                        log_message(output_field, tr_log("log_folder_renamed_success_with_id", patient_folder, target_folder_name, new_patient_id))
                    else:
                        log_message(output_field, tr_log("log_folder_renamed_success", patient_folder, target_folder_name))
                    return parent_path
                else:
                    log_message(output_field, tr_log("log_folder_rename_error", patient_folder, last_error))
                    return path

            else:
                # Папка пациента уже существует.
                if os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(parent_path)):
                    auto_heal_split_patient_folders(parent_path, new_patient_id, output_field)
                    return parent_path

                incoming_uid = info.get('study_instance_uid', '')
                is_incoming_struct_only = is_structure_only_folder(path)

                # Проверим, лежит ли существующее исследование в корне parent_path (плоская структура)
                exist_info = get_folder_study_info(parent_path)
                is_parent_flat = exist_info and (os.path.dirname(os.path.abspath(exist_info['target_file'])) == os.path.abspath(parent_path))

                if is_parent_flat:
                    exist_uid = exist_info.get('study_instance_uid', '')
                    if (exist_uid and incoming_uid and exist_uid == incoming_uid) or is_incoming_struct_only or (exist_info.get('date_only_str') == date_only_str):
                        # Совпадает по StudyInstanceUID или структуре, сливаем в корень parent_path
                        try:
                            safe_merge_folders(path, parent_path, new_patient_id)
                            lock.update_path(parent_path)
                            if id_changed:
                                log_message(output_field, tr_log("log_files_merged_success_with_id", os.path.basename(parent_path), new_patient_id, patient_folder))
                            else:
                                log_message(output_field, tr_log("log_files_merged_success", os.path.basename(parent_path), patient_folder))
                        except Exception as e:
                            log_message(output_field, tr_log("log_folders_merge_error", patient_folder, os.path.basename(parent_path), e))
                        return parent_path

                # Если целевая папка плоская, переведем её в иерархическую структуру
                if not make_folder_hierarchical(parent_path, output_field):
                    return path
                    
                # Ищем подпапку исследования для совпадения по StudyInstanceUID (или по дате)
                matching_sub = find_matching_study_subfolder(parent_path, incoming_uid, date_only_str, study_date_str)
                
                # Если входящая папка - только структуры, но точного UID не нашлось, берем подпапку со срезами КТ
                if not matching_sub and is_incoming_struct_only:
                    try:
                        for item in os.listdir(parent_path):
                            sub_path = os.path.join(parent_path, item)
                            if os.path.isdir(sub_path) and not is_structure_only_folder(sub_path):
                                matching_sub = sub_path
                                break
                    except Exception:
                        pass

                target_sub = matching_sub if matching_sub else os.path.join(parent_path, f"[{study_date_str}]")
                
                if os.path.exists(target_sub):
                    try:
                        safe_merge_folders(path, target_sub, new_patient_id)
                        lock.update_path(parent_path)
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
                        lock.update_path(parent_path)
                        if id_changed:
                            log_message(output_field, tr_log("log_folder_renamed_success_with_id", patient_folder, f"{target_folder_name}/{os.path.basename(target_sub)}", new_patient_id))
                        else:
                            log_message(output_field, tr_log("log_folder_renamed_success", patient_folder, f"{target_folder_name}/{os.path.basename(target_sub)}"))
                    else:
                        log_message(output_field, tr_log("log_folder_rename_error", patient_folder, last_error))

                # Автолечение на случай ранее разделенных папок
                auto_heal_split_patient_folders(parent_path, new_patient_id, output_field)
                return parent_path
        return path


def move_single_study_folder(src_study_path: str, dest_patient_path: str, output_field=None) -> bool:
    """
    Переносит одно конкретное исследование (src_study_path) в целевую папку пациента (dest_patient_path).
    Сохраняет иерархическую структуру при наличии нескольких исследований одного пациента.
    """
    if not os.path.exists(src_study_path):
        return False

    info = get_folder_study_info(src_study_path)
    if not info:
        if not os.path.exists(dest_patient_path):
            shutil.move(src_study_path, dest_patient_path)
        else:
            target_dest = os.path.join(dest_patient_path, os.path.basename(src_study_path))
            if os.path.exists(target_dest):
                safe_merge_folders(src_study_path, target_dest, "")
            else:
                shutil.move(src_study_path, target_dest)
        return True

    patient_id = info.get('patient_id', '')
    study_date_str = info.get('study_date_str', '')
    date_only_str = info.get('date_only_str', '')
    incoming_uid = info.get('study_instance_uid', '')
    is_incoming_struct_only = is_structure_only_folder(src_study_path)

    # 1. Если папки пациента в целевом каталоге ещё нет:
    if not os.path.exists(dest_patient_path):
        if os.path.basename(src_study_path).startswith("[") and os.path.basename(src_study_path).endswith("]"):
            os.makedirs(dest_patient_path, exist_ok=True)
            dest_sub = os.path.join(dest_patient_path, os.path.basename(src_study_path))
            shutil.move(src_study_path, dest_sub)
        else:
            shutil.move(src_study_path, dest_patient_path)
        return True

    # 2. Папка пациента в целевом каталоге уже существует!
    exist_info = get_folder_study_info(dest_patient_path)
    is_dest_flat = exist_info and (os.path.dirname(os.path.abspath(exist_info['target_file'])) == os.path.abspath(dest_patient_path))

    if is_dest_flat:
        exist_uid = exist_info.get('study_instance_uid', '')
        if (exist_uid and incoming_uid and exist_uid == incoming_uid) or is_incoming_struct_only or (exist_info.get('date_only_str') == date_only_str):
            # Тот же StudyInstanceUID или дата — сливаем в корень dest_patient_path
            safe_merge_folders(src_study_path, dest_patient_path, patient_id)
            return True

        # Разные исследования: переводим целевую плоскую папку в иерархическую структуру
        if not make_folder_hierarchical(dest_patient_path, output_field):
            return False

    # 3. Целевая папка иерархическая (содержит подпапки [дата1], [дата2]...)
    matching_sub = find_matching_study_subfolder(dest_patient_path, incoming_uid, date_only_str, study_date_str)
    
    if not matching_sub and is_incoming_struct_only:
        try:
            for item in os.listdir(dest_patient_path):
                sub_path = os.path.join(dest_patient_path, item)
                if os.path.isdir(sub_path) and not is_structure_only_folder(sub_path):
                    matching_sub = sub_path
                    break
        except Exception:
            pass

    target_sub = matching_sub if matching_sub else os.path.join(dest_patient_path, f"[{study_date_str}]")

    if os.path.exists(target_sub):
        safe_merge_folders(src_study_path, target_sub, patient_id)
    else:
        shutil.move(src_study_path, target_sub)

    # Автолечение на случай дублирования
    auto_heal_split_patient_folders(dest_patient_path, patient_id, output_field)
    return True


def move_study_folder_hierarchical(src_path: str, dest_root_dir: str, output_field=None) -> bool:
    """
    Перемещает исследование или папку пациента из исходного каталога в целевой (архив или ct_images)
    с сохранением иерархической структуры исследований одного пациента.
    """
    if not os.path.exists(src_path):
        return False
        
    os.makedirs(dest_root_dir, exist_ok=True)
    
    src_parent = os.path.dirname(os.path.abspath(src_path))
    src_name = os.path.basename(os.path.abspath(src_path))
    
    try:
        immediate_subdirs = [os.path.join(src_path, d) for d in os.listdir(src_path)
                             if os.path.isdir(os.path.join(src_path, d)) and d.startswith("[") and d.endswith("]")]
    except Exception:
        immediate_subdirs = []

    # Если src_path — это родительская папка пациента, в которой есть подпапки исследований:
    if immediate_subdirs:
        dest_patient_path = os.path.join(dest_root_dir, src_name)
        if not os.path.exists(dest_patient_path):
            shutil.move(src_path, dest_patient_path)
            touch_folder_tree(dest_patient_path)
            return True
        else:
            if not make_folder_hierarchical(dest_patient_path, output_field):
                return False
            for sub in immediate_subdirs:
                move_single_study_folder(sub, dest_patient_path, output_field)
            touch_folder_tree(dest_patient_path)
            try:
                if os.path.exists(src_path) and not os.listdir(src_path):
                    os.rmdir(src_path)
            except Exception:
                pass
            return True

    # Иначе src_path — это одиночное исследование
    if src_name.startswith("[") and src_name.endswith("]"):
        patient_folder_name = os.path.basename(src_parent)
    else:
        patient_folder_name = src_name

    dest_patient_path = os.path.join(dest_root_dir, patient_folder_name)
    success = move_single_study_folder(src_path, dest_patient_path, output_field)
    if success:
        touch_folder_tree(dest_patient_path)
    
    if src_name.startswith("[") and src_name.endswith("]"):
        try:
            if os.path.exists(src_parent) and not os.listdir(src_parent):
                os.rmdir(src_parent)
        except Exception:
            pass
            
    return success

