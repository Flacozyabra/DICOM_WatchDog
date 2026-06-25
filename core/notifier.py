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


def show_notification(title: str, msg: str, durations: str, ico_path: str) -> None:
    """Show a desktop notification.

    Uses winotify Toast on Windows 10+, falls back to QSystemTrayIcon
    balloon message on older Windows versions (7 / 8 / 8.1).
    """
    if _HAS_WINOTIFY:
        try:
            toast = Notification(
                app_id='DICOM WatchDog',
                title=title,
                msg=msg,
                duration=durations,
                icon=rf'{ico_path}'
            )
            toast.set_audio(winotify_audio.Default, loop=False)
            toast.show()
            return
        except Exception:
            pass  # Fall through to QSystemTrayIcon fallback

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
