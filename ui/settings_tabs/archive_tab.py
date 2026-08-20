# -*- coding: utf-8 -*-
"""Archive Settings Tab."""

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                             QLineEdit, QPushButton, QFormLayout, 
                             QSpinBox, QFrame)

from ui.toggle_switch import ToggleSwitch
from core.locale_utils import tr_ui


def build_archive_tab(dialog):
    archive_widget = QWidget()
    archive_layout = QVBoxLayout(archive_widget)
    archive_form = QFormLayout()
    
    # Включить вкладку архива
    dialog.show_tab_archive_cb = ToggleSwitch()
    dialog.show_tab_archive_cb.setChecked(dialog.config.get('show_tab_archive', 'True').lower() == 'true')
    dialog.lbl_show_tab_archive = QLabel()
    archive_form.addRow(dialog.lbl_show_tab_archive, dialog.show_tab_archive_cb)

    # Разделитель после мастер-свича архива
    line_archive = QFrame()
    line_archive.setFrameShape(QFrame.Shape.HLine)
    line_archive.setFrameShadow(QFrame.Shadow.Sunken)
    line_archive.setStyleSheet("background-color: #2d2d2d; margin-top: 6px; margin-bottom: 6px;")
    archive_form.addRow(line_archive)

    # Archive Dir
    dialog.archive_edit = QLineEdit(dialog.config.get('archive_dir', ''))
    dialog.btn_archive_browse = QPushButton()
    dialog.btn_archive_browse.clicked.connect(lambda: dialog.browse_folder(dialog.archive_edit, tr_ui("settings_archive_dir").rstrip(":")))
    h_layout2 = QHBoxLayout()
    h_layout2.addWidget(dialog.archive_edit)
    h_layout2.addWidget(dialog.btn_archive_browse)
    dialog.lbl_archive_dir = QLabel()
    archive_form.addRow(dialog.lbl_archive_dir, h_layout2)
    
    # Archive Slice (Max visible rows)
    dialog.archive_slice_spin = QSpinBox()
    dialog.archive_slice_spin.setRange(0, 1000)
    dialog.archive_slice_spin.setValue(int(dialog.config.get('archive_slice', 0)))
    dialog.lbl_archive_slice = QLabel()
    archive_form.addRow(dialog.lbl_archive_slice, dialog.archive_slice_spin)

    # Автоматическое архивирование (свич и количество дней в одной строке)
    dialog.archive_enabled_cb = ToggleSwitch()
    dialog.archive_enabled_cb.setChecked(dialog.config.get('archive_enabled', 'False').lower() == 'true')
    
    dialog.archive_days_spin = QSpinBox()
    dialog.archive_days_spin.setRange(1, 365)
    dialog.archive_days_spin.setValue(int(dialog.config.get('archive_days', 3)))
    dialog.archive_days_spin.setFixedWidth(60)
    dialog.archive_days_spin.setStyleSheet(
        "QSpinBox { background-color: #1e1e1e; color: #ffffff; border: 1px solid #2d2d2d; padding: 2px; border-radius: 4px; }"
        "QSpinBox:disabled { background-color: #141414; color: #666666; border: 1px solid #1c1c1c; }"
    )

    dialog.archive_label_through = QLabel(tr_ui("lbl_archive_through"))
    dialog.archive_label_through.setStyleSheet(
        "QLabel { color: #aaaaaa; }"
        "QLabel:disabled { color: #444444; }"
    )
    dialog.archive_label_days = QLabel(tr_ui("lbl_archive_days"))
    dialog.archive_label_days.setStyleSheet(
        "QLabel { color: #aaaaaa; }"
        "QLabel:disabled { color: #444444; }"
    )

    archive_row_layout = QHBoxLayout()
    archive_row_layout.addWidget(dialog.archive_enabled_cb)
    archive_row_layout.addStretch()
    archive_row_layout.addWidget(dialog.archive_label_through)
    archive_row_layout.addSpacing(8)
    archive_row_layout.addWidget(dialog.archive_days_spin)
    archive_row_layout.addWidget(dialog.archive_label_days)

    dialog.lbl_auto_archive_row = QLabel()
    archive_form.addRow(dialog.lbl_auto_archive_row, archive_row_layout)

    # Автоочистка архива (свич и количество дней в одной строке)
    dialog.archive_cleanup_enabled_cb = ToggleSwitch()
    dialog.archive_cleanup_enabled_cb.setChecked(dialog.config.get('archive_cleanup_enabled', 'False').lower() == 'true')
    
    dialog.archive_cleanup_days_spin = QSpinBox()
    dialog.archive_cleanup_days_spin.setRange(1, 365)
    dialog.archive_cleanup_days_spin.setValue(int(dialog.config.get('archive_cleanup_days', 30)))
    dialog.archive_cleanup_days_spin.setFixedWidth(60)
    dialog.archive_cleanup_days_spin.setStyleSheet(
        "QSpinBox { background-color: #1e1e1e; color: #ffffff; border: 1px solid #2d2d2d; padding: 2px; border-radius: 4px; }"
        "QSpinBox:disabled { background-color: #141414; color: #666666; border: 1px solid #1c1c1c; }"
    )

    dialog.cleanup_label_through = QLabel(tr_ui("lbl_archive_through"))
    dialog.cleanup_label_through.setStyleSheet(
        "QLabel { color: #aaaaaa; }"
        "QLabel:disabled { color: #444444; }"
    )
    dialog.cleanup_label_days = QLabel(tr_ui("lbl_archive_days"))
    dialog.cleanup_label_days.setStyleSheet(
        "QLabel { color: #aaaaaa; }"
        "QLabel:disabled { color: #444444; }"
    )

    cleanup_row_layout = QHBoxLayout()
    cleanup_row_layout.addWidget(dialog.archive_cleanup_enabled_cb)
    cleanup_row_layout.addStretch()
    cleanup_row_layout.addWidget(dialog.cleanup_label_through)
    cleanup_row_layout.addSpacing(8)
    cleanup_row_layout.addWidget(dialog.archive_cleanup_days_spin)
    cleanup_row_layout.addWidget(dialog.cleanup_label_days)

    dialog.lbl_auto_cleanup_row = QLabel()
    archive_form.addRow(dialog.lbl_auto_cleanup_row, cleanup_row_layout)
    
    archive_layout.addLayout(archive_form)
    archive_layout.addStretch()
    
    return archive_widget


def retranslate_archive_tab(dialog):
    dialog.lbl_show_tab_archive.setText(tr_ui("settings_show_tab_archive"))
    dialog.lbl_show_tab_archive.setToolTip(tr_ui("tooltip_show_tab_archive"))
    dialog.show_tab_archive_cb.setToolTip(tr_ui("tooltip_show_tab_archive"))
    dialog.lbl_archive_dir.setText(tr_ui("settings_archive_dir"))
    dialog.btn_archive_browse.setText(tr_ui("settings_browse"))
    dialog.lbl_archive_slice.setText(tr_ui("settings_archive_slice"))
    dialog.lbl_auto_archive_row.setText(tr_ui("settings_auto_archive_row"))
    dialog.archive_label_through.setText(tr_ui("lbl_archive_through"))
    dialog.archive_label_days.setText(tr_ui("lbl_archive_days"))
    dialog.lbl_auto_cleanup_row.setText(tr_ui("settings_auto_cleanup_row"))
    dialog.cleanup_label_through.setText(tr_ui("lbl_archive_through"))
    dialog.cleanup_label_days.setText(tr_ui("lbl_archive_days"))
    
    dialog.btn_archive_browse.setToolTip(tr_ui("tooltip_settings_archive_browse"))
    dialog.archive_enabled_cb.setToolTip(tr_ui("tooltip_switch_archive_enabled"))
    dialog.archive_cleanup_enabled_cb.setToolTip(tr_ui("tooltip_switch_archive_cleanup"))
