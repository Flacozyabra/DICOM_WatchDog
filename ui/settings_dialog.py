import os
import sys
import json
from PyQt6.QtCore import Qt, QRect, QPoint, QPropertyAnimation, pyqtProperty, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QBrush, QPen
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                             QLineEdit, QPushButton, QFileDialog, QFormLayout, 
                             QSpinBox, QCheckBox, QDialogButtonBox, QMessageBox,
                             QComboBox, QListWidget, QStackedWidget, QWidget, QFrame)

from ui.toggle_switch import ToggleSwitch


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
            'cleanup_structures_enabled': 'True',
            'fix_patient_id_enabled': 'True',
            'id_prefixes': 'CT_',
            'client_dir': '',
            'archive_slice': 0,
            'x': 1000,
            'y': 600,
            'dx': 350,
            'dy': 100,
            'log_font_size': 12,
            'notification_is': 'on',
            'icon_path': '',
            'pacs_scan_time': 10000,
            'auto_update_is': 'on',
            'pacs_notification_is': 'off',
            'patient_font_size': 14,
            'patient_weight': 'Regular',
            'archive_enabled': 'True',
            'archive_days': 3,
            'archive_cleanup_enabled': 'False',
            'archive_cleanup_days': 30,
            'pacs_ip': '127.0.0.1',
            'pacs_port': 11112,
            'pacs_called_aet': 'ANY-SCP',
            'pacs_calling_aet': 'ECHOSCU',
            'tables_state': {}
        }
        
        # 1. Проверяем config.json
        if os.path.exists("config.json"):
            try:
                with open("config.json", "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    config.update(loaded)
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
                
                # Сохраняем в config.json и бэкапим config.txt
                with open("config.json", "w", encoding="utf-8") as f_json:
                    json.dump(config, f_json, ensure_ascii=False, indent=4)
                    
                if os.path.exists("config.txt.bak"):
                    os.remove("config.txt.bak")
                os.rename("config.txt", "config.txt.bak")
                
            except Exception as e:
                print(f"Error migrating config.txt: {e}")
                
        return config

    def save_config(self):
        try:
            with open("config.json", "w", encoding="utf-8") as f:
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
        self.cleanup_str_cb.setChecked(self.config.get('cleanup_structures_enabled', 'True').lower() == 'true')
        self.cleanup_str_cb.setToolTip("Удаляются старые файлы структур и остается только последний файл.")
        general_form.addRow("Автоудаление дубликатов структур:", self.cleanup_str_cb)

        # Исправление ID
        self.fix_patient_id_cb = ToggleSwitch()
        self.fix_patient_id_cb.setChecked(self.config.get('fix_patient_id_enabled', 'True').lower() == 'true')
        general_form.addRow("Исправление ID:", self.fix_patient_id_cb)

        # Поле ввода префиксов
        self.id_prefixes_edit = QLineEdit(self.config.get('id_prefixes', 'CT_'))
        self.id_prefixes_edit.setPlaceholderText("Например: CT_, PT_")
        self.id_prefixes_edit.setStyleSheet(
            "QLineEdit { background-color: #1e1e1e; color: #ffffff; border: 1px solid #2d2d2d; padding: 4px; border-radius: 4px; }"
            "QLineEdit:disabled { background-color: #141414; color: #808080; border: 1px solid #1a1a1a; }"
        )
        general_form.addRow("Префиксы для удаления:", self.id_prefixes_edit)
        
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
        
        ui_layout.addLayout(ui_form)
        ui_layout.addStretch()
        self.stacked_widget.addWidget(ui_widget)
        
        # 4. Вкладка PACS
        pacs_widget = QWidget()
        pacs_layout = QVBoxLayout(pacs_widget)
        pacs_layout.setSpacing(12)
        
        pacs_form = QFormLayout()
        pacs_form.setContentsMargins(0, 0, 0, 0)
        
        # PACS Scan Interval (sec)
        self.pacs_scan_spin = QSpinBox()
        self.pacs_scan_spin.setRange(1, 300)
        self.pacs_scan_spin.setValue(self.config['pacs_scan_time'] // 1000)
        pacs_form.addRow("Интервал сканирования PACS (сек):", self.pacs_scan_spin)

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
        pacs_form.addRow("AET Remote:", self.pacs_called_aet_edit)

        # AET Local (метка слева, поле ввода справа)
        self.pacs_calling_aet_edit = QLineEdit(self.config.get('pacs_calling_aet', 'ECHOSCU'))
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

    def update_fields_state(self):
        self.archive_days_spin.setEnabled(self.archive_enabled_cb.isChecked())
        self.archive_cleanup_days_spin.setEnabled(self.archive_cleanup_enabled_cb.isChecked())
        self.id_prefixes_edit.setEnabled(self.fix_patient_id_cb.isChecked())

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
        self.pacs_scan_spin.valueChanged.connect(self.on_setting_changed)
        self.archive_slice_spin.valueChanged.connect(self.on_setting_changed)
        self.font_size_spin.valueChanged.connect(self.on_setting_changed)
        self.patient_font_spin.valueChanged.connect(self.on_setting_changed)
        self.patient_weight_combo.currentTextChanged.connect(self.on_setting_changed)
        self.notify_cb.toggled.connect(self.on_setting_changed)
        self.pacs_notify_cb.toggled.connect(self.on_setting_changed)
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

    def on_setting_changed(self):
        # Обновляем текущую конфигурацию
        self.config['archive_dir'] = self.archive_edit.text()
        self.config['pacs_scan_time'] = self.pacs_scan_spin.value() * 1000
        self.config['archive_slice'] = self.archive_slice_spin.value()
        self.config['log_font_size'] = self.font_size_spin.value()
        self.config['patient_font_size'] = self.patient_font_spin.value()
        self.config['patient_weight'] = self.patient_weight_combo.currentText()
        self.config['notification_is'] = 'on' if self.notify_cb.isChecked() else 'off'
        self.config['pacs_notification_is'] = 'on' if self.pacs_notify_cb.isChecked() else 'off'
        self.config['auto_update_is'] = 'on'
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
