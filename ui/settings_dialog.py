import os
import sys
import json
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                             QLineEdit, QPushButton, QFileDialog, QFormLayout, 
                             QSpinBox, QDialogButtonBox, QMessageBox,
                             QComboBox, QListWidget, QStackedWidget, QWidget, QFrame)

from ui.toggle_switch import ToggleSwitch
from core.config_utils import get_config_path, get_app_data_dir


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


class UpdateCheckWorker(QThread):
    finished = pyqtSignal(str, str)

    def run(self):
        from core.config_utils import check_github_updates
        tag, url = check_github_updates()
        self.finished.emit(tag or "", url or "")


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройки")
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
            'notification_is': 'on',
            'icon_path': '',
            'pacs_scan_time': 10000,
            'auto_update_is': 'off',
            'check_updates_at_startup': 'on',
            'pacs_notification_is': 'off',
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
            'highlight_no_str_enabled': 'False'
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
                        'name': f"Сервер ({pacs_ip}:{pacs_port})",
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
                        'name': f"Сервер ({pacs_ip}:{pacs_port})",
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
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить конфигурацию: {e}")

    def init_ui(self):
        # Главный горизонтальный макет окна
        main_layout = QHBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # Левая часть: боковое меню
        self.sidebar = QListWidget()
        self.sidebar.setObjectName("settingsSidebar")
        self.sidebar.addItems(["General", "Archive", "UI Settings", "PACS"])
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
        ct_images_btn = QPushButton("Обзор...")
        ct_images_btn.clicked.connect(lambda: self.browse_folder(self.ct_images_edit, "Выберите папку КТ-изображений"))
        h_layout_ct = QHBoxLayout()
        h_layout_ct.addWidget(self.ct_images_edit)
        h_layout_ct.addWidget(ct_images_btn)
        general_form.addRow("Папка CT Images:", h_layout_ct)

        # App Settings Dir
        self.app_data_edit = QLineEdit(get_app_data_dir())
        self.app_data_edit.setReadOnly(True)
        self.app_data_edit.setStyleSheet(
            "QLineEdit { background-color: #1e1e1e; color: #888888; border: 1px solid #2d2d2d; padding: 4px; border-radius: 4px; }"
        )
        app_data_btn = QPushButton("Открыть")
        app_data_btn.clicked.connect(self.open_app_data_folder)
        h_layout_app = QHBoxLayout()
        h_layout_app.addWidget(self.app_data_edit)
        h_layout_app.addWidget(app_data_btn)
        general_form.addRow("Папка настроек:", h_layout_app)

        # Разделитель для КТ-папки
        line_ct = QFrame()
        line_ct.setFrameShape(QFrame.Shape.HLine)
        line_ct.setFrameShadow(QFrame.Shadow.Sunken)
        line_ct.setStyleSheet("background-color: #2d2d2d; margin-top: 5px; margin-bottom: 5px;")
        general_form.addRow(line_ct)
        
        # Notifications
        self.notify_cb = ToggleSwitch()
        self.notify_cb.setChecked(self.config.get('notification_is', 'on').lower() == 'on')
        general_form.addRow("Уведомления:", self.notify_cb)

        # PACS Notifications
        self.pacs_notify_cb = ToggleSwitch()
        self.pacs_notify_cb.setChecked(self.config.get('pacs_notification_is', 'off').lower() == 'on')
        general_form.addRow("Уведомления PACS:", self.pacs_notify_cb)
        
        # Разделитель
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        line.setStyleSheet("background-color: #2d2d2d; margin-top: 10px; margin-bottom: 10px;")
        general_form.addRow(line)

        # Автоудаление дубликатов структур
        self.cleanup_str_cb = ToggleSwitch()
        self.cleanup_str_cb.setChecked(self.config.get('cleanup_structures_enabled', 'False').lower() == 'true')
        self.cleanup_str_cb.setToolTip("Удаляются старые файлы структур и остается только последний файл.")
        general_form.addRow("Автоудаление дубликатов структур:", self.cleanup_str_cb)

        # Исправление ID
        self.fix_patient_id_cb = ToggleSwitch()
        self.fix_patient_id_cb.setChecked(self.config.get('fix_patient_id_enabled', 'False').lower() == 'true')
        general_form.addRow("Исправление ID:", self.fix_patient_id_cb)

        # Поле ввода префиксов
        self.id_prefixes_edit = QLineEdit(self.config.get('id_prefixes', 'CT_'))
        self.id_prefixes_edit.setPlaceholderText("Например: CT_, PT_")
        self.id_prefixes_edit.setStyleSheet(
            "QLineEdit { background-color: #1e1e1e; color: #ffffff; border: 1px solid #2d2d2d; padding: 4px; border-radius: 4px; }"
            "QLineEdit:disabled { background-color: #141414; color: #808080; border: 1px solid #1a1a1a; }"
        )
        general_form.addRow("Префиксы для удаления:", self.id_prefixes_edit)

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
        
        self.check_updates_cb = ToggleSwitch("Проверять обновления при запуске")
        self.check_updates_cb.setChecked(self.config.get('check_updates_at_startup', 'on').lower() == 'on')
        
        self.btn_check_updates = QPushButton("Проверить обновления")
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
        archive_btn = QPushButton("Обзор...")
        archive_btn.clicked.connect(lambda: self.browse_folder(self.archive_edit, "Выберите папку архива"))
        h_layout2 = QHBoxLayout()
        h_layout2.addWidget(self.archive_edit)
        h_layout2.addWidget(archive_btn)
        archive_form.addRow("Папка CT Archive:", h_layout2)
        
        # Archive Slice (Max visible rows)
        self.archive_slice_spin = QSpinBox()
        self.archive_slice_spin.setRange(0, 1000)
        self.archive_slice_spin.setValue(self.config['archive_slice'])
        self.archive_slice_spin.setToolTip("Максимальное количество отображаемых пациентов в архиве. Установите 0, чтобы показывать всех пациентов без ограничений.")
        archive_form.addRow("Лимит строк архива:", self.archive_slice_spin)

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

        self.archive_label_through = QLabel("через")
        self.archive_label_through.setStyleSheet(
            "QLabel { color: #aaaaaa; }"
            "QLabel:disabled { color: #444444; }"
        )
        self.archive_label_days = QLabel("дн.")
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

        archive_form.addRow("Автоматическое архивирование:", archive_row_layout)

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

        self.cleanup_label_through = QLabel("через")
        self.cleanup_label_through.setStyleSheet(
            "QLabel { color: #aaaaaa; }"
            "QLabel:disabled { color: #444444; }"
        )
        self.cleanup_label_days = QLabel("дн.")
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

        archive_form.addRow("Автоочистка архива:", cleanup_row_layout)
        
        archive_layout.addLayout(archive_form)
        archive_layout.addStretch()
        self.stacked_widget.addWidget(archive_widget)
        
        # 3. Вкладка UI Settings
        ui_widget = QWidget()
        ui_layout = QVBoxLayout(ui_widget)
        ui_form = QFormLayout()
        
        # Patient Font Size
        self.patient_font_spin = QSpinBox()
        self.patient_font_spin.setRange(8, 36)
        self.patient_font_spin.setValue(self.config.get('patient_font_size', 16))
        ui_form.addRow("Размер шрифта пациентов:", self.patient_font_spin)
        
        # Patient Font Weight
        self.patient_weight_combo = QComboBox()
        self.patient_weight_combo.addItems(["Regular", "Semibold", "Bold"])
        self.patient_weight_combo.setCurrentText(self.config.get('patient_weight', 'Semibold'))
        ui_form.addRow("Толщина шрифта списков:", self.patient_weight_combo)
        
        # Font size (logs)
        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(8, 24)
        self.font_size_spin.setValue(self.config['log_font_size'])
        ui_form.addRow("Размер шрифта логов:", self.font_size_spin)
        
        # Разделитель
        ui_line = QFrame()
        ui_line.setFrameShape(QFrame.Shape.HLine)
        ui_line.setFrameShadow(QFrame.Shadow.Sunken)
        ui_line.setStyleSheet("background-color: #2d2d2d; margin-top: 10px; margin-bottom: 10px;")
        ui_form.addRow(ui_line)
        
        # Основной свич подсветки
        self.highlighting_cb = ToggleSwitch()
        self.highlighting_cb.setChecked(self.config.get('highlighting_enabled', 'False').lower() == 'true')
        ui_form.addRow("Включить цветовую подсветку исследований:", self.highlighting_cb)
        
        self.lbl_highlight_new = QLabel("Выделять новые исследования:")
        self.lbl_highlight_new.setStyleSheet("QLabel { padding-left: 30px; }")
        self.highlight_new_cb = ToggleSwitch()
        self.highlight_new_cb.setChecked(self.config.get('highlight_new_enabled', 'False').lower() == 'true')
        ui_form.addRow(self.lbl_highlight_new, self.highlight_new_cb)
        
        self.lbl_highlight_today = QLabel("Выделять сегодняшние исследования:")
        self.lbl_highlight_today.setStyleSheet("QLabel { padding-left: 30px; }")
        self.highlight_today_cb = ToggleSwitch()
        self.highlight_today_cb.setChecked(self.config.get('highlight_today_enabled', 'False').lower() == 'true')
        ui_form.addRow(self.lbl_highlight_today, self.highlight_today_cb)
        
        self.lbl_highlight_no_str = QLabel("Выделять исследования без структур:")
        self.lbl_highlight_no_str.setStyleSheet("QLabel { padding-left: 30px; }")
        self.highlight_no_str_cb = ToggleSwitch()
        self.highlight_no_str_cb.setChecked(self.config.get('highlight_no_str_enabled', 'False').lower() == 'true')
        ui_form.addRow(self.lbl_highlight_no_str, self.highlight_no_str_cb)
        
        ui_layout.addLayout(ui_form)
        ui_layout.addStretch()
        self.stacked_widget.addWidget(ui_widget)
        
        # 4. Вкладка PACS
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
        
        pacs_form.addRow("PACS сервер:", server_select_layout)
        
        # PACS Scan Interval (sec)
        self.pacs_scan_spin = QSpinBox()
        self.pacs_scan_spin.setRange(1, 300)
        self.pacs_scan_spin.setValue(self.config['pacs_scan_time'] // 1000)
        pacs_form.addRow("Интервал автообновления в режиме ожидания:", self.pacs_scan_spin)

        # IP PACS и Port на одной строке
        ip_port_layout = QHBoxLayout()
        ip_port_layout.setSpacing(10)
        
        self.pacs_ip_edit = QLineEdit(self.config.get('pacs_ip', '127.0.0.1'))
        
        port_label = QLabel("Port:")
        self.pacs_port_spin = QSpinBox()
        self.pacs_port_spin.setRange(1, 65535)
        self.pacs_port_spin.setValue(int(self.config.get('pacs_port', 11112)))
        
        ip_port_layout.addWidget(self.pacs_ip_edit, stretch=1)
        ip_port_layout.addWidget(port_label)
        ip_port_layout.addWidget(self.pacs_port_spin)
        
        pacs_form.addRow("IP PACS:", ip_port_layout)

        # AET Remote (метка слева, поле ввода справа)
        self.pacs_called_aet_edit = QLineEdit(self.config.get('pacs_called_aet', 'ANY-SCP'))
        self.pacs_called_aet_edit.setMaxLength(16)
        pacs_form.addRow("AET Remote:", self.pacs_called_aet_edit)

        # AET Local (метка слева, поле ввода справа)
        self.pacs_calling_aet_edit = QLineEdit(self.config.get('pacs_calling_aet', 'ECHOSCU'))
        self.pacs_calling_aet_edit.setMaxLength(16)
        pacs_form.addRow("AET Local:", self.pacs_calling_aet_edit)
        
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
        
        # Dialog Buttons (OK / Cancel)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept_settings)
        buttons.rejected.connect(self.reject)
        
        # Контейнер для кнопок с правым отступом
        button_layout = QHBoxLayout()
        button_layout.setContentsMargins(0, 5, 15, 0)
        button_layout.addStretch()
        button_layout.addWidget(buttons)
        outer_layout.addLayout(button_layout)
        
        self.setLayout(outer_layout)
        
        # Подключаем слежение за состоянием полей архива и префиксов ID
        self.archive_enabled_cb.toggled.connect(self.update_fields_state)
        self.archive_cleanup_enabled_cb.toggled.connect(self.update_fields_state)
        self.fix_patient_id_cb.toggled.connect(self.update_fields_state)
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

        highlighting_active = self.highlighting_cb.isChecked()
        self.lbl_highlight_new.setEnabled(highlighting_active)
        self.highlight_new_cb.setEnabled(highlighting_active)
        self.lbl_highlight_today.setEnabled(highlighting_active)
        self.highlight_today_cb.setEnabled(highlighting_active)
        self.lbl_highlight_no_str.setEnabled(highlighting_active)
        self.highlight_no_str_cb.setEnabled(highlighting_active)

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
                msg.setWindowTitle("Ошибка")
                msg.setText("Папка CT Images и папка CT Archive не могут быть одной и той же папкой.")
                apply_dark_title_bar(msg)
                msg.exec()
                return

        if self.archive_enabled_cb.isChecked() and not archive_text:
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Icon.Warning)
            msg.setWindowTitle("Предупреждение")
            msg.setText("Включено автоархивирование, но не указан путь к папке архива.\nПожалуйста, укажите путь к архиву или отключите автоархивирование.")
            apply_dark_title_bar(msg)
            msg.exec()
            return

        # Валидация AE Title
        called_aet = self.pacs_called_aet_edit.text().strip()
        calling_aet = self.pacs_calling_aet_edit.text().strip()
        if len(called_aet) > 16 or len(calling_aet) > 16:
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Icon.Warning)
            msg.setWindowTitle("Ошибка")
            msg.setText(
                "AE Title не должен превышать 16 символов по стандарту DICOM.\n\n"
                f"AET Remote: \"{called_aet}\" ({len(called_aet)} симв.)\n"
                f"AET Local: \"{calling_aet}\" ({len(calling_aet)} симв.)\n\n"
                "Пожалуйста, исправьте значения."
            )
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
        self.pacs_scan_spin.valueChanged.connect(self.on_setting_changed)
        self.archive_slice_spin.valueChanged.connect(self.on_setting_changed)
        self.font_size_spin.valueChanged.connect(self.on_setting_changed)
        self.patient_font_spin.valueChanged.connect(self.on_setting_changed)
        self.patient_weight_combo.currentTextChanged.connect(self.on_setting_changed)
        self.notify_cb.toggled.connect(self.on_setting_changed)
        self.highlighting_cb.toggled.connect(self.on_highlighting_toggled)
        self.highlight_new_cb.toggled.connect(self.on_setting_changed)
        self.highlight_today_cb.toggled.connect(self.on_setting_changed)
        self.highlight_no_str_cb.toggled.connect(self.on_setting_changed)
        self.pacs_notify_cb.toggled.connect(self.on_setting_changed)
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
        
        self.highlight_new_cb.setChecked(checked)
        self.highlight_today_cb.setChecked(checked)
        self.highlight_no_str_cb.setChecked(checked)
        
        self.highlight_new_cb.blockSignals(False)
        self.highlight_today_cb.blockSignals(False)
        self.highlight_no_str_cb.blockSignals(False)
        
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
        self.config['notification_is'] = 'on' if self.notify_cb.isChecked() else 'off'
        self.config['pacs_notification_is'] = 'on' if self.pacs_notify_cb.isChecked() else 'off'
        self.config['check_updates_at_startup'] = 'on' if self.check_updates_cb.isChecked() else 'off'
        self.config['auto_update_is'] = self.config.get('auto_update_is', 'off')
        self.config['cleanup_structures_enabled'] = 'True' if self.cleanup_str_cb.isChecked() else 'False'
        self.config['fix_patient_id_enabled'] = 'True' if self.fix_patient_id_cb.isChecked() else 'False'
        self.config['id_prefixes'] = self.id_prefixes_edit.text()
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

        # Применяем настройки на лету в главном окне
        from ui.main_window import MainWindow
        if MainWindow.instance:
            MainWindow.instance.apply_settings_dynamic(self.config)

    def ping_pacs_action(self):
        pacs_ip = self.pacs_ip_edit.text().strip()
        pacs_port = self.pacs_port_spin.value()
        called_aet = self.pacs_called_aet_edit.text().strip()
        calling_aet = self.pacs_calling_aet_edit.text().strip()

        if not pacs_ip:
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Icon.Warning)
            msg.setWindowTitle("Ошибка")
            msg.setText("Пожалуйста, укажите IP-адрес PACS сервера.")
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
            msg.setWindowTitle("Успешно")
        else:
            msg.setIcon(QMessageBox.Icon.Warning)
            msg.setWindowTitle("Сбой")
        msg.setText(message)
        apply_dark_title_bar(msg)
        msg.exec()

    def manual_check_updates(self):
        self.btn_check_updates.setEnabled(False)
        self.btn_check_updates.setText("Проверка...")
        
        self.manual_update_worker = UpdateCheckWorker()
        self.manual_update_worker.finished.connect(self.on_manual_update_checked)
        self.manual_update_worker.start()

    def on_manual_update_checked(self, latest_version, html_url):
        self.btn_check_updates.setEnabled(True)
        self.btn_check_updates.setText("Проверить обновления")
        
        from core.config_utils import VERSION, is_newer_version
        if latest_version and is_newer_version(VERSION, latest_version):
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Icon.Information)
            msg.setWindowTitle("Доступно обновление")
            msg.setText(f"Доступна новая версия: {latest_version}.\n\nХотите перейти на страницу скачивания?")
            msg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            msg.setDefaultButton(QMessageBox.StandardButton.Yes)
            apply_dark_title_bar(msg)
            
            if msg.exec() == QMessageBox.StandardButton.Yes:
                from PyQt6.QtGui import QDesktopServices
                from PyQt6.QtCore import QUrl
                QDesktopServices.openUrl(QUrl(html_url))
        else:
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Icon.Information)
            msg.setWindowTitle("Обновление")
            msg.setText("У вас установлена актуальная версия приложения.")
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
        
        name, ok = QInputDialog.getText(self, "Новый сервер", "Введите имя PACS-сервера:")
        if ok and name.strip():
            name = name.strip()
            servers = self.config.get('pacs_servers', [])
            # Check for duplicate names
            if any(s['name'] == name for s in servers):
                QMessageBox.warning(self, "Предупреждение", "Сервер с таким именем уже существует.")
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
            QMessageBox.warning(self, "Предупреждение", "Нельзя удалить единственный сервер.")
            return
            
        idx = self.settings_server_combo.currentIndex()
        if 0 <= idx < len(servers):
            confirm = QMessageBox.question(
                self, "Удаление сервера", 
                f"Вы уверены, что хотите удалить PACS-сервер '{servers[idx]['name']}'?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if confirm == QMessageBox.StandardButton.Yes:
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
            name, ok = QInputDialog.getText(self, "Переименовать сервер", f"Введите новое имя для '{old_name}':", text=old_name)
            if ok and name.strip() and name.strip() != old_name:
                name = name.strip()
                if any(s['name'] == name for s in servers):
                    QMessageBox.warning(self, "Предупреждение", "Сервер с таким именем уже существует.")
                    return
                servers[idx]['name'] = name
                self.config['pacs_current_server_name'] = name
                self.populate_server_combo()

