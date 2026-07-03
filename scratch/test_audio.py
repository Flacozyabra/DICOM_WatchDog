import os
from winotify import Notification, audio as winotify_audio

print("Test without set_audio:")
try:
    toast = Notification(
        app_id='DICOM WatchDog',
        title='Тест без set_audio',
        msg='Это уведомление без вызова set_audio',
        duration='short',
        icon=os.path.abspath("src/folder_notification.png")
    )
    toast.show()
    print("Toast 1 shown successfully")
except Exception as e:
    print(f"Toast 1 failed: {e}")

print("Test with Silent set_audio:")
try:
    toast = Notification(
        app_id='DICOM WatchDog',
        title='Тест с Silent set_audio',
        msg='Это уведомление с вызовом set_audio(Silent)',
        duration='short',
        icon=os.path.abspath("src/folder_notification.png")
    )
    toast.set_audio(winotify_audio.Silent, loop=False)
    toast.show()
    print("Toast 2 shown successfully")
except Exception as e:
    print(f"Toast 2 failed: {e}")
