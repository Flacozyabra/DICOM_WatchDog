# -*- coding: utf-8 -*-
"""Archive Tab (Архив КТ) for DICOM WatchDog."""

import os
try:
    from PyQt6.QtCore import Qt, QSize
    from PyQt6.QtGui import QIcon
    from PyQt6.QtWidgets import (
        QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton
    )
except ImportError:
    from PyQt5.QtCore import Qt, QSize
    from PyQt5.QtGui import QIcon
    from PyQt5.QtWidgets import (
        QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton
    )

from ui.table_widgets import ToggleTableWidget
from core.config_utils import get_resource_path
from core.locale_utils import tr_ui


class ArchiveTab(QWidget):
    """Виджет вкладки «Архив КТ»."""
    def __init__(self, main_window=None, parent=None):
        super().__init__(parent)
        self.main_window = main_window
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 10, 4, 6)
        layout.setSpacing(10)

        # Таблица архива
        self.table = ToggleTableWidget(self)
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels([
            "Patient ID", "Patient Name", "Modality", "Slices", "Scanning Area", 
            "Study datetime", "Folder datetime", "STR"
        ])
        self.table.setColumnHidden(2, True)
        self.table.horizontalHeader().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        if self.main_window:
            self.table.horizontalHeader().customContextMenuRequested.connect(
                lambda pos: self.main_window.show_header_context_menu(pos, self.table)
            )
            self.main_window.setup_table_properties(self.table)
            self.main_window.restore_table_state(self.table)
            self.table.cellDoubleClicked.connect(self.main_window.on_archive_double_clicked)
            self.table.customContextMenuRequested.connect(self.main_window.show_archive_context_menu)
            self.table.itemSelectionChanged.connect(self.main_window.on_archive_selection_changed)

        self.table.set_placeholder_text("В этой папке нет исследований")
        self.table.update_placeholder_visibility()
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        layout.addWidget(self.table)

        # Панель поиска и восстановления
        search_layout = QHBoxLayout()
        search_layout.setContentsMargins(5, 0, 5, 0)
        search_layout.setSpacing(10)

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
            self.search_entry.textChanged.connect(self.main_window.search_patient_archive)
        search_layout.addWidget(self.search_entry, stretch=1, alignment=Qt.AlignmentFlag.AlignVCenter)

        # Кнопка поиска
        self.search_btn = QPushButton("Search", self)
        self.search_btn.setFixedHeight(30)
        if self.main_window:
            self.search_btn.clicked.connect(self.main_window.search_patient_archive)
        search_layout.addWidget(self.search_btn, alignment=Qt.AlignmentFlag.AlignVCenter)

        # Кнопка восстановления
        self.move_from_archive_btn = QPushButton("Move to CT images", self)
        self.move_from_archive_btn.setFixedHeight(30)
        self.move_from_archive_btn.setEnabled(False)
        if self.main_window:
            self.move_from_archive_btn.clicked.connect(self.main_window.move_from_archive_cmd)
        search_layout.addWidget(self.move_from_archive_btn, alignment=Qt.AlignmentFlag.AlignVCenter)

        # Кнопка настроек (шестеренка)
        self.settings_btn = QPushButton(self)
        self.settings_btn.setIcon(QIcon(get_resource_path("themes/settings.svg")))
        self.settings_btn.setIconSize(QSize(20, 20))
        self.settings_btn.setFixedSize(35, 30)
        self.settings_btn.setToolTip("Настройки папок и интервалов")
        if self.main_window:
            self.settings_btn.clicked.connect(self.main_window.open_settings_cmd)
        search_layout.addWidget(self.settings_btn, alignment=Qt.AlignmentFlag.AlignVCenter)

        layout.addLayout(search_layout)

    def retranslate_ui(self):
        self.search_entry.setPlaceholderText(tr_ui("placeholder_search_patient"))
        self.search_btn.setText(tr_ui("btn_search"))
        if self.main_window:
            self.move_from_archive_btn.setText(self.main_window.get_restore_to_ct_text())
        self.table.set_placeholder_text(tr_ui("placeholder_no_studies_in_folder"))
        self.settings_btn.setToolTip(tr_ui("tooltip_settings_btn"))
        self.search_entry.setToolTip(tr_ui("tooltip_search_archive_entry"))
        self.search_btn.setToolTip(tr_ui("tooltip_search_archive_btn"))
        self.move_from_archive_btn.setToolTip(tr_ui("tooltip_restore_from_archive"))
