import sys
import os

_active_sound_effects = []


def _play_wav(wav_path: str) -> None:
    if not wav_path or not os.path.exists(wav_path):
        return
    try:
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QUrl
        try:
            from PyQt6.QtMultimedia import QSoundEffect
        except ImportError:
            from PyQt5.QtMultimedia import QSoundEffect  # type: ignore

        app = QApplication.instance()
        if app is not None:
            effect = QSoundEffect()
            effect.setSource(QUrl.fromLocalFile(os.path.abspath(wav_path)))
            effect.setVolume(1.0)
            global _active_sound_effects
            _active_sound_effects.append(effect)
            effect.playingChanged.connect(lambda: _active_sound_effects.remove(effect) if not effect.isPlaying() and effect in _active_sound_effects else None)
            effect.play()
            return
    except Exception:
        pass

    if sys.platform == "win32":
        try:
            import winsound
            winsound.PlaySound(os.path.abspath(wav_path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        except Exception:
            pass


import re


def preprocess_tts_text(text: str) -> str:
    """
    Преобразует знаки ударения '+' около гласных в заглавные гласные буквы (например, гам+амед -> гамАмед)
    для идеального звучания ударения во всех движках Windows SAPI5 (Ирина, Павел, RHVoice) без выкрикивания слова "плюс".
    """
    if not text:
        return ""
    
    vowel_map = {
        'а': 'А', 'е': 'Е', 'ё': 'Ё', 'и': 'И', 'о': 'О',
        'у': 'У', 'ы': 'Ы', 'э': 'Э', 'ю': 'Ю', 'я': 'Я'
    }
    
    # 1. Заменяем +vowel и vowel+ на Заглавную гласную
    for v_lower, v_upper in vowel_map.items():
        text = text.replace('+' + v_lower, v_upper)
        text = text.replace(v_lower + '+', v_upper)
        text = text.replace('+' + v_upper, v_upper)
        text = text.replace(v_upper + '+', v_upper)
        
    # 2. Удаляем любые оставшиеся знаки плюса
    text = text.replace('+', '')
    return text


def show_notification(
    title: str,
    msg: str,
    durations: str,
    ico_path: str,
    sound_setting: str = 'default',
    show_toast: bool = True,
    play_sound: bool = True,
    duration_setting: str = None,
    position_setting: str = None,
    custom_voice_text: str = None
) -> None:
    """Show a desktop notification using native Qt ToastNotification."""
    try:
        from core.config_utils import get_log_path
        import datetime
        with open(get_log_path(), "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now()}] show_notification called: title={title}, msg={msg}, sound_setting={sound_setting}, show_toast={show_toast}, play_sound={play_sound}, duration_setting={duration_setting}, position_setting={position_setting}, ico_path={ico_path}, custom_voice_text={custom_voice_text}\n")
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
            _play_wav(wav_path)
        elif sound_setting and sound_setting != 'default' and sys.platform == "win32":
            # Озвучиваем кастомный текст или имя пациента через SAPI TTS
            raw_text = custom_voice_text.strip() if (custom_voice_text and custom_voice_text.strip()) else title
            raw_text = raw_text.replace('{name}', title).replace('{patient}', title)
            text_to_speak = preprocess_tts_text(raw_text)
            text_to_speak = text_to_speak.replace('"', '`"').replace("'", "''")
            ps_code = f"""
$speech = New-Object -ComObject SAPI.SpVoice
$voice = $speech.GetVoices() | Where-Object {{ $_.GetDescription() -eq "{sound_setting}" }} | Select-Object -First 1
if ($voice) {{
    $speech.Voice = $voice
}}
$speech.Speak('<silence msec="400"/>{text_to_speak}', 8)
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

            dur_val = duration_setting if duration_setting is not None else cfg.get('toast_duration', '5')
            dur_str = str(dur_val).lower()
            if dur_str == 'manual':
                duration_ms = 0
            else:
                try:
                    duration_ms = int(dur_str) * 1000
                except ValueError:
                    duration_ms = 5000

            position = position_setting if position_setting is not None else cfg.get('toast_position', 'bottom_right')
            show_qt_toast(title, msg, durations, ico_path, duration_ms=duration_ms, position=position)
        except Exception as e:
            try:
                from core.config_utils import get_log_path
                import datetime
                with open(get_log_path(), "a", encoding="utf-8") as f:
                    f.write(f"[{datetime.datetime.now()}] Custom Qt Toast error: {e}\n")
            except Exception:
                pass
