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


def get_rhvoice_from_disk():
    """Поиск установленных голосов RHVoice напрямую на диске."""
    voices = []
    paths = [
        r"C:\ProgramData\Olga Yakovleva\RHVoice\data\voices",
        r"C:\ProgramData\rhvoice.org\RHVoice\data\voices",
        r"C:\Program Files\RHVoice\data\voices",
        r"C:\Program Files (x86)\RHVoice\data\voices",
        r"C:\Program Files\rhvoice.org\RHVoice\data\voices",
        r"C:\Program Files (x86)\rhvoice.org\RHVoice\data\voices",
        os.path.expandvars(r"%APPDATA%\RHVoice\voices")
    ]
    for p in paths:
        if os.path.exists(p):
            try:
                for item in os.listdir(p):
                    full = os.path.join(p, item)
                    if os.path.isdir(full) and item.lower() not in ("languages", "dicts", "configs"):
                        voices.append(item.capitalize())
            except Exception:
                pass
    return list(dict.fromkeys(voices))


def get_voices_from_registry():
    """Чтение установленных голосов SAPI5 напрямую из реестра Windows (HKLM и HKCU)."""
    if sys.platform != "win32":
        return []
    import winreg
    voices = []
    reg_paths = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Speech\Voices\Tokens"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Speech\Voices\Tokens"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Speech\Voices\Tokens"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Speech\Voices\TokenEnums"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Speech\Voices\TokenEnums"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Speech\Voices\TokenEnums")
    ]
    flags = [winreg.KEY_READ]
    if hasattr(winreg, 'KEY_WOW64_32KEY'):
        flags.append(winreg.KEY_READ | winreg.KEY_WOW64_32KEY)
    if hasattr(winreg, 'KEY_WOW64_64KEY'):
        flags.append(winreg.KEY_READ | winreg.KEY_WOW64_64KEY)

    for root, rel_path in reg_paths:
        for flag in flags:
            try:
                key = winreg.OpenKey(root, rel_path, 0, flag)
                count = winreg.QueryInfoKey(key)[0]
                for i in range(count):
                    token_name = winreg.EnumKey(key, i)
                    token_path = f"{rel_path}\\{token_name}"
                    
                    sub_tokens = [token_path]
                    try:
                        sub_k = winreg.OpenKey(root, token_path, 0, flag)
                        sub_count = winreg.QueryInfoKey(sub_k)[0]
                        for j in range(sub_count):
                            child_name = winreg.EnumKey(sub_k, j)
                            sub_tokens.append(f"{token_path}\\{child_name}")
                        winreg.CloseKey(sub_k)
                    except Exception:
                        pass

                    for t_path in sub_tokens:
                        name = None
                        try:
                            attr_key = winreg.OpenKey(root, f"{t_path}\\Attributes", 0, flag)
                            name, _ = winreg.QueryValueEx(attr_key, "Name")
                            winreg.CloseKey(attr_key)
                        except Exception:
                            pass
                        if not name:
                            try:
                                t_key = winreg.OpenKey(root, t_path, 0, flag)
                                name, _ = winreg.QueryValueEx(t_key, "")
                                winreg.CloseKey(t_key)
                            except Exception:
                                pass
                        if not name:
                            try:
                                t_key = winreg.OpenKey(root, t_path, 0, flag)
                                name, _ = winreg.QueryValueEx(t_key, "419")
                                winreg.CloseKey(t_key)
                            except Exception:
                                pass
                        if name and isinstance(name, str):
                            clean = name.strip()
                            if clean and clean not in voices:
                                voices.append(clean)
                winreg.CloseKey(key)
            except Exception:
                pass
    return list(dict.fromkeys(voices))


def format_voice_name(voice_raw):
    name = voice_raw.replace("Desktop", "")
    if "-" in name:
        left, right = name.split("-", 1)
        left = " ".join(left.split())
        right_clean = right.replace("(", " ").replace(")", " ")
        right_words = right_clean.strip().split()
        lang = right_words[0] if right_words else ""
        return f"{left} - {lang}"
    else:
        return " ".join(name.split())


def get_installed_sapi_voices():
    """Возвращает уникальный список установленных голосов Windows SAPI5 и RHVoice."""
    if sys.platform != "win32":
        return []
    import tempfile
    import subprocess

    ps_voices = []
    try:
        ps_code = """
$speech = New-Object -ComObject SAPI.SpVoice
foreach ($v in $speech.GetVoices()) {
    $d = $v.GetDescription()
    if ($d) { [Console]::WriteLine($d) }
}
"""
        fd, path = tempfile.mkstemp(suffix=".ps1", text=True)
        with os.fdopen(fd, "w", encoding="utf-8-sig") as f:
            f.write(ps_code)
        
        creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        raw_bytes = subprocess.check_output(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path],
            creationflags=creation_flags,
            timeout=8
        )
        text = None
        for enc in ["utf-8", "cp1251", "cp866"]:
            try:
                decoded = raw_bytes.decode(enc)
                if decoded and decoded.strip():
                    text = decoded
                    break
            except Exception:
                pass
        if not text:
            text = raw_bytes.decode("utf-8", errors="replace")
            
        for line in text.splitlines():
            line_str = line.strip()
            if line_str:
                ps_voices.append(line_str)
    except Exception as e:
        pass

    raw_list = ps_voices if ps_voices else get_voices_from_registry()
    
    for rh_v in get_rhvoice_from_disk():
        if not any(rh_v.lower() == x.strip().lower() or rh_v.lower() in x.strip().lower() for x in raw_list):
            raw_list.append(rh_v)

    seen_formatted = set()
    unique_voices = []
    for voice in raw_list:
        clean_v = voice.strip()
        if not clean_v:
            continue
        fmt = format_voice_name(clean_v).strip().lower()
        if fmt and fmt not in seen_formatted:
            seen_formatted.add(fmt)
            unique_voices.append(clean_v)
            
    return unique_voices


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
    try:
        val = max(0, min(100, int(volume_percent)))
    except (ValueError, TypeError):
        val = 100
    if val <= 0:
        return 0.0, 0
    vol_float = float(val) / 100.0
    vol_int = val
    return vol_float, vol_int


def speak_sapi_tts(sound_setting: str, text_to_speak: str, vol_int: int) -> None:
    """Озвучивание текста через SAPI TTS в PowerShell."""
    if sys.platform != "win32" or not sound_setting or sound_setting == 'default':
        return

    import tempfile
    import subprocess

    ps_text = text_to_speak.replace('"', '`"').replace("'", "''")
    sound_setting_escaped = sound_setting.replace('"', '`"').replace("'", "''")
    ps_code = f"""
$speech = New-Object -ComObject SAPI.SpVoice
$targetName = "{sound_setting_escaped}"
$targetLower = $targetName.ToLower()
$targetNorm = $targetLower.Replace("alexandr", "aleksandr").Replace("александр", "aleksandr").Replace("анна", "anna").Replace("елена", "elena").Replace("ирина", "irina").Replace("павел", "pavel")
$targetClean = $targetNorm.Replace("microsoft", "").Replace("desktop", "").Replace("onecore", "").Replace("rhvoice", "").Trim()

$voices = @($speech.GetVoices())
# 1. Прямое совпадение по описанию
$voice = $voices | Where-Object {{
    $d = $_.GetDescription().ToLower()
    $d -eq $targetLower -or $d -eq $targetNorm
}} | Select-Object -First 1

# 2. Совпадение по очищенному имени
if (-not $voice) {{
    $voice = $voices | Where-Object {{
        $clean = $_.GetDescription().ToLower().Replace("microsoft", "").Replace("desktop", "").Replace("onecore", "").Replace("rhvoice", "").Trim().Replace("alexandr", "aleksandr").Replace("александр", "aleksandr").Replace("анна", "anna").Replace("елена", "elena").Replace("ирина", "irina").Replace("павел", "pavel")
        $clean -eq $targetClean -or $clean -eq $targetNorm
    }} | Select-Object -First 1
}}

# 3. Частичное совпадение (с защитой от англоязычной Microsoft Anna при выборе русской Анны)
if (-not $voice) {{
    $voice = $voices | Where-Object {{
        $desc = $_.GetDescription().ToLower()
        if ($desc -like "*microsoft anna*" -and $targetLower -notlike "*microsoft*") {{
            $false
        }} else {{
            $clean = $desc.Replace("microsoft", "").Replace("desktop", "").Replace("onecore", "").Replace("rhvoice", "").Trim().Replace("alexandr", "aleksandr").Replace("александр", "aleksandr").Replace("анна", "anna").Replace("елена", "elena").Replace("ирина", "irina").Replace("павел", "pavel")
            $clean -like "*$targetClean*" -or $targetClean -like "*$clean*"
        }}
    }} | Select-Object -First 1
}}

if ($voice) {{
    $speech.Voice = $voice
}}

$speech.Volume = {vol_int}
try {{
    $speech.Speak('<silence msec="400"/><volume level="{vol_int}">{ps_text}</volume>', 8)
}} catch {{
    try {{
        $speech.Speak('{ps_text}', 0)
    }} catch {{}}
}}
Remove-Item $MyInvocation.MyCommand.Path -Force -ErrorAction SilentlyContinue
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
                      duration_setting: str = None, position_setting: str = None, icon_path: str = None) -> None:
    # 1. Воспроизводим звук/голос
    if play_sound:
        vol_float, vol_int = get_perceptual_volume(volume)
        if vol_int > 0:
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
            show_qt_toast(title, message, 'short', icon_path, duration_ms=duration_ms, position=position)
        except Exception as e:
            try:
                from core.config_utils import get_log_path
                import datetime
                with open(get_log_path(), "a", encoding="utf-8") as f:
                    f.write(f"[{datetime.datetime.now()}] Custom Qt Toast error: {e}\n")
            except Exception:
                pass
