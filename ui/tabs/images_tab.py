# -*- coding: utf-8 -*-
"""Images Tab (Снимки КТ) for DICOM WatchDog."""

import os
from datetime import datetime
from collections import defaultdict
try:
    from PyQt6.QtCore import Qt, QSize, QItemSelectionModel
    from PyQt6.QtGui import QIcon, QColor
    from PyQt6.QtWidgets import (
        QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton, QTableWidgetItem
    )
except ImportError:
    from PyQt5.QtCore import Qt, QSize, QItemSelectionModel
    from PyQt5.QtGui import QIcon, QColor
    from PyQt5.QtWidgets import (
        QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton, QTableWidgetItem
    )

from ui.table_widgets import ToggleTableWidget
from core.config_utils import get_resource_path
from core.logger import log_message
from core.locale_utils import tr_ui, tr_log


class ImagesTab(QWidget):
    """Виджет вкладки «Снимки КТ»."""
    def __init__(self, main_window=None, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 10, 4, 6)
        layout.setSpacing(10)

        # Таблица КТ-изображений
        self.table = ToggleTableWidget(self)
        self.table.setColumnCount(10)
        self.table.setHorizontalHeaderLabels([
            "Patient ID", "Patient Name", "Modality", "Slices", "Scanning Area", 
            "Study datetime", "Folder datetime", "STR", "RTD", "RTP"
        ])
        self.table.setColumnHidden(2, True)
        self.table.setColumnHidden(8, True)
        self.table.setColumnHidden(9, True)
        self.table.horizontalHeader().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        if self.main_window:
            self.table.horizontalHeader().customContextMenuRequested.connect(
                lambda pos: self.main_window.show_header_context_menu(pos, self.table)
            )
            self.main_window.setup_table_properties(self.table)
            self.main_window.restore_table_state(self.table)
            self.table.cellDoubleClicked.connect(self.main_window.on_images_double_clicked)
            self.table.customContextMenuRequested.connect(self.main_window.show_images_context_menu)
            self.table.itemSelectionChanged.connect(self.main_window.on_images_selection_changed)
            self.table.delete_requested.connect(lambda: self.main_window.delete_patient_action())

        self.table.set_placeholder_text("В этой папке нет исследований")
        self.table.update_placeholder_visibility()
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        layout.addWidget(self.table)

        # Нижняя панель управления
        control_layout = QHBoxLayout()
        control_layout.setContentsMargins(5, 0, 5, 0)
        control_layout.setSpacing(10)

        # Поле поиска
        self.search_entry = QLineEdit(self)
        self.search_entry.setPlaceholderText("Введите имя пациента для поиска")
        self.search_entry.setFixedHeight(30)
        self.clear_action = self.search_entry.addAction(
            QIcon(get_resource_path("themes/clear.svg")), 
            QLineEdit.ActionPosition.TrailingPosition
        )
        self.clear_action.setVisible(False)
        self.clear_action.triggered.connect(self.search_entry.clear)
        self.search_entry.textChanged.connect(lambda t: self.clear_action.setVisible(bool(t)))
        if self.main_window:
            self.search_entry.textChanged.connect(self.main_window.search_patient_images)
        control_layout.addWidget(self.search_entry, stretch=1, alignment=Qt.AlignmentFlag.AlignVCenter)

        # Кнопка поиска
        self.search_btn = QPushButton("Search", self)
        self.search_btn.setFixedHeight(30)
        if self.main_window:
            self.search_btn.clicked.connect(self.main_window.search_patient_images)
        control_layout.addWidget(self.search_btn, alignment=Qt.AlignmentFlag.AlignVCenter)

        # Кнопка перемещения в архив
        self.move_to_archive_btn = QPushButton("Move to Archive", self)
        self.move_to_archive_btn.setEnabled(False)
        self.move_to_archive_btn.setFixedHeight(30)
        self.move_to_archive_btn.setObjectName("moveToArchiveBtn")
        if self.main_window:
            self.move_to_archive_btn.clicked.connect(self.main_window.move_to_archive_cmd)
        control_layout.addWidget(self.move_to_archive_btn, alignment=Qt.AlignmentFlag.AlignVCenter)

        # Кнопка настроек (шестеренка)
        self.settings_btn = QPushButton(self)
        self.settings_btn.setIcon(QIcon(get_resource_path("themes/settings.svg")))
        self.settings_btn.setIconSize(QSize(20, 20))
        self.settings_btn.setFixedSize(35, 30)
        self.settings_btn.setToolTip("Настройки папок и интервалов")
        if self.main_window:
            self.settings_btn.clicked.connect(self.main_window.open_settings_cmd)
        control_layout.addWidget(self.settings_btn, alignment=Qt.AlignmentFlag.AlignVCenter)

        layout.addLayout(control_layout)

    def retranslate_ui(self):
        self.search_entry.setPlaceholderText(tr_ui("placeholder_search_patient"))
        self.search_btn.setText(tr_ui("btn_search"))
        if self.main_window:
            self.move_to_archive_btn.setText(self.main_window.get_move_to_archive_text())
        self.table.set_placeholder_text(tr_ui("placeholder_no_studies_in_folder"))
        self.settings_btn.setToolTip(tr_ui("tooltip_settings_btn"))
        self.search_entry.setToolTip(tr_ui("tooltip_search_images_entry"))
        self.search_btn.setToolTip(tr_ui("tooltip_search_images_btn"))
        self.move_to_archive_btn.setToolTip(tr_ui("tooltip_move_to_archive"))

    @staticmethod
    def _compute_row_color(data, config):
        color = QColor("#ffffff")
        if config.get('highlighting_enabled', 'False').lower() == 'true':
            folder_dt = data.get('folder_datetime')
            if folder_dt:
                highlight_new = config.get('highlight_new_enabled', 'False').lower() == 'true'
                highlight_today = config.get('highlight_today_enabled', 'False').lower() == 'true'
                highlight_no_str = config.get('highlight_no_str_enabled', 'False').lower() == 'true'
                highlight_no_slices = config.get('highlight_no_slices_enabled', 'False').lower() == 'true'

                if highlight_new and (datetime.now() - folder_dt).total_seconds() / 3600 < 1:
                    color = QColor("lime")
                elif highlight_today and folder_dt.date() == datetime.now().date():
                    color = QColor("mediumturquoise")

                if highlight_no_str and (data.get('str', 0) == 0 or data.get('str', 0) > 1):
                    color = QColor("crimson")
                if highlight_no_slices and data.get('slices', 0) == 0:
                    color = QColor("crimson")
        return color

    def populate_table(self, images_cache=None, config=None, output_field=None):
        if images_cache is None:
            if self.main_window and hasattr(self.main_window, 'images_cache'):
                images_cache = self.main_window.images_cache
        if images_cache is None:
            return

        if config is None:
            config = getattr(self.main_window, 'config', {}) if self.main_window else {}
        if output_field is None:
            output_field = getattr(self.main_window, 'output_field', None) if self.main_window else None

        self.table.setUpdatesEnabled(False)
        self.table.blockSignals(True)

        # Remember selected patients and row types
        selected_items = set()
        selected_ranges = self.table.selectedRanges()
        if selected_ranges:
            for rng in selected_ranges:
                for r in range(rng.topRow(), rng.bottomRow() + 1):
                    id_item = self.table.item(r, 0)
                    name_item = self.table.item(r, 1)
                    if id_item:
                        pid = id_item.data(Qt.ItemDataRole.UserRole)
                        is_child = bool(name_item and name_item.text().startswith("  ↳"))
                        if pid is not None:
                            selected_items.add((pid, is_child))

        if self.main_window:
            self.main_window.selected_images_items = selected_items

        self.table.setRowCount(0)
        search_text = self.search_entry.text().lower()

        # Filter patients with valid DICOM data and matching search query
        valid_patients = {}
        for patient_id, data in images_cache.items():
            if 'patient_name' not in data or 'study_datetime' not in data or 'folder_datetime' not in data or 'str' not in data:
                if output_field:
                    log_message(output_field, tr_log("log_skipped_patient_incomplete", patient_id))
                continue

            patient_name = str(data.get('patient_name', '')).lower()
            p_id = str(data.get('patient_id', patient_id)).lower()
            if search_text:
                words = patient_name.replace('^', ' ').split()
                name_match = bool(words and words[0].startswith(search_text))
                id_match = p_id.startswith(search_text)
                if not (name_match or id_match):
                    continue

            valid_patients[patient_id] = data

        # Group studies by (patient_name, patient_id)
        grouped_patients = defaultdict(list)
        for key, data in valid_patients.items():
            p_name = str(data.get('patient_name', 'Unknown'))
            p_id = str(data.get('patient_id', 'Unknown'))
            grouped_patients[(p_name, p_id)].append((key, data))

        # Sort studies inside each patient group by study_datetime descending
        for p_info in grouped_patients:
            grouped_patients[p_info].sort(key=lambda x: x[1]['study_datetime'], reverse=True)

        # Sort patients by latest study's folder_datetime descending, then patient_name
        def get_patient_sort_key(item):
            p_info, studies = item
            latest_study = studies[0][1]
            folder_dt = latest_study['folder_datetime']
            patient_name = str(p_info[0]).lower()
            return (-folder_dt.timestamp(), patient_name)

        sorted_grouped_patients = sorted(grouped_patients.items(), key=get_patient_sort_key)

        # Fill table rows
        row_idx = 0
        for p_info, studies in sorted_grouped_patients:
            p_name, p_id_val = p_info

            if len(studies) == 1:
                patient_key, data = studies[0]
                self.table.insertRow(row_idx)

                id_item = QTableWidgetItem(str(data.get('patient_id', p_id_val)))
                id_item.setData(Qt.ItemDataRole.UserRole, patient_key)
                name_item = QTableWidgetItem(str(data['patient_name']))
                modality_item = QTableWidgetItem(str(data.get('modality', 'CT')))
                slices_item = QTableWidgetItem(str(data.get('slices', 0)))
                area_item = QTableWidgetItem(str(data.get('body_part', '')))
                study_item = QTableWidgetItem(data['study_datetime'].strftime('%d.%m.%y - %H:%M'))
                folder_item = QTableWidgetItem(data['folder_datetime'].strftime('%d.%m.%y - %H:%M'))
                str_item = QTableWidgetItem(str(data['str']))
                rtd_item = QTableWidgetItem(str(data.get('rtd', 0)))
                rtp_item = QTableWidgetItem(str(data.get('rtp', 0)))

                for item in [id_item, name_item, modality_item, slices_item, area_item, study_item, folder_item, str_item, rtd_item, rtp_item]:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter)
                    if item in [modality_item, slices_item, area_item, study_item, folder_item, str_item, rtd_item, rtp_item]:
                        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

                color = self._compute_row_color(data, config)
                for item in [id_item, name_item, modality_item, slices_item, area_item, study_item, folder_item, str_item, rtd_item, rtp_item]:
                    item.setForeground(color)

                self.table.setItem(row_idx, 0, id_item)
                self.table.setItem(row_idx, 1, name_item)
                self.table.setItem(row_idx, 2, modality_item)
                self.table.setItem(row_idx, 3, slices_item)
                self.table.setItem(row_idx, 4, area_item)
                self.table.setItem(row_idx, 5, study_item)
                self.table.setItem(row_idx, 6, folder_item)
                self.table.setItem(row_idx, 7, str_item)
                self.table.setItem(row_idx, 8, rtd_item)
                self.table.setItem(row_idx, 9, rtp_item)

                row_idx += 1
            else:
                # Parent row
                self.table.insertRow(row_idx)

                id_item = QTableWidgetItem(p_id_val)
                id_item.setData(Qt.ItemDataRole.UserRole, studies[0][0])

                name_item = QTableWidgetItem(p_name)
                modality_item = QTableWidgetItem("")
                slices_item = QTableWidgetItem("")
                area_item = QTableWidgetItem("")
                study_item = QTableWidgetItem("")
                folder_item = QTableWidgetItem("")
                str_item = QTableWidgetItem("")
                rtd_item = QTableWidgetItem("")
                rtp_item = QTableWidgetItem("")

                for item in [id_item, name_item, modality_item, slices_item, area_item, study_item, folder_item, str_item, rtd_item, rtp_item]:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter)
                    if item in [modality_item, slices_item, area_item, study_item, folder_item, str_item, rtd_item, rtp_item]:
                        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    item.setForeground(QColor("#ffffff"))

                self.table.setItem(row_idx, 0, id_item)
                self.table.setItem(row_idx, 1, name_item)
                self.table.setItem(row_idx, 2, modality_item)
                self.table.setItem(row_idx, 3, slices_item)
                self.table.setItem(row_idx, 4, area_item)
                self.table.setItem(row_idx, 5, study_item)
                self.table.setItem(row_idx, 6, folder_item)
                self.table.setItem(row_idx, 7, str_item)
                self.table.setItem(row_idx, 8, rtd_item)
                self.table.setItem(row_idx, 9, rtp_item)

                row_idx += 1

                # Child rows
                for patient_key, data in studies:
                    self.table.insertRow(row_idx)

                    id_child = QTableWidgetItem("")
                    id_child.setData(Qt.ItemDataRole.UserRole, patient_key)
                    name_child = QTableWidgetItem("  ↳")
                    modality_child = QTableWidgetItem(str(data.get('modality', 'CT')))
                    slices_child = QTableWidgetItem(str(data.get('slices', 0)))
                    area_child = QTableWidgetItem(str(data.get('body_part', '')))
                    study_child = QTableWidgetItem(data['study_datetime'].strftime('%d.%m.%y - %H:%M'))
                    folder_child = QTableWidgetItem(data['folder_datetime'].strftime('%d.%m.%y - %H:%M'))
                    str_child = QTableWidgetItem(str(data['str']))
                    rtd_child = QTableWidgetItem(str(data.get('rtd', 0)))
                    rtp_child = QTableWidgetItem(str(data.get('rtp', 0)))

                    for item in [id_child, name_child, modality_child, slices_child, area_child, study_child, folder_child, str_child, rtd_child, rtp_child]:
                        item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter)
                        if item in [modality_child, slices_child, area_child, study_child, folder_child, str_child, rtd_child, rtp_child]:
                            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

                    color = self._compute_row_color(data, config)
                    for item in [id_child, name_child, modality_child, slices_child, area_child, study_child, folder_child, str_child, rtd_child, rtp_child]:
                        item.setForeground(color)

                    self.table.setItem(row_idx, 0, id_child)
                    self.table.setItem(row_idx, 1, name_child)
                    self.table.setItem(row_idx, 2, modality_child)
                    self.table.setItem(row_idx, 3, slices_child)
                    self.table.setItem(row_idx, 4, area_child)
                    self.table.setItem(row_idx, 5, study_child)
                    self.table.setItem(row_idx, 6, folder_child)
                    self.table.setItem(row_idx, 7, str_child)
                    self.table.setItem(row_idx, 8, rtd_child)
                    self.table.setItem(row_idx, 9, rtp_child)

                    row_idx += 1

        # Restore selection
        if selected_items:
            sel_model = self.table.selectionModel()
            first_matched_row = None
            for r in range(self.table.rowCount()):
                id_item = self.table.item(r, 0)
                name_item = self.table.item(r, 1)
                if id_item:
                    pid = id_item.data(Qt.ItemDataRole.UserRole)
                    is_child = bool(name_item and name_item.text().startswith("  ↳"))
                    if (pid, is_child) in selected_items:
                        if first_matched_row is None:
                            first_matched_row = r
                        sel_model.select(
                            self.table.model().index(r, 0),
                            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
                        )
            if first_matched_row is not None:
                sel_model.setCurrentIndex(
                    self.table.model().index(first_matched_row, 0),
                    QItemSelectionModel.SelectionFlag.NoUpdate
                )

        if search_text and self.table.rowCount() == 0 and bool(images_cache):
            self.table.set_placeholder_state(tr_ui("placeholder_no_filter_matches"), show_button=False, color="crimson")
        else:
            self.table.set_placeholder_state(tr_ui("placeholder_no_studies_in_folder"), show_button=False)
        self.table.update_placeholder_visibility()
        self.table.blockSignals(False)
        self.table.setUpdatesEnabled(True)
        if self.main_window and hasattr(self.main_window, 'on_images_selection_changed'):
            self.main_window.on_images_selection_changed()

