import winreg
import os
from winotify import Notification

def fix():
    try:
        key_path = r"Software\Classes\AppUserModelId\DICOM WatchDog"
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path)
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, "DICOM WatchDog")
        
        # Используем PNG вместо ICO
        icon_path = os.path.abspath("src/folder_notification.png")
        if os.path.exists(icon_path):
            winreg.SetValueEx(key, "IconUri", 0, winreg.REG_SZ, icon_path)
            print(f"Fixed IconUri to PNG: {icon_path}")
        winreg.CloseKey(key)
        print("Registry updated")
    except Exception as e:
        print(f"Error: {e}")

fix()

try:
    toast = Notification(
        app_id='DICOM WatchDog',
        title='Проверка после фикса',
        msg='Это уведомление должно прийти с PNG иконкой в реестре',
        duration='short',
        icon=os.path.abspath("src/folder_notification.png")
    )
    toast.show()
    print("Toast triggered successfully")
except Exception as e:
    print(f"Toast trigger error: {e}")
