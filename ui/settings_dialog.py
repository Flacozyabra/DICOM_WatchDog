import os
import sys
import json
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont, QPixmap
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                             QLineEdit, QPushButton, QFileDialog, QFormLayout, 
                             QSpinBox, QDialogButtonBox, QMessageBox,
                             QComboBox, QListWidget, QStackedWidget, QWidget, QFrame)

from ui.toggle_switch import ToggleSwitch
from core.config_utils import get_config_path, get_app_data_dir, get_resource_path
from core.locale_utils import tr_ui, set_current_langs


def get_system_voices():
    import subprocess
    import sys
    if sys.platform != "win32":
        return []
    try:
        cmd = [
            "powershell", "-NoProfile", "-Command",
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; $speech = New-Object -ComObject SAPI.SpVoice; foreach ($v in $speech.GetVoices()) { $v.GetDescription() }"
        ]
        output = subprocess.check_output(cmd, text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW)
        voices = [line.strip() for line in output.splitlines() if line.strip()]
        seen_names = set()
        unique_voices = []
        for voice in voices:
            parts = voice.replace("Microsoft", "").replace("Desktop", "").split("-")[0].strip().split()
            if not parts:
                continue
            base_name = parts[0].lower()
            if base_name not in seen_names:
                seen_names.add(base_name)
                unique_voices.append(voice)
        return unique_voices
    except Exception as e:
        print("Error getting system voices:", e)
        return []


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


def find_matching_voice_index(combo, sound_name):
    if not sound_name or sound_name == 'default':
        return 0
    idx = combo.findData(sound_name)
    if idx >= 0:
        return idx
    parts = sound_name.replace("Microsoft", "").replace("Desktop", "").split("-")[0].strip().split()
    if parts:
        base_name = parts[0].lower()
        for i in range(combo.count()):
            data = combo.itemData(i)
            if data and data != 'default':
                d_parts = data.replace("Microsoft", "").replace("Desktop", "").split("-")[0].strip().split()
                if d_parts and d_parts[0].lower() == base_name:
                    return i
    return 0


def are_onecore_voices_locked():
    import winreg
    import sys
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


class PacsPingWorker(QThread):
    finished = pyqtSignal(bool, str)

    def __init__(self, pacs_ip, pacs_port, called_aet, calling_aet):
        super().__init__()
        self.pacs_ip = pacs_ip
        self.pacs_port = pacs_port
        self.called_aet = called_aet
        self.calling_aet = calling_aet

    def run(self):
        from core.pacs import ping_pacs
        success, msg = ping_pacs(self.pacs_ip, self.pacs_port, self.called_aet, self.calling_aet)
        self.finished.emit(success, msg)


from ui.updater import UpdateCheckWorker


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


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr_ui("settings_title"))
        self.setMinimumWidth(650)
        
        # Темная рамка окна Windows и цвет заголовка
        if sys.platform == "win32":
            import ctypes
            try:
                hwnd = int(self.winId())
                # Включение темного режима (Immersive Dark Mode)
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

            # Установка точного серого цвета #2b2b2b (BGR: 0x002b2b2b) для Windows 11
            try:
                hwnd = int(self.winId())
                # DWMWA_CAPTION_COLOR = 35
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, 35, ctypes.byref(ctypes.c_int(0x002b2b2b)), ctypes.sizeof(ctypes.c_int)
                )
                # DWMWA_TEXT_COLOR = 36 (белый текст заголовка)
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, 36, ctypes.byref(ctypes.c_int(0x00ffffff)), ctypes.sizeof(ctypes.c_int)
                )
            except Exception:
                pass

        self.config = self.load_config()
        self.initial_config = self.config.copy()
        self.init_ui()

    def load_config(self):
        config = {
            'ct_images_dir': '',
            'archive_dir': '',
            'fix_switch_value': 'True',
            'cleanup_structures_enabled': 'False',
            'fix_patient_id_enabled': 'False',
            'id_prefixes': 'CT_',
            'client_dir': '',
            'archive_slice': 0,
            'x': 1100,
            'y': 600,
            'dx': 350,
            'dy': 100,
            'log_font_size': 12,
            'notifications_enabled': 'False',
            'ct_notification_toast_enabled': 'False',
            'ct_notification_sound_enabled': 'False',
            'ct_notification_sound': 'default',
            'pacs_notification_toast_enabled': 'False',
            'pacs_notification_sound_enabled': 'False',
            'pacs_notification_sound': 'default',
            'icon_path': '',
            'pacs_scan_time': 10000,
            'auto_update_is': 'off',
            'check_updates_at_startup': 'on',
            'patient_font_size': 16,
            'patient_weight': 'Semibold',
            'archive_enabled': 'False',
            'archive_days': 3,
            'archive_cleanup_enabled': 'False',
            'archive_cleanup_days': 30,
            'pacs_ip': '127.0.0.1',
            'pacs_port': 11112,
            'pacs_called_aet': 'ANY-SCP',
            'pacs_calling_aet': 'ECHOSCU',
            'tables_state': {},
            'highlighting_enabled': 'False',
            'highlight_new_enabled': 'False',
            'highlight_today_enabled': 'False',
            'highlight_no_str_enabled': 'False',
            'highlight_no_slices_enabled': 'False',
            'rename_study_folder_enabled': 'False',
            'rename_study_folder_mode': 'id',
            'interface_lang': 'en',
            'log_lang': 'en'
        }
        
        # 1. Проверяем config.json в AppData
        config_path = get_config_path()
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    config.update(loaded)
                
                # Инициализация списка PACS серверов для обратной совместимости
                if 'pacs_servers' not in config:
                    config['pacs_servers'] = []
                if not config['pacs_servers']:
                    pacs_ip = config.get('pacs_ip', '127.0.0.1')
                    pacs_port = int(config.get('pacs_port', 11112))
                    pacs_called_aet = config.get('pacs_called_aet', 'ANY-SCP')
                    pacs_calling_aet = config.get('pacs_calling_aet', 'ECHOSCU')
                    default_server = {
                        'name': f"Server ({pacs_ip}:{pacs_port})",
                        'pacs_ip': pacs_ip,
                        'pacs_port': pacs_port,
                        'pacs_called_aet': pacs_called_aet,
                        'pacs_calling_aet': pacs_calling_aet
                    }
                    config['pacs_servers'].append(default_server)
                    config['pacs_current_server_name'] = default_server['name']
                elif 'pacs_current_server_name' not in config or not config['pacs_current_server_name']:
                    config['pacs_current_server_name'] = config['pacs_servers'][0]['name']

                return config
            except Exception as e:
                print(f"Error loading config.json: {e}")
                
        # 2. Если JSON нет, но есть config.txt - делаем миграцию
        if os.path.exists("config.txt"):
            try:
                with open("config.txt", "r", encoding="utf-8") as f:
                    lines = f.readlines()
                    if len(lines) > 0: config['ct_images_dir'] = lines[0].strip()
                    if len(lines) > 1: config['archive_dir'] = lines[1].strip()
                    if len(lines) > 4: config['fix_switch_value'] = lines[4].strip()
                    if len(lines) > 7: config['client_dir'] = lines[7].strip()
                    if len(lines) > 10: config['archive_slice'] = int(lines[10].strip() or "0")
                    if len(lines) > 16:
                          config['x'] = int(lines[13].strip() or "1000")
                          config['y'] = int(lines[14].strip() or "600")
                          config['dx'] = int(lines[15].strip() or "350")
                          config['dy'] = int(lines[16].strip() or "100")
                    if len(lines) > 19: config['log_font_size'] = int(lines[19].strip() or "12")
                    if len(lines) > 22: config['folder_scan_time'] = int(lines[22].strip() or "10000")
                    if len(lines) > 25: config['notification_is'] = lines[25].strip()
                    if len(lines) > 28: config['icon_path'] = lines[28].strip()
                    if len(lines) > 31: config['pacs_scan_time'] = int(lines[31].strip() or "10000")
                    if len(lines) > 34: config['auto_update_is'] = lines[34].strip()
                    if len(lines) > 37: config['patient_font_size'] = int(lines[37].strip() or "14")
                    if len(lines) > 40: config['patient_weight'] = lines[40].strip()
                    if len(lines) > 43: config['archive_enabled'] = lines[43].strip()
                    if len(lines) > 46: config['archive_days'] = int(lines[46].strip() or "3")
                    if len(lines) > 49: config['archive_cleanup_enabled'] = lines[49].strip()
                    if len(lines) > 52: config['archive_cleanup_days'] = int(lines[52].strip() or "30")
                    if len(lines) > 55: config['pacs_ip'] = lines[55].strip()
                    if len(lines) > 58: config['pacs_port'] = int(lines[58].strip() or "11112")
                    if len(lines) > 61: config['pacs_called_aet'] = lines[61].strip()
                    if len(lines) > 64: config['pacs_calling_aet'] = lines[64].strip()
                
                # Инициализация списка PACS серверов для обратной совместимости
                if 'pacs_servers' not in config:
                    config['pacs_servers'] = []
                if not config['pacs_servers']:
                    pacs_ip = config.get('pacs_ip', '127.0.0.1')
                    pacs_port = int(config.get('pacs_port', 11112))
                    pacs_called_aet = config.get('pacs_called_aet', 'ANY-SCP')
                    pacs_calling_aet = config.get('pacs_calling_aet', 'ECHOSCU')
                    default_server = {
                        'name': f"Server ({pacs_ip}:{pacs_port})",
                        'pacs_ip': pacs_ip,
                        'pacs_port': pacs_port,
                        'pacs_called_aet': pacs_called_aet,
                        'pacs_calling_aet': pacs_calling_aet
                    }
                    config['pacs_servers'].append(default_server)
                    config['pacs_current_server_name'] = default_server['name']
                elif 'pacs_current_server_name' not in config or not config['pacs_current_server_name']:
                    config['pacs_current_server_name'] = config['pacs_servers'][0]['name']

                # Сохраняем в config.json в AppData и бэкапим config.txt
                with open(get_config_path(), "w", encoding="utf-8") as f_json:
                    json.dump(config, f_json, ensure_ascii=False, indent=4)
                    
                if os.path.exists("config.txt.bak"):
                    os.remove("config.txt.bak")
                os.rename("config.txt", "config.txt.bak")
                
            except Exception as e:
                print(f"Error migrating config.txt: {e}")
                
        return config

    def save_config(self):
        try:
            with open(get_config_path(), "w", encoding="utf-8") as f:
                json.dump(self.config, f, ensure_ascii=False, indent=4)
        except Exception as e:
            QMessageBox.critical(self, tr_ui("dlg_error_title"), f"Failed to save configuration: {e}")

    def init_ui(self):
        # Получаем голоса
        self.system_voices = get_system_voices()
        
        # Главный горизонтальный макет окна
        main_layout = QHBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # Левая часть: боковое меню
        self.sidebar = QListWidget()
        self.sidebar.setObjectName("settingsSidebar")
        main_layout.addWidget(self.sidebar)
        
        # Правая часть: stacked widget с контентом
        self.stacked_widget = QStackedWidget()
        self.stacked_widget.setStyleSheet("QStackedWidget { background-color: #141414; padding: 15px; }")
        
        # 1. Вкладка General
        general_widget = QWidget()
        general_layout = QVBoxLayout(general_widget)
        general_form = QFormLayout()
        
        # CT Images Dir
        self.ct_images_edit = QLineEdit(self.config.get('ct_images_dir', ''))
        self.btn_ct_images_browse = QPushButton()
        self.btn_ct_images_browse.clicked.connect(lambda: self.browse_folder(self.ct_images_edit, tr_ui("settings_ct_images_folder").rstrip(":")))
        h_layout_ct = QHBoxLayout()
        h_layout_ct.addWidget(self.ct_images_edit)
        h_layout_ct.addWidget(self.btn_ct_images_browse)
        self.lbl_ct_folder = QLabel()
        general_form.addRow(self.lbl_ct_folder, h_layout_ct)

        # App Settings Dir
        self.app_data_edit = QLineEdit(get_app_data_dir())
        self.app_data_edit.setReadOnly(True)
        self.app_data_edit.setStyleSheet(
            "QLineEdit { background-color: #1e1e1e; color: #888888; border: 1px solid #2d2d2d; padding: 4px; border-radius: 4px; }"
        )
        self.btn_app_data_open = QPushButton()
        self.btn_app_data_open.clicked.connect(self.open_app_data_folder)
        h_layout_app = QHBoxLayout()
        h_layout_app.addWidget(self.app_data_edit)
        h_layout_app.addWidget(self.btn_app_data_open)
        self.lbl_settings_folder = QLabel()
        general_form.addRow(self.lbl_settings_folder, h_layout_app)

        # Разделитель
        line_ct = QFrame()
        line_ct.setFrameShape(QFrame.Shape.HLine)
        line_ct.setFrameShadow(QFrame.Shadow.Sunken)
        line_ct.setStyleSheet("background-color: #2d2d2d; margin-top: 10px; margin-bottom: 10px;")
        general_form.addRow(line_ct)

        # Автоудаление дубликатов структур
        self.cleanup_str_cb = ToggleSwitch()
        self.cleanup_str_cb.setChecked(self.config.get('cleanup_structures_enabled', 'False').lower() == 'true')
        self.lbl_cleanup_str = QLabel()
        general_form.addRow(self.lbl_cleanup_str, self.cleanup_str_cb)

        # Fix Patient ID
        self.fix_patient_id_cb = ToggleSwitch()
        self.fix_patient_id_cb.setChecked(self.config.get('fix_patient_id_enabled', 'False').lower() == 'true')
        self.lbl_fix_id = QLabel()
        general_form.addRow(self.lbl_fix_id, self.fix_patient_id_cb)

        # ID prefixes field
        self.id_prefixes_edit = QLineEdit(self.config.get('id_prefixes', 'CT_'))
        self.id_prefixes_edit.setStyleSheet(
            "QLineEdit { background-color: #1e1e1e; color: #ffffff; border: 1px solid #2d2d2d; padding: 4px; border-radius: 4px; }"
            "QLineEdit:disabled { background-color: #141414; color: #808080; border: 1px solid #1a1a1a; }"
        )
        self.lbl_id_prefixes = QLabel()
        general_form.addRow(self.lbl_id_prefixes, self.id_prefixes_edit)

        # Rename Study Folder
        self.rename_study_folder_cb = ToggleSwitch()
        self.rename_study_folder_cb.setChecked(self.config.get('rename_study_folder_enabled', 'False').lower() == 'true')
        self.lbl_rename_folder = QLabel()
        general_form.addRow(self.lbl_rename_folder, self.rename_study_folder_cb)

        # Rename Study Folder Mode
        self.rename_study_folder_mode_combo = QComboBox()
        self.lbl_rename_folder_mode = QLabel()
        general_form.addRow(self.lbl_rename_folder_mode, self.rename_study_folder_mode_combo)

        # Разделитель под префиксами
        line_updates = QFrame()
        line_updates.setFrameShape(QFrame.Shape.HLine)
        line_updates.setFrameShadow(QFrame.Shadow.Sunken)
        line_updates.setStyleSheet("background-color: #2d2d2d; margin-top: 15px; margin-bottom: 10px;")
        general_form.addRow(line_updates)

        # Контейнер для проверки обновлений и свитча
        updates_layout = QHBoxLayout()
        updates_layout.setContentsMargins(0, 5, 0, 5)
        updates_layout.setSpacing(10)
        
        self.check_updates_cb = ToggleSwitch()
        self.check_updates_cb.setChecked(self.config.get('check_updates_at_startup', 'on').lower() == 'on')
        
        self.btn_check_updates = QPushButton()
        self.btn_check_updates.setFixedHeight(30)
        self.btn_check_updates.setMinimumWidth(180)
        self.btn_check_updates.clicked.connect(self.manual_check_updates)
        
        updates_layout.addWidget(self.check_updates_cb)
        updates_layout.addStretch()
        updates_layout.addWidget(self.btn_check_updates)
        general_form.addRow(updates_layout)
        
        general_layout.addLayout(general_form)
        general_layout.addStretch()
        self.stacked_widget.addWidget(general_widget)
        
        # 2. Вкладка Archive
        archive_widget = QWidget()
        archive_layout = QVBoxLayout(archive_widget)
        archive_form = QFormLayout()
        
        # Archive Dir
        self.archive_edit = QLineEdit(self.config['archive_dir'])
        self.btn_archive_browse = QPushButton()
        self.btn_archive_browse.clicked.connect(lambda: self.browse_folder(self.archive_edit, tr_ui("settings_archive_dir").rstrip(":")))
        h_layout2 = QHBoxLayout()
        h_layout2.addWidget(self.archive_edit)
        h_layout2.addWidget(self.btn_archive_browse)
        self.lbl_archive_dir = QLabel()
        archive_form.addRow(self.lbl_archive_dir, h_layout2)
        
        # Archive Slice (Max visible rows)
        self.archive_slice_spin = QSpinBox()
        self.archive_slice_spin.setRange(0, 1000)
        self.archive_slice_spin.setValue(self.config['archive_slice'])
        self.lbl_archive_slice = QLabel()
        archive_form.addRow(self.lbl_archive_slice, self.archive_slice_spin)

        # Автоматическое архивирование (свич и количество дней в одной строке)
        self.archive_enabled_cb = ToggleSwitch()
        self.archive_enabled_cb.setChecked(self.config.get('archive_enabled', 'False').lower() == 'true')
        
        self.archive_days_spin = QSpinBox()
        self.archive_days_spin.setRange(1, 365)
        self.archive_days_spin.setValue(int(self.config.get('archive_days', 3)))
        self.archive_days_spin.setFixedWidth(60)
        self.archive_days_spin.setStyleSheet(
            "QSpinBox { background-color: #1e1e1e; color: #ffffff; border: 1px solid #2d2d2d; padding: 2px; border-radius: 4px; }"
            "QSpinBox:disabled { background-color: #141414; color: #666666; border: 1px solid #1c1c1c; }"
        )

        self.archive_label_through = QLabel(tr_ui("lbl_archive_through"))
        self.archive_label_through.setStyleSheet(
            "QLabel { color: #aaaaaa; }"
            "QLabel:disabled { color: #444444; }"
        )
        self.archive_label_days = QLabel(tr_ui("lbl_archive_days"))
        self.archive_label_days.setStyleSheet(
            "QLabel { color: #aaaaaa; }"
            "QLabel:disabled { color: #444444; }"
        )

        archive_row_layout = QHBoxLayout()
        archive_row_layout.addWidget(self.archive_enabled_cb)
        archive_row_layout.addStretch()
        archive_row_layout.addWidget(self.archive_label_through)
        archive_row_layout.addSpacing(8)
        archive_row_layout.addWidget(self.archive_days_spin)
        archive_row_layout.addWidget(self.archive_label_days)

        self.lbl_auto_archive_row = QLabel()
        archive_form.addRow(self.lbl_auto_archive_row, archive_row_layout)

        # Автоочистка архива (свич и количество дней в одной строке)
        self.archive_cleanup_enabled_cb = ToggleSwitch()
        self.archive_cleanup_enabled_cb.setChecked(self.config.get('archive_cleanup_enabled', 'False').lower() == 'true')
        
        self.archive_cleanup_days_spin = QSpinBox()
        self.archive_cleanup_days_spin.setRange(1, 365)
        self.archive_cleanup_days_spin.setValue(int(self.config.get('archive_cleanup_days', 30)))
        self.archive_cleanup_days_spin.setFixedWidth(60)
        self.archive_cleanup_days_spin.setStyleSheet(
            "QSpinBox { background-color: #1e1e1e; color: #ffffff; border: 1px solid #2d2d2d; padding: 2px; border-radius: 4px; }"
            "QSpinBox:disabled { background-color: #141414; color: #666666; border: 1px solid #1c1c1c; }"
        )

        self.cleanup_label_through = QLabel(tr_ui("lbl_archive_through"))
        self.cleanup_label_through.setStyleSheet(
            "QLabel { color: #aaaaaa; }"
            "QLabel:disabled { color: #444444; }"
        )
        self.cleanup_label_days = QLabel(tr_ui("lbl_archive_days"))
        self.cleanup_label_days.setStyleSheet(
            "QLabel { color: #aaaaaa; }"
            "QLabel:disabled { color: #444444; }"
        )

        cleanup_row_layout = QHBoxLayout()
        cleanup_row_layout.addWidget(self.archive_cleanup_enabled_cb)
        cleanup_row_layout.addStretch()
        cleanup_row_layout.addWidget(self.cleanup_label_through)
        cleanup_row_layout.addSpacing(8)
        cleanup_row_layout.addWidget(self.archive_cleanup_days_spin)
        cleanup_row_layout.addWidget(self.cleanup_label_days)

        self.lbl_auto_cleanup_row = QLabel()
        archive_form.addRow(self.lbl_auto_cleanup_row, cleanup_row_layout)
        
        archive_layout.addLayout(archive_form)
        archive_layout.addStretch()
        self.stacked_widget.addWidget(archive_widget)
        
        # 3. Вкладка UI Settings
        ui_widget = QWidget()
        ui_layout = QVBoxLayout(ui_widget)
        ui_form = QFormLayout()
        
        # Язык интерфейса
        self.interface_lang_switch = LanguageSwitch(self, command=self.on_interface_lang_changed, current_lang=self.config.get('interface_lang', 'en'))
        self.lbl_interface_lang = QLabel()
        ui_form.addRow(self.lbl_interface_lang, self.interface_lang_switch)
        
        # Язык лога
        self.log_lang_switch = LanguageSwitch(self, command=self.on_log_lang_changed, current_lang=self.config.get('log_lang', 'en'))
        self.lbl_log_lang = QLabel()
        ui_form.addRow(self.lbl_log_lang, self.log_lang_switch)

        # Разделитель под языками
        lang_line = QFrame()
        lang_line.setFrameShape(QFrame.Shape.HLine)
        lang_line.setFrameShadow(QFrame.Shadow.Sunken)
        lang_line.setStyleSheet("background-color: #2d2d2d; margin-top: 10px; margin-bottom: 10px;")
        ui_form.addRow(lang_line)

        # Patient Font Size
        self.patient_font_spin = QSpinBox()
        self.patient_font_spin.setRange(8, 36)
        self.patient_font_spin.setValue(self.config.get('patient_font_size', 16))
        self.lbl_patient_font = QLabel()
        ui_form.addRow(self.lbl_patient_font, self.patient_font_spin)
        
        # Patient Font Weight
        self.patient_weight_combo = QComboBox()
        self.patient_weight_combo.addItems(["Regular", "Semibold", "Bold"])
        self.patient_weight_combo.setCurrentText(self.config.get('patient_weight', 'Semibold'))
        self.lbl_patient_weight = QLabel()
        ui_form.addRow(self.lbl_patient_weight, self.patient_weight_combo)
        
        # Font size (logs)
        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(8, 24)
        self.font_size_spin.setValue(self.config['log_font_size'])
        self.lbl_log_font = QLabel()
        ui_form.addRow(self.lbl_log_font, self.font_size_spin)
        
        # Разделитель
        ui_line = QFrame()
        ui_line.setFrameShape(QFrame.Shape.HLine)
        ui_line.setFrameShadow(QFrame.Shadow.Sunken)
        ui_line.setStyleSheet("background-color: #2d2d2d; margin-top: 10px; margin-bottom: 10px;")
        ui_form.addRow(ui_line)
        
        # Основной свич подсветки
        self.highlighting_cb = ToggleSwitch()
        self.highlighting_cb.setChecked(self.config.get('highlighting_enabled', 'False').lower() == 'true')
        self.lbl_highlighting = QLabel()
        ui_form.addRow(self.lbl_highlighting, self.highlighting_cb)
        
        self.lbl_highlight_new = QLabel()
        self.lbl_highlight_new.setStyleSheet("QLabel { padding-left: 30px; }")
        self.highlight_new_cb = ToggleSwitch()
        self.highlight_new_cb.setChecked(self.config.get('highlight_new_enabled', 'False').lower() == 'true')
        ui_form.addRow(self.lbl_highlight_new, self.highlight_new_cb)
        
        self.lbl_highlight_today = QLabel()
        self.lbl_highlight_today.setStyleSheet("QLabel { padding-left: 30px; }")
        self.highlight_today_cb = ToggleSwitch()
        self.highlight_today_cb.setChecked(self.config.get('highlight_today_enabled', 'False').lower() == 'true')
        ui_form.addRow(self.lbl_highlight_today, self.highlight_today_cb)
        
        self.lbl_highlight_no_str = QLabel()
        self.lbl_highlight_no_str.setStyleSheet("QLabel { padding-left: 30px; }")
        self.highlight_no_str_cb = ToggleSwitch()
        self.highlight_no_str_cb.setChecked(self.config.get('highlight_no_str_enabled', 'False').lower() == 'true')
        ui_form.addRow(self.lbl_highlight_no_str, self.highlight_no_str_cb)
        
        self.lbl_highlight_no_slices = QLabel()
        self.lbl_highlight_no_slices.setStyleSheet("QLabel { padding-left: 30px; }")
        self.highlight_no_slices_cb = ToggleSwitch()
        self.highlight_no_slices_cb.setChecked(self.config.get('highlight_no_slices_enabled', 'False').lower() == 'true')
        ui_form.addRow(self.lbl_highlight_no_slices, self.highlight_no_slices_cb)
        
        ui_layout.addLayout(ui_form)
        ui_layout.addStretch()
        self.stacked_widget.addWidget(ui_widget)
        
        # 4. Вкладка Notifications
        notifications_widget = QWidget()
        notifications_layout = QVBoxLayout(notifications_widget)
        notifications_form = QFormLayout()

        # Глобальный мастер-свич "Оповещения"
        self.notifications_enabled_cb = ToggleSwitch()
        self.notifications_enabled_cb.setChecked(self.config.get('notifications_enabled', 'False').lower() == 'true')
        self.lbl_notifications_enabled = QLabel()
        notifications_form.addRow(self.lbl_notifications_enabled, self.notifications_enabled_cb)

        # Разделитель после мастер-свича
        line_master = QFrame()
        line_master.setFrameShape(QFrame.Shape.HLine)
        line_master.setFrameShadow(QFrame.Shadow.Sunken)
        line_master.setStyleSheet("background-color: #2d2d2d; margin-top: 10px; margin-bottom: 10px;")
        notifications_form.addRow(line_master)

        # РАЗДЕЛ: КТ-уведомления
        self.lbl_ct_section = QLabel()
        self.lbl_ct_section.setStyleSheet("font-weight: bold; font-size: 14px; color: #1f538d; margin-top: 5px; margin-bottom: 5px;")
        notifications_form.addRow(self.lbl_ct_section)

        # КТ Оповещения Windows
        self.ct_toast_cb = ToggleSwitch()
        self.ct_toast_cb.setChecked(self.config.get('ct_notification_toast_enabled', 'True').lower() == 'true')
        self.lbl_ct_toast = QLabel()
        notifications_form.addRow(self.lbl_ct_toast, self.ct_toast_cb)

        # КТ Длительность показа
        self.ct_toast_duration_combo = QComboBox()
        self.lbl_ct_toast_duration = QLabel()
        notifications_form.addRow(self.lbl_ct_toast_duration, self.ct_toast_duration_combo)

        # КТ Расположение на экране
        self.ct_toast_position_combo = QComboBox()
        self.lbl_ct_toast_position = QLabel()
        notifications_form.addRow(self.lbl_ct_toast_position, self.ct_toast_position_combo)

        # КТ Звуковые оповещения
        self.ct_sound_cb = ToggleSwitch()
        self.ct_sound_cb.setChecked(self.config.get('ct_notification_sound_enabled', 'False').lower() == 'true')
        self.lbl_ct_sound_enabled = QLabel()
        notifications_form.addRow(self.lbl_ct_sound_enabled, self.ct_sound_cb)

        # Селектор звука КТ
        self.ct_sound_combo = QComboBox()
        self.lbl_ct_sound = QLabel()
        notifications_form.addRow(self.lbl_ct_sound, self.ct_sound_combo)

        # Текст голосового оповещения КТ
        self.ct_voice_text_edit = QLineEdit(self.config.get('ct_voice_text', ''))
        self.lbl_ct_voice_text = QLabel()
        notifications_form.addRow(self.lbl_ct_voice_text, self.ct_voice_text_edit)

        # Заполняем ct_sound_combo
        self._populate_sound_combo(self.ct_sound_combo, self.config.get('ct_notification_sound', 'default'))
        self.ct_sound_combo.activated.connect(lambda: self.play_sound_preview(self.ct_sound_combo))

        # Разделитель после КТ
        line_notif = QFrame()
        line_notif.setFrameShape(QFrame.Shape.HLine)
        line_notif.setFrameShadow(QFrame.Shadow.Sunken)
        line_notif.setStyleSheet("background-color: #2d2d2d; margin-top: 10px; margin-bottom: 10px;")
        notifications_form.addRow(line_notif)

        # РАЗДЕЛ: PACS-уведомления
        self.lbl_pacs_section = QLabel()
        self.lbl_pacs_section.setStyleSheet("font-weight: bold; font-size: 14px; color: #1f538d; margin-top: 5px; margin-bottom: 5px;")
        notifications_form.addRow(self.lbl_pacs_section)

        # PACS Оповещения Windows
        self.pacs_toast_cb = ToggleSwitch()
        self.pacs_toast_cb.setChecked(self.config.get('pacs_notification_toast_enabled', 'True').lower() == 'true')
        self.lbl_pacs_toast = QLabel()
        notifications_form.addRow(self.lbl_pacs_toast, self.pacs_toast_cb)

        # PACS Длительность показа
        self.pacs_toast_duration_combo = QComboBox()
        self.lbl_pacs_toast_duration = QLabel()
        notifications_form.addRow(self.lbl_pacs_toast_duration, self.pacs_toast_duration_combo)

        # PACS Расположение на экране
        self.pacs_toast_position_combo = QComboBox()
        self.lbl_pacs_toast_position = QLabel()
        notifications_form.addRow(self.lbl_pacs_toast_position, self.pacs_toast_position_combo)

        # PACS Звуковые оповещения
        self.pacs_sound_cb = ToggleSwitch()
        self.pacs_sound_cb.setChecked(self.config.get('pacs_notification_sound_enabled', 'False').lower() == 'true')
        self.lbl_pacs_sound_enabled = QLabel()
        notifications_form.addRow(self.lbl_pacs_sound_enabled, self.pacs_sound_cb)

        # Селектор звука PACS
        self.pacs_sound_combo = QComboBox()
        self.lbl_pacs_sound = QLabel()
        notifications_form.addRow(self.lbl_pacs_sound, self.pacs_sound_combo)

        # Текст голосового оповещения PACS
        self.pacs_voice_text_edit = QLineEdit(self.config.get('pacs_voice_text', ''))
        self.lbl_pacs_voice_text = QLabel()
        notifications_form.addRow(self.lbl_pacs_voice_text, self.pacs_voice_text_edit)

        # Заполняем pacs_sound_combo
        self._populate_sound_combo(self.pacs_sound_combo, self.config.get('pacs_notification_sound', 'default'))
        self.pacs_sound_combo.activated.connect(lambda: self.play_sound_preview(self.pacs_sound_combo))

        # Разблокировка голосов Windows OneCore
        self.btn_unlock_voices = QPushButton()
        self.btn_unlock_voices.setFixedHeight(32)
        self.btn_unlock_voices.clicked.connect(self.unlock_system_voices)
        
        if are_onecore_voices_locked():
            notifications_form.addRow(self.btn_unlock_voices)
        else:
            self.btn_unlock_voices.setVisible(False)

        # Интерактивная логика связывания переключателей
        def update_notification_states():
            is_master_on = self.notifications_enabled_cb.isChecked()
            ct_toast_on = is_master_on and self.ct_toast_cb.isChecked()
            ct_sound_on = is_master_on and self.ct_sound_cb.isChecked()
            pacs_toast_on = is_master_on and self.pacs_toast_cb.isChecked()
            pacs_sound_on = is_master_on and self.pacs_sound_cb.isChecked()
            
            # Активируем/деактивируем КТ виджеты
            self.ct_toast_cb.setEnabled(is_master_on)
            self.lbl_ct_toast.setEnabled(is_master_on)
            self.ct_toast_duration_combo.setEnabled(ct_toast_on)
            self.lbl_ct_toast_duration.setEnabled(ct_toast_on)
            self.ct_toast_position_combo.setEnabled(ct_toast_on)
            self.lbl_ct_toast_position.setEnabled(ct_toast_on)

            self.ct_sound_cb.setEnabled(is_master_on)
            self.lbl_ct_sound_enabled.setEnabled(is_master_on)
            self.ct_sound_combo.setEnabled(ct_sound_on)
            self.lbl_ct_sound.setEnabled(ct_sound_on)
            self.ct_voice_text_edit.setEnabled(ct_sound_on)
            self.lbl_ct_voice_text.setEnabled(ct_sound_on)

            # Активируем/деактивируем PACS виджеты
            self.pacs_toast_cb.setEnabled(is_master_on)
            self.lbl_pacs_toast.setEnabled(is_master_on)
            self.pacs_toast_duration_combo.setEnabled(pacs_toast_on)
            self.lbl_pacs_toast_duration.setEnabled(pacs_toast_on)
            self.pacs_toast_position_combo.setEnabled(pacs_toast_on)
            self.lbl_pacs_toast_position.setEnabled(pacs_toast_on)

            self.pacs_sound_cb.setEnabled(is_master_on)
            self.lbl_pacs_sound_enabled.setEnabled(is_master_on)
            self.pacs_sound_combo.setEnabled(pacs_sound_on)
            self.lbl_pacs_sound.setEnabled(pacs_sound_on)
            self.pacs_voice_text_edit.setEnabled(pacs_sound_on)
            self.lbl_pacs_voice_text.setEnabled(pacs_sound_on)

        def on_master_toggled(checked):
            if not checked:
                self.ct_toast_cb.blockSignals(True)
                self.ct_sound_cb.blockSignals(True)
                self.pacs_toast_cb.blockSignals(True)
                self.pacs_sound_cb.blockSignals(True)
                
                self.ct_toast_cb.setChecked(False)
                self.pacs_toast_cb.setChecked(False)
                self.ct_sound_cb.setChecked(False)
                self.pacs_sound_cb.setChecked(False)
                
                self.ct_toast_cb.blockSignals(False)
                self.ct_sound_cb.blockSignals(False)
                self.pacs_toast_cb.blockSignals(False)
                self.pacs_sound_cb.blockSignals(False)
                
            update_notification_states()
            self.on_setting_changed()

        def on_sub_toggled(checked):
            update_notification_states()

        self.notifications_enabled_cb.toggled.connect(on_master_toggled)
        self.ct_toast_cb.toggled.connect(on_sub_toggled)
        self.ct_sound_cb.toggled.connect(on_sub_toggled)
        self.pacs_toast_cb.toggled.connect(on_sub_toggled)
        self.pacs_sound_cb.toggled.connect(on_sub_toggled)
        
        # Начальная инициализация
        update_notification_states()

        notifications_layout.addLayout(notifications_form)
        notifications_layout.addStretch()
        self.stacked_widget.addWidget(notifications_widget)
        
        # 5. Вкладка PACS
        pacs_widget = QWidget()
        pacs_layout = QVBoxLayout(pacs_widget)
        pacs_layout.setSpacing(12)
        
        pacs_form = QFormLayout()
        pacs_form.setContentsMargins(0, 0, 0, 0)
        
        # Выбор сервера PACS
        server_select_layout = QHBoxLayout()
        server_select_layout.setSpacing(10)
        
        self.settings_server_combo = QComboBox()
        self.settings_server_combo.setFixedHeight(30)
        
        self.add_server_btn = QPushButton("Add")
        self.add_server_btn.setFixedWidth(60)
        self.add_server_btn.setFixedHeight(30)
        self.add_server_btn.clicked.connect(self.add_server_action)
        
        self.del_server_btn = QPushButton("Del")
        self.del_server_btn.setFixedWidth(60)
        self.del_server_btn.setFixedHeight(30)
        self.del_server_btn.clicked.connect(self.del_server_action)

        self.rename_server_btn = QPushButton("Rename")
        self.rename_server_btn.setFixedWidth(80)
        self.rename_server_btn.setFixedHeight(30)
        self.rename_server_btn.clicked.connect(self.rename_server_action)

        server_select_layout.addWidget(self.settings_server_combo, stretch=1)
        server_select_layout.addWidget(self.add_server_btn)
        server_select_layout.addWidget(self.del_server_btn)
        server_select_layout.addWidget(self.rename_server_btn)
        
        self.lbl_pacs_server = QLabel()
        pacs_form.addRow(self.lbl_pacs_server, server_select_layout)
        
        # PACS Scan Interval (sec)
        self.pacs_scan_spin = QSpinBox()
        self.pacs_scan_spin.setRange(1, 300)
        self.pacs_scan_spin.setValue(self.config['pacs_scan_time'] // 1000)
        self.lbl_standby_interval = QLabel()
        pacs_form.addRow(self.lbl_standby_interval, self.pacs_scan_spin)

        # IP PACS and Port on same row
        ip_port_layout = QHBoxLayout()
        ip_port_layout.setSpacing(10)
        
        self.pacs_ip_edit = QLineEdit(self.config.get('pacs_ip', '127.0.0.1'))
        
        self.lbl_port = QLabel("Port:")
        self.pacs_port_spin = QSpinBox()
        self.pacs_port_spin.setRange(1, 65535)
        self.pacs_port_spin.setValue(int(self.config.get('pacs_port', 11112)))
        
        ip_port_layout.addWidget(self.pacs_ip_edit, stretch=1)
        ip_port_layout.addWidget(self.lbl_port)
        ip_port_layout.addWidget(self.pacs_port_spin)
        
        self.lbl_pacs_ip = QLabel()
        pacs_form.addRow(self.lbl_pacs_ip, ip_port_layout)

        # AET Remote
        self.pacs_called_aet_edit = QLineEdit(self.config.get('pacs_called_aet', 'ANY-SCP'))
        self.pacs_called_aet_edit.setMaxLength(16)
        self.lbl_pacs_called_aet = QLabel()
        pacs_form.addRow(self.lbl_pacs_called_aet, self.pacs_called_aet_edit)

        # AET Local
        self.pacs_calling_aet_edit = QLineEdit(self.config.get('pacs_calling_aet', 'ECHOSCU'))
        self.pacs_calling_aet_edit.setMaxLength(16)
        self.lbl_pacs_calling_aet = QLabel()
        pacs_form.addRow(self.lbl_pacs_calling_aet, self.pacs_calling_aet_edit)
        
        pacs_layout.addLayout(pacs_form)

        # Кнопка Ping
        self.ping_btn = QPushButton("Ping")
        self.ping_btn.setFixedHeight(30)
        self.ping_btn.clicked.connect(self.ping_pacs_action)
        
        pacs_layout.addSpacing(10)
        pacs_layout.addWidget(self.ping_btn)
        
        pacs_layout.addStretch()
        self.stacked_widget.addWidget(pacs_widget)

        # Инициализация списка серверов
        self.populate_server_combo()
        self.settings_server_combo.currentIndexChanged.connect(self.on_settings_server_changed)

        
        # Подключаем сигналы переключения меню к QStackedWidget
        self.sidebar.currentRowChanged.connect(self.stacked_widget.setCurrentIndex)
        self.sidebar.setCurrentRow(0)
        
        # Добавляем правый контент в главный горизонтальный макет
        main_layout.addWidget(self.stacked_widget, stretch=1)
        
        # Основной вертикальный макет диалога
        outer_layout = QVBoxLayout()
        outer_layout.setContentsMargins(0, 0, 0, 10)
        outer_layout.addLayout(main_layout)
        
        # Dialog Buttons (Save / Cancel)
        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.button_box.accepted.connect(self.accept_settings)
        self.button_box.rejected.connect(self.reject)
        
        # Контейнер для кнопок с правым отступом
        button_layout = QHBoxLayout()
        button_layout.setContentsMargins(0, 5, 15, 0)
        button_layout.addStretch()
        button_layout.addWidget(self.button_box)
        outer_layout.addLayout(button_layout)
        
        self.setLayout(outer_layout)
        
        # Переводим интерфейс диалога при первом открытии
        self.retranslate_ui()
        
        # Подключаем слежение за состоянием полей архива и префиксов ID
        self.archive_enabled_cb.toggled.connect(self.update_fields_state)
        self.archive_cleanup_enabled_cb.toggled.connect(self.update_fields_state)
        self.fix_patient_id_cb.toggled.connect(self.update_fields_state)
        self.rename_study_folder_cb.toggled.connect(self.update_fields_state)
        self.update_fields_state()

        self.setup_dynamic_updates()

    def browse_folder(self, line_edit, title):
        dir_path = QFileDialog.getExistingDirectory(self, title, line_edit.text())
        if dir_path:
            line_edit.setText(os.path.normpath(dir_path))

    def open_app_data_folder(self):
        import subprocess
        app_data_dir = get_app_data_dir()
        if os.path.exists(app_data_dir):
            if sys.platform == "win32":
                os.startfile(app_data_dir)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", app_data_dir])
            else:
                subprocess.Popen(["xdg-open", app_data_dir])

    def update_fields_state(self):
        archive_active = self.archive_enabled_cb.isChecked()
        self.archive_days_spin.setEnabled(archive_active)
        self.archive_label_through.setEnabled(archive_active)
        self.archive_label_days.setEnabled(archive_active)

        cleanup_active = self.archive_cleanup_enabled_cb.isChecked()
        self.archive_cleanup_days_spin.setEnabled(cleanup_active)
        self.cleanup_label_through.setEnabled(cleanup_active)
        self.cleanup_label_days.setEnabled(cleanup_active)

        self.id_prefixes_edit.setEnabled(self.fix_patient_id_cb.isChecked())
        
        rename_folder_active = self.rename_study_folder_cb.isChecked()
        self.rename_study_folder_mode_combo.setEnabled(rename_folder_active)

        highlighting_active = self.highlighting_cb.isChecked()
        self.lbl_highlight_new.setEnabled(highlighting_active)
        self.highlight_new_cb.setEnabled(highlighting_active)
        self.lbl_highlight_today.setEnabled(highlighting_active)
        self.highlight_today_cb.setEnabled(highlighting_active)
        self.lbl_highlight_no_str.setEnabled(highlighting_active)
        self.highlight_no_str_cb.setEnabled(highlighting_active)
        self.lbl_highlight_no_slices.setEnabled(highlighting_active)
        self.highlight_no_slices_cb.setEnabled(highlighting_active)

    def accept_settings(self):
        # Save active inputs to current server structure
        self.save_current_fields_to_config()

        ct_text = self.ct_images_edit.text().strip()
        archive_text = self.archive_edit.text().strip()
        
        if ct_text and archive_text:
            ct_dir = os.path.normpath(ct_text)
            archive_dir = os.path.normpath(archive_text)
            if ct_dir.lower() == archive_dir.lower():
                msg = QMessageBox(self)
                msg.setIcon(QMessageBox.Icon.Warning)
                msg.setWindowTitle(tr_ui("dlg_error_title"))
                msg.setText(tr_ui("dlg_ct_archive_same"))
                apply_dark_title_bar(msg)
                msg.exec()
                return

        if self.archive_enabled_cb.isChecked() and not archive_text:
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Icon.Warning)
            msg.setWindowTitle(tr_ui("dlg_warning_title"))
            msg.setText(tr_ui("dlg_archive_empty_path"))
            apply_dark_title_bar(msg)
            msg.exec()
            return

        # Валидация AE Title
        called_aet = self.pacs_called_aet_edit.text().strip()
        calling_aet = self.pacs_calling_aet_edit.text().strip()
        if len(called_aet) > 16 or len(calling_aet) > 16:
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Icon.Warning)
            msg.setWindowTitle(tr_ui("dlg_error_title"))
            msg.setText(tr_ui("dlg_aet_too_long", called_aet, len(called_aet), calling_aet, len(calling_aet)))
            apply_dark_title_bar(msg)
            msg.exec()
            return

        # Принудительно синхронизируем все настройки перед сохранением
        self.on_setting_changed()
        # Save to file
        self.save_config()
        self.accept()

    def reject(self):
        # Откатываем настройки в MainWindow назад к исходным
        from ui.main_window import MainWindow
        if MainWindow.instance:
            MainWindow.instance.apply_settings_dynamic(self.initial_config)
        super().reject()

    def setup_dynamic_updates(self):
        # Подключаем сигналы изменения виджетов для применения на лету
        self.ct_images_edit.textChanged.connect(self.on_setting_changed)
        self.rename_study_folder_cb.toggled.connect(self.on_setting_changed)
        self.rename_study_folder_mode_combo.currentIndexChanged.connect(self.on_setting_changed)
        self.pacs_scan_spin.valueChanged.connect(self.on_setting_changed)
        self.archive_slice_spin.valueChanged.connect(self.on_setting_changed)
        self.font_size_spin.valueChanged.connect(self.on_setting_changed)
        self.patient_font_spin.valueChanged.connect(self.on_setting_changed)
        self.patient_weight_combo.currentTextChanged.connect(self.on_setting_changed)
    def setup_dynamic_updates(self):
        # Подключаем сигналы изменения виджетов для применения на лету
        self.ct_images_edit.textChanged.connect(self.on_setting_changed)
        self.rename_study_folder_cb.toggled.connect(self.on_setting_changed)
        self.rename_study_folder_mode_combo.currentIndexChanged.connect(self.on_setting_changed)
        self.pacs_scan_spin.valueChanged.connect(self.on_setting_changed)
        self.archive_slice_spin.valueChanged.connect(self.on_setting_changed)
        self.font_size_spin.valueChanged.connect(self.on_setting_changed)
        self.patient_font_spin.valueChanged.connect(self.on_setting_changed)
        self.patient_weight_combo.currentTextChanged.connect(self.on_setting_changed)
        self.notifications_enabled_cb.toggled.connect(self.on_setting_changed)
        self.ct_toast_cb.toggled.connect(self.on_setting_changed)
        self.ct_toast_duration_combo.currentIndexChanged.connect(self.on_setting_changed)
        self.ct_toast_position_combo.currentIndexChanged.connect(self.on_setting_changed)
        self.ct_sound_cb.toggled.connect(self.on_setting_changed)
        self.ct_sound_combo.currentIndexChanged.connect(self.on_setting_changed)
        self.ct_voice_text_edit.textChanged.connect(self.on_setting_changed)
        self.highlighting_cb.toggled.connect(self.on_highlighting_toggled)
        self.highlight_new_cb.toggled.connect(self.on_setting_changed)
        self.highlight_today_cb.toggled.connect(self.on_setting_changed)
        self.highlight_no_str_cb.toggled.connect(self.on_setting_changed)
        self.highlight_no_slices_cb.toggled.connect(self.on_setting_changed)
        self.pacs_toast_cb.toggled.connect(self.on_setting_changed)
        self.pacs_toast_duration_combo.currentIndexChanged.connect(self.on_setting_changed)
        self.pacs_toast_position_combo.currentIndexChanged.connect(self.on_setting_changed)
        self.pacs_sound_cb.toggled.connect(self.on_setting_changed)
        self.pacs_sound_combo.currentIndexChanged.connect(self.on_setting_changed)
        self.pacs_voice_text_edit.textChanged.connect(self.on_setting_changed)
        self.check_updates_cb.toggled.connect(self.on_setting_changed)
        self.cleanup_str_cb.toggled.connect(self.on_setting_changed)
        self.fix_patient_id_cb.toggled.connect(self.on_setting_changed)
        self.id_prefixes_edit.textChanged.connect(self.on_setting_changed)
        self.archive_edit.textChanged.connect(self.on_setting_changed)
        self.archive_enabled_cb.toggled.connect(self.on_setting_changed)
        self.archive_days_spin.valueChanged.connect(self.on_setting_changed)
        self.archive_cleanup_enabled_cb.toggled.connect(self.on_setting_changed)
        self.archive_cleanup_days_spin.valueChanged.connect(self.on_setting_changed)
        self.pacs_ip_edit.textChanged.connect(self.on_setting_changed)
        self.pacs_port_spin.valueChanged.connect(self.on_setting_changed)
        self.pacs_called_aet_edit.textChanged.connect(self.on_setting_changed)
        self.pacs_calling_aet_edit.textChanged.connect(self.on_setting_changed)

    def on_highlighting_toggled(self, checked):
        self.highlight_new_cb.blockSignals(True)
        self.highlight_today_cb.blockSignals(True)
        self.highlight_no_str_cb.blockSignals(True)
        self.highlight_no_slices_cb.blockSignals(True)
        
        self.highlight_new_cb.setChecked(checked)
        self.highlight_today_cb.setChecked(checked)
        self.highlight_no_str_cb.setChecked(checked)
        self.highlight_no_slices_cb.setChecked(checked)
        
        self.highlight_new_cb.blockSignals(False)
        self.highlight_today_cb.blockSignals(False)
        self.highlight_no_str_cb.blockSignals(False)
        self.highlight_no_slices_cb.blockSignals(False)
        
        self.update_fields_state()
        self.on_setting_changed()

    def on_setting_changed(self):
        # Обновляем текущую конфигурацию
        self.config['ct_images_dir'] = self.ct_images_edit.text()
        self.config['archive_dir'] = self.archive_edit.text()
        self.config['pacs_scan_time'] = self.pacs_scan_spin.value() * 1000
        self.config['archive_slice'] = self.archive_slice_spin.value()
        self.config['log_font_size'] = self.font_size_spin.value()
        self.config['patient_font_size'] = self.patient_font_spin.value()
        self.config['patient_weight'] = self.patient_weight_combo.currentText()
        self.config['notifications_enabled'] = 'True' if self.notifications_enabled_cb.isChecked() else 'False'
        self.config['ct_notification_toast_enabled'] = 'True' if self.ct_toast_cb.isChecked() else 'False'
        self.config['ct_toast_duration'] = self.ct_toast_duration_combo.currentData()
        self.config['ct_toast_position'] = self.ct_toast_position_combo.currentData()
        self.config['ct_notification_sound_enabled'] = 'True' if self.ct_sound_cb.isChecked() else 'False'
        self.config['ct_notification_sound'] = self.ct_sound_combo.currentData()
        self.config['ct_voice_text'] = self.ct_voice_text_edit.text()
        self.config['pacs_notification_toast_enabled'] = 'True' if self.pacs_toast_cb.isChecked() else 'False'
        self.config['pacs_toast_duration'] = self.pacs_toast_duration_combo.currentData()
        self.config['pacs_toast_position'] = self.pacs_toast_position_combo.currentData()
        self.config['pacs_notification_sound_enabled'] = 'True' if self.pacs_sound_cb.isChecked() else 'False'
        self.config['pacs_notification_sound'] = self.pacs_sound_combo.currentData()
        self.config['pacs_voice_text'] = self.pacs_voice_text_edit.text()
        self.config['check_updates_at_startup'] = 'on' if self.check_updates_cb.isChecked() else 'off'
        self.config['auto_update_is'] = self.config.get('auto_update_is', 'off')
        self.config['cleanup_structures_enabled'] = 'True' if self.cleanup_str_cb.isChecked() else 'False'
        self.config['fix_patient_id_enabled'] = 'True' if self.fix_patient_id_cb.isChecked() else 'False'
        self.config['id_prefixes'] = self.id_prefixes_edit.text()
        self.config['rename_study_folder_enabled'] = 'True' if self.rename_study_folder_cb.isChecked() else 'False'
        idx = self.rename_study_folder_mode_combo.currentIndex()
        if idx == 0:
            self.config['rename_study_folder_mode'] = 'id'
        elif idx == 1:
            self.config['rename_study_folder_mode'] = 'name'
        elif idx == 2:
            self.config['rename_study_folder_mode'] = 'name_id'
        elif idx == 3:
            self.config['rename_study_folder_mode'] = 'id_name'
        else:
            self.config['rename_study_folder_mode'] = 'id'
        self.config['archive_enabled'] = 'True' if self.archive_enabled_cb.isChecked() else 'False'
        self.config['archive_days'] = self.archive_days_spin.value()
        self.config['archive_cleanup_enabled'] = 'True' if self.archive_cleanup_enabled_cb.isChecked() else 'False'
        self.config['archive_cleanup_days'] = self.archive_cleanup_days_spin.value()
        self.config['pacs_ip'] = self.pacs_ip_edit.text()
        self.config['pacs_port'] = self.pacs_port_spin.value()
        self.config['pacs_called_aet'] = self.pacs_called_aet_edit.text()
        self.config['pacs_calling_aet'] = self.pacs_calling_aet_edit.text()
        self.config['highlighting_enabled'] = 'True' if self.highlighting_cb.isChecked() else 'False'
        self.config['highlight_new_enabled'] = 'True' if self.highlight_new_cb.isChecked() else 'False'
        self.config['highlight_today_enabled'] = 'True' if self.highlight_today_cb.isChecked() else 'False'
        self.config['highlight_no_str_enabled'] = 'True' if self.highlight_no_str_cb.isChecked() else 'False'
        self.config['highlight_no_slices_enabled'] = 'True' if self.highlight_no_slices_cb.isChecked() else 'False'
        self.config['interface_lang'] = self.interface_lang_switch.lang
        self.config['log_lang'] = self.log_lang_switch.lang

        # Применяем настройки на лету в главном окне
        from ui.main_window import MainWindow
        if MainWindow.instance:
            MainWindow.instance.apply_settings_dynamic(self.config)

    def _populate_sound_combo(self, combo, current_val):
        from core.locale_utils import tr_ui
        from PyQt6.QtCore import Qt, QSize
        combo.clear()
        combo.addItem(tr_ui("settings_sound_default"), "default")
        combo.addItem(tr_ui("settings_sound_chime"), "sound_chime")
        combo.addItem(tr_ui("settings_sound_ping"), "sound_ping")
        combo.addItem(tr_ui("settings_sound_pop"), "sound_pop")
        combo.addItem(tr_ui("settings_sound_soft"), "sound_soft")
        if self.system_voices:
            sep_idx = combo.count()
            combo.insertSeparator(sep_idx)
            combo.setItemData(sep_idx, QSize(0, 26), Qt.ItemDataRole.SizeHintRole)
            for voice in self.system_voices:
                combo.addItem(format_voice_name(voice), voice)
        idx = combo.findData(current_val)
        combo.setCurrentIndex(idx if idx >= 0 else 0)

    def play_sound_preview(self, combo):
        sound_setting = combo.currentData()
        if not sound_setting:
            return

        sound_map = {
            'default': "src/notification.wav",
            'sound_chime': "src/notification_chime.wav",
            'sound_ping': "src/notification_ping.wav",
            'sound_pop': "src/notification_pop.wav",
            'sound_soft': "src/notification_soft.wav",
        }
        if sound_setting in sound_map:
            from core.config_utils import get_resource_path
            from core.notifier import _play_wav
            wav_path = get_resource_path(sound_map[sound_setting])
            _play_wav(wav_path)
        elif sys.platform == "win32":
            lang = self.config.get('interface_lang', 'en')
            default_text = "Проверка звука" if lang == "ru" else "Sound check"
            custom_text = ""
            if combo == self.ct_sound_combo:
                custom_text = self.ct_voice_text_edit.text().strip()
            elif combo == self.pacs_sound_combo:
                custom_text = self.pacs_voice_text_edit.text().strip()
            from core.notifier import preprocess_tts_text
            raw_text = custom_text if custom_text else default_text
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
            except Exception:
                pass

    def unlock_system_voices(self):
        import subprocess
        import sys
        import os
        import tempfile
        from PyQt6.QtWidgets import QMessageBox
        from core.locale_utils import tr_ui

        ps_code = """
$src = "HKLM:\\SOFTWARE\\Microsoft\\Speech_OneCore\\Voices\\Tokens"
$dst64 = "HKLM:\\SOFTWARE\\Microsoft\\Speech\\Voices\\Tokens"
$dst32 = "HKLM:\\SOFTWARE\\Wow6432Node\\Microsoft\\Speech\\Voices\\Tokens"

function Copy-VoiceTokens($srcPath, $dstPath) {
    if (Test-Path $srcPath) {
        if (-not (Test-Path $dstPath)) {
            New-Item -Path $dstPath -Force | Out-Null
        }
        Get-ChildItem $srcPath | ForEach-Object {
            $name = $_.PSChildName
            $dstToken = "$dstPath\\$name"
            if (-not (Test-Path $dstToken)) {
                Copy-Item -Path "$srcPath\\$name" -Destination $dstPath -Recurse -Force
            }
        }
    }
}

Copy-VoiceTokens $src $dst64
Copy-VoiceTokens $src $dst32
"""
        try:
            fd, path = tempfile.mkstemp(suffix=".ps1", text=True)
            with os.fdopen(fd, "w", encoding="utf-8-sig") as f:
                f.write(ps_code)
            
            cmd = f"Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File \"{path}\"'"
            subprocess.run(["powershell", "-NoProfile", "-Command", cmd], creationflags=subprocess.CREATE_NO_WINDOW)
            
            QMessageBox.information(
                self,
                tr_ui("dlg_ping_success_title"),
                tr_ui("msg_voices_unlock_initiated")
            )
            self.btn_unlock_voices.setVisible(False)
        except Exception as e:
            print("Error unlocking voices:", e)

    def on_interface_lang_changed(self, lang):
        self.config['interface_lang'] = lang
        set_current_langs(self.config.get('interface_lang', 'en'), self.config.get('log_lang', 'en'))
        # Retranslate SettingsDialog itself
        self.retranslate_ui()
        # Apply to MainWindow
        from ui.main_window import MainWindow
        if MainWindow.instance:
            MainWindow.instance.apply_settings_dynamic(self.config)

    def on_log_lang_changed(self, lang):
        self.config['log_lang'] = lang
        set_current_langs(self.config.get('interface_lang', 'en'), self.config.get('log_lang', 'en'))
        # Apply to MainWindow
        from ui.main_window import MainWindow
        if MainWindow.instance:
            MainWindow.instance.apply_settings_dynamic(self.config)

    def retranslate_sidebar(self):
        self.sidebar.blockSignals(True)
        current_row = self.sidebar.currentRow()
        self.sidebar.clear()
        self.sidebar.addItems([
            tr_ui("settings_tab_general"),
            tr_ui("settings_tab_archive"),
            tr_ui("settings_tab_ui"),
            tr_ui("settings_tab_notifications"),
            tr_ui("settings_tab_pacs")
        ])
        if current_row >= 0:
            self.sidebar.setCurrentRow(current_row)
        else:
            self.sidebar.setCurrentRow(0)
        self.sidebar.blockSignals(False)

    def retranslate_ui(self):
        self.setWindowTitle(tr_ui("settings_title"))
        
        # Sidebar
        self.retranslate_sidebar()
        
        # Labels in Form Layouts:
        self.lbl_ct_folder.setText(tr_ui("settings_ct_images_folder"))
        self.lbl_settings_folder.setText(tr_ui("settings_settings_folder"))
        self.lbl_notifications_enabled.setText(tr_ui("settings_notifications_enabled"))
        self.lbl_ct_section.setText(tr_ui("settings_ct_section_title"))
        self.lbl_ct_toast.setText(tr_ui("settings_notifications_toast_enabled"))
        self.lbl_ct_toast_duration.setText(tr_ui("settings_toast_duration_label"))
        self.lbl_ct_toast_position.setText(tr_ui("settings_toast_position_label"))

        # Populate ct_toast_duration_combo items
        self.ct_toast_duration_combo.blockSignals(True)
        cur_dur_ct = self.ct_toast_duration_combo.currentData()
        if not cur_dur_ct:
            cur_dur_ct = str(self.config.get('ct_toast_duration', self.config.get('toast_duration', '5')))
        self.ct_toast_duration_combo.clear()
        self.ct_toast_duration_combo.addItem(tr_ui("settings_toast_dur_3s"), "3")
        self.ct_toast_duration_combo.addItem(tr_ui("settings_toast_dur_5s"), "5")
        self.ct_toast_duration_combo.addItem(tr_ui("settings_toast_dur_8s"), "8")
        self.ct_toast_duration_combo.addItem(tr_ui("settings_toast_dur_15s"), "15")
        self.ct_toast_duration_combo.addItem(tr_ui("settings_toast_dur_manual"), "manual")
        idx_dur_ct = self.ct_toast_duration_combo.findData(cur_dur_ct)
        self.ct_toast_duration_combo.setCurrentIndex(idx_dur_ct if idx_dur_ct >= 0 else 1)
        self.ct_toast_duration_combo.blockSignals(False)

        # Populate ct_toast_position_combo items
        self.ct_toast_position_combo.blockSignals(True)
        cur_pos_ct = self.ct_toast_position_combo.currentData()
        if not cur_pos_ct:
            cur_pos_ct = str(self.config.get('ct_toast_position', self.config.get('toast_position', 'bottom_right')))
        self.ct_toast_position_combo.clear()
        self.ct_toast_position_combo.addItem(tr_ui("settings_toast_pos_bottom_right"), "bottom_right")
        self.ct_toast_position_combo.addItem(tr_ui("settings_toast_pos_bottom_left"), "bottom_left")
        self.ct_toast_position_combo.addItem(tr_ui("settings_toast_pos_top_right"), "top_right")
        self.ct_toast_position_combo.addItem(tr_ui("settings_toast_pos_top_left"), "top_left")
        idx_pos_ct = self.ct_toast_position_combo.findData(cur_pos_ct)
        self.ct_toast_position_combo.setCurrentIndex(idx_pos_ct if idx_pos_ct >= 0 else 0)
        self.ct_toast_position_combo.blockSignals(False)

        self.lbl_ct_sound_enabled.setText(tr_ui("settings_notifications_sound_enabled"))
        self.lbl_ct_sound.setText(tr_ui("settings_ct_sound_label"))
        self.lbl_ct_voice_text.setText(tr_ui("settings_ct_voice_text_label"))
        self.ct_voice_text_edit.setPlaceholderText(tr_ui("settings_ct_voice_text_placeholder"))
        self.lbl_ct_voice_text.setToolTip(tr_ui("tooltip_voice_text_hint"))
        self.ct_voice_text_edit.setToolTip(tr_ui("tooltip_voice_text_hint"))

        # PACS Section Labels and Comboboxes
        self.lbl_pacs_section.setText(tr_ui("settings_pacs_section_title"))
        self.lbl_pacs_toast.setText(tr_ui("settings_notifications_toast_enabled"))
        self.lbl_pacs_toast_duration.setText(tr_ui("settings_toast_duration_label"))
        self.lbl_pacs_toast_position.setText(tr_ui("settings_toast_position_label"))

        # Populate pacs_toast_duration_combo items
        self.pacs_toast_duration_combo.blockSignals(True)
        cur_dur_pacs = self.pacs_toast_duration_combo.currentData()
        if not cur_dur_pacs:
            cur_dur_pacs = str(self.config.get('pacs_toast_duration', self.config.get('toast_duration', '5')))
        self.pacs_toast_duration_combo.clear()
        self.pacs_toast_duration_combo.addItem(tr_ui("settings_toast_dur_3s"), "3")
        self.pacs_toast_duration_combo.addItem(tr_ui("settings_toast_dur_5s"), "5")
        self.pacs_toast_duration_combo.addItem(tr_ui("settings_toast_dur_8s"), "8")
        self.pacs_toast_duration_combo.addItem(tr_ui("settings_toast_dur_15s"), "15")
        self.pacs_toast_duration_combo.addItem(tr_ui("settings_toast_dur_manual"), "manual")
        idx_dur_pacs = self.pacs_toast_duration_combo.findData(cur_dur_pacs)
        self.pacs_toast_duration_combo.setCurrentIndex(idx_dur_pacs if idx_dur_pacs >= 0 else 1)
        self.pacs_toast_duration_combo.blockSignals(False)

        # Populate pacs_toast_position_combo items
        self.pacs_toast_position_combo.blockSignals(True)
        cur_pos_pacs = self.pacs_toast_position_combo.currentData()
        if not cur_pos_pacs:
            cur_pos_pacs = str(self.config.get('pacs_toast_position', self.config.get('toast_position', 'bottom_right')))
        self.pacs_toast_position_combo.clear()
        self.pacs_toast_position_combo.addItem(tr_ui("settings_toast_pos_bottom_right"), "bottom_right")
        self.pacs_toast_position_combo.addItem(tr_ui("settings_toast_pos_bottom_left"), "bottom_left")
        self.pacs_toast_position_combo.addItem(tr_ui("settings_toast_pos_top_right"), "top_right")
        self.pacs_toast_position_combo.addItem(tr_ui("settings_toast_pos_top_left"), "top_left")
        idx_pos_pacs = self.pacs_toast_position_combo.findData(cur_pos_pacs)
        self.pacs_toast_position_combo.setCurrentIndex(idx_pos_pacs if idx_pos_pacs >= 0 else 0)
        self.pacs_toast_position_combo.blockSignals(False)
        self.lbl_pacs_toast.setText(tr_ui("settings_notifications_toast_enabled"))
        self.lbl_pacs_sound_enabled.setText(tr_ui("settings_notifications_sound_enabled"))
        self.lbl_pacs_sound.setText(tr_ui("settings_pacs_sound_label"))
        self.lbl_pacs_voice_text.setText(tr_ui("settings_pacs_voice_text_label"))
        self.pacs_voice_text_edit.setPlaceholderText(tr_ui("settings_pacs_voice_text_placeholder"))
        self.lbl_pacs_voice_text.setToolTip(tr_ui("tooltip_voice_text_hint"))
        self.pacs_voice_text_edit.setToolTip(tr_ui("tooltip_voice_text_hint"))
        self.ct_sound_combo.setItemText(0, tr_ui("settings_sound_default"))
        self.pacs_sound_combo.setItemText(0, tr_ui("settings_sound_default"))
        self.btn_unlock_voices.setText(tr_ui("settings_btn_unlock_voices"))
        self.lbl_cleanup_str.setText(tr_ui("settings_cleanup_str"))
        self.lbl_fix_id.setText(tr_ui("settings_fix_id_label"))
        self.lbl_id_prefixes.setText(tr_ui("settings_id_prefixes_label"))
        self.id_prefixes_edit.setPlaceholderText(tr_ui("settings_id_prefixes_placeholder"))
        self.lbl_rename_folder.setText(tr_ui("settings_rename_folder_label"))
        self.lbl_rename_folder_mode.setText(tr_ui("settings_rename_folder_mode_label"))
        
        # Populate rename folder mode combo
        self.rename_study_folder_mode_combo.blockSignals(True)
        current_idx = self.rename_study_folder_mode_combo.currentIndex()
        if current_idx < 0:
            current_mode = self.config.get('rename_study_folder_mode', 'id')
            mode_map = {'id': 0, 'name': 1, 'name_id': 2, 'id_name': 3}
            current_idx = mode_map.get(current_mode, 0)
        self.rename_study_folder_mode_combo.clear()
        self.rename_study_folder_mode_combo.addItem(tr_ui("settings_rename_folder_mode_id"))
        self.rename_study_folder_mode_combo.addItem(tr_ui("settings_rename_folder_mode_name"))
        self.rename_study_folder_mode_combo.addItem(tr_ui("settings_rename_folder_mode_name_id"))
        self.rename_study_folder_mode_combo.addItem(tr_ui("settings_rename_folder_mode_id_name"))
        self.rename_study_folder_mode_combo.setCurrentIndex(current_idx)
        self.rename_study_folder_mode_combo.blockSignals(False)
        
        self.check_updates_cb.setText(tr_ui("settings_check_updates_toggle"))
        self.btn_check_updates.setText(tr_ui("settings_check_updates_btn"))
        
        # Browse/open buttons in General tab
        self.btn_ct_images_browse.setText(tr_ui("settings_browse"))
        self.btn_app_data_open.setText(tr_ui("settings_open"))
        
        # Archive tab
        self.lbl_archive_dir.setText(tr_ui("settings_archive_dir"))
        self.btn_archive_browse.setText(tr_ui("settings_browse"))
        self.lbl_archive_slice.setText(tr_ui("settings_archive_slice"))
        self.lbl_auto_archive_row.setText(tr_ui("settings_auto_archive_row"))
        self.archive_label_through.setText(tr_ui("lbl_archive_through"))
        self.archive_label_days.setText(tr_ui("lbl_archive_days"))
        self.lbl_auto_cleanup_row.setText(tr_ui("settings_auto_cleanup_row"))
        self.cleanup_label_through.setText(tr_ui("lbl_archive_through"))
        self.cleanup_label_days.setText(tr_ui("lbl_archive_days"))
        
        # UI tab
        self.lbl_interface_lang.setText(tr_ui("settings_interface_lang"))
        self.lbl_log_lang.setText(tr_ui("settings_log_lang"))
        self.lbl_patient_font.setText(tr_ui("settings_patient_font"))
        self.lbl_patient_weight.setText(tr_ui("settings_patient_weight"))
        self.lbl_log_font.setText(tr_ui("settings_log_font"))
        self.lbl_highlighting.setText(tr_ui("settings_highlighting"))
        self.lbl_highlight_new.setText(tr_ui("settings_highlight_new"))
        self.lbl_highlight_today.setText(tr_ui("settings_highlight_today"))
        self.lbl_highlight_no_str.setText(tr_ui("settings_highlight_no_str"))
        self.lbl_highlight_no_slices.setText(tr_ui("settings_highlight_no_slices"))
        
        # PACS tab
        self.lbl_pacs_server.setText(tr_ui("settings_pacs_server_label"))
        self.lbl_standby_interval.setText(tr_ui("settings_standby_interval"))
        self.lbl_pacs_ip.setText(tr_ui("settings_pacs_ip"))
        self.lbl_pacs_called_aet.setText(tr_ui("settings_pacs_called_aet"))
        self.lbl_pacs_calling_aet.setText(tr_ui("settings_pacs_calling_aet"))
        self.add_server_btn.setText(tr_ui("settings_btn_add"))
        self.del_server_btn.setText(tr_ui("settings_btn_del"))
        self.rename_server_btn.setText(tr_ui("settings_btn_rename"))
        
        # Standard buttons Save/Cancel
        save_btn = self.button_box.button(QDialogButtonBox.StandardButton.Save)
        if save_btn:
            save_btn.setText(tr_ui("btn_save"))
            save_btn.setToolTip(tr_ui("tooltip_settings_save"))
        cancel_btn = self.button_box.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_btn:
            cancel_btn.setText(tr_ui("btn_cancel"))
            cancel_btn.setToolTip(tr_ui("tooltip_settings_cancel"))

        # Подсказки (tooltips)
        self.btn_ct_images_browse.setToolTip(tr_ui("tooltip_settings_ct_browse"))
        self.btn_app_data_open.setToolTip(tr_ui("tooltip_settings_app_data"))
        self.btn_check_updates.setToolTip(tr_ui("tooltip_settings_check_updates"))
        self.btn_archive_browse.setToolTip(tr_ui("tooltip_settings_archive_browse"))
        self.add_server_btn.setToolTip(tr_ui("tooltip_settings_add_server"))
        self.del_server_btn.setToolTip(tr_ui("tooltip_settings_del_server"))
        self.rename_server_btn.setToolTip(tr_ui("tooltip_settings_rename_server"))
        self.ping_btn.setToolTip(tr_ui("tooltip_settings_ping_server"))
        
        self.notifications_enabled_cb.setToolTip(tr_ui("tooltip_switch_notify"))
        self.ct_toast_cb.setToolTip(tr_ui("tooltip_switch_notify"))
        self.pacs_toast_cb.setToolTip(tr_ui("tooltip_switch_pacs_notify"))
        self.cleanup_str_cb.setToolTip(tr_ui("tooltip_switch_cleanup_str"))
        self.fix_patient_id_cb.setToolTip(tr_ui("tooltip_switch_fix_id"))
        self.rename_study_folder_cb.setToolTip(tr_ui("tooltip_switch_rename_folder"))
        self.check_updates_cb.setToolTip(tr_ui("tooltip_switch_check_updates"))
        self.archive_enabled_cb.setToolTip(tr_ui("tooltip_switch_archive_enabled"))
        self.archive_cleanup_enabled_cb.setToolTip(tr_ui("tooltip_switch_archive_cleanup"))
        self.highlighting_cb.setToolTip(tr_ui("tooltip_switch_highlighting"))
        self.highlight_new_cb.setToolTip(tr_ui("tooltip_switch_highlight_new"))
        self.highlight_today_cb.setToolTip(tr_ui("tooltip_switch_highlight_today"))
        self.highlight_no_str_cb.setToolTip(tr_ui("tooltip_switch_highlight_no_str"))
        self.highlight_no_slices_cb.setToolTip(tr_ui("tooltip_switch_highlight_no_slices"))

    def ping_pacs_action(self):
        pacs_ip = self.pacs_ip_edit.text().strip()
        pacs_port = self.pacs_port_spin.value()
        called_aet = self.pacs_called_aet_edit.text().strip()
        calling_aet = self.pacs_calling_aet_edit.text().strip()

        if not pacs_ip:
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Icon.Warning)
            msg.setWindowTitle(tr_ui("dlg_error_title"))
            msg.setText(tr_ui("dlg_ping_ip_empty"))
            apply_dark_title_bar(msg)
            msg.exec()
            return

        self.ping_btn.setEnabled(False)
        self.ping_btn.setText("Ping...")

        self.ping_worker = PacsPingWorker(pacs_ip, pacs_port, called_aet, calling_aet)
        self.ping_worker.finished.connect(self.on_ping_finished)
        self.ping_worker.start()

    def on_ping_finished(self, success, message):
        self.ping_btn.setEnabled(True)
        self.ping_btn.setText("Ping")

        msg = QMessageBox(self)
        if success:
            msg.setIcon(QMessageBox.Icon.Information)
            msg.setWindowTitle(tr_ui("dlg_ping_success_title"))
        else:
            msg.setIcon(QMessageBox.Icon.Warning)
            msg.setWindowTitle(tr_ui("dlg_ping_fail_title"))
        msg.setText(message)
        apply_dark_title_bar(msg)
        msg.exec()

    def manual_check_updates(self):
        self.btn_check_updates.setEnabled(False)
        self.btn_check_updates.setText(tr_ui("btn_checking"))
        
        self.manual_update_worker = UpdateCheckWorker()
        self.manual_update_worker.finished.connect(self.on_manual_update_checked)
        self.manual_update_worker.start()

    def on_manual_update_checked(self, latest_version, html_url, assets):
        self.btn_check_updates.setEnabled(True)
        self.btn_check_updates.setText(tr_ui("settings_check_updates_btn"))
        
        if not latest_version:
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Icon.Warning)
            msg.setWindowTitle(tr_ui("dlg_update_error_title"))
            msg.setText(tr_ui("dlg_update_error_msg"))
            apply_dark_title_bar(msg)
            msg.exec()
            return
            
        from core.config_utils import VERSION
        from ui.updater import is_newer_version
        if is_newer_version(VERSION, latest_version):
            from ui.updater import run_auto_update
            run_auto_update(self, latest_version, assets)
        else:
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Icon.Information)
            msg.setWindowTitle(tr_ui("dlg_update_current_title"))
            msg.setText(tr_ui("dlg_update_current_msg"))
            apply_dark_title_bar(msg)
            msg.exec()

    def populate_server_combo(self):
        self.settings_server_combo.blockSignals(True)
        self.settings_server_combo.clear()
        
        servers = self.config.get('pacs_servers', [])
        current_name = self.config.get('pacs_current_server_name', '')
        
        active_idx = 0
        for i, s in enumerate(servers):
            self.settings_server_combo.addItem(s['name'])
            if s['name'] == current_name:
                active_idx = i
                
        self.settings_server_combo.setCurrentIndex(active_idx)
        self.last_selected_server_idx = active_idx
        self.settings_server_combo.blockSignals(False)
        
        # Load fields for the active server
        self.load_server_fields(active_idx)

    def load_server_fields(self, index):
        servers = self.config.get('pacs_servers', [])
        if 0 <= index < len(servers):
            s = servers[index]
            self.pacs_ip_edit.setText(s.get('pacs_ip', '127.0.0.1'))
            self.pacs_port_spin.setValue(int(s.get('pacs_port', 11112)))
            self.pacs_called_aet_edit.setText(s.get('pacs_called_aet', 'ANY-SCP'))
            self.pacs_calling_aet_edit.setText(s.get('pacs_calling_aet', 'ECHOSCU'))

    def save_current_fields_to_config(self, idx=None):
        servers = self.config.get('pacs_servers', [])
        if idx is None:
            idx = self.settings_server_combo.currentIndex()
        if 0 <= idx < len(servers):
            servers[idx]['pacs_ip'] = self.pacs_ip_edit.text().strip()
            servers[idx]['pacs_port'] = self.pacs_port_spin.value()
            servers[idx]['pacs_called_aet'] = self.pacs_called_aet_edit.text().strip()
            servers[idx]['pacs_calling_aet'] = self.pacs_calling_aet_edit.text().strip()
            
            # Also update current server config keys for backward compatibility
            if idx == self.settings_server_combo.currentIndex():
                self.config['pacs_ip'] = servers[idx]['pacs_ip']
                self.config['pacs_port'] = servers[idx]['pacs_port']
                self.config['pacs_called_aet'] = servers[idx]['pacs_called_aet']
                self.config['pacs_calling_aet'] = servers[idx]['pacs_calling_aet']
                self.config['pacs_current_server_name'] = servers[idx]['name']

    def on_settings_server_changed(self, index):
        # Save inputs to the PREVIOUSLY active server index
        if hasattr(self, 'last_selected_server_idx') and self.last_selected_server_idx != index:
            self.save_current_fields_to_config(self.last_selected_server_idx)
            
        # Load fields for the newly selected server
        self.load_server_fields(index)
        
        # Update last selected index
        self.last_selected_server_idx = index
        
        # Update currently active server name
        servers = self.config.get('pacs_servers', [])
        if 0 <= index < len(servers):
            self.config['pacs_current_server_name'] = servers[index]['name']

    def add_server_action(self):
        from PyQt6.QtWidgets import QInputDialog
        self.save_current_fields_to_config(self.last_selected_server_idx)
        
        dialog = QInputDialog(self)
        dialog.setWindowTitle(tr_ui("dlg_add_server_title"))
        dialog.setLabelText(tr_ui("dlg_add_server_label"))
        apply_dark_title_bar(dialog)
        
        ok = dialog.exec()
        name = dialog.textValue()
        
        if ok and name.strip():
            name = name.strip()
            servers = self.config.get('pacs_servers', [])
            # Check for duplicate names
            if any(s['name'] == name for s in servers):
                msg = QMessageBox(self)
                msg.setIcon(QMessageBox.Icon.Warning)
                msg.setWindowTitle(tr_ui("dlg_warning_title"))
                msg.setText(tr_ui("dlg_server_exists"))
                apply_dark_title_bar(msg)
                msg.exec()
                return
                
            new_server = {
                'name': name,
                'pacs_ip': '127.0.0.1',
                'pacs_port': 11112,
                'pacs_called_aet': 'ANY-SCP',
                'pacs_calling_aet': 'ECHOSCU'
            }
            servers.append(new_server)
            self.config['pacs_current_server_name'] = name
            self.populate_server_combo()

    def del_server_action(self):
        servers = self.config.get('pacs_servers', [])
        if len(servers) <= 1:
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Icon.Warning)
            msg.setWindowTitle(tr_ui("dlg_warning_title"))
            msg.setText(tr_ui("dlg_del_last_server"))
            apply_dark_title_bar(msg)
            msg.exec()
            return
            
        idx = self.settings_server_combo.currentIndex()
        if 0 <= idx < len(servers):
            confirm = QMessageBox(self)
            confirm.setIcon(QMessageBox.Icon.Question)
            confirm.setWindowTitle(tr_ui("dlg_del_server_title"))
            confirm.setText(tr_ui("dlg_del_server_msg", servers[idx]['name']))
            confirm.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            apply_dark_title_bar(confirm)
            if confirm.exec() == QMessageBox.StandardButton.Yes:
                servers.pop(idx)
                # Set active to the first remaining server
                self.config['pacs_current_server_name'] = servers[0]['name']
                self.populate_server_combo()

    def rename_server_action(self):
        from PyQt6.QtWidgets import QInputDialog
        servers = self.config.get('pacs_servers', [])
        idx = self.settings_server_combo.currentIndex()
        if 0 <= idx < len(servers):
            old_name = servers[idx]['name']
            
            dialog = QInputDialog(self)
            dialog.setWindowTitle(tr_ui("dlg_rename_server_title"))
            dialog.setLabelText(tr_ui("dlg_rename_server_label", old_name))
            dialog.setTextValue(old_name)
            apply_dark_title_bar(dialog)
            
            ok = dialog.exec()
            name = dialog.textValue()
            
            if ok and name.strip() and name.strip() != old_name:
                name = name.strip()
                if any(s['name'] == name for s in servers):
                    msg = QMessageBox(self)
                    msg.setIcon(QMessageBox.Icon.Warning)
                    msg.setWindowTitle(tr_ui("dlg_warning_title"))
                    msg.setText(tr_ui("dlg_server_exists"))
                    apply_dark_title_bar(msg)
                    msg.exec()
                    return
                servers[idx]['name'] = name
                self.config['pacs_current_server_name'] = name
                self.populate_server_combo()

