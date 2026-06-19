import os
import time
import shutil
from datetime import datetime
from collections import defaultdict
from prettytable import PrettyTable
import pydicom

remember_move = []
remember_fname = []
remember_fnname = []
remember_id = []


def rename_patient_folder(path):
    """Переименовывает папку и изменяет PatientID файлов внутри"""
    global remember_fname, remember_fnname, remember_id

    patient_folder = os.path.basename(path)
    ds = pydicom.read_file(os.path.join(path, os.listdir(path)[0]))

    if patient_folder != ds.PatientID:
        new_patient_id = ds.PatientID[3:]
        new_folder = str(new_patient_id)
        new_path = os.path.join(os.path.dirname(path), new_folder)

        if os.path.exists(new_path):
            # Если папка уже существует, добавляем файлы в нее
            for dirpath, dirnames, filenames in os.walk(path):
                for filename in filenames:
                    if filename.endswith('.dcm'):
                        ds = pydicom.read_file(os.path.join(dirpath, filename))
                        ds.PatientID = new_patient_id
                        ds.save_as(os.path.join(new_path, filename))
                    #if filename.startswith('STR'):
                    #    ds.save_as(os.path.join(new_path, filename))

            remember_fname.append(patient_folder)
            remember_fnname.append(new_folder)
            remember_id.append(new_patient_id)

            print(f"Файлы успешно добавлены в существующую папку: {new_folder}, новый PatientID: {new_patient_id}")
            shutil.rmtree(path)
            print(f'Исходная папка удалена {patient_folder}')
        else:
            try:
                os.rename(path, new_path)
                for dirpath, dirnames, filenames in os.walk(new_path):
                    for filename in filenames:
                        if filename.endswith('.dcm'):
                            ds = pydicom.read_file(os.path.join(dirpath, filename))
                            ds.PatientID = new_patient_id
                            ds.save_as(os.path.join(new_path, filename))
                        #if filename.startswith('STR'):
                        #    ds.save_as(os.path.join(new_path, filename))

                remember_fname.append(patient_folder)
                remember_fnname.append(new_folder)
                remember_id.append(new_patient_id)

                print(f"Переименовано: {patient_folder} -> {new_folder}, новый PatientID: {new_patient_id}")
            except Exception as e:
                print(f"Ошибка переименования {patient_folder}: {e}")


def update_table():
    """Обновляет таблицу"""
    print('Обновляю таблицу...')
    patient_data = defaultdict(dict)
    for root, dirs, files in os.walk('E:\planw-directories\ct_images'):
        for file in files:
            if file.endswith('.dcm'):
                try:
                    ds = pydicom.read_file(os.path.join(root, file))
                    patient_data[ds.PatientID]['patient_id'] = ds.PatientID
                    patient_data[ds.PatientID]['patient_name'] = ds.PatientName
                    patient_data[ds.PatientID]['study_datetime'] = datetime.strptime(ds.StudyDate + ds.StudyTime, '%Y%m%d%H%M%S.%f')
                    patient_data[ds.PatientID]['folder_datetime'] = datetime.fromtimestamp(os.path.getctime(root))
                    patient_data[ds.PatientID]['str'] = patient_data[ds.PatientID].get('str', 0) + int(file.startswith('STR'))
                except Exception as e:
                    print(f"Ошибка чтения файла {os.path.join(root, file)}: {e}")
    table = PrettyTable()
    table.align = 'l'  # выравнивание по левому краю
    table.field_names = [
        'Patient ID', 'Patient Name', 'Study Date', 'Folder Date', 'STR'
    ]

    GREEN = '\033[92m'
    YELLOW = '\033[33m'
    END = '\033[0m'

    # Обновляем таблицу
    for patient_id, data in sorted(patient_data.items(), key=lambda x: str(x[1]['patient_name'])):
        patient_name = str(data['patient_name'])
        study_datetime = data['study_datetime']
        study_date = study_datetime.date().strftime('%d.%m.%y')
        study_time = study_datetime.time().strftime('%H:%M')
        folder_datetime = data['folder_datetime']
        folder_date = folder_datetime.strftime('%d.%m.%y - %H:%M')
        str_count = data['str']

        if (datetime.now() - datetime.strptime(folder_date, '%d.%m.%y - %H:%M')).total_seconds() / 3600 < 1:
            row_color = GREEN
            table.add_row([row_color + patient_id, row_color + patient_name, row_color + study_date + ' - ' + study_time,
                           row_color + folder_date, row_color + str(str_count) + END])
        elif (datetime.now() - datetime.strptime(folder_date, '%d.%m.%y - %H:%M')).total_seconds() / (3600 * 24) < 1:
            row_color = YELLOW
            table.add_row([row_color + patient_id, row_color + patient_name, row_color + study_date + ' - ' + study_time,
                           row_color + folder_date, row_color + str(str_count) + END])
        else:
            table.add_row([patient_id, patient_name, study_date + ' - ' + study_time, folder_date, str(str_count)])

        # table.add_row([row_color + patient_id, row_color + patient_name, row_color + study_date + ' - ' + study_time,
        #                row_color + folder_date, row_color + str(str_count) + END])
    os.system('cls')
    print(table)

def move_old_folders_to_archive():
    global remember_move
    for root, dirs, files in os.walk('E:\planw-directories\ct_images'):
        for dir in dirs:
            folder_path = os.path.join(root, dir)
            folder_date = datetime.fromtimestamp(os.path.getctime(folder_path))
            if (datetime.now() - folder_date).days >= 3:
                archive_path = 'E:\planw-directories\ct_archive'
                if not os.path.exists(archive_path):
                    os.makedirs(archive_path)
                os.rename(folder_path, os.path.join(archive_path, dir))
                remember_move.append(dir)
                print(f"Папка {dir} перемещена в архив")

# Проверяем и переименовываем папки раз в 15 секунд
while True:
    for root, dirs, files in os.walk('E:\planw-directories\ct_images'):
        for dir in dirs:
            rename_patient_folder(os.path.join(root, dir))
    move_old_folders_to_archive()
    update_table()
    #print('\n')

    for fname, fnname, pid in zip(remember_fname, remember_fnname, remember_id):
        print(f"Переименовано: {fname} -> {fnname}, новый PatientID: {pid}")

    for item in remember_move:
        print(f'Папка {item} была перемещена в архив')

    time.sleep(20)
    os.system('cls')


