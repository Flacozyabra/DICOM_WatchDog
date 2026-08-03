from pydicom.dataset import Dataset
from pynetdicom import AE, debug_logger
from pynetdicom.sop_class import PatientRootQueryRetrieveInformationModelFind

from collections import defaultdict
from datetime import datetime, timedelta
from pprint import pprint

from core.logger import log_message
from core.config_utils import get_log_path
from core.locale_utils import tr_log, tr_ui


class BackgroundDicomServer:
    """Фоновый DICOM SCP сервер для ответа на опрос (C-ECHO) сервера PACS и приема снимков (C-STORE)."""
    def __init__(self):
        self.server = None
        self.port = None
        self.ae_title = None

    def start(self, port=11112, ae_title="DW_GAMMA", target_dir=None):
        if self.server:
            self.stop()

        from pynetdicom import AE, evt, ALL_TRANSFER_SYNTAXES
        VerificationSOPClass = '1.2.840.10008.1.1'
        from pynetdicom.sop_class import (
            CTImageStorage,
            MRImageStorage,
            RTStructureSetStorage,
            SecondaryCaptureImageStorage,
            PositronEmissionTomographyImageStorage
        )
        import os

        self.port = port
        self.ae_title = ae_title

        def handle_echo(event):
            return 0x0000

        def handle_store(event):
            try:
                if not target_dir or not os.path.exists(target_dir):
                    return 0xC000
                d_set = event.dataset
                d_set.file_meta = event.file_meta
                
                pid = str(d_set.get('PatientID', 'UNKNOWN')).strip()
                safe_pid = "".join([c for c in pid if c.isalnum() or c in (' ', '_', '-')]).strip()
                if not safe_pid:
                    safe_pid = "UNKNOWN"
                    
                p_dir = os.path.join(target_dir, safe_pid)
                os.makedirs(p_dir, exist_ok=True)
                
                file_path = os.path.join(p_dir, f"{d_set.SOPInstanceUID}.dcm")
                d_set.save_as(file_path, write_like_original=False)
                return 0x0000
            except Exception:
                return 0xC000

        handlers = [
            (evt.EVT_C_ECHO, handle_echo),
            (evt.EVT_C_STORE, handle_store)
        ]

        ae = AE(ae_title=ae_title)
        ae.add_supported_context(VerificationSOPClass, ALL_TRANSFER_SYNTAXES)
        for sop in [CTImageStorage, MRImageStorage, RTStructureSetStorage, SecondaryCaptureImageStorage, PositronEmissionTomographyImageStorage]:
            ae.add_supported_context(sop, ALL_TRANSFER_SYNTAXES)

        try:
            self.server = ae.start_server(('', port), block=False, evt_handlers=handlers)
        except Exception:
            self.server = None

    def stop(self):
        if self.server:
            try:
                self.server.shutdown()
            except Exception:
                pass
            self.server = None

_global_dicom_server = BackgroundDicomServer()

def start_background_pacs_server(port=11112, ae_title="DW_GAMMA", target_dir=None):
    _global_dicom_server.start(port=port, ae_title=ae_title, target_dir=target_dir)

def stop_background_pacs_server():
    _global_dicom_server.stop()


def pacs_dict_create(output_field, slice=None, pacs_ip="127.0.0.1", pacs_port=11112, called_aet="ANY-SCP", calling_aet="ECHOSCU", study_date=None):
    pacs_data = defaultdict(dict)
    con = False
    
    if len(calling_aet) > 16:
        log_message(output_field, tr_log("log_pacs_aet_local_too_long", calling_aet))
        return pacs_data, False
    if len(called_aet) > 16:
        log_message(output_field, tr_log("log_pacs_aet_remote_too_long", called_aet))
        return pacs_data, False

    ae = AE()
    ae.ae_title = calling_aet
    ae.add_requested_context('1.2.840.10008.5.1.4.1.2.1.1')  # C-FIND (Patient Root Query)

    # Create our Identifier (query) dataset
    ds = Dataset()
    ds.PatientName = '*'
    ds.PatientID = '*'
    ds.StudyTime = ''
    if study_date:
        ds.StudyDate = study_date
    else:
        today = datetime.today().strftime('%Y%m%d')
        ds.StudyDate = today
    ds.QueryRetrieveLevel = 'STUDY'
    
    # Запрашиваем дополнительные поля для области сканирования
    ds.BodyPartExamined = ''
    ds.StudyDescription = ''
    ds.NumberOfStudyRelatedInstances = ''
    ds.ModalitiesInStudy = ''
    ds.StudyInstanceUID = ''

    # Associate with the peer AE
    assoc = ae.associate(pacs_ip, pacs_port, ae_title=called_aet)

    if assoc.is_established:
        con = True
        try:
            # Send the C-FIND request
            responses = assoc.send_c_find(ds, '1.2.840.10008.5.1.4.1.2.1.1')

            for (status, identifier) in responses:
                if status and identifier:
                    patient_id = identifier.get('PatientID', 'N/A')
                    pacs_data[patient_id]['study_patient_id'] = patient_id
                    pacs_data[patient_id]['patient_name'] = identifier.get(
                        'PatientName', 'N/A')
                    pacs_data[patient_id]['study_time'] = identifier.get(
                        'StudyTime', 'N/A')
                    pacs_data[patient_id]['study_date'] = identifier.get(
                        'StudyDate', 'N/A')
                    pacs_data[patient_id]['slices'] = str(identifier.get('NumberOfStudyRelatedInstances', '0'))
                    pacs_data[patient_id]['modality'] = str(identifier.get('ModalitiesInStudy', 'CT'))
                    pacs_data[patient_id]['study_instance_uid'] = str(identifier.get('StudyInstanceUID', ''))

                    # Область сканирования
                    body_part = identifier.get('BodyPartExamined', '')
                    if not body_part:
                        body_part = identifier.get('StudyDescription', '')
                    body_part_str = str(body_part).strip()
                    if not body_part_str:
                        body_part_str = "Unknown"
                    pacs_data[patient_id]['body_part'] = body_part_str

                    try:
                        # Преобразование времени
                        format_string = '%H%M%S' if '.' not in pacs_data[patient_id]['study_time'] else '%H%M%S.%f'
                        time_obj = datetime.strptime(pacs_data[patient_id]['study_time'], format_string)
                        time_formatted = time_obj.strftime('%H:%M')
                        # Преобразование даты
                        date_obj = datetime.strptime(pacs_data[patient_id]['study_date'], '%Y%m%d')
                        date_formatted = date_obj.strftime('%d.%m.%y')
                        # Комбинирование времени и даты
                        date_time = f"{date_formatted} - {time_formatted}"

                        # Создание объекта datetime, представляющего дату и время
                        study_datetime_obj = date_obj + timedelta(hours=time_obj.hour, minutes=time_obj.minute,
                                                               seconds=time_obj.second, microseconds=time_obj.microsecond)
                    except Exception:
                        study_datetime_obj = datetime.now()
                        date_time = study_datetime_obj.strftime('%d.%m.%y - %H:%M')

                    pacs_data[patient_id]['study_datetime_obj'] = study_datetime_obj
                    pacs_data[patient_id]['study_datetime_str'] = date_time
            
            if assoc.is_aborted or assoc.is_rejected:
                con = False
                log_message(output_field, tr_log("log_pacs_cfind_aborted", calling_aet))
            else:
                con = True
        except Exception as e:
            con = False
            log_message(output_field, tr_log("log_pacs_cfind_error", e))
        finally:
            assoc.release()
    else:
        con = False
        log_message(output_field, tr_log("log_failed_connect_pacs"))

    return pacs_data, con


def ping_pacs(pacs_ip, pacs_port, called_aet="ANY-SCP", calling_aet="ECHOSCU"):
    if len(calling_aet) > 16:
        return False, tr_ui("ping_aet_local_too_long", calling_aet)
    if len(called_aet) > 16:
        return False, tr_ui("ping_aet_remote_too_long", called_aet)

    ae = AE()
    ae.ae_title = calling_aet
    ae.connection_timeout = 5
    ae.network_timeout = 5
    ae.acse_timeout = 5
    ae.dimse_timeout = 5
    ae.add_requested_context('1.2.840.10008.1.1')  # C-ECHO ONLY

    try:
        assoc = ae.associate(pacs_ip, pacs_port, ae_title=called_aet)
        if assoc.is_established:
            status = assoc.send_c_echo()
            assoc.release()
            if status and hasattr(status, 'Status') and status.Status == 0x0000:
                return True, tr_ui("ping_success")
            else:
                st_hex = f"0x{status.Status:04X}" if status and hasattr(status, 'Status') else "N/A"
                return False, f"PACS сервер вернул статус {st_hex} на C-ECHO."
        else:
            return False, f"PACS сервер ({pacs_ip}:{pacs_port}) отклонил DICOM-ассоциацию C-ECHO."
    except Exception as e:
        return False, f"Ошибка при подключении к PACS: {e}"


def download_patient_from_pacs(patient_id, target_dir, pacs_ip, pacs_port, called_aet, calling_aet, progress_callback=None, is_cancelled_callback=None, local_port=11112, study_instance_uid=None):
    if len(calling_aet) > 16:
        return False, tr_ui("ping_aet_local_too_long", calling_aet)
    if len(called_aet) > 16:
        return False, tr_ui("ping_aet_remote_too_long", called_aet)

    from pydicom.dataset import Dataset
    from pynetdicom import AE, evt, build_role, ALL_TRANSFER_SYNTAXES
    from pynetdicom.sop_class import (
        PatientRootQueryRetrieveInformationModelMove,
        StudyRootQueryRetrieveInformationModelMove,
        PatientRootQueryRetrieveInformationModelGet,
        StudyRootQueryRetrieveInformationModelGet,
        CTImageStorage,
        MRImageStorage,
        RTStructureSetStorage,
        SecondaryCaptureImageStorage,
        PositronEmissionTomographyImageStorage
    )
    import os
    import shutil

    saved_files_count = [0]
    created_patient_dir = [None]

    def handle_store(event, dest_dir):
        if is_cancelled_callback and is_cancelled_callback():
            return 0xFE00
        try:
            d_set = event.dataset
            d_set.file_meta = event.file_meta
            
            pid = str(d_set.get('PatientID', 'UNKNOWN')).strip()
            safe_pid = "".join([c for c in pid if c.isalnum() or c in (' ', '_', '-')]).strip()
            if not safe_pid:
                safe_pid = "UNKNOWN"
                
            p_dir = os.path.join(dest_dir, safe_pid)
            os.makedirs(p_dir, exist_ok=True)
            created_patient_dir[0] = p_dir
            
            file_path = os.path.join(p_dir, f"{d_set.SOPInstanceUID}.dcm")
            d_set.save_as(file_path, write_like_original=False)
            saved_files_count[0] += 1
            return 0x0000
        except Exception as e:
            return 0xC000

    handlers = [(evt.EVT_C_STORE, handle_store, [target_dir])]

    storage_classes = [
        CTImageStorage,
        MRImageStorage,
        RTStructureSetStorage,
        SecondaryCaptureImageStorage,
        PositronEmissionTomographyImageStorage
    ]

    # Подготавливаем локальные серверы C-STORE SCP на портах (local_port, pacs_port, 11112, 104)
    scp_servers = []
    ports_to_try = []
    for p in [local_port, pacs_port, 11112, 104]:
        if p and isinstance(p, int) and p not in ports_to_try:
            ports_to_try.append(p)

    ae_move = AE()
    ae_move.ae_title = calling_aet
    ae_move.connection_timeout = 5
    ae_move.network_timeout = 5
    ae_move.acse_timeout = 5
    ae_move.dimse_timeout = 10
    ae_move.add_requested_context(PatientRootQueryRetrieveInformationModelMove)
    ae_move.add_requested_context(StudyRootQueryRetrieveInformationModelMove)

    for sop_class in storage_classes:
        ae_move.add_requested_context(sop_class, ALL_TRANSFER_SYNTAXES)

    for p in ports_to_try:
        try:
            srv = ae_move.start_server(('', p), block=False, evt_handlers=handlers)
            if srv:
                scp_servers.append(srv)
        except Exception:
            pass

    clean_pid = str(patient_id).strip() if patient_id else ""
    clean_uid = str(study_instance_uid).strip() if study_instance_uid else ""

    safe_pid = "".join([c for c in clean_pid if c.isalnum() or c in (' ', '_', '-')]).strip() or "UNKNOWN"
    p_dir = os.path.join(target_dir, safe_pid)

    def count_downloaded_files():
        if os.path.exists(p_dir):
            return len([f for f in os.listdir(p_dir) if f.endswith('.dcm')])
        return 0

    initial_files = count_downloaded_files()

    # Создаем комбинации корректных DICOM datasets для C-MOVE
    query_datasets = []

    # 1. Study Root C-MOVE на уровне STUDY
    if clean_uid:
        ds_study_root = Dataset()
        ds_study_root.QueryRetrieveLevel = 'STUDY'
        ds_study_root.StudyInstanceUID = clean_uid
        query_datasets.append((ds_study_root, StudyRootQueryRetrieveInformationModelMove))

    # 2. Patient Root C-MOVE на уровне STUDY по PatientID + StudyInstanceUID
    if clean_pid and clean_uid:
        ds_pat_study = Dataset()
        ds_pat_study.QueryRetrieveLevel = 'STUDY'
        ds_pat_study.PatientID = clean_pid
        ds_pat_study.StudyInstanceUID = clean_uid
        query_datasets.append((ds_pat_study, PatientRootQueryRetrieveInformationModelMove))

    # 3. Patient Root C-MOVE на уровне STUDY только по PatientID
    if clean_pid:
        ds_pat_only = Dataset()
        ds_pat_only.QueryRetrieveLevel = 'STUDY'
        ds_pat_only.PatientID = clean_pid
        query_datasets.append((ds_pat_only, PatientRootQueryRetrieveInformationModelMove))

    last_error_details = []

    # Пробуем варианты C-MOVE
    for query_ds, move_sop_class in query_datasets:
        if (count_downloaded_files() > initial_files or count_downloaded_files() > 0) or (is_cancelled_callback and is_cancelled_callback()):
            break
        try:
            assoc_move = ae_move.associate(pacs_ip, pacs_port, ae_title=called_aet)
            if assoc_move.is_established:
                responses = assoc_move.send_c_move(query_ds, calling_aet, move_sop_class)
                for (status, identifier) in responses:
                    if is_cancelled_callback and is_cancelled_callback():
                        try:
                            assoc_move.abort()
                        except Exception:
                            pass
                        break
                    if status:
                        st_code = getattr(status, 'Status', None)
                        if st_code is not None and st_code not in (0x0000, 0xFF00, 0xFF01):
                            hex_st = f"0x{st_code:04X}"
                            if st_code == 0xA801:
                                last_error_details.append(f"Код {hex_st} (AET '{calling_aet}' не прописан в базе назначения C-MOVE сервера PACS)")
                            elif st_code in (0xA701, 0xA702):
                                last_error_details.append(f"Код {hex_st} (PACS отказал в C-STORE)")
                            elif st_code == 0xA900:
                                last_error_details.append(f"Код {hex_st} (Неверный формат параметров C-MOVE)")
                            elif st_code == 0xC000:
                                last_error_details.append(f"Код {hex_st} (Сервер не принял параметры запроса C-MOVE)")
                            else:
                                last_error_details.append(f"Код {hex_st}")
                        if progress_callback:
                            completed = getattr(status, 'NumberOfCompletedSuboperations', 0)
                            remaining = getattr(status, 'NumberOfRemainingSuboperations', 0)
                            failed = getattr(status, 'NumberOfFailedSuboperations', 0)
                            completed_val = completed.value if hasattr(completed, 'value') else int(completed or 0)
                            remaining_val = remaining.value if hasattr(remaining, 'value') else int(remaining or 0)
                            failed_val = failed.value if hasattr(failed, 'value') else int(failed or 0)
                            total_val = completed_val + remaining_val + failed_val
                            if total_val > 0:
                                progress_callback(completed_val, total_val)
                assoc_move.release()
            else:
                last_error_details.append("PACS отклонил C-MOVE ассоциацию")
        except Exception as e:
            last_error_details.append(f"Ошибка C-MOVE: {e}")

    for srv in scp_servers:
        try:
            srv.shutdown()
        except Exception:
            pass

    if is_cancelled_callback and is_cancelled_callback():
        if created_patient_dir[0] and os.path.exists(created_patient_dir[0]):
            shutil.rmtree(created_patient_dir[0], ignore_errors=True)
        return False, "Скачивание отменено пользователем."

    if count_downloaded_files() > initial_files or count_downloaded_files() > 0 or saved_files_count[0] > 0:
        return True, tr_log("log_pacs_download_success", patient_id)

    err_reason = "; ".join(list(dict.fromkeys(last_error_details))) if last_error_details else "Сервер не передал файлы"
    return False, f"Ошибка скачивания: Сервер PACS ({pacs_ip}:{pacs_port}) вернул 0 файлов.\nДетали: {err_reason}.\nПроверьте регистрацию AET '{calling_aet}' на сервере PACS."

