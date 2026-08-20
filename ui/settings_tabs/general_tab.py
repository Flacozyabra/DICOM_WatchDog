# -*- coding: utf-8 -*-
"""General & Scanning Settings Tab."""

import os
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                             QLineEdit, QPushButton, QFormLayout, 
                             QComboBox, QFrame)

from ui.toggle_switch import ToggleSwitch
from core.config_utils import get_app_data_dir
from core.locale_utils import tr_ui


def build_general_tab(dialog):
    general_widget = QWidget()
    general_layout = QVBoxLayout(general_widget)
    general_form = QFormLayout()
    
    # CT Images Dir
    dialog.ct_images_edit = QLineEdit(dialog.config.get('ct_images_dir', ''))
    dialog.btn_ct_images_browse = QPushButton()
    dialog.btn_ct_images_browse.clicked.connect(lambda: dialog.browse_folder(dialog.ct_images_edit, tr_ui("settings_ct_images_folder").rstrip(":")))
    h_layout_ct = QHBoxLayout()
    h_layout_ct.addWidget(dialog.ct_images_edit)
    h_layout_ct.addWidget(dialog.btn_ct_images_browse)
    dialog.lbl_ct_folder = QLabel()
    general_form.addRow(dialog.lbl_ct_folder, h_layout_ct)

    # App Settings Dir
    dialog.app_data_edit = QLineEdit(get_app_data_dir())
    dialog.app_data_edit.setReadOnly(True)
    dialog.app_data_edit.setStyleSheet(
        "QLineEdit { background-color: #1e1e1e; color: #888888; border: 1px solid #2d2d2d; padding: 4px; border-radius: 4px; }"
    )
    dialog.btn_app_data_open = QPushButton()
    dialog.btn_app_data_open.clicked.connect(dialog.open_app_data_folder)
    h_layout_app = QHBoxLayout()
    h_layout_app.addWidget(dialog.app_data_edit)
    h_layout_app.addWidget(dialog.btn_app_data_open)
    dialog.lbl_settings_folder = QLabel()
    general_form.addRow(dialog.lbl_settings_folder, h_layout_app)

    # Разделитель
    line_ct = QFrame()
    line_ct.setFrameShape(QFrame.Shape.HLine)
    line_ct.setFrameShadow(QFrame.Shadow.Sunken)
    line_ct.setStyleSheet("background-color: #2d2d2d; margin-top: 10px; margin-bottom: 10px;")
    general_form.addRow(line_ct)

    # Автоудаление дубликатов структур
    dialog.cleanup_str_cb = ToggleSwitch()
    dialog.cleanup_str_cb.setChecked(dialog.config.get('cleanup_structures_enabled', 'False').lower() == 'true')
    dialog.lbl_cleanup_str = QLabel()
    general_form.addRow(dialog.lbl_cleanup_str, dialog.cleanup_str_cb)

    # Отображение количества исследований на вкладках
    dialog.show_study_counts_cb = ToggleSwitch()
    dialog.show_study_counts_cb.setChecked(dialog.config.get('show_study_counts', 'True').lower() == 'true')
    dialog.lbl_show_study_counts = QLabel()
    general_form.addRow(dialog.lbl_show_study_counts, dialog.show_study_counts_cb)

    # Fix Patient ID
    dialog.fix_patient_id_cb = ToggleSwitch()
    dialog.fix_patient_id_cb.setChecked(dialog.config.get('fix_patient_id_enabled', 'False').lower() == 'true')
    dialog.lbl_fix_id = QLabel()
    general_form.addRow(dialog.lbl_fix_id, dialog.fix_patient_id_cb)

    # ID prefixes field
    dialog.id_prefixes_edit = QLineEdit(dialog.config.get('id_prefixes', 'CT_'))
    dialog.id_prefixes_edit.setStyleSheet(
        "QLineEdit { background-color: #1e1e1e; color: #ffffff; border: 1px solid #2d2d2d; padding: 4px; border-radius: 4px; }"
        "QLineEdit:disabled { background-color: #141414; color: #808080; border: 1px solid #1a1a1a; }"
    )
    dialog.lbl_id_prefixes = QLabel()
    general_form.addRow(dialog.lbl_id_prefixes, dialog.id_prefixes_edit)

    # Rename Study Folder
    dialog.rename_study_folder_cb = ToggleSwitch()
    dialog.rename_study_folder_cb.setChecked(dialog.config.get('rename_study_folder_enabled', 'False').lower() == 'true')
    dialog.lbl_rename_folder = QLabel()
    general_form.addRow(dialog.lbl_rename_folder, dialog.rename_study_folder_cb)

    # Rename Study Folder Mode
    dialog.rename_study_folder_mode_combo = QComboBox()
    dialog.lbl_rename_folder_mode = QLabel()
    general_form.addRow(dialog.lbl_rename_folder_mode, dialog.rename_study_folder_mode_combo)

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
    
    dialog.check_updates_cb = ToggleSwitch()
    dialog.check_updates_cb.setChecked(dialog.config.get('check_updates_at_startup', 'on').lower() == 'on')
    
    dialog.btn_check_updates = QPushButton()
    dialog.btn_check_updates.setFixedHeight(30)
    dialog.btn_check_updates.setMinimumWidth(180)
    dialog.btn_check_updates.clicked.connect(dialog.manual_check_updates)
    
    updates_layout.addWidget(dialog.check_updates_cb)
    updates_layout.addStretch()
    updates_layout.addWidget(dialog.btn_check_updates)
    general_form.addRow(updates_layout)
    
    general_layout.addLayout(general_form)
    general_layout.addStretch()
    
    return general_widget


def retranslate_general_tab(dialog):
    dialog.lbl_ct_folder.setText(tr_ui("settings_ct_images_folder"))
    dialog.lbl_settings_folder.setText(tr_ui("settings_settings_folder"))
    dialog.lbl_cleanup_str.setText(tr_ui("settings_cleanup_str"))
    dialog.lbl_show_study_counts.setText(tr_ui("settings_show_study_counts"))
    dialog.lbl_show_study_counts.setToolTip(tr_ui("tooltip_show_study_counts"))
    dialog.show_study_counts_cb.setToolTip(tr_ui("tooltip_show_study_counts"))
    dialog.lbl_fix_id.setText(tr_ui("settings_fix_id_label"))
    dialog.lbl_id_prefixes.setText(tr_ui("settings_id_prefixes_label"))
    dialog.id_prefixes_edit.setPlaceholderText(tr_ui("settings_id_prefixes_placeholder"))
    dialog.lbl_rename_folder.setText(tr_ui("settings_rename_folder_label"))
    dialog.lbl_rename_folder_mode.setText(tr_ui("settings_rename_folder_mode_label"))
    
    # Populate rename folder mode combo
    dialog.rename_study_folder_mode_combo.blockSignals(True)
    current_idx = dialog.rename_study_folder_mode_combo.currentIndex()
    if current_idx < 0:
        current_mode = dialog.config.get('rename_study_folder_mode', 'id')
        mode_map = {'id': 0, 'name': 1, 'name_id': 2, 'id_name': 3}
        current_idx = mode_map.get(current_mode, 0)
    dialog.rename_study_folder_mode_combo.clear()
    dialog.rename_study_folder_mode_combo.addItem(tr_ui("settings_rename_folder_mode_id"))
    dialog.rename_study_folder_mode_combo.addItem(tr_ui("settings_rename_folder_mode_name"))
    dialog.rename_study_folder_mode_combo.addItem(tr_ui("settings_rename_folder_mode_name_id"))
    dialog.rename_study_folder_mode_combo.addItem(tr_ui("settings_rename_folder_mode_id_name"))
    dialog.rename_study_folder_mode_combo.setCurrentIndex(current_idx)
    dialog.rename_study_folder_mode_combo.blockSignals(False)
    
    dialog.check_updates_cb.setText(tr_ui("settings_check_updates_toggle"))
    dialog.btn_check_updates.setText(tr_ui("settings_check_updates_btn"))
    
    dialog.btn_ct_images_browse.setText(tr_ui("settings_browse"))
    dialog.btn_app_data_open.setText(tr_ui("settings_open"))
    
    dialog.btn_ct_images_browse.setToolTip(tr_ui("tooltip_settings_ct_browse"))
    dialog.btn_app_data_open.setToolTip(tr_ui("tooltip_settings_app_data"))
    dialog.btn_check_updates.setToolTip(tr_ui("tooltip_settings_check_updates"))
    dialog.cleanup_str_cb.setToolTip(tr_ui("tooltip_switch_cleanup_str"))
    dialog.fix_patient_id_cb.setToolTip(tr_ui("tooltip_switch_fix_id"))
    dialog.rename_study_folder_cb.setToolTip(tr_ui("tooltip_switch_rename_folder"))
    dialog.check_updates_cb.setToolTip(tr_ui("tooltip_switch_check_updates"))
