# -*- coding: utf-8 -*-
"""PACS Tab for DICOM WatchDog."""

import os
from datetime import datetime
try:
    from PyQt6.QtCore import Qt, QSize, QDate
    from PyQt6.QtGui import QIcon, QColor
    from PyQt6.QtWidgets import (
        QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
        QComboBox, QTableWidgetItem
    )
except ImportError:
    from PyQt5.QtCore import Qt, QSize, QDate
    from PyQt5.QtGui import QIcon, QColor
    from PyQt5.QtWidgets import (
        QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
        QComboBox, QTableWidgetItem
    )

from ui.table_widgets import ToggleTableWidget
from ui.centered_date_edit import CenteredDateEdit
from ui.toggle_switch import ToggleSwitch
from core.config_utils import get_resource_path
from core.locale_utils import tr_ui


class PacsTab(QWidget):
    """Виджет вкладки «PACS»."""
    def __init__(self, main_window=None, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 10, 4, 6)
        layout.setSpacing(10)

        # Таблица PACS
        self.table = ToggleTableWidget(self)
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels([
            "Patient ID", "Patient Name", "Modality", "Slices", "Scanning Area", "Study datetime"
        ])
        self.table.setColumnHidden(2, True)
        self.table.horizontalHeader().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        if self.main_window:
            self.table.horizontalHeader().customContextMenuRequested.connect(
                lambda pos: self.main_window.show_header_context_menu(pos, self.table)
            )
            self.main_window.setup_table_properties(self.table)
            self.main_window.restore_table_state(self.table)
            self.table.customContextMenuRequested.connect(self.main_window.show_pacs_context_menu)
            self.table.itemSelectionChanged.connect(self.main_window.on_pacs_selection_changed)

        self.table.set_placeholder_text("Сканирование сервера PACS не настроено")
        self.table.update_placeholder_visibility()
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        layout.addWidget(self.table)

        # Панель управления PACS
        control_layout = QHBoxLayout()
        control_layout.setContentsMargins(5, 0, 5, 0)
        control_layout.setSpacing(6)

        # Кнопки быстрых интервалов
        self.today_btn = QPushButton("Today", self)
        self.today_btn.setFixedHeight(30)
        if self.main_window:
            self.today_btn.clicked.connect(self.main_window.pacs_set_today)
        control_layout.addWidget(self.today_btn)

        self.last_3days_btn = QPushButton("Last 3 days", self)
        self.last_3days_btn.setFixedHeight(30)
        if self.main_window:
            self.last_3days_btn.clicked.connect(self.main_window.pacs_set_3days)
        control_layout.addWidget(self.last_3days_btn)

        # Выбор диапазона дат
        self.lbl_from = QLabel(tr_ui("lbl_from"), self)
        self.lbl_from.setStyleSheet("color: #ffffff; font-family: 'Segoe UI'; font-size: 13px;")
        control_layout.addWidget(self.lbl_from)

        self.date_from = CenteredDateEdit(self)
        self.date_from.setDisplayFormat("dd.MM.yyyy")
        self.date_from.setDate(QDate.currentDate())
        self.date_from.setFixedHeight(30)
        if self.main_window:
            self.date_from.dateChanged.connect(lambda: self.main_window.fill_pacs_list(silent=True))
        control_layout.addWidget(self.date_from)

        self.lbl_to = QLabel(tr_ui("lbl_to"), self)
        self.lbl_to.setStyleSheet("color: #ffffff; font-family: 'Segoe UI'; font-size: 13px;")
        control_layout.addWidget(self.lbl_to)

        self.date_to = CenteredDateEdit(self)
        self.date_to.setDisplayFormat("dd.MM.yyyy")
        self.date_to.setDate(QDate.currentDate())
        self.date_to.setFixedHeight(30)
        if self.main_window:
            self.date_to.dateChanged.connect(lambda: self.main_window.fill_pacs_list(silent=True))
        control_layout.addWidget(self.date_to)

        # Выбор сервера
        self.lbl_server = QLabel(tr_ui("lbl_server"), self)
        self.lbl_server.setStyleSheet("color: #ffffff; font-family: 'Segoe UI'; font-size: 13px;")
        control_layout.addWidget(self.lbl_server)

        self.server_combo = QComboBox(self)
        self.server_combo.setFixedHeight(30)
        self.server_combo.setStyleSheet("""
            QComboBox {
                background-color: #2b2b2b;
                color: #ffffff;
                border: 1px solid #3d3d3d;
                border-radius: 4px;
                padding-left: 8px;
                font-family: 'Segoe UI';
                font-size: 13px;
            }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
            QComboBox QAbstractItemView {
                background-color: #1a1a1a;
                color: #ffffff;
                selection-background-color: #1f538d;
            }
        """)
        if self.main_window:
            self.server_combo.currentIndexChanged.connect(self.main_window.on_pacs_server_changed)
        control_layout.addWidget(self.server_combo)

        # Режим ожидания (Standby)
        auto_update_state = False
        if self.main_window and hasattr(self.main_window, 'config'):
            auto_update_state = self.main_window.config.get('auto_update_is', 'off').lower() == 'on'
        self.auto_scan_cb = ToggleSwitch(tr_ui("pacs_standby_mode"), self)
        self.auto_scan_cb.setChecked(auto_update_state)
        self.auto_scan_cb.setToolTip(tr_ui("tooltip_pacs_auto_scan"))
        if self.main_window:
            self.auto_scan_cb.stateChanged.connect(self.main_window.on_pacs_auto_scan_changed)
        control_layout.addWidget(self.auto_scan_cb)

        # Поиск
        self.search_entry = QLineEdit(self)
        self.search_entry.setPlaceholderText(tr_ui("placeholder_search_patient"))
        self.search_entry.setFixedHeight(30)
        self.search_entry.setFixedWidth(160)
        self.clear_action = self.search_entry.addAction(
            QIcon(get_resource_path("themes/clear.svg")), 
            QLineEdit.ActionPosition.TrailingPosition
        )
        self.clear_action.setVisible(False)
        self.clear_action.triggered.connect(self.search_entry.clear)
        self.search_entry.textChanged.connect(lambda t: self.clear_action.setVisible(bool(t)))
        if self.main_window:
            self.search_entry.textChanged.connect(self.main_window.search_patient_pacs)
        control_layout.addWidget(self.search_entry)

        # Кнопка скачивания
        self.send_to_ct_btn = QPushButton(tr_ui("btn_send_to_ct"), self)
        self.send_to_ct_btn.setFixedHeight(30)
        self.send_to_ct_btn.setEnabled(False)
        if self.main_window:
            self.send_to_ct_btn.clicked.connect(self.main_window.send_to_ct_images_cmd)
        control_layout.addWidget(self.send_to_ct_btn)

        # Кнопка настроек (шестеренка)
        self.settings_btn = QPushButton(self)
        self.settings_btn.setIcon(QIcon(get_resource_path("themes/settings.svg")))
        self.settings_btn.setIconSize(QSize(20, 20))
        self.settings_btn.setFixedSize(35, 30)
        self.settings_btn.setToolTip("Настройки папок и интервалов")
        if self.main_window:
            self.settings_btn.clicked.connect(self.main_window.open_settings_cmd)
        control_layout.addWidget(self.settings_btn)

        layout.addLayout(control_layout)

    def retranslate_ui(self):
        self.today_btn.setText(tr_ui("btn_today"))
        self.last_3days_btn.setText(tr_ui("btn_3days"))
        self.lbl_from.setText(tr_ui("lbl_from"))
        self.lbl_to.setText(tr_ui("lbl_to"))
        self.lbl_server.setText(tr_ui("lbl_server"))
        self.search_entry.setPlaceholderText(tr_ui("placeholder_search_patient"))
        if self.main_window:
            self.send_to_ct_btn.setText(self.main_window.get_send_to_ct_text())
        self.auto_scan_cb.setText(tr_ui("pacs_standby_mode"))
        self.settings_btn.setToolTip(tr_ui("tooltip_settings_btn"))
        self.auto_scan_cb.setToolTip(tr_ui("tooltip_pacs_auto_scan"))
        self.today_btn.setToolTip(tr_ui("tooltip_pacs_today_btn"))
        self.last_3days_btn.setToolTip(tr_ui("tooltip_pacs_3days_btn"))
        self.date_from.setToolTip(tr_ui("tooltip_pacs_date_from"))
        self.date_to.setToolTip(tr_ui("tooltip_pacs_date_to"))
        self.server_combo.setToolTip(tr_ui("tooltip_pacs_server_combo"))
        self.search_entry.setToolTip(tr_ui("tooltip_search_pacs_entry"))
        self.send_to_ct_btn.setToolTip(tr_ui("tooltip_send_to_ct"))

    @staticmethod
    def _compute_row_color(data, config):
        color = QColor("#ffffff")
        if config.get('highlighting_enabled', 'False').lower() == 'true':
            highlight_new = config.get('highlight_new_enabled', 'False').lower() == 'true'
            highlight_today = config.get('highlight_today_enabled', 'False').lower() == 'true'
            d_time = data.get('study_datetime_obj')
            if d_time:
                if highlight_new and (datetime.now() - d_time).total_seconds() / 3600 < 1:
                    color = QColor("lime")
                elif highlight_today and d_time.date() == datetime.now().date():
                    color = QColor("mediumturquoise")
        return color

    def render_table(self, pacs_data=None, config=None):
        if pacs_data is None:
            if self.main_window and hasattr(self.main_window, 'pacs_data'):
                pacs_data = self.main_window.pacs_data
        if pacs_data is None:
            return

        if config is None:
            config = getattr(self.main_window, 'config', {}) if self.main_window else {}

        search_text = self.search_entry.text().lower().strip()

        filtered_items = {}
        for patient_id, data in pacs_data.items():
            patient_name = str(data.get('patient_name', '')).lower()
            p_id = str(data.get('patient_id', patient_id)).lower()
            if search_text:
                words = patient_name.replace('^', ' ').split()
                name_match = bool(words and words[0].startswith(search_text))
                id_match = p_id.startswith(search_text)
                if not (name_match or id_match):
                    continue
            filtered_items[patient_id] = data

        self.table.setUpdatesEnabled(False)
        self.table.blockSignals(True)

        self.table.setRowCount(0)
        row_idx = 0
        sorted_items = sorted(filtered_items.items(), key=lambda x: x[1]['study_datetime_obj'], reverse=True)

        for patient_id, data in sorted_items:
            self.table.insertRow(row_idx)

            p_display_id = str(data.get('study_patient_id', data.get('patient_id', patient_id)))
            id_item = QTableWidgetItem(p_display_id)
            id_item.setData(Qt.ItemDataRole.UserRole, data.get('study_instance_uid', ''))
            name_item = QTableWidgetItem(str(data['patient_name']))
            modality_item = QTableWidgetItem(str(data.get('modality', 'CT')))
            slices_item = QTableWidgetItem(str(data.get('slices', '0')))
            area_item = QTableWidgetItem(str(data.get('body_part', '')))
            study_item = QTableWidgetItem(data['study_datetime_str'])

            id_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            name_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            modality_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            slices_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            area_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            study_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            color = self._compute_row_color(data, config)
            for item in [id_item, name_item, modality_item, slices_item, area_item, study_item]:
                item.setForeground(color)

            self.table.setItem(row_idx, 0, id_item)
            self.table.setItem(row_idx, 1, name_item)
            self.table.setItem(row_idx, 2, modality_item)
            self.table.setItem(row_idx, 3, slices_item)
            self.table.setItem(row_idx, 4, area_item)
            self.table.setItem(row_idx, 5, study_item)

            row_idx += 1

        selected_id = getattr(self.main_window, 'selected_pacs_patient_id', None) if self.main_window else None
        if selected_id:
            for r in range(self.table.rowCount()):
                id_item = self.table.item(r, 0)
                if id_item and id_item.text() == selected_id:
                    self.table.selectRow(r)
                    break

        if search_text and self.table.rowCount() == 0 and bool(pacs_data):
            self.table.set_placeholder_text(tr_ui("placeholder_no_filter_matches"), color="crimson")
        elif not search_text:
            auto_update_on = config.get('auto_update_is', 'off').lower() == 'on'
            if auto_update_on:
                self.table.set_placeholder_text(tr_ui("placeholder_standby"))
            else:
                self.table.set_placeholder_text(tr_ui("placeholder_no_studies"))

        self.table.update_placeholder_visibility()
        self.table.blockSignals(False)
        self.table.setUpdatesEnabled(True)
        if self.main_window and hasattr(self.main_window, 'on_pacs_selection_changed'):
            self.main_window.on_pacs_selection_changed()

