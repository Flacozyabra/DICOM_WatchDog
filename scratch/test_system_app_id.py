import os
from winotify import Notification

print("Test with Windows PowerShell app_id:")
try:
    toast = Notification(
        app_id='Windows PowerShell',
        title='Тест с PowerShell ID',
        msg='Это уведомление использует системный app_id и должно гарантированно всплыть',
        duration='short',
        icon=os.path.abspath("src/folder_notification.png")
    )
    toast.show()
    print("System app_id toast shown successfully")
except Exception as e:
    print(f"System app_id toast failed: {e}")
