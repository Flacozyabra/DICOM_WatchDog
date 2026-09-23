# -*- coding: utf-8 -*-
"""Table state and column layout manager for DICOM WatchDog."""

try:
    from PyQt6.QtWidgets import QAbstractItemView, QHeaderView
except ImportError:
    from PyQt5.QtWidgets import QAbstractItemView, QHeaderView


class TableStateManager:
    """Менеджер визуального состояния, стилей и расположения колонок таблиц."""

    def __init__(self, main_window):
        self.main_window = main_window

    @property
    def config(self):
        return getattr(self.main_window, 'config', {})

    def setup_table_properties(self, table):
        # Настройка поведения таблиц
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setAlternatingRowColors(False)  # Отключаем зебру
        table.setShowGrid(False)  # Отключаем сетку
        table.verticalHeader().setVisible(False)

        # Динамическая высота строки в зависимости от размера шрифта
        font_size = self.config.get('patient_font_size', 16)
        row_height = max(25, font_size + 12)
        table.verticalHeader().setDefaultSectionSize(row_height)

        # Установка шрифтов через styleSheet, так как глобальный QSS переопределяет setFont()
        weight_map = {
            "Regular": "400",
            "Semibold": "600",
            "Bold": "700"
        }
        weight_str = self.config.get('patient_weight', 'Semibold')
        weight = weight_map.get(weight_str, "400")
        table_style = f"font-size: {font_size}px; font-weight: {weight}; font-family: 'Segoe UI';"
        header_style = """
            QHeaderView::section {
                background-color: #1a1a1a;
                color: #ffffff;
                padding: 6px;
                border: none;
                border-left: 1px solid qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 transparent, stop:0.25 transparent, stop:0.3 #3d3d3d, stop:0.7 #3d3d3d, stop:0.75 transparent, stop:1 transparent);
                font-size: 14px;
                font-weight: normal;
                font-family: 'Segoe UI';
            }
            QHeaderView::section:first {
                border-left: none;
            }
            QHeaderView {
                background-color: #1a1a1a;
                border: none;
            }
        """
        table.setStyleSheet(table_style)
        table.horizontalHeader().setStyleSheet(header_style)

        table.horizontalHeader().setSectionsMovable(True)
        table.horizontalHeader().sectionMoved.connect(
            lambda logical, old, new, t=table: self.on_section_moved(logical, old, new, t)
        )

        # Растягивание колонок
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(False)

        # Установим пропорции ширины по умолчанию
        if table.columnCount() in (8, 10):
            table.setColumnWidth(0, 140)  # ID
            table.setColumnWidth(1, 300)  # Name
            table.setColumnWidth(2, 65)   # Modality
            table.setColumnWidth(3, 65)   # Slices
            table.setColumnWidth(4, 120)  # Scanning Area
            table.setColumnWidth(5, 150)  # Study
            table.setColumnWidth(6, 150)  # Folder
            table.setColumnWidth(7, 45)   # STR
            if table.columnCount() == 10:
                table.setColumnWidth(8, 45)   # RTD
                table.setColumnWidth(9, 45)   # RTP
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)  # Имя тянется
        elif table.columnCount() == 6:
            table.setColumnWidth(0, 140)  # ID
            table.setColumnWidth(1, 300)  # Name
            table.setColumnWidth(2, 70)   # Modality
            table.setColumnWidth(3, 65)   # Slices
            table.setColumnWidth(4, 130)  # Scanning Area
            table.setColumnWidth(5, 150)  # Study
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)

    def on_section_moved(self, logical, old, new, table):
        self.save_table_state(table)

    def _get_table_name(self, table):
        if getattr(self.main_window, 'images_table', None) == table:
            return "images_table"
        if getattr(self.main_window, 'archive_table', None) == table:
            return "archive_table"
        if getattr(self.main_window, 'pacs_table', None) == table:
            return "pacs_table"
        if table.columnCount() in (8, 10):
            if getattr(self.main_window, 'images_table', None) is None:
                return "images_table"
            return "archive_table"
        if table.columnCount() == 6:
            return "pacs_table"
        return None

    def save_table_state(self, table):
        table_name = self._get_table_name(table)
        if not table_name:
            return

        header = table.horizontalHeader()
        column_count = table.columnCount()

        visual_order = []
        for visual_idx in range(column_count):
            visual_order.append(header.logicalIndex(visual_idx))

        visibility = []
        for i in range(column_count):
            visibility.append(not table.isColumnHidden(i))

        if 'tables_state' not in self.config:
            self.config['tables_state'] = {}

        self.config['tables_state'][table_name] = {
            'visual_order': visual_order,
            'visibility': visibility
        }
        if hasattr(self.main_window, 'save_current_config'):
            self.main_window.save_current_config()

    def restore_table_state(self, table):
        table_name = self._get_table_name(table)
        if not table_name:
            return

        tables_state = self.config.get('tables_state', {})
        state = tables_state.get(table_name)
        if not state:
            return

        header = table.horizontalHeader()
        column_count = table.columnCount()

        header.blockSignals(True)

        # 1. Восстанавливаем порядок
        visual_order = state.get('visual_order')
        if visual_order:
            if len(visual_order) < column_count:
                existing_set = set(visual_order)
                missing = [idx for idx in range(column_count) if idx not in existing_set]
                visual_order = list(visual_order) + missing
            if len(visual_order) == column_count:
                for visual_idx, logical_idx in enumerate(visual_order):
                    current_visual_idx = header.visualIndex(logical_idx)
                    if current_visual_idx != visual_idx:
                        header.moveSection(current_visual_idx, visual_idx)

        # 2. Восстанавливаем видимость
        visibility = state.get('visibility')
        if visibility:
            if len(visibility) < column_count:
                # По умолчанию новые колонки (RTD, RTP) выключены
                visibility = list(visibility) + [False] * (column_count - len(visibility))
            for i, visible in enumerate(visibility[:column_count]):
                table.setColumnHidden(i, not visible)

        # Гарантируем растягивание колонки Patient Name (1)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.blockSignals(False)
