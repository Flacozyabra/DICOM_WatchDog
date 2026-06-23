from pydicom.dataset import Dataset
from pynetdicom import AE, debug_logger
from pynetdicom.sop_class import PatientRootQueryRetrieveInformationModelFind

from collections import defaultdict
from datetime import datetime, timedelta
from pprint import pprint

from core.logger import log_message


def pacs_dict_create(output_field, slice=None, pacs_ip="127.0.0.1", pacs_port=11112, called_aet="ANY-SCP", calling_aet="ECHOSCU", study_date=None):
    pacs_data = defaultdict(dict)
    con = False
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

    # Associate with the peer AE
    assoc = ae.associate(pacs_ip, pacs_port, ae_title=called_aet)

    if assoc.is_established:
        con = True
        try:
            # Send the C-FIND request
            responses = assoc.send_c_find(ds, '1.2.840.10008.5.1.4.1.2.1.1')

            for (status, identifier) in responses:
                if status and identifier:
                    patient_id = identifier.get('PatientID', 'Нет инфы об айди')
                    pacs_data[patient_id]['study_patient_id'] = patient_id
                    pacs_data[patient_id]['patient_name'] = identifier.get(
                        'PatientName', 'Нет инфы об имени')
                    pacs_data[patient_id]['study_time'] = identifier.get(
                        'StudyTime', 'Нет инфы о времени')
                    pacs_data[patient_id]['study_date'] = identifier.get(
                        'StudyDate', 'Нет инфы о дате')
                    pacs_data[patient_id]['slices'] = str(identifier.get('NumberOfStudyRelatedInstances', '0'))

                    # Область сканирования
                    body_part = identifier.get('BodyPartExamined', '')
                    if not body_part:
                        body_part = identifier.get('StudyDescription', '')
                    body_part_str = str(body_part).strip()
                    if not body_part_str:
                        body_part_str = "Unknown"
                    pacs_data[patient_id]['body_part'] = body_part_str

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

                    pacs_data[patient_id]['study_datetime_obj'] = study_datetime_obj
                    pacs_data[patient_id]['study_datetime_str'] = date_time
            
            if assoc.is_aborted:
                log_message(output_field, f"Соединение с PACS было сброшено сервером во время поиска.\nВозможно, локальный AE Title ({calling_aet}) или IP этого компьютера не зарегистрированы на сервере PACS.")
        except Exception as e:
            log_message(output_field, f"Ошибка при выполнении запроса C-FIND к PACS: {e}")
        finally:
            assoc.release()
    else:
        con = False
        log_message(output_field, "Не удалось подключиться к серверу PACS")

    return pacs_data, con


def ping_pacs(pacs_ip, pacs_port, called_aet="ANY-SCP", calling_aet="ECHOSCU"):
    ae = AE()
    ae.ae_title = calling_aet
    ae.connection_timeout = 3
    ae.add_requested_context('1.2.840.10008.1.1')  # C-ECHO
    ae.add_requested_context('1.2.840.10008.5.1.4.1.2.1.1')  # C-FIND
    try:
        assoc = ae.associate(pacs_ip, pacs_port, ae_title=called_aet)
        if assoc.is_established:
            # 1. Проверяем C-ECHO
            echo_status = assoc.send_c_echo()
            if not echo_status or echo_status.Status != 0x0000:
                assoc.release()
                status_hex = f"0x{echo_status.Status:04x}" if echo_status and echo_status.Status is not None else "None"
                return False, f"Ошибка: PACS сервер вернул код статуса {status_hex} на C-ECHO."

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
                return False, (
                    f"Связь есть, но устройство не зарегистрировано!\n\n"
                    f"PACS сервер успешно ответил на C-ECHO (пинг), но сбросил соединение при попытке C-FIND (поиск).\n"
                    f"Проверьте, что локальный AE Title (AET Local: \"{calling_aet}\") и IP-адрес этого компьютера зарегистрированы на PACS сервере, либо неверно указан AET Remote (\"{called_aet}\")."
                )
                
            return True, "Соединение успешно установлено!\nPACS сервер ответил на пинг и разрешил поиск (устройство зарегистрировано)."
        else:
            return False, "Ошибка: Не удалось установить связь с PACS сервером.\nПроверьте IP-адрес, порт и AE Titles."
    except Exception as e:
        return False, f"Произошла ошибка при подключении:\n{str(e)}"


def download_patient_from_pacs(patient_id, target_dir, pacs_ip, pacs_port, called_aet, calling_aet):
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
                with open("pacs_error.log", "a", encoding="utf-8") as f:
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
                    with open("pacs_error.log", "a", encoding="utf-8") as f:
                        f.write(f"\n--- C-GET Status Response ({datetime.now()}) ---\n")
                        f.write(str(status))
                        f.write("\n")
                except Exception:
                    pass
        assoc.release()
        
        if status_list and (status_list[-1] == 0x0000 or status_list[-1] == 0xB000):
            success = True
            msg = f"Пациент {patient_id} успешно скачан из PACS"
        elif status_list and (0x0000 in status_list or 0xB000 in status_list):
            success = True
            msg = f"Пациент {patient_id} успешно скачан из PACS"
        else:
            last_status = f"0x{status_list[-1]:04x}" if status_list else "unknown status"
            success = False
            msg = f"PACS сервер вернул ошибку при скачивании: {last_status}"
    else:
        success = False
        msg = "Не удалось установить соединение с PACS сервером для скачивания."
        
    return success, msg

