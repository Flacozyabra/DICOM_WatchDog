import sys
import os


def show_notification(
    title: str,
    msg: str,
    durations: str,
    ico_path: str,
    sound_setting: str = 'default',
    show_toast: bool = True,
    play_sound: bool = True
) -> None:
    """Show a desktop notification using native Qt ToastNotification."""
    try:
        from core.config_utils import get_log_path
        import datetime
        with open(get_log_path(), "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now()}] show_notification called: title={title}, msg={msg}, show_toast={show_toast}, play_sound={play_sound}, ico_path={ico_path}\n")
    except Exception:
        pass

    # 1. Воспроизводим звук/голос
    if play_sound:
        sound_map = {
            'default': "src/notification.wav",
            'sound_chime': "src/notification_chime.wav",
            'sound_ping': "src/notification_ping.wav",
            'sound_pop': "src/notification_pop.wav",
            'sound_soft': "src/notification_soft.wav",
        }
        if sound_setting in sound_map:
            from core.config_utils import get_resource_path
            wav_path = get_resource_path(sound_map[sound_setting])
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
            # Озвучиваем имя через SAPI TTS
            text_to_speak = title
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

    # 2. Показываем всплывающее тост-уведомление PyQt
    if show_toast:
        try:
            from ui.toast_notification import show_qt_toast
            from core.config_utils import load_config
            cfg = load_config()

            dur_str = str(cfg.get('toast_duration', '5')).lower()
            if dur_str == 'manual':
                duration_ms = 0
            else:
                try:
                    duration_ms = int(dur_str) * 1000
                except ValueError:
                    duration_ms = 5000

            position = cfg.get('toast_position', 'bottom_right')
            show_qt_toast(title, msg, durations, ico_path, duration_ms=duration_ms, position=position)
        except Exception as e:
            try:
                from core.config_utils import get_log_path
                import datetime
                with open(get_log_path(), "a", encoding="utf-8") as f:
                    f.write(f"[{datetime.datetime.now()}] Custom Qt Toast error: {e}\n")
            except Exception:
                pass
