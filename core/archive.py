import os
import shutil
from datetime import datetime

from core.logger import log_message


def move_old_folders_to_archive(ct_images_dir, archive_dir, output_field):
    for root, dirs, files in os.walk(ct_images_dir):
        for dir in dirs:
            folder_path = os.path.join(root, dir)
            folder_date = datetime.fromtimestamp(os.path.getctime(folder_path))

            if (datetime.now() - folder_date).days >= 3:
                if not os.path.exists(archive_dir):
                    os.makedirs(archive_dir)

                archive_path = os.path.join(archive_dir, dir)

                if os.path.exists(archive_path):
                    # Удаляем существующую папку с таким же именем
                    shutil.rmtree(archive_path)

                shutil.move(folder_path, archive_path)
                log_message(output_field, f"Папка {dir} перемещена в архив")
