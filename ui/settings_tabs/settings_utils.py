# -*- coding: utf-8 -*-
"""Settings utility functions and custom widgets."""

import sys
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QWidget, QFrame, QLabel

from core.config_utils import get_resource_path


def find_matching_voice_index(combo, sound_name):
    if not sound_name or sound_name == 'default':
        return 0
    # 1. Точное совпадение по значению
    idx = combo.findData(sound_name)
    if idx >= 0:
        return idx
        
    # 2. Совпадение по очищенной/нормализованной строке (с поддержкой кириллицы и alexandr/aleksandr)
    clean_target = sound_name.replace("Microsoft", "").replace("Desktop", "").replace("OneCore", "").replace("RHVoice", "").strip().lower()
    clean_target_norm = (clean_target
                         .replace("alexandr", "aleksandr")
                         .replace("александр", "aleksandr")
                         .replace("анна", "anna")
                         .replace("елена", "elena")
                         .replace("ирина", "irina")
                         .replace("павел", "pavel"))
    for i in range(combo.count()):
        data = combo.itemData(i)
        if data and data not in ('default', 'sound_chime', 'sound_ping', 'sound_pop', 'sound_soft'):
            data_clean = data.replace("Microsoft", "").replace("Desktop", "").replace("OneCore", "").replace("RHVoice", "").strip().lower()
            data_clean_norm = (data_clean
                               .replace("alexandr", "aleksandr")
                               .replace("александр", "aleksandr")
                               .replace("анна", "anna")
                               .replace("елена", "elena")
                               .replace("ирина", "irina")
                               .replace("павел", "pavel"))
            if (clean_target == data_clean or clean_target in data_clean or data_clean in clean_target or
                clean_target_norm == data_clean_norm or clean_target_norm in data_clean_norm or data_clean_norm in clean_target_norm):
                return i
                
    # 3. Совпадение по первому ключу (имени диктора)
    words = [w for w in clean_target_norm.replace("-", " ").replace("(", " ").replace(")", " ").split() if len(w) > 1]
    if words:
        main_word = words[0]
        for i in range(combo.count()):
            data = combo.itemData(i)
            if data and data not in ('default', 'sound_chime', 'sound_ping', 'sound_pop', 'sound_soft'):
                data_clean = data.replace("Microsoft", "").replace("Desktop", "").replace("OneCore", "").replace("RHVoice", "").strip().lower()
                data_clean_norm = (data_clean
                                   .replace("alexandr", "aleksandr")
                                   .replace("александр", "aleksandr")
                                   .replace("анна", "anna")
                                   .replace("елена", "elena")
                                   .replace("ирина", "irina")
                                   .replace("павел", "pavel"))
                if main_word in data_clean_norm:
                    return i
    return -1


def are_onecore_voices_locked():
    import winreg
    if sys.platform != "win32":
        return False
    try:
        onecore_path = r"SOFTWARE\Microsoft\Speech_OneCore\Voices\Tokens"
        onecore_key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, onecore_path)
        onecore_count = winreg.QueryInfoKey(onecore_key)[0]
        onecore_names = set()
        for i in range(onecore_count):
            onecore_names.add(winreg.EnumKey(onecore_key, i))
        winreg.CloseKey(onecore_key)
        
        if not onecore_names:
            return False
            
        sapi5_path = r"SOFTWARE\Microsoft\Speech\Voices\Tokens"
        sapi5_key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, sapi5_path)
        sapi5_count = winreg.QueryInfoKey(sapi5_key)[0]
        sapi5_names = set()
        for i in range(sapi5_count):
            sapi5_names.add(winreg.EnumKey(sapi5_key, i))
        winreg.CloseKey(sapi5_key)
        
        missing = onecore_names - sapi5_names
        return len(missing) > 0
    except Exception:
        return False


def apply_dark_title_bar(widget):
    if sys.platform == "win32":
        import ctypes
        try:
            hwnd = int(widget.winId())
            # Immersive Dark Mode
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 20, ctypes.byref(ctypes.c_int(1)), ctypes.sizeof(ctypes.c_int)
            )
        except Exception:
            try:
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, 19, ctypes.byref(ctypes.c_int(1)), ctypes.sizeof(ctypes.c_int)
                )
            except Exception:
                pass
        try:
            hwnd = int(widget.winId())
            # Caption Color (#2b2b2b)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 35, ctypes.byref(ctypes.c_int(0x002b2b2b)), ctypes.sizeof(ctypes.c_int)
            )
            # Text Color (White)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 36, ctypes.byref(ctypes.c_int(0x00ffffff)), ctypes.sizeof(ctypes.c_int)
            )
        except Exception:
            pass


class LanguageSwitch(QFrame):
    """Кастомный горизонтальный переключатель языков с флагами."""

    def __init__(self, parent: QWidget, command=None, current_lang: str = "ru") -> None:
        super().__init__(parent)
        self.command = command
        self.lang = current_lang
        
        self.setFixedSize(76, 30)
        self.setStyleSheet("""
            QFrame {
                background-color: #2D2D2D;
                border: 1px solid #4B5563;
                border-radius: 15px;
            }
        """)

        # Загружаем картинки флагов
        self.px_ru = QPixmap(get_resource_path("themes/ru_flag.png"))
        self.px_gb = QPixmap(get_resource_path("themes/gb_flag.png"))

        # Метка RU флага (слева)
        self.lbl_ru = QLabel(self)
        self.lbl_ru.setPixmap(self.px_ru)
        self.lbl_ru.setScaledContents(True)
        self.lbl_ru.setFixedSize(24, 16)
        self.lbl_ru.move(9, 7)
        self.lbl_ru.setStyleSheet("background: transparent; border: none;")

        # Метка GB флага (справа)
        self.lbl_gb = QLabel(self)
        self.lbl_gb.setPixmap(self.px_gb)
        self.lbl_gb.setScaledContents(True)
        self.lbl_gb.setFixedSize(24, 16)
        self.lbl_gb.move(43, 7)
        self.lbl_gb.setStyleSheet("background: transparent; border: none;")

        # Ползунок (slider)
        self.slider = QFrame(self)
        self.slider.setFixedSize(36, 24)
        self.slider.setStyleSheet("""
            QFrame {
                background-color: #4B5563;
                border: none;
                border-radius: 12px;
            }
        """)

        self.slider_img = QLabel(self.slider)
        self.slider_img.setScaledContents(True)
        self.slider_img.setFixedSize(24, 16)
        self.slider_img.move(6, 4)
        self.slider_img.setStyleSheet("background: transparent; border: none;")

        self.update_slider_position()

    def update_slider_position(self) -> None:
        if self.lang == "ru":
            self.slider.move(3, 3)
            self.slider_img.setPixmap(self.px_ru)
        else:
            self.slider.move(37, 3)
            self.slider_img.setPixmap(self.px_gb)

    def mousePressEvent(self, event) -> None:
        if self.lang == "ru":
            self.lang = "en"
        else:
            self.lang = "ru"
        self.update_slider_position()
        if self.command:
            self.command(self.lang)
