# -*- coding: utf-8 -*-
"""Sound & Notifications Settings Tab."""

import sys
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                             QLineEdit, QPushButton, QFormLayout, 
                             QComboBox, QFrame, QSlider)

from ui.toggle_switch import ToggleSwitch
from ui.settings_tabs.settings_utils import (
    find_matching_voice_index, 
    are_onecore_voices_locked
)
from core.notifier import format_voice_name
from core.locale_utils import tr_ui


SLIDER_QSS = """
QSlider::groove:horizontal {
    border: 1px solid #3d3d3d;
    height: 6px;
    background: #1a1a1a;
    margin: 0px;
    border-radius: 3px;
}
QSlider::sub-page:horizontal {
    background: #1f538d;
    border-radius: 3px;
}
QSlider::add-page:horizontal {
    background: #2b2b2b;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    background: #3a8ee6;
    border: 1px solid #1f538d;
    width: 14px;
    height: 14px;
    margin: -4px 0;
    border-radius: 7px;
}
QSlider::handle:horizontal:hover {
    background: #52a5ff;
    border: 1px solid #3a8ee6;
}
QSlider::handle:horizontal:disabled {
    background: #444444;
    border: 1px solid #333333;
}
"""


def build_notifications_tab(dialog):
    notifications_widget = QWidget()
    notifications_layout = QVBoxLayout(notifications_widget)
    notifications_form = QFormLayout()

    # Глобальный мастер-свич "Оповещения"
    dialog.notifications_enabled_cb = ToggleSwitch()
    dialog.notifications_enabled_cb.setChecked(dialog.config.get('notifications_enabled', 'False').lower() == 'true')
    dialog.lbl_notifications_enabled = QLabel()
    notifications_form.addRow(dialog.lbl_notifications_enabled, dialog.notifications_enabled_cb)

    # Разделитель после мастер-свича
    line_master = QFrame()
    line_master.setFrameShape(QFrame.Shape.HLine)
    line_master.setFrameShadow(QFrame.Shadow.Sunken)
    line_master.setStyleSheet("background-color: #2d2d2d; margin-top: 10px; margin-bottom: 10px;")
    notifications_form.addRow(line_master)

    # РАЗДЕЛ: КТ-уведомления
    dialog.lbl_ct_section = QLabel()
    dialog.lbl_ct_section.setStyleSheet("font-weight: bold; font-size: 14px; color: #1f538d; margin-top: 5px; margin-bottom: 5px;")
    notifications_form.addRow(dialog.lbl_ct_section)

    # КТ Оповещения Windows
    dialog.ct_toast_cb = ToggleSwitch()
    dialog.ct_toast_cb.setChecked(dialog.config.get('ct_notification_toast_enabled', 'True').lower() == 'true')
    dialog.lbl_ct_toast = QLabel()
    notifications_form.addRow(dialog.lbl_ct_toast, dialog.ct_toast_cb)

    # КТ Длительность показа
    dialog.ct_toast_duration_combo = QComboBox()
    dialog.lbl_ct_toast_duration = QLabel()
    notifications_form.addRow(dialog.lbl_ct_toast_duration, dialog.ct_toast_duration_combo)

    # КТ Расположение на экране
    dialog.ct_toast_position_combo = QComboBox()
    dialog.lbl_ct_toast_position = QLabel()
    notifications_form.addRow(dialog.lbl_ct_toast_position, dialog.ct_toast_position_combo)

    # КТ Звуковые оповещения
    dialog.ct_sound_cb = ToggleSwitch()
    dialog.ct_sound_cb.setChecked(dialog.config.get('ct_notification_sound_enabled', 'False').lower() == 'true')
    dialog.lbl_ct_sound_enabled = QLabel()
    notifications_form.addRow(dialog.lbl_ct_sound_enabled, dialog.ct_sound_cb)

    # Селектор звука КТ
    dialog.ct_sound_combo = QComboBox()
    dialog.lbl_ct_sound = QLabel()
    notifications_form.addRow(dialog.lbl_ct_sound, dialog.ct_sound_combo)

    # Громкость уведомлений КТ
    dialog.ct_volume_slider = QSlider(Qt.Orientation.Horizontal)
    dialog.ct_volume_slider.setStyleSheet(SLIDER_QSS)
    dialog.ct_volume_slider.setRange(0, 100)
    ct_vol_val = int(dialog.config.get('ct_notification_volume', 100))
    dialog.ct_volume_slider.setValue(ct_vol_val)
    dialog.ct_volume_label_val = QLabel(f"{ct_vol_val}%")
    dialog.ct_volume_label_val.setFixedWidth(40)
    dialog.ct_volume_label_val.setStyleSheet("font-weight: bold; color: #3a8ee6;")
    ct_vol_layout = QHBoxLayout()
    ct_vol_layout.addWidget(dialog.ct_volume_slider)
    ct_vol_layout.addWidget(dialog.ct_volume_label_val)
    dialog.ct_volume_slider.valueChanged.connect(lambda v: dialog.ct_volume_label_val.setText(f"{v}%"))
    dialog.ct_volume_slider.sliderReleased.connect(lambda: dialog.play_sound_preview(dialog.ct_sound_combo))
    dialog.lbl_ct_volume = QLabel(tr_ui("settings_ct_volume_label"))
    notifications_form.addRow(dialog.lbl_ct_volume, ct_vol_layout)

    # Текст голосового оповещения КТ
    dialog.ct_voice_text_edit = QLineEdit(dialog.config.get('ct_voice_text', ''))
    dialog.lbl_ct_voice_text = QLabel()
    notifications_form.addRow(dialog.lbl_ct_voice_text, dialog.ct_voice_text_edit)

    # Заполняем ct_sound_combo
    dialog._populate_sound_combo(dialog.ct_sound_combo, dialog.config.get('ct_notification_sound', 'default'))
    dialog.ct_sound_combo.activated.connect(lambda: dialog.play_sound_preview(dialog.ct_sound_combo))

    # Разделитель после КТ
    line_notif = QFrame()
    line_notif.setFrameShape(QFrame.Shape.HLine)
    line_notif.setFrameShadow(QFrame.Shadow.Sunken)
    line_notif.setStyleSheet("background-color: #2d2d2d; margin-top: 10px; margin-bottom: 10px;")
    notifications_form.addRow(line_notif)

    # РАЗДЕЛ: PACS-уведомления
    dialog.lbl_pacs_section = QLabel()
    dialog.lbl_pacs_section.setStyleSheet("font-weight: bold; font-size: 14px; color: #1f538d; margin-top: 5px; margin-bottom: 5px;")
    notifications_form.addRow(dialog.lbl_pacs_section)

    # PACS Оповещения Windows
    dialog.pacs_toast_cb = ToggleSwitch()
    dialog.pacs_toast_cb.setChecked(dialog.config.get('pacs_notification_toast_enabled', 'False').lower() == 'true')
    dialog.lbl_pacs_toast = QLabel()
    notifications_form.addRow(dialog.lbl_pacs_toast, dialog.pacs_toast_cb)

    # PACS Длительность показа
    dialog.pacs_toast_duration_combo = QComboBox()
    dialog.lbl_pacs_toast_duration = QLabel()
    notifications_form.addRow(dialog.lbl_pacs_toast_duration, dialog.pacs_toast_duration_combo)

    # PACS Расположение на экране
    dialog.pacs_toast_position_combo = QComboBox()
    dialog.lbl_pacs_toast_position = QLabel()
    notifications_form.addRow(dialog.lbl_pacs_toast_position, dialog.pacs_toast_position_combo)

    # PACS Звуковые оповещения
    dialog.pacs_sound_cb = ToggleSwitch()
    dialog.pacs_sound_cb.setChecked(dialog.config.get('pacs_notification_sound_enabled', 'False').lower() == 'true')
    dialog.lbl_pacs_sound_enabled = QLabel()
    notifications_form.addRow(dialog.lbl_pacs_sound_enabled, dialog.pacs_sound_cb)

    # Селектор звука PACS
    dialog.pacs_sound_combo = QComboBox()
    dialog.lbl_pacs_sound = QLabel()
    notifications_form.addRow(dialog.lbl_pacs_sound, dialog.pacs_sound_combo)

    # Громкость уведомлений PACS
    dialog.pacs_volume_slider = QSlider(Qt.Orientation.Horizontal)
    dialog.pacs_volume_slider.setStyleSheet(SLIDER_QSS)
    dialog.pacs_volume_slider.setRange(0, 100)
    pacs_vol_val = int(dialog.config.get('pacs_notification_volume', 100))
    dialog.pacs_volume_slider.setValue(pacs_vol_val)
    dialog.pacs_volume_label_val = QLabel(f"{pacs_vol_val}%")
    dialog.pacs_volume_label_val.setFixedWidth(40)
    dialog.pacs_volume_label_val.setStyleSheet("font-weight: bold; color: #3a8ee6;")
    pacs_vol_layout = QHBoxLayout()
    pacs_vol_layout.addWidget(dialog.pacs_volume_slider)
    pacs_vol_layout.addWidget(dialog.pacs_volume_label_val)
    dialog.pacs_volume_slider.valueChanged.connect(lambda v: dialog.pacs_volume_label_val.setText(f"{v}%"))
    dialog.pacs_volume_slider.sliderReleased.connect(lambda: dialog.play_sound_preview(dialog.pacs_sound_combo))
    dialog.lbl_pacs_volume = QLabel(tr_ui("settings_pacs_volume_label"))
    notifications_form.addRow(dialog.lbl_pacs_volume, pacs_vol_layout)

    # Текст голосового оповещения PACS
    dialog.pacs_voice_text_edit = QLineEdit(dialog.config.get('pacs_voice_text', ''))
    dialog.lbl_pacs_voice_text = QLabel()
    notifications_form.addRow(dialog.lbl_pacs_voice_text, dialog.pacs_voice_text_edit)

    # Заполняем pacs_sound_combo
    dialog._populate_sound_combo(dialog.pacs_sound_combo, dialog.config.get('pacs_notification_sound', 'default'))
    dialog.pacs_sound_combo.activated.connect(lambda: dialog.play_sound_preview(dialog.pacs_sound_combo))

    # Разблокировка голосов Windows OneCore
    dialog.btn_unlock_voices = QPushButton()
    dialog.btn_unlock_voices.setFixedHeight(32)
    dialog.btn_unlock_voices.clicked.connect(dialog.unlock_system_voices)
    
    if are_onecore_voices_locked():
        notifications_form.addRow(dialog.btn_unlock_voices)
    else:
        dialog.btn_unlock_voices.setVisible(False)

    # Интерактивная логика связывания переключателей
    def update_notification_states():
        is_master_on = dialog.notifications_enabled_cb.isChecked()
        ct_toast_on = is_master_on and dialog.ct_toast_cb.isChecked()
        ct_sound_on = is_master_on and dialog.ct_sound_cb.isChecked()
        pacs_toast_on = is_master_on and dialog.pacs_toast_cb.isChecked()
        pacs_sound_on = is_master_on and dialog.pacs_sound_cb.isChecked()
        
        # Активируем/деактивируем КТ виджеты
        dialog.ct_toast_cb.setEnabled(is_master_on)
        dialog.lbl_ct_toast.setEnabled(is_master_on)
        dialog.ct_toast_duration_combo.setEnabled(ct_toast_on)
        dialog.lbl_ct_toast_duration.setEnabled(ct_toast_on)
        dialog.ct_toast_position_combo.setEnabled(ct_toast_on)
        dialog.lbl_ct_toast_position.setEnabled(ct_toast_on)

        dialog.ct_sound_cb.setEnabled(is_master_on)
        dialog.lbl_ct_sound_enabled.setEnabled(is_master_on)
        dialog.ct_sound_combo.setEnabled(ct_sound_on)
        dialog.lbl_ct_sound.setEnabled(ct_sound_on)
        dialog.ct_volume_slider.setEnabled(ct_sound_on)
        dialog.lbl_ct_volume.setEnabled(ct_sound_on)
        dialog.ct_volume_label_val.setEnabled(ct_sound_on)
        dialog.ct_voice_text_edit.setEnabled(ct_sound_on)
        dialog.lbl_ct_voice_text.setEnabled(ct_sound_on)

        # Активируем/деактивируем PACS виджеты
        dialog.pacs_toast_cb.setEnabled(is_master_on)
        dialog.lbl_pacs_toast.setEnabled(is_master_on)
        dialog.pacs_toast_duration_combo.setEnabled(pacs_toast_on)
        dialog.lbl_pacs_toast_duration.setEnabled(pacs_toast_on)
        dialog.pacs_toast_position_combo.setEnabled(pacs_toast_on)
        dialog.lbl_pacs_toast_position.setEnabled(pacs_toast_on)

        dialog.pacs_sound_cb.setEnabled(is_master_on)
        dialog.lbl_pacs_sound_enabled.setEnabled(is_master_on)
        dialog.pacs_sound_combo.setEnabled(pacs_sound_on)
        dialog.lbl_pacs_sound.setEnabled(pacs_sound_on)
        dialog.pacs_volume_slider.setEnabled(pacs_sound_on)
        dialog.lbl_pacs_volume.setEnabled(pacs_sound_on)
        dialog.pacs_volume_label_val.setEnabled(pacs_sound_on)
        dialog.pacs_voice_text_edit.setEnabled(pacs_sound_on)
        dialog.lbl_pacs_voice_text.setEnabled(pacs_sound_on)

    def on_master_toggled(checked):
        if not checked:
            dialog.ct_toast_cb.blockSignals(True)
            dialog.ct_sound_cb.blockSignals(True)
            dialog.pacs_toast_cb.blockSignals(True)
            dialog.pacs_sound_cb.blockSignals(True)
            
            dialog.ct_toast_cb.setChecked(False)
            dialog.pacs_toast_cb.setChecked(False)
            dialog.ct_sound_cb.setChecked(False)
            dialog.pacs_sound_cb.setChecked(False)
            
            dialog.ct_toast_cb.blockSignals(False)
            dialog.ct_sound_cb.blockSignals(False)
            dialog.pacs_toast_cb.blockSignals(False)
            dialog.pacs_sound_cb.blockSignals(False)
            
        update_notification_states()
        dialog.on_setting_changed()

    def on_sub_toggled(checked):
        update_notification_states()

    dialog.notifications_enabled_cb.toggled.connect(on_master_toggled)
    dialog.ct_toast_cb.toggled.connect(on_sub_toggled)
    dialog.ct_sound_cb.toggled.connect(on_sub_toggled)
    dialog.pacs_toast_cb.toggled.connect(on_sub_toggled)
    dialog.pacs_sound_cb.toggled.connect(on_sub_toggled)
    
    # Начальная инициализация
    update_notification_states()

    notifications_layout.addLayout(notifications_form)
    notifications_layout.addStretch()
    
    return notifications_widget


def retranslate_notifications_tab(dialog):
    dialog.lbl_notifications_enabled.setText(tr_ui("settings_notifications_enabled"))
    dialog.lbl_ct_section.setText(tr_ui("settings_ct_section_title"))
    dialog.lbl_ct_toast.setText(tr_ui("settings_notifications_toast_enabled"))
    dialog.lbl_ct_toast_duration.setText(tr_ui("settings_toast_duration_label"))
    dialog.lbl_ct_toast_position.setText(tr_ui("settings_toast_position_label"))

    # Populate ct_toast_duration_combo items
    dialog.ct_toast_duration_combo.blockSignals(True)
    cur_dur_ct = dialog.ct_toast_duration_combo.currentData()
    if not cur_dur_ct:
        cur_dur_ct = str(dialog.config.get('ct_toast_duration', dialog.config.get('toast_duration', '5')))
    dialog.ct_toast_duration_combo.clear()
    dialog.ct_toast_duration_combo.addItem(tr_ui("settings_toast_dur_3s"), "3")
    dialog.ct_toast_duration_combo.addItem(tr_ui("settings_toast_dur_5s"), "5")
    dialog.ct_toast_duration_combo.addItem(tr_ui("settings_toast_dur_8s"), "8")
    dialog.ct_toast_duration_combo.addItem(tr_ui("settings_toast_dur_15s"), "15")
    dialog.ct_toast_duration_combo.addItem(tr_ui("settings_toast_dur_manual"), "manual")
    idx_dur_ct = dialog.ct_toast_duration_combo.findData(cur_dur_ct)
    dialog.ct_toast_duration_combo.setCurrentIndex(idx_dur_ct if idx_dur_ct >= 0 else 1)
    dialog.ct_toast_duration_combo.blockSignals(False)

    # Populate ct_toast_position_combo items
    dialog.ct_toast_position_combo.blockSignals(True)
    cur_pos_ct = dialog.ct_toast_position_combo.currentData()
    if not cur_pos_ct:
        cur_pos_ct = str(dialog.config.get('ct_toast_position', dialog.config.get('toast_position', 'bottom_right')))
    dialog.ct_toast_position_combo.clear()
    dialog.ct_toast_position_combo.addItem(tr_ui("settings_toast_pos_bottom_right"), "bottom_right")
    dialog.ct_toast_position_combo.addItem(tr_ui("settings_toast_pos_bottom_left"), "bottom_left")
    dialog.ct_toast_position_combo.addItem(tr_ui("settings_toast_pos_top_right"), "top_right")
    dialog.ct_toast_position_combo.addItem(tr_ui("settings_toast_pos_top_left"), "top_left")
    idx_pos_ct = dialog.ct_toast_position_combo.findData(cur_pos_ct)
    dialog.ct_toast_position_combo.setCurrentIndex(idx_pos_ct if idx_pos_ct >= 0 else 0)
    dialog.ct_toast_position_combo.blockSignals(False)

    dialog.lbl_ct_sound_enabled.setText(tr_ui("settings_notifications_sound_enabled"))
    dialog.lbl_ct_sound.setText(tr_ui("settings_ct_sound_label"))
    dialog.lbl_ct_voice_text.setText(tr_ui("settings_ct_voice_text_label"))
    dialog.ct_voice_text_edit.setPlaceholderText(tr_ui("settings_ct_voice_text_placeholder"))
    dialog.lbl_ct_voice_text.setToolTip(tr_ui("tooltip_voice_text_hint"))
    dialog.ct_voice_text_edit.setToolTip(tr_ui("tooltip_voice_text_hint"))

    # PACS Section Labels and Comboboxes
    dialog.lbl_pacs_section.setText(tr_ui("settings_pacs_section_title"))
    dialog.lbl_pacs_toast.setText(tr_ui("settings_notifications_toast_enabled"))
    dialog.lbl_pacs_toast_duration.setText(tr_ui("settings_toast_duration_label"))
    dialog.lbl_pacs_toast_position.setText(tr_ui("settings_toast_position_label"))

    # Populate pacs_toast_duration_combo items
    dialog.pacs_toast_duration_combo.blockSignals(True)
    cur_dur_pacs = dialog.pacs_toast_duration_combo.currentData()
    if not cur_dur_pacs:
        cur_dur_pacs = str(dialog.config.get('pacs_toast_duration', dialog.config.get('toast_duration', '5')))
    dialog.pacs_toast_duration_combo.clear()
    dialog.pacs_toast_duration_combo.addItem(tr_ui("settings_toast_dur_3s"), "3")
    dialog.pacs_toast_duration_combo.addItem(tr_ui("settings_toast_dur_5s"), "5")
    dialog.pacs_toast_duration_combo.addItem(tr_ui("settings_toast_dur_8s"), "8")
    dialog.pacs_toast_duration_combo.addItem(tr_ui("settings_toast_dur_15s"), "15")
    dialog.pacs_toast_duration_combo.addItem(tr_ui("settings_toast_dur_manual"), "manual")
    idx_dur_pacs = dialog.pacs_toast_duration_combo.findData(cur_dur_pacs)
    dialog.pacs_toast_duration_combo.setCurrentIndex(idx_dur_pacs if idx_dur_pacs >= 0 else 1)
    dialog.pacs_toast_duration_combo.blockSignals(False)

    # Populate pacs_toast_position_combo items
    dialog.pacs_toast_position_combo.blockSignals(True)
    cur_pos_pacs = dialog.pacs_toast_position_combo.currentData()
    if not cur_pos_pacs:
        cur_pos_pacs = str(dialog.config.get('pacs_toast_position', dialog.config.get('toast_position', 'bottom_right')))
    dialog.pacs_toast_position_combo.clear()
    dialog.pacs_toast_position_combo.addItem(tr_ui("settings_toast_pos_bottom_right"), "bottom_right")
    dialog.pacs_toast_position_combo.addItem(tr_ui("settings_toast_pos_bottom_left"), "bottom_left")
    dialog.pacs_toast_position_combo.addItem(tr_ui("settings_toast_pos_top_right"), "top_right")
    dialog.pacs_toast_position_combo.addItem(tr_ui("settings_toast_pos_top_left"), "top_left")
    idx_pos_pacs = dialog.pacs_toast_position_combo.findData(cur_pos_pacs)
    dialog.pacs_toast_position_combo.setCurrentIndex(idx_pos_pacs if idx_pos_pacs >= 0 else 0)
    dialog.pacs_toast_position_combo.blockSignals(False)

    dialog.lbl_pacs_sound_enabled.setText(tr_ui("settings_notifications_sound_enabled"))
    dialog.lbl_pacs_sound.setText(tr_ui("settings_pacs_sound_label"))
    dialog.lbl_pacs_voice_text.setText(tr_ui("settings_pacs_voice_text_label"))
    dialog.pacs_voice_text_edit.setPlaceholderText(tr_ui("settings_pacs_voice_text_placeholder"))
    dialog.lbl_pacs_voice_text.setToolTip(tr_ui("tooltip_voice_text_hint"))
    dialog.pacs_voice_text_edit.setToolTip(tr_ui("tooltip_voice_text_hint"))
    dialog.ct_sound_combo.setItemText(0, tr_ui("settings_sound_default"))
    dialog.pacs_sound_combo.setItemText(0, tr_ui("settings_sound_default"))
    dialog.btn_unlock_voices.setText(tr_ui("settings_btn_unlock_voices"))

    dialog.notifications_enabled_cb.setToolTip(tr_ui("tooltip_switch_notify"))
    dialog.ct_toast_cb.setToolTip(tr_ui("tooltip_switch_notify"))
    dialog.pacs_toast_cb.setToolTip(tr_ui("tooltip_switch_pacs_notify"))
