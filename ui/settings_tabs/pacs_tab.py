# -*- coding: utf-8 -*-
"""PACS Server Settings Tab."""

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                             QLineEdit, QPushButton, QFormLayout, 
                             QSpinBox, QComboBox, QFrame)

from ui.toggle_switch import ToggleSwitch
from core.locale_utils import tr_ui


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


def build_pacs_tab(dialog):
    pacs_widget = QWidget()
    pacs_layout = QVBoxLayout(pacs_widget)
    pacs_layout.setSpacing(12)
    
    pacs_form = QFormLayout()
    pacs_form.setContentsMargins(0, 0, 0, 0)
    
    # Включить вкладку PACS
    dialog.show_tab_pacs_cb = ToggleSwitch()
    dialog.show_tab_pacs_cb.setChecked(dialog.config.get('show_tab_pacs', 'True').lower() == 'true')
    dialog.lbl_show_tab_pacs = QLabel()
    pacs_form.addRow(dialog.lbl_show_tab_pacs, dialog.show_tab_pacs_cb)

    # Разделитель после мастер-свича PACS
    line_pacs = QFrame()
    line_pacs.setFrameShape(QFrame.Shape.HLine)
    line_pacs.setFrameShadow(QFrame.Shadow.Sunken)
    line_pacs.setStyleSheet("background-color: #2d2d2d; margin-top: 6px; margin-bottom: 6px;")
    pacs_form.addRow(line_pacs)

    # Выбор сервера PACS
    server_select_layout = QHBoxLayout()
    server_select_layout.setSpacing(10)
    
    dialog.settings_server_combo = QComboBox()
    dialog.settings_server_combo.setFixedHeight(30)
    
    dialog.add_server_btn = QPushButton("Add")
    dialog.add_server_btn.setFixedWidth(60)
    dialog.add_server_btn.setFixedHeight(30)
    dialog.add_server_btn.clicked.connect(dialog.add_server_action)
    
    dialog.del_server_btn = QPushButton("Del")
    dialog.del_server_btn.setFixedWidth(60)
    dialog.del_server_btn.setFixedHeight(30)
    dialog.del_server_btn.clicked.connect(dialog.del_server_action)

    dialog.rename_server_btn = QPushButton("Rename")
    dialog.rename_server_btn.setFixedWidth(80)
    dialog.rename_server_btn.setFixedHeight(30)
    dialog.rename_server_btn.clicked.connect(dialog.rename_server_action)

    server_select_layout.addWidget(dialog.settings_server_combo, stretch=1)
    server_select_layout.addWidget(dialog.add_server_btn)
    server_select_layout.addWidget(dialog.del_server_btn)
    server_select_layout.addWidget(dialog.rename_server_btn)
    
    dialog.lbl_pacs_server = QLabel()
    pacs_form.addRow(dialog.lbl_pacs_server, server_select_layout)
    
    # PACS Scan Interval (sec)
    dialog.pacs_scan_spin = QSpinBox()
    dialog.pacs_scan_spin.setRange(1, 300)
    dialog.pacs_scan_spin.setValue(int(dialog.config.get('pacs_scan_time', 10000)) // 1000)
    dialog.lbl_standby_interval = QLabel()
    pacs_form.addRow(dialog.lbl_standby_interval, dialog.pacs_scan_spin)

    # IP PACS and Port on same row
    ip_port_layout = QHBoxLayout()
    ip_port_layout.setSpacing(10)
    
    dialog.pacs_ip_edit = QLineEdit(dialog.config.get('pacs_ip', '127.0.0.1'))
    
    dialog.lbl_port = QLabel("Port:")
    dialog.pacs_port_spin = QSpinBox()
    dialog.pacs_port_spin.setRange(1, 65535)
    dialog.pacs_port_spin.setValue(int(dialog.config.get('pacs_port', 11112)))
    
    ip_port_layout.addWidget(dialog.pacs_ip_edit, stretch=1)
    ip_port_layout.addWidget(dialog.lbl_port)
    ip_port_layout.addWidget(dialog.pacs_port_spin)
    
    dialog.lbl_pacs_ip = QLabel()
    pacs_form.addRow(dialog.lbl_pacs_ip, ip_port_layout)

    # AET Remote
    dialog.pacs_called_aet_edit = QLineEdit(dialog.config.get('pacs_called_aet', 'ANY-SCP'))
    dialog.pacs_called_aet_edit.setMaxLength(16)
    dialog.lbl_pacs_called_aet = QLabel()
    pacs_form.addRow(dialog.lbl_pacs_called_aet, dialog.pacs_called_aet_edit)

    # AET Local
    dialog.pacs_calling_aet_edit = QLineEdit(dialog.config.get('pacs_calling_aet', 'ECHOSCU'))
    dialog.pacs_calling_aet_edit.setMaxLength(16)
    dialog.lbl_pacs_calling_aet = QLabel()
    pacs_form.addRow(dialog.lbl_pacs_calling_aet, dialog.pacs_calling_aet_edit)
    
    # DICOM SCP Server (Local Port)
    dialog.pacs_local_port_spin = QSpinBox()
    dialog.pacs_local_port_spin.setRange(1, 65535)
    dialog.pacs_local_port_spin.setValue(int(dialog.config.get('pacs_local_port', 11112)))
    dialog.pacs_local_port_spin.setToolTip(tr_ui("tooltip_dicom_scp_port"))
    dialog.lbl_dicom_scp_port = QLabel(tr_ui("settings_dicom_scp_port_label"))
    dialog.lbl_dicom_scp_port.setToolTip(tr_ui("tooltip_dicom_scp_port"))
    pacs_form.addRow(dialog.lbl_dicom_scp_port, dialog.pacs_local_port_spin)
    
    pacs_layout.addLayout(pacs_form)

    # Кнопка Ping
    dialog.ping_btn = QPushButton("Ping")
    dialog.ping_btn.setFixedHeight(30)
    dialog.ping_btn.clicked.connect(dialog.ping_pacs_action)
    
    pacs_layout.addSpacing(10)
    pacs_layout.addWidget(dialog.ping_btn)
    pacs_layout.addStretch()
    
    return pacs_widget


def retranslate_pacs_tab(dialog):
    dialog.lbl_show_tab_pacs.setText(tr_ui("settings_show_tab_pacs"))
    dialog.lbl_show_tab_pacs.setToolTip(tr_ui("tooltip_show_tab_pacs"))
    dialog.show_tab_pacs_cb.setToolTip(tr_ui("tooltip_show_tab_pacs"))
    dialog.lbl_pacs_server.setText(tr_ui("settings_pacs_server_label"))
    dialog.lbl_standby_interval.setText(tr_ui("settings_standby_interval"))
    dialog.lbl_pacs_ip.setText(tr_ui("settings_pacs_ip"))
    dialog.lbl_pacs_called_aet.setText(tr_ui("settings_pacs_called_aet"))
    dialog.lbl_pacs_calling_aet.setText(tr_ui("settings_pacs_calling_aet"))
    dialog.lbl_dicom_scp_port.setText(tr_ui("settings_dicom_scp_port_label"))
    dialog.lbl_dicom_scp_port.setToolTip(tr_ui("tooltip_dicom_scp_port"))
    dialog.pacs_local_port_spin.setToolTip(tr_ui("tooltip_dicom_scp_port"))
    dialog.add_server_btn.setText(tr_ui("settings_btn_add"))
    dialog.del_server_btn.setText(tr_ui("settings_btn_del"))
    dialog.rename_server_btn.setText(tr_ui("settings_btn_rename"))
    
    dialog.add_server_btn.setToolTip(tr_ui("tooltip_settings_add_server"))
    dialog.del_server_btn.setToolTip(tr_ui("tooltip_settings_del_server"))
    dialog.rename_server_btn.setToolTip(tr_ui("tooltip_settings_rename_server"))
    dialog.ping_btn.setToolTip(tr_ui("tooltip_settings_ping_server"))
