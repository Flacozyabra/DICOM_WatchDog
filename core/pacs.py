from pydicom.dataset import Dataset
from pynetdicom import AE, debug_logger
from pynetdicom.sop_class import PatientRootQueryRetrieveInformationModelFind

from collections import defaultdict
from datetime import datetime, timedelta
from pprint import pprint

from core.logger import log_message


def pacs_dict_create(output_field, slice=None):
    pacs_data = defaultdict(dict)
    ae = AE()
    ae.add_requested_context('1.2.840.10008.5.1.4.1.2.1.1')  # C-FIND (Patient Root Query)

    # Create our Identifier (query) dataset
    ds = Dataset()
    ds.PatientName = '*'
    ds.PatientID = '*'
    ds.StudyTime = ''
    today = datetime.today().strftime('%Y%m%d')
    ds.StudyDate = today
    ds.QueryRetrieveLevel = 'STUDY'
    
    # Запрашиваем дополнительные поля для области сканирования
    ds.BodyPartExamined = ''
    ds.StudyDescription = ''

    # Associate with the peer AE at IP 127.0.0.1 and port 11112
    assoc = ae.associate("192.168.5.155", 4242)

    if assoc.is_established:
        # Send the C-FIND request
        responses = assoc.send_c_find(ds, '1.2.840.10008.5.1.4.1.2.1.1')

        for (status, identifier) in responses:
            con = True
            if status and identifier:
                patient_id = identifier.get('PatientID', 'Нет инфы об айди')
                pacs_data[patient_id]['study_patient_id'] = patient_id
                pacs_data[patient_id]['patient_name'] = identifier.get(
                    'PatientName', 'Нет инфы об имени')
                pacs_data[patient_id]['study_time'] = identifier.get(
                    'StudyTime', 'Нет инфы о времени')
                pacs_data[patient_id]['study_date'] = identifier.get(
                    'StudyDate', 'Нет инфы о дате')

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

        assoc.release()
    else:
        con = False
        log_message(output_field, "Не удалось подключиться к серверу PACS")

    return pacs_data, con
