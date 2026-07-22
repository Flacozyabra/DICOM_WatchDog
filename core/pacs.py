from pydicom.dataset import Dataset
from pynetdicom import AE, debug_logger
from pynetdicom.sop_class import PatientRootQueryRetrieveInformationModelFind

from collections import defaultdict
from datetime import datetime, timedelta
from pprint import pprint

from core.logger import log_message
from core.config_utils import get_log_path
from core.locale_utils import tr_log


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

    import io
    import logging

    # Capture pynetdicom logs to diagnose exact network/association issues
    log_stream = io.StringIO()
    log_handler = logging.StreamHandler(log_stream)
    log_handler.setLevel(logging.INFO)

    pynetdicom_logger = logging.getLogger('pynetdicom')
    orig_level = pynetdicom_logger.level
    pynetdicom_logger.setLevel(logging.INFO)
    pynetdicom_logger.addHandler(log_handler)

    ae = AE()
    ae.ae_title = calling_aet
    ae.connection_timeout = 3
    ae.add_requested_context('1.2.840.10008.1.1')  # C-ECHO
    ae.add_requested_context('1.2.840.10008.5.1.4.1.2.1.1')  # C-FIND

    assoc = None
    try:
        assoc = ae.associate(pacs_ip, pacs_port, ae_title=called_aet)

        # Remove logger handlers to prevent resource leaks
        pynetdicom_logger.removeHandler(log_handler)
        pynetdicom_logger.setLevel(orig_level)
        log_output = log_stream.getvalue()

        if assoc.is_established:
            # 1. Проверяем C-ECHO
            echo_status = assoc.send_c_echo()
            if not echo_status or echo_status.Status != 0x0000:
                assoc.release()
                status_hex = f"0x{echo_status.Status:04x}" if echo_status and echo_status.Status is not None else "None"
                return False, tr_ui("ping_echo_bad_status", status_hex)

            # 2. Проверяем возможность C-FIND (проверка регистрации AET/IP)
            from pydicom.dataset import Dataset
            ds = Dataset()
            ds.QueryRetrieveLevel = 'STUDY'
            ds.PatientName = '*'
            ds.PatientID = '*'
            ds.StudyTime = ''
            ds.StudyDate = '19000101'  # Тестовая дата
            
            find_aborted = False
            try:
                responses = list(assoc.send_c_find(ds, '1.2.840.10008.5.1.4.1.2.1.1'))
                if not responses or not responses[0][0].keys() or assoc.is_aborted:
                    find_aborted = True
            except Exception:
                find_aborted = True
                
            assoc.release()
            
            if find_aborted:
                return False, tr_ui("ping_cfind_unregistered", calling_aet, called_aet)

            return True, tr_ui("ping_success")
        else:
            # Parse reject/connection details from logs
            if "timed out" in log_output or "timeout" in log_output or "Connection timed out" in log_output:
                return False, tr_ui("ping_timeout", pacs_ip, pacs_port)
            elif "Connection refused" in log_output or "refused" in log_output:
                return False, tr_ui("ping_refused", pacs_ip, pacs_port, pacs_port)
            elif "Calling AE title not recognised" in log_output or "Calling AE Title Not Recognized" in log_output:
                import socket
                local_ip = "N/A"
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    s.connect((pacs_ip, pacs_port))
                    local_ip = s.getsockname()[0]
                    s.close()
                except Exception:
                    pass
                return False, tr_ui("ping_calling_not_recognized", calling_aet, local_ip, calling_aet)
            elif "Called AE title not recognised" in log_output or "Called AE Title Not Recognized" in log_output:
                return False, tr_ui("ping_called_not_recognized", called_aet)

            # Extract generic reason if present
            reason_line = ""
            for line in log_output.splitlines():
                if "Reason:" in line:
                    reason_line = line.split("Reason:")[-1].strip()
                    break

            if reason_line:
                return False, tr_ui("ping_rejected_with_reason", reason_line)

            return False, tr_ui("ping_generic_fail")
    except Exception as e:
        try:
            pynetdicom_logger.removeHandler(log_handler)
            pynetdicom_logger.setLevel(orig_level)
        except Exception:
            pass
        return False, tr_ui("ping_exception", str(e))


def download_patient_from_pacs(patient_id, target_dir, pacs_ip, pacs_port, called_aet, calling_aet, progress_callback=None):
    if len(calling_aet) > 16:
        return False, tr_ui("ping_aet_local_too_long", calling_aet)
    if len(called_aet) > 16:
        return False, tr_ui("ping_aet_remote_too_long", called_aet)

    from pydicom.dataset import Dataset
    from pynetdicom import AE, evt, build_role, ALL_TRANSFER_SYNTAXES
    from pynetdicom.sop_class import (
        PatientRootQueryRetrieveInformationModelGet,
        CTImageStorage,
        MRImageStorage,
        RTStructureSetStorage,
        SecondaryCaptureImageStorage,
        PositronEmissionTomographyImageStorage
    )
    import os

    ae = AE()
    ae.ae_title = calling_aet
    ae.add_requested_context(PatientRootQueryRetrieveInformationModelGet)
    
    storage_classes = [
        CTImageStorage,
        MRImageStorage,
        RTStructureSetStorage,
        SecondaryCaptureImageStorage,
        PositronEmissionTomographyImageStorage
    ]
    
    roles = []
    for sop_class in storage_classes:
        ae.add_requested_context(sop_class, ALL_TRANSFER_SYNTAXES)
        roles.append(build_role(sop_class, scp_role=True))
        
    ds = Dataset()
    ds.QueryRetrieveLevel = 'PATIENT'
    ds.PatientID = patient_id
    
    def handle_store(event, dest_dir):
        try:
            d_set = event.dataset
            d_set.file_meta = event.file_meta
            
            pid = str(d_set.get('PatientID', 'UNKNOWN')).strip()
            safe_pid = "".join([c for c in pid if c.isalnum() or c in (' ', '_', '-')]).strip()
            if not safe_pid:
                safe_pid = "UNKNOWN"
                
            p_dir = os.path.join(dest_dir, safe_pid)
            os.makedirs(p_dir, exist_ok=True)
            
            file_path = os.path.join(p_dir, f"{d_set.SOPInstanceUID}.dcm")
            d_set.save_as(file_path, write_like_original=False)
            return 0x0000
        except Exception as e:
            import traceback
            from datetime import datetime
            try:
                with open(get_log_path(), "a", encoding="utf-8") as f:
                    f.write(f"\n--- {datetime.now()} ---\n")
                    traceback.print_exc(file=f)
                    f.write(f"Error details: {str(e)}\n")
            except Exception:
                pass
            traceback.print_exc()
            return 0xC000
            
    handlers = [(evt.EVT_C_STORE, handle_store, [target_dir])]
    assoc = ae.associate(pacs_ip, pacs_port, ae_title=called_aet, evt_handlers=handlers, ext_neg=roles)
    
    success = False
    msg = ""
    if assoc.is_established:
        responses = assoc.send_c_get(ds, PatientRootQueryRetrieveInformationModelGet)
        status_list = []
        for (status, identifier) in responses:
            if status:
                status_list.append(status.Status)
                try:
                    with open(get_log_path(), "a", encoding="utf-8") as f:
                        f.write(f"\n--- C-GET Status Response ({datetime.now()}) ---\n")
                        f.write(str(status))
                        f.write("\n")
                except Exception:
                    pass
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
        is_aborted_or_rejected = getattr(assoc, 'is_aborted', False) or getattr(assoc, 'is_rejected', False)
        assoc.release()
        
        if is_aborted_or_rejected:
            success = False
            msg = tr_log("log_pacs_download_aborted", calling_aet)
        elif status_list and (status_list[-1] == 0x0000 or status_list[-1] == 0xB000):
            success = True
            msg = tr_log("log_pacs_download_success", patient_id)
        elif status_list and (0x0000 in status_list or 0xB000 in status_list):
            success = True
            msg = tr_log("log_pacs_download_success", patient_id)
        else:
            last_status = f"0x{status_list[-1]:04x}" if status_list else "unknown status"
            success = False
            msg = tr_log("log_pacs_download_server_error", last_status)
    else:
        success = False
        msg = tr_log("log_pacs_download_no_connection")
        
    return success, msg

