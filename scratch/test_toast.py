import sys
import os
from winotify import Notification

print("Has winotify")
try:
    toast = Notification(
        app_id='DICOM WatchDog',
        title='Тестовое уведомление',
        msg='Это проверка работы winotify тостов в Windows',
        duration='short'
    )
    toast.show()
    print("Toast shown successfully")
except Exception as e:
    print(f"Failed to show toast: {e}")
