# -*- coding: utf-8 -*-
"""Settings Dialog for DICOM WatchDog."""

import os
import sys
import json
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QMessageBox,
                             QListWidget, QStackedWidget, QDialogButtonBox, QWidget)

from core.config_utils import get_config_path, get_app_data_dir, VERSION
from core.locale_utils import tr_ui, set_current_langs
from core.notifier import (
    get_installed_sapi_voices as get_system_voices,
    format_voice_name,
    get_perceptual_volume,
    preprocess_tts_text,
    speak_sapi_tts,
    _play_wav
)
from ui.updater import UpdateCheckWorker, is_newer_version, run_auto_update
from ui.settings_tabs.settings_utils import (
    find_matching_voice_index,
    are_onecore_voices_locked,
    apply_dark_title_bar,
    LanguageSwitch
)
from ui.settings_tabs.general_tab import build_general_tab, retranslate_general_tab
from ui.settings_tabs.archive_tab import build_archive_tab, retranslate_archive_tab
from ui.settings_tabs.ui_tab import build_ui_tab, retranslate_ui_tab
from ui.settings_tabs.notifications_tab import build_notifications_tab, retranslate_notifications_tab
from ui.settings_tabs.pacs_tab import build_pacs_tab, retranslate_pacs_tab, PacsPingWorker


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr_ui("settings_title"))
        self.setMinimumWidth(650)
        
        apply_dark_title_bar(self)

        self.config = self.load_config()
        self.initial_config = self.config.copy()
        self.init_ui()

    @staticmethod
    def load_config():
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
        
        # 1. Вкладка General & Scanning
        general_widget = build_general_tab(self)
        self.stacked_widget.addWidget(general_widget)
        
        # 2. Вкладка Archive
        archive_widget = build_archive_tab(self)
        self.stacked_widget.addWidget(archive_widget)
        
        # 3. Вкладка UI & Appearance
        ui_widget = build_ui_tab(self)
        self.stacked_widget.addWidget(ui_widget)
        
        # 4. Вкладка Notifications & Sound
        notifications_widget = build_notifications_tab(self)
        self.stacked_widget.addWidget(notifications_widget)
        
        # 5. Вкладка PACS
        pacs_widget = build_pacs_tab(self)
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
        save_btn = self.button_box.button(QDialogButtonBox.StandardButton.Save)
        if save_btn:
            save_btn.clicked.connect(self.accept_settings)
        cancel_btn = self.button_box.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_btn:
            cancel_btn.clicked.connect(self.reject)
        
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
        from PyQt6.QtWidgets import QFileDialog
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
        archive_tab_active = self.show_tab_archive_cb.isChecked()
        self.lbl_archive_dir.setEnabled(archive_tab_active)
        self.archive_edit.setEnabled(archive_tab_active)
        self.btn_archive_browse.setEnabled(archive_tab_active)
        self.lbl_archive_slice.setEnabled(archive_tab_active)
        self.archive_slice_spin.setEnabled(archive_tab_active)
        self.lbl_auto_archive_row.setEnabled(archive_tab_active)
        self.archive_enabled_cb.setEnabled(archive_tab_active)
        self.lbl_auto_cleanup_row.setEnabled(archive_tab_active)
        self.archive_cleanup_enabled_cb.setEnabled(archive_tab_active)

        archive_active = archive_tab_active and self.archive_enabled_cb.isChecked()
        self.archive_days_spin.setEnabled(archive_active)
        self.archive_label_through.setEnabled(archive_active)
        self.archive_label_days.setEnabled(archive_active)

        cleanup_active = archive_tab_active and self.archive_cleanup_enabled_cb.isChecked()
        self.archive_cleanup_days_spin.setEnabled(cleanup_active)
        self.cleanup_label_through.setEnabled(cleanup_active)
        self.cleanup_label_days.setEnabled(cleanup_active)

        pacs_tab_active = self.show_tab_pacs_cb.isChecked()
        self.lbl_pacs_server.setEnabled(pacs_tab_active)
        self.settings_server_combo.setEnabled(pacs_tab_active)
        self.add_server_btn.setEnabled(pacs_tab_active)
        self.del_server_btn.setEnabled(pacs_tab_active)
        self.rename_server_btn.setEnabled(pacs_tab_active)
        self.lbl_standby_interval.setEnabled(pacs_tab_active)
        self.pacs_scan_spin.setEnabled(pacs_tab_active)
        self.lbl_pacs_ip.setEnabled(pacs_tab_active)
        self.pacs_ip_edit.setEnabled(pacs_tab_active)
        self.lbl_port.setEnabled(pacs_tab_active)
        self.pacs_port_spin.setEnabled(pacs_tab_active)
        self.lbl_pacs_called_aet.setEnabled(pacs_tab_active)
        self.pacs_called_aet_edit.setEnabled(pacs_tab_active)
        self.lbl_pacs_calling_aet.setEnabled(pacs_tab_active)
        self.pacs_calling_aet_edit.setEnabled(pacs_tab_active)
        self.lbl_dicom_scp_port.setEnabled(pacs_tab_active)
        self.pacs_local_port_spin.setEnabled(pacs_tab_active)
        self.ping_btn.setEnabled(pacs_tab_active)

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
        try:
            # Save active inputs to current server structure
            self.save_current_fields_to_config()

            ct_text = self.ct_images_edit.text().strip()
            archive_text = self.archive_edit.text().strip()
            
            is_archive_enabled = self.show_tab_archive_cb.isChecked()
            is_pacs_enabled = self.show_tab_pacs_cb.isChecked()

            if is_archive_enabled and ct_text and archive_text:
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

            if is_archive_enabled and self.archive_enabled_cb.isChecked() and not archive_text:
                msg = QMessageBox(self)
                msg.setIcon(QMessageBox.Icon.Warning)
                msg.setWindowTitle(tr_ui("dlg_warning_title"))
                msg.setText(tr_ui("dlg_archive_empty_path"))
                apply_dark_title_bar(msg)
                msg.exec()
                return

            # Валидация AE Title только если PACS включен
            if is_pacs_enabled:
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
            self.initial_config = self.config.copy()
            self.accept()
        except Exception as e:
            print(f"Error in accept_settings: {e}")
            self.save_config()
            self.initial_config = self.config.copy()
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
        self.show_study_counts_cb.toggled.connect(self.on_setting_changed)
        self.show_tab_archive_cb.toggled.connect(self.update_fields_state)
        self.show_tab_archive_cb.toggled.connect(self.on_setting_changed)
        self.show_tab_pacs_cb.toggled.connect(self.update_fields_state)
        self.show_tab_pacs_cb.toggled.connect(self.on_setting_changed)
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
        self.ct_volume_slider.valueChanged.connect(self.on_setting_changed)
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
        self.pacs_volume_slider.valueChanged.connect(self.on_setting_changed)
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
        self.pacs_local_port_spin.valueChanged.connect(self.on_setting_changed)
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
        self.config['ct_notification_volume'] = self.ct_volume_slider.value()
        self.config['ct_voice_text'] = self.ct_voice_text_edit.text()
        self.config['pacs_notification_toast_enabled'] = 'True' if self.pacs_toast_cb.isChecked() else 'False'
        self.config['pacs_toast_duration'] = self.pacs_toast_duration_combo.currentData()
        self.config['pacs_toast_position'] = self.pacs_toast_position_combo.currentData()
        self.config['pacs_notification_sound_enabled'] = 'True' if self.pacs_sound_cb.isChecked() else 'False'
        self.config['pacs_notification_sound'] = self.pacs_sound_combo.currentData()
        self.config['pacs_notification_volume'] = self.pacs_volume_slider.value()
        self.config['pacs_voice_text'] = self.pacs_voice_text_edit.text()
        self.config['pacs_local_port'] = self.pacs_local_port_spin.value()
        self.config['check_updates_at_startup'] = 'on' if self.check_updates_cb.isChecked() else 'off'
        self.config['auto_update_is'] = self.config.get('auto_update_is', 'off')
        self.config['cleanup_structures_enabled'] = 'True' if self.cleanup_str_cb.isChecked() else 'False'
        self.config['show_study_counts'] = 'True' if self.show_study_counts_cb.isChecked() else 'False'
        self.config['show_tab_archive'] = 'True' if self.show_tab_archive_cb.isChecked() else 'False'
        self.config['show_tab_pacs'] = 'True' if self.show_tab_pacs_cb.isChecked() else 'False'
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
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(tr_ui("settings_sound_default"), "default")
        combo.addItem(tr_ui("settings_sound_chime"), "sound_chime")
        combo.addItem(tr_ui("settings_sound_ping"), "sound_ping")
        combo.addItem(tr_ui("settings_sound_pop"), "sound_pop")
        combo.addItem(tr_ui("settings_sound_soft"), "sound_soft")
        for voice in self.system_voices:
            combo.addItem(format_voice_name(voice), voice)
        idx = find_matching_voice_index(combo, current_val)
        if idx >= 0:
            combo.setCurrentIndex(idx)
        elif current_val and current_val not in ('default', 'sound_chime', 'sound_ping', 'sound_pop', 'sound_soft'):
            # Защита: сохраняем выбранный ранее голос, даже если он не вернулся из системы
            combo.addItem(format_voice_name(current_val), current_val)
            combo.setCurrentIndex(combo.count() - 1)
        else:
            combo.setCurrentIndex(0)
        combo.blockSignals(False)

    def play_sound_preview(self, combo):
        sound_setting = combo.currentData()
        if not sound_setting:
            return

        vol = 100
        if combo == getattr(self, 'ct_sound_combo', None):
            vol = self.ct_volume_slider.value()
        elif combo == getattr(self, 'pacs_sound_combo', None):
            vol = self.pacs_volume_slider.value()

        vol_float, vol_int = get_perceptual_volume(vol)
        if vol_int <= 0:
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
            wav_path = get_resource_path(sound_map[sound_setting])
            _play_wav(wav_path, volume=vol_float)
        elif sys.platform == "win32":
            lang = self.config.get('interface_lang', 'en')
            default_text = "Проверка звука" if lang == "ru" else "Sound check"
            custom_text = ""
            if combo == self.ct_sound_combo:
                custom_text = self.ct_voice_text_edit.text().strip()
            elif combo == self.pacs_sound_combo:
                custom_text = self.pacs_voice_text_edit.text().strip()
            raw_text = custom_text if custom_text else default_text
            text_to_speak = preprocess_tts_text(raw_text)
            speak_sapi_tts(sound_setting, text_to_speak, vol_int)

    def unlock_system_voices(self):
        import subprocess
        import tempfile

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
        self.retranslate_sidebar()
        
        retranslate_general_tab(self)
        retranslate_archive_tab(self)
        retranslate_ui_tab(self)
        retranslate_notifications_tab(self)
        retranslate_pacs_tab(self)
        
        # Standard buttons Save/Cancel
        save_btn = self.button_box.button(QDialogButtonBox.StandardButton.Save)
        if save_btn:
            save_btn.setText(tr_ui("btn_save"))
            save_btn.setToolTip(tr_ui("tooltip_settings_save"))
        cancel_btn = self.button_box.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_btn:
            cancel_btn.setText(tr_ui("btn_cancel"))
            cancel_btn.setToolTip(tr_ui("tooltip_settings_cancel"))

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
            
        if is_newer_version(VERSION, latest_version):
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
