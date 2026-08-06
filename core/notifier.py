import sys
import os

_active_sound_effects = []


def _play_wav(wav_path: str, volume: float = 1.0) -> None:
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
            effect = QSoundEffect(parent=app)
            effect.setSource(QUrl.fromLocalFile(os.path.abspath(wav_path)))
            vol_clamp = max(0.0, min(1.0, float(volume)))
            effect.setVolume(vol_clamp)
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
    Преобразует знаки ударения '+' около гласных в удвоенные гласные буквы (например, гам+амед -> гамаамед)
    для устойчивой постановки ударения во всех движках Windows SAPI5 (Ирина, Павел, RHVoice и др.)
    без выкрикивания слова "плюс" и без пропуска слов.
    """
    if not text:
        return ""
    
    vowels = "аеёиоуыэюяАЕЁИОУЫЭЮЯ"
    
    def make_doubled_vowel(match):
        char = match.group(1).lower()
        return char + char

    # Плюс ПЕРЕД гласной: +а -> аа
    text = re.sub(r'\+([' + vowels + r'])', make_doubled_vowel, text)
    # Плюс ПОСЛЕ гласной: а+ -> аа
    text = re.sub(r'([' + vowels + r'])\+', make_doubled_vowel, text)
    
    # Удаляем любые оставшиеся знаки плюса
    text = text.replace('+', '')
    return text


def get_perceptual_volume(volume_percent: int) -> tuple:
    """Прямая линейная зависимость громкости (0..100) от значения ползунка."""
    val = max(0, min(100, int(volume_percent)))
    if val <= 0:
        return 0.0, 0
    vol_float = float(val) / 100.0
    vol_int = val
    return vol_float, vol_int


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
    custom_voice_text: str = None,
    volume: int = 100
) -> None:
    """Show a desktop notification using native Qt ToastNotification."""
    try:
        from core.config_utils import get_log_path
        import datetime
        with open(get_log_path(), "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now()}] show_notification called: title={title}, msg={msg}, sound_setting={sound_setting}, show_toast={show_toast}, play_sound={play_sound}, duration_setting={duration_setting}, position_setting={position_setting}, ico_path={ico_path}, custom_voice_text={custom_voice_text}, volume={volume}\n")
    except Exception:
        pass

    # 1. Воспроизводим звук/голос
    if play_sound:
        vol_float, vol_int = get_perceptual_volume(volume)
        if vol_int <= 0:
            pass
        else:
            sound_map = {
                'default': "src/notification.wav",
                'sound_chime': "src/notification_chime.wav",
                'sound_ping': "src/notification_ping.wav",
                'sound_pop': "src/notification_pop.wav",
                'sound_soft': "src/notification_soft.wav",
            }

def speak_sapi_tts(sound_setting: str, text_to_speak: str, vol_int: int) -> None:
    """Озвучивание текста через SAPI TTS (VBScript с автосбросом аудиовыхода + фолбэк на PowerShell)."""
    if sys.platform != "win32" or not sound_setting or sound_setting == 'default':
        return

    import tempfile
    import subprocess

    vbs_text = text_to_speak.replace('"', '""')
    vbs_code = f"""Set speech = CreateObject("SAPI.SpVoice")
On Error Resume Next
Set speech.AudioOutput = Nothing
On Error GoTo 0
targetName = "{sound_setting}"
Set foundVoice = Nothing
For Each v In speech.GetVoices()
    If LCase(v.GetDescription()) = LCase(targetName) Then
        Set foundVoice = v
        Exit For
    End If
Next
If foundVoice Is Nothing Then
    targetNorm = Replace(LCase(targetName), "alexandr", "aleksandr")
    For Each v In speech.GetVoices()
        cleanDesc = Replace(Replace(Replace(Replace(LCase(v.GetDescription()), "microsoft", ""), "desktop", ""), "onecore", ""), "rhvoice", "")
        cleanDesc = Trim(cleanDesc)
        cleanDesc = Replace(cleanDesc, "alexandr", "aleksandr")
        If InStr(cleanDesc, targetNorm) > 0 Or InStr(targetNorm, cleanDesc) > 0 Then
            Set foundVoice = v
            Exit For
        End If
    Next
End If
If Not foundVoice Is Nothing Then
    Set speech.Voice = foundVoice
End If
speech.Volume = {vol_int}
speech.Speak "<silence msec=""400""/><volume level=""{vol_int}"">{vbs_text}</volume>", 8
"""
    vbs_success = False
    try:
        fd, path = tempfile.mkstemp(suffix=".vbs", text=True)
        with os.fdopen(fd, "w", encoding="cp1251", errors="replace") as f:
            f.write(vbs_code)
        subprocess.Popen(
            ["cscript", "//NoLogo", path],
            creationflags=subprocess.CREATE_NO_WINDOW
        )
        vbs_success = True
    except Exception as e:
        print("VBScript TTS launch warning:", e)

    if not vbs_success:
        ps_text = text_to_speak.replace('"', '`"').replace("'", "''")
        ps_code = f"""
$speech = New-Object -ComObject SAPI.SpVoice
$speech.AudioOutput = $null
$targetName = "{sound_setting}"
$voice = $speech.GetVoices() | Where-Object {{ $_.GetDescription() -eq $targetName }} | Select-Object -First 1
if (-not $voice) {{
    $cleanTarget = $targetName.Replace("Microsoft", "").Replace("Desktop", "").Replace("OneCore", "").Replace("RHVoice", "").Trim().ToLower()
    $cleanTargetNorm = $cleanTarget.Replace("alexandr", "aleksandr")
    $voice = $speech.GetVoices() | Where-Object {{
        $desc = $_.GetDescription().ToLower()
        $descNorm = $desc.Replace("alexandr", "aleksandr")
        $desc -like "*$cleanTarget*" -or $descNorm -like "*$cleanTargetNorm*" -or $cleanTargetNorm -like "*$descNorm*"
    }} | Select-Object -First 1
}}
if ($voice) {{
    $speech.Voice = $voice
}}
$speech.Volume = {vol_int}
$speech.Speak('<silence msec="400"/><volume level="{vol_int}">{ps_text}</volume>', 8)
Remove-Item $MyInvocation.MyCommand.Path -Force
"""
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


def show_notification(title: str, message: str, sound_setting: str = 'default', volume: int = 100,
                      custom_voice_text: str = "", play_sound: bool = True, show_toast: bool = True,
                      duration_setting: str = None, toast_position: str = None) -> None:
    # 1. Воспроизводим звук/голос
    if play_sound:
        vol_float, vol_int = get_perceptual_volume(volume)
        if vol_int <= 0:
            pass
        else:
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
                _play_wav(wav_path, volume=vol_float)
            elif sound_setting and sound_setting != 'default' and sys.platform == "win32":
                # Озвучиваем кастомный текст или имя пациента через SAPI TTS
                raw_text = custom_voice_text.strip() if (custom_voice_text and custom_voice_text.strip()) else title
                raw_text = raw_text.replace('{name}', title).replace('{patient}', title)
                text_to_speak = preprocess_tts_text(raw_text)
                speak_sapi_tts(sound_setting, text_to_speak, vol_int)

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
