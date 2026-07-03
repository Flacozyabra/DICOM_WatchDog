import sys
import os
import winreg
from winotify import Notification

def register_app_id():
    try:
        key_path = r"Software\Classes\AppUserModelId\DICOM WatchDog"
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path)
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, "DICOM WatchDog")
        
        icon_path = os.path.abspath("src/app_icon.ico")
        if os.path.exists(icon_path):
            winreg.SetValueEx(key, "IconUri", 0, winreg.REG_SZ, icon_path)
            print(f"Registered IconUri: {icon_path}")
        else:
            print("Icon not found for registry registration")
            
        winreg.CloseKey(key)
        print("AppUserModelID registered in registry successfully")
    except Exception as e:
        print(f"Failed to register: {e}")

register_app_id()

try:
    toast = Notification(
        app_id='DICOM WatchDog',
        title='Тестовое уведомление с регистрацией',
        msg='Это проверка работы winotify после регистрации AUMID в реестре',
        duration='short',
        icon=os.path.abspath("src/folder_notification.png")
    )
    toast.show()
    print("Toast shown")
except Exception as e:
    print(f"Failed to show: {e}")
