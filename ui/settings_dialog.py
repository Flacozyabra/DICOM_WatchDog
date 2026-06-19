import os
import sys
from PyQt6.QtCore import Qt, QRect, QPoint, QPropertyAnimation, pyqtProperty
from PyQt6.QtGui import QColor, QFont, QPainter, QBrush, QPen
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                             QLineEdit, QPushButton, QFileDialog, QFormLayout, 
                             QSpinBox, QCheckBox, QDialogButtonBox, QMessageBox,
                             QComboBox, QListWidget, QStackedWidget, QWidget)

from ui.toggle_switch import ToggleSwitch


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
            'client_dir': '',
            'archive_slice': 2,
            'x': 1000,
            'y': 600,
            'dx': 350,
            'dy': 100,
            'log_font_size': 12,
            'folder_scan_time': 10000,
            'notification_is': 'on',
            'icon_path': '',
            'pacs_scan_time': 10000,
            'auto_update_is': 'on',
            'patient_font_size': 14,
            'patient_weight': 'Regular',
            'archive_enabled': 'True',
            'archive_days': 3,
            'archive_cleanup_enabled': 'False',
            'archive_cleanup_days': 30
        }
        if os.path.exists("config.txt"):
            try:
                with open("config.txt", "r", encoding="utf-8") as f:
                    lines = f.readlines()
                    if len(lines) > 0: config['ct_images_dir'] = lines[0].strip()
                    if len(lines) > 1: config['archive_dir'] = lines[1].strip()
                    if len(lines) > 4: config['fix_switch_value'] = lines[4].strip()
                    if len(lines) > 7: config['client_dir'] = lines[7].strip()
                    if len(lines) > 10: config['archive_slice'] = int(lines[10].strip() or "2")
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
            except Exception as e:
                print(f"Error loading config.txt: {e}")
        return config

    def save_config(self):
        lines = ["" for _ in range(53)]
        lines[0] = f"{self.config['ct_images_dir']}\n"
        lines[1] = f"{self.config['archive_dir']}\n"
        lines[2] = "\n"
        lines[3] = "fix_switch_value:\n"
        lines[4] = f"{self.config['fix_switch_value']}\n"
        lines[5] = "\n"
        lines[6] = "client_dir:\n"
        lines[7] = f"{self.config['client_dir']}\n"
        lines[8] = "\n"
        lines[9] = "archive_slice:\n"
        lines[10] = f"{self.config['archive_slice']}\n"
        lines[11] = "\n"
        lines[12] = "window_config(x, y, dx, dy):\n"
        lines[13] = f"{self.config['x']}\n"
        lines[14] = f"{self.config['y']}\n"
        lines[15] = f"{self.config['dx']}\n"
        lines[16] = f"{self.config['dy']}\n"
        lines[17] = "\n"
        lines[18] = "log_font_size:\n"
        lines[19] = f"{self.config['log_font_size']}\n"
        lines[20] = "\n"
        lines[21] = "folder_scan_time(msec):\n"
        lines[22] = f"{self.config['folder_scan_time']}\n"
        lines[23] = "\n"
        lines[24] = "notification_is:\n"
        lines[25] = f"{self.config['notification_is']}\n"
        lines[26] = "\n"
        lines[27] = "icon_path:\n"
        lines[28] = f"{self.config['icon_path']}\n"
        lines[29] = "\n"
        lines[30] = "pacs_scan_time(msec):\n"
        lines[31] = f"{self.config['pacs_scan_time']}\n"
        lines[32] = "\n"
        lines[33] = "auto_update_is:\n"
        lines[34] = f"{self.config['auto_update_is']}\n"
        lines[35] = "\n"
        lines[36] = "patient_font_size:\n"
        lines[37] = f"{self.config['patient_font_size']}\n"
        lines[38] = "\n"
        lines[39] = "patient_weight:\n"
        lines[40] = f"{self.config.get('patient_weight', 'Regular')}\n"
        lines[41] = "\n"
        lines[42] = "archive_enabled:\n"
        lines[43] = f"{self.config.get('archive_enabled', 'True')}\n"
        lines[44] = "\n"
        lines[45] = "archive_days:\n"
        lines[46] = f"{self.config.get('archive_days', 3)}\n"
        lines[47] = "\n"
        lines[48] = "archive_cleanup_enabled:\n"
        lines[49] = f"{self.config.get('archive_cleanup_enabled', 'False')}\n"
        lines[50] = "\n"
        lines[51] = "archive_cleanup_days:\n"
        lines[52] = f"{self.config.get('archive_cleanup_days', 30)}\n"

        try:
            with open("config.txt", "w", encoding="utf-8") as f:
                f.writelines(lines)
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
        
        # Folder Scan Interval (sec)
        self.folder_scan_spin = QSpinBox()
        self.folder_scan_spin.setRange(1, 300)
        self.folder_scan_spin.setValue(self.config['folder_scan_time'] // 1000)
        general_form.addRow("Интервал сканирования папок (сек):", self.folder_scan_spin)
        
        # Auto Update
        self.auto_update_cb = ToggleSwitch()
        self.auto_update_cb.setChecked(self.config.get('auto_update_is', 'on').lower() == 'on')
        general_form.addRow("Автообновление (Auto update):", self.auto_update_cb)
        
        # Fix Switch value
        self.fix_cb = ToggleSwitch()
        self.fix_cb.setChecked(self.config['fix_switch_value'].lower() == 'true')
        general_form.addRow("Разрешить Fix Files (исправление):", self.fix_cb)
        
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
        self.archive_slice_spin.setRange(1, 1000)
        self.archive_slice_spin.setValue(self.config['archive_slice'])
        archive_form.addRow("Лимит строк архива:", self.archive_slice_spin)

        # Archive Enabled (Switch)
        self.archive_enabled_cb = ToggleSwitch()
        self.archive_enabled_cb.setChecked(self.config.get('archive_enabled', 'True').lower() == 'true')
        archive_form.addRow("Автоматическое архивирование:", self.archive_enabled_cb)

        # Archive Days
        self.archive_days_spin = QSpinBox()
        self.archive_days_spin.setRange(1, 365)
        self.archive_days_spin.setValue(int(self.config.get('archive_days', 3)))
        archive_form.addRow("Переносить в архив через (дней):", self.archive_days_spin)

        # Archive Cleanup Enabled (Switch)
        self.archive_cleanup_enabled_cb = ToggleSwitch()
        self.archive_cleanup_enabled_cb.setChecked(self.config.get('archive_cleanup_enabled', 'False').lower() == 'true')
        archive_form.addRow("Автоочистка архива:", self.archive_cleanup_enabled_cb)

        # Archive Cleanup Days
        self.archive_cleanup_days_spin = QSpinBox()
        self.archive_cleanup_days_spin.setRange(1, 365)
        self.archive_cleanup_days_spin.setValue(int(self.config.get('archive_cleanup_days', 30)))
        archive_form.addRow("Удалять из архива через (дней):", self.archive_cleanup_days_spin)
        
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
        self.patient_font_spin.setValue(self.config.get('patient_font_size', 14))
        ui_form.addRow("Размер шрифта пациентов:", self.patient_font_spin)
        
        # Patient Font Weight
        self.patient_weight_combo = QComboBox()
        self.patient_weight_combo.addItems(["Regular", "Semibold", "Bold"])
        self.patient_weight_combo.setCurrentText(self.config.get('patient_weight', 'Regular'))
        ui_form.addRow("Толщина шрифта пациентов:", self.patient_weight_combo)
        
        # Font size (logs)
        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(8, 24)
        self.font_size_spin.setValue(self.config['log_font_size'])
        ui_form.addRow("Размер шрифта логов:", self.font_size_spin)
        
        # Notifications
        self.notify_cb = ToggleSwitch()
        self.notify_cb.setChecked(self.config['notification_is'].lower() == 'on')
        ui_form.addRow("Включить уведомления:", self.notify_cb)
        
        ui_layout.addLayout(ui_form)
        ui_layout.addStretch()
        self.stacked_widget.addWidget(ui_widget)
        
        # 4. Вкладка PACS
        pacs_widget = QWidget()
        pacs_layout = QVBoxLayout(pacs_widget)
        pacs_form = QFormLayout()
        
        # PACS Scan Interval (sec)
        self.pacs_scan_spin = QSpinBox()
        self.pacs_scan_spin.setRange(1, 300)
        self.pacs_scan_spin.setValue(self.config['pacs_scan_time'] // 1000)
        pacs_form.addRow("Интервал сканирования PACS (сек):", self.pacs_scan_spin)
        
        pacs_layout.addLayout(pacs_form)
        pacs_layout.addStretch()
        self.stacked_widget.addWidget(pacs_widget)
        
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
        
        # Подключаем слежение за состоянием полей архива
        self.archive_enabled_cb.toggled.connect(self.update_archive_fields_state)
        self.archive_cleanup_enabled_cb.toggled.connect(self.update_archive_fields_state)
        self.update_archive_fields_state()

        self.setup_dynamic_updates()

    def browse_folder(self, line_edit, title):
        dir_path = QFileDialog.getExistingDirectory(self, title, line_edit.text())
        if dir_path:
            line_edit.setText(os.path.normpath(dir_path))

    def update_archive_fields_state(self):
        self.archive_days_spin.setEnabled(self.archive_enabled_cb.isChecked())
        self.archive_cleanup_days_spin.setEnabled(self.archive_cleanup_enabled_cb.isChecked())

    def accept_settings(self):
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
        self.folder_scan_spin.valueChanged.connect(self.on_setting_changed)
        self.pacs_scan_spin.valueChanged.connect(self.on_setting_changed)
        self.archive_slice_spin.valueChanged.connect(self.on_setting_changed)
        self.font_size_spin.valueChanged.connect(self.on_setting_changed)
        self.patient_font_spin.valueChanged.connect(self.on_setting_changed)
        self.patient_weight_combo.currentTextChanged.connect(self.on_setting_changed)
        self.notify_cb.toggled.connect(self.on_setting_changed)
        self.auto_update_cb.toggled.connect(self.on_setting_changed)
        self.fix_cb.toggled.connect(self.on_setting_changed)
        self.archive_edit.textChanged.connect(self.on_setting_changed)
        self.archive_enabled_cb.toggled.connect(self.on_setting_changed)
        self.archive_days_spin.valueChanged.connect(self.on_setting_changed)
        self.archive_cleanup_enabled_cb.toggled.connect(self.on_setting_changed)
        self.archive_cleanup_days_spin.valueChanged.connect(self.on_setting_changed)

    def on_setting_changed(self):
        # Обновляем текущую конфигурацию
        self.config['archive_dir'] = self.archive_edit.text()
        self.config['folder_scan_time'] = self.folder_scan_spin.value() * 1000
        self.config['pacs_scan_time'] = self.pacs_scan_spin.value() * 1000
        self.config['archive_slice'] = self.archive_slice_spin.value()
        self.config['log_font_size'] = self.font_size_spin.value()
        self.config['patient_font_size'] = self.patient_font_spin.value()
        self.config['patient_weight'] = self.patient_weight_combo.currentText()
        self.config['notification_is'] = 'on' if self.notify_cb.isChecked() else 'off'
        self.config['auto_update_is'] = 'on' if self.auto_update_cb.isChecked() else 'off'
        self.config['fix_switch_value'] = 'True' if self.fix_cb.isChecked() else 'False'
        self.config['archive_enabled'] = 'True' if self.archive_enabled_cb.isChecked() else 'False'
        self.config['archive_days'] = self.archive_days_spin.value()
        self.config['archive_cleanup_enabled'] = 'True' if self.archive_cleanup_enabled_cb.isChecked() else 'False'
        self.config['archive_cleanup_days'] = self.archive_cleanup_days_spin.value()

        # Применяем настройки на лету в главном окне
        from ui.main_window import MainWindow
        if MainWindow.instance:
            MainWindow.instance.apply_settings_dynamic(self.config)
