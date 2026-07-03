import sys
import os

# Determine if winotify (Windows 10+ Toast notifications) is available
_HAS_WINOTIFY = False
if sys.platform == "win32":
    try:
        _win_major = sys.getwindowsversion().major
        _win_minor = sys.getwindowsversion().minor
        # Windows 10 is NT 10.0; Windows 7 is NT 6.1
        if _win_major >= 10:
            import winotify
            # Patch TEMPLATE to use modern ToastGeneric template and place the icon as a large side logo (appLogoOverride)
            winotify.TEMPLATE = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
[Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$Template = @"
<toast {launch} duration="{duration}">
    <visual>
        <binding template="ToastGeneric">
            <text><![CDATA[{title}]]></text>
            <text><![CDATA[{msg}]]></text>
            <image placement="appLogoOverride" src="{icon}" />
        </binding>
    </visual>
</toast>
"@

$SerializedXml = New-Object Windows.Data.Xml.Dom.XmlDocument
$SerializedXml.LoadXml($Template)

$Toast = [Windows.UI.Notifications.ToastNotification]::new($SerializedXml)
$Toast.Tag = "{tag}"
$Toast.Group = "{group}"

$Notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("{app_id}")
$Notifier.Show($Toast);
"""
            from winotify import Notification, audio as winotify_audio
            _HAS_WINOTIFY = True
    except Exception:
        _HAS_WINOTIFY = False

# Fallback tray icon instance (lazy-created, reused across calls)
_tray_icon = None


def _get_tray_icon(ico_path: str):
    """Return a cached QSystemTrayIcon instance for legacy balloon notifications."""
    global _tray_icon
    if _tray_icon is not None:
        return _tray_icon
    try:
        from PyQt6.QtWidgets import QSystemTrayIcon, QApplication
        from PyQt6.QtGui import QIcon
    except ImportError:
        from PyQt5.QtWidgets import QSystemTrayIcon, QApplication  # type: ignore[no-redef]
        from PyQt5.QtGui import QIcon  # type: ignore[no-redef]

    app = QApplication.instance()
    if app is None:
        return None

    icon = QIcon(ico_path) if ico_path and os.path.exists(ico_path) else app.style().standardIcon(
        app.style().StandardPixmap.SP_ComputerIcon
        if hasattr(app.style().StandardPixmap, 'SP_ComputerIcon')
        else app.style().SP_ComputerIcon  # PyQt5 fallback
    )
    _tray_icon = QSystemTrayIcon(icon, app)
    _tray_icon.show()
    return _tray_icon


def show_notification(title: str, msg: str, durations: str, ico_path: str, sound_setting: str = 'default', show_toast: bool = True, play_sound: bool = True) -> None:
    """Show a desktop notification.

    Uses winotify Toast on Windows 10+, falls back to QSystemTrayIcon
    balloon message on older Windows versions (7 / 8 / 8.1).
    """
    try:
        from core.config_utils import get_log_path
        import datetime
        with open(get_log_path(), "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now()}] show_notification called: title={title}, msg={msg}, show_toast={show_toast}, play_sound={play_sound}, ico_path={ico_path}\n")
    except Exception:
        pass
    # 1. Сначала воспроизводим звук/голос
    if play_sound:
        if sound_setting == 'default':
            from core.config_utils import get_resource_path
            wav_path = get_resource_path("src/notification.wav")
            if os.path.exists(wav_path) and sys.platform == "win32":
                try:
                    import winsound
                    winsound.PlaySound(wav_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
                except Exception as e:
                    try:
                        from core.config_utils import get_log_path
                        import datetime
                        with open(get_log_path(), "a", encoding="utf-8") as f:
                            f.write(f"[{datetime.datetime.now()}] Winsound error playing {wav_path}: {e}\n")
                    except Exception:
                        pass
        elif sound_setting and sound_setting != 'default' and sys.platform == "win32":
            # Озвучиваем имя
            text_to_speak = title  # В заголовке у нас лежит Patient Name
            ps_code = f"""
$speech = New-Object -ComObject SAPI.SpVoice
$voice = $speech.GetVoices() | Where-Object {{ $_.GetDescription() -eq "{sound_setting}" }} | Select-Object -First 1
if ($voice) {{
    $speech.Voice = $voice
}}
$speech.Speak("{text_to_speak}")
Remove-Item $MyInvocation.MyCommand.Path -Force
"""
            import tempfile
            import subprocess
            try:
                fd, path = tempfile.mkstemp(suffix=".ps1", text=True)
                with os.fdopen(fd, "w", encoding="utf-8-sig") as f:
                    f.write(ps_code)
                subprocess.Popen(
                    ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path],
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
            except Exception as e:
                try:
                    from core.config_utils import get_log_path
                    import datetime
                    with open(get_log_path(), "a", encoding="utf-8") as f:
                        f.write(f"[{datetime.datetime.now()}] TTS subprocess error: {e}\n")
                except Exception:
                    pass

    # 2. Показываем всплывающее уведомление
    if show_toast:
        if _HAS_WINOTIFY:
            try:
                toast_icon = ico_path
                if toast_icon and os.path.exists(toast_icon):
                    toast_icon = "file:///" + os.path.abspath(toast_icon).replace("\\", "/")

                toast = Notification(
                    app_id='DICOM WatchDog',
                    title=title,
                    msg=msg,
                    duration=durations,
                    icon=toast_icon
                )
                # Всегда используем пустой тег аудио, так как '<audio silent="true" />'
                # ломает показ тостов на некоторых системах Windows 10/11.
                toast.audio = ''
                toast.show()
                return
            except Exception as e:
                try:
                    from core.config_utils import get_log_path
                    import datetime
                    with open(get_log_path(), "a", encoding="utf-8") as f:
                        f.write(f"[{datetime.datetime.now()}] Winotify error: {e}\n")
                except Exception:
                    pass

        # Legacy fallback: balloon notification via Qt system tray
        try:
            tray = _get_tray_icon(ico_path)
            if tray is None:
                return
            try:
                from PyQt6.QtWidgets import QSystemTrayIcon
                msg_icon = QSystemTrayIcon.MessageIcon.Information
            except ImportError:
                from PyQt5.QtWidgets import QSystemTrayIcon  # type: ignore[no-redef]
                msg_icon = QSystemTrayIcon.Information  # type: ignore[attr-defined]
            duration_ms = 3000 if durations == 'short' else 7000
            tray.showMessage(title, msg, msg_icon, duration_ms)
        except Exception:
            pass  # Silent fail — notifications are non-critical
