# -*- coding: utf-8 -*-
"""UI & Appearance Settings Tab."""

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QLabel, 
                             QFormLayout, QSpinBox, QComboBox, QFrame)

from ui.toggle_switch import ToggleSwitch
from ui.settings_tabs.settings_utils import LanguageSwitch
from core.locale_utils import tr_ui


def build_ui_tab(dialog):
    ui_widget = QWidget()
    ui_layout = QVBoxLayout(ui_widget)
    ui_form = QFormLayout()
    
    # Язык интерфейса
    dialog.interface_lang_switch = LanguageSwitch(dialog, command=dialog.on_interface_lang_changed, current_lang=dialog.config.get('interface_lang', 'en'))
    dialog.lbl_interface_lang = QLabel()
    ui_form.addRow(dialog.lbl_interface_lang, dialog.interface_lang_switch)
    
    # Язык лога
    dialog.log_lang_switch = LanguageSwitch(dialog, command=dialog.on_log_lang_changed, current_lang=dialog.config.get('log_lang', 'en'))
    dialog.lbl_log_lang = QLabel()
    ui_form.addRow(dialog.lbl_log_lang, dialog.log_lang_switch)

    # Разделитель под языками
    lang_line = QFrame()
    lang_line.setFrameShape(QFrame.Shape.HLine)
    lang_line.setFrameShadow(QFrame.Shadow.Sunken)
    lang_line.setStyleSheet("background-color: #2d2d2d; margin-top: 10px; margin-bottom: 10px;")
    ui_form.addRow(lang_line)

    # Patient Font Size
    dialog.patient_font_spin = QSpinBox()
    dialog.patient_font_spin.setRange(8, 36)
    dialog.patient_font_spin.setValue(int(dialog.config.get('patient_font_size', 16)))
    dialog.lbl_patient_font = QLabel()
    ui_form.addRow(dialog.lbl_patient_font, dialog.patient_font_spin)
    
    # Patient Font Weight
    dialog.patient_weight_combo = QComboBox()
    dialog.patient_weight_combo.addItems(["Regular", "Semibold", "Bold"])
    dialog.patient_weight_combo.setCurrentText(dialog.config.get('patient_weight', 'Semibold'))
    dialog.lbl_patient_weight = QLabel()
    ui_form.addRow(dialog.lbl_patient_weight, dialog.patient_weight_combo)
    
    # Font size (logs)
    dialog.font_size_spin = QSpinBox()
    dialog.font_size_spin.setRange(8, 24)
    dialog.font_size_spin.setValue(int(dialog.config.get('log_font_size', 12)))
    dialog.lbl_log_font = QLabel()
    ui_form.addRow(dialog.lbl_log_font, dialog.font_size_spin)
    
    # Разделитель
    ui_line = QFrame()
    ui_line.setFrameShape(QFrame.Shape.HLine)
    ui_line.setFrameShadow(QFrame.Shadow.Sunken)
    ui_line.setStyleSheet("background-color: #2d2d2d; margin-top: 10px; margin-bottom: 10px;")
    ui_form.addRow(ui_line)
    
    # Основной свич подсветки
    dialog.highlighting_cb = ToggleSwitch()
    dialog.highlighting_cb.setChecked(dialog.config.get('highlighting_enabled', 'False').lower() == 'true')
    dialog.lbl_highlighting = QLabel()
    ui_form.addRow(dialog.lbl_highlighting, dialog.highlighting_cb)
    
    dialog.lbl_highlight_new = QLabel()
    dialog.lbl_highlight_new.setStyleSheet("QLabel { padding-left: 30px; }")
    dialog.highlight_new_cb = ToggleSwitch()
    dialog.highlight_new_cb.setChecked(dialog.config.get('highlight_new_enabled', 'False').lower() == 'true')
    ui_form.addRow(dialog.lbl_highlight_new, dialog.highlight_new_cb)
    
    dialog.lbl_highlight_today = QLabel()
    dialog.lbl_highlight_today.setStyleSheet("QLabel { padding-left: 30px; }")
    dialog.highlight_today_cb = ToggleSwitch()
    dialog.highlight_today_cb.setChecked(dialog.config.get('highlight_today_enabled', 'False').lower() == 'true')
    ui_form.addRow(dialog.lbl_highlight_today, dialog.highlight_today_cb)
    
    dialog.lbl_highlight_no_str = QLabel()
    dialog.lbl_highlight_no_str.setStyleSheet("QLabel { padding-left: 30px; }")
    dialog.highlight_no_str_cb = ToggleSwitch()
    dialog.highlight_no_str_cb.setChecked(dialog.config.get('highlight_no_str_enabled', 'False').lower() == 'true')
    ui_form.addRow(dialog.lbl_highlight_no_str, dialog.highlight_no_str_cb)
    
    dialog.lbl_highlight_no_slices = QLabel()
    dialog.lbl_highlight_no_slices.setStyleSheet("QLabel { padding-left: 30px; }")
    dialog.highlight_no_slices_cb = ToggleSwitch()
    dialog.highlight_no_slices_cb.setChecked(dialog.config.get('highlight_no_slices_enabled', 'False').lower() == 'true')
    ui_form.addRow(dialog.lbl_highlight_no_slices, dialog.highlight_no_slices_cb)
    
    ui_layout.addLayout(ui_form)
    ui_layout.addStretch()
    
    return ui_widget


def retranslate_ui_tab(dialog):
    dialog.lbl_interface_lang.setText(tr_ui("settings_interface_lang"))
    dialog.lbl_log_lang.setText(tr_ui("settings_log_lang"))
    dialog.lbl_patient_font.setText(tr_ui("settings_patient_font"))
    dialog.lbl_patient_weight.setText(tr_ui("settings_patient_weight"))
    dialog.lbl_log_font.setText(tr_ui("settings_log_font"))
    dialog.lbl_highlighting.setText(tr_ui("settings_highlighting"))
    dialog.lbl_highlight_new.setText(tr_ui("settings_highlight_new"))
    dialog.lbl_highlight_today.setText(tr_ui("settings_highlight_today"))
    dialog.lbl_highlight_no_str.setText(tr_ui("settings_highlight_no_str"))
    dialog.lbl_highlight_no_slices.setText(tr_ui("settings_highlight_no_slices"))
    
    dialog.highlighting_cb.setToolTip(tr_ui("tooltip_switch_highlighting"))
    dialog.highlight_new_cb.setToolTip(tr_ui("tooltip_switch_highlight_new"))
    dialog.highlight_today_cb.setToolTip(tr_ui("tooltip_switch_highlight_today"))
    dialog.highlight_no_str_cb.setToolTip(tr_ui("tooltip_switch_highlight_no_str"))
    dialog.highlight_no_slices_cb.setToolTip(tr_ui("tooltip_switch_highlight_no_slices"))
