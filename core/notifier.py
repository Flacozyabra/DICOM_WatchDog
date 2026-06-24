from winotify import Notification, audio


def show_notification(title, msg, durations, ico_path):
    toast = Notification(
        app_id='DICOM WatchDog',  # Изменили на новое название приложения
        title=title, 
        msg=msg, 
        duration=durations,
        icon=rf'{ico_path}'
    )
    toast.set_audio(audio.Default, loop=False)
    toast.show()
