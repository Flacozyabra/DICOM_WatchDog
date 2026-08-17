# -*- coding: utf-8 -*-
"""Table and Tab Context Menu Controller for DICOM WatchDog."""

import os
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QMenu, QTableWidget, QInputDialog, QTabBar

from core.locale_utils import tr_ui, tr_log
from core.logger import log_message


def apply_dark_title_bar(widget):
    import sys
    if sys.platform == "win32":
        try:
            import ctypes
            hwnd = int(widget.winId())
            DWMWA_USE_IMMERSIVE_DARK_MODE = 20
            set_window_attribute = ctypes.windll.dwmapi.DwmSetWindowAttribute
            value = ctypes.c_int(2)
            set_window_attribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(value), ctypes.sizeof(value))
        except Exception:
            pass


class TableContextMenuManager:
    """Управляет контекстными меню для таблиц (CT Images, Archive, PACS), заголовков и вкладок."""

    def __init__(self, main_window):
        self.mw = main_window

    def show_header_context_menu(self, pos: QPoint, table: QTableWidget):
        header = table.horizontalHeader()
        menu = QMenu(self.mw)
        menu.setStyleSheet("QMenu { background-color: #1a1a1a; color: #ffffff; border: 1px solid #3d3d3d; } "
                           "QMenu::item:selected { background-color: #2b2b2b; }")
        
        column_count = table.columnCount()
        for i in range(column_count):
            label = table.horizontalHeaderItem(i).text()
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(not table.isColumnHidden(i))
            action.toggled.connect(lambda checked, idx=i, t=table: [t.setColumnHidden(idx, not checked), self.mw.save_table_state(t)])
            
        menu.exec(header.mapToGlobal(pos))

    def show_tab_context_menu(self, pos: QPoint):
        index = self.mw.tab_widget.tabBar().tabAt(pos)
        if index < 0:
            return

        widget = self.mw.tab_widget.widget(index)
        menu = QMenu(self.mw)

        # Только для вкладок КТ-снимки и Архив есть пункт "Открыть папку"
        if widget == self.mw.images_tab or widget == self.mw.archive_tab:
            open_folder_action = QAction(tr_ui("ctx_open_folder"), self.mw)
            path = self.mw.config.get('ct_images_dir', '') if widget == self.mw.images_tab else self.mw.config.get('archive_dir', '')

            def open_dir():
                if path and os.path.exists(path):
                    try:
                        os.startfile(path)
                    except Exception as e:
                        log_message(self.mw.output_field, tr_log("log_failed_open_folder", os.path.basename(path), e))

            open_folder_action.triggered.connect(open_dir)
            if not path or not os.path.exists(path):
                open_folder_action.setEnabled(False)
            menu.addAction(open_folder_action)

        # Для всех вкладок есть пункт "Переименовать"
        rename_action = QAction(tr_ui("ctx_rename"), self.mw)
        rename_action.triggered.connect(lambda: self.rename_tab_dialog(index))
        menu.addAction(rename_action)

        # Если задано кастомное имя, добавляем пункт "Сбросить название"
        key = None
        if widget == self.mw.images_tab:
            key = 'custom_tab_name_ct'
        elif widget == self.mw.archive_tab:
            key = 'custom_tab_name_archive'
        elif widget == self.mw.pacs_tab:
            key = 'custom_tab_name_pacs'

        if key and self.mw.config.get(key):
            reset_action = QAction(tr_ui("ctx_reset_tab_name"), self.mw)
            def do_reset():
                self.mw.config.pop(key, None)
                self.mw.save_current_config()
                self.mw.retranslate_ui()
            reset_action.triggered.connect(do_reset)
            menu.addAction(reset_action)

        menu.exec(self.mw.tab_widget.tabBar().mapToGlobal(pos))

    def rename_tab_dialog(self, index: int):
        current_name = self.mw.tab_widget.tabText(index)
        widget = self.mw.tab_widget.widget(index)

        dialog = QInputDialog(self.mw)
        dialog.setWindowTitle(tr_ui("dlg_rename_tab_title"))
        dialog.setLabelText(tr_ui("dlg_rename_tab_label", current_name))
        dialog.setTextValue(current_name)
        dialog.setInputMode(QInputDialog.InputMode.TextInput)
        dialog.setStyleSheet(self.mw.styleSheet())
        apply_dark_title_bar(dialog)

        ok = dialog.exec()
        new_name = dialog.textValue()

        if ok:
            new_name = new_name.strip()
            key = None
            if widget == self.mw.images_tab:
                key = 'custom_tab_name_ct'
            elif widget == self.mw.archive_tab:
                key = 'custom_tab_name_archive'
            elif widget == self.mw.pacs_tab:
                key = 'custom_tab_name_pacs'

            if key:
                if new_name:
                    self.mw.config[key] = new_name
                else:
                    self.mw.config.pop(key, None)
                self.mw.save_current_config()
                self.mw.retranslate_ui()

    def show_images_context_menu(self, pos: QPoint):
        index = self.mw.images_table.indexAt(pos)
        if not index.isValid():
            return

        row = index.row()
        id_item = self.mw.images_table.item(row, 0)
        patient_id = id_item.data(Qt.ItemDataRole.UserRole) if id_item else ""
        patient_name = self.mw.images_table.item(row, 1).text()

        if patient_id in self.mw.active_file_operations:
            return

        menu = QMenu(self.mw)

        open_folder_action = QAction(tr_ui("ctx_open_folder"), self.mw)
        open_folder_action.triggered.connect(lambda: self.mw.open_patient_folder(patient_id, is_archive=False))

        delete_action = QAction(tr_ui("ctx_delete_patient"), self.mw)
        delete_action.triggered.connect(lambda: self.mw.delete_patient_action(patient_id, patient_name))

        archive_action = QAction(self.mw.get_move_to_archive_text(), self.mw)
        archive_action.triggered.connect(lambda: self.mw.archive_patient_action(patient_id, patient_name))

        clean_str_action = QAction(tr_ui("ctx_delete_str"), self.mw)
        clean_str_action.triggered.connect(lambda: self.mw.clean_str_action(patient_id))

        menu.addAction(open_folder_action)
        menu.addAction(delete_action)
        if self.mw.config.get('show_tab_archive', 'True').lower() == 'true':
            menu.addAction(archive_action)
        menu.addAction(clean_str_action)

        menu.exec(self.mw.images_table.viewport().mapToGlobal(pos))

    def show_archive_context_menu(self, pos: QPoint):
        index = self.mw.archive_table.indexAt(pos)
        if not index.isValid():
            return

        row = index.row()
        id_item = self.mw.archive_table.item(row, 0)
        patient_id = id_item.data(Qt.ItemDataRole.UserRole) if id_item else ""
        patient_name = self.mw.archive_table.item(row, 1).text()

        if patient_id in self.mw.active_file_operations:
            return

        menu = QMenu(self.mw)

        open_folder_action = QAction(tr_ui("ctx_open_folder"), self.mw)
        open_folder_action.triggered.connect(lambda: self.mw.open_patient_folder(patient_id, is_archive=True))

        restore_action = QAction(self.mw.get_restore_to_ct_text(), self.mw)
        restore_action.triggered.connect(self.mw.move_from_archive_cmd)

        delete_action = QAction(tr_ui("ctx_delete_archive_patient"), self.mw)
        delete_action.triggered.connect(lambda: self.mw.delete_archive_patient_action(patient_id, patient_name))

        menu.addAction(open_folder_action)
        menu.addAction(restore_action)
        menu.addAction(delete_action)

        menu.exec(self.mw.archive_table.viewport().mapToGlobal(pos))

    def show_pacs_context_menu(self, pos: QPoint):
        item = self.mw.pacs_table.itemAt(pos)
        if not item:
            return
        row = item.row()
        self.mw.pacs_table.selectRow(row)

        menu = QMenu(self.mw)
        menu.setStyleSheet("""
            QMenu {
                background-color: #1f1f1f;
                color: #ffffff;
                border: 1px solid #3d3d3d;
                border-radius: 4px;
                padding: 4px;
                font-family: 'Segoe UI';
                font-size: 13px;
            }
            QMenu::item {
                padding: 6px 16px;
                border-radius: 3px;
            }
            QMenu::item:selected {
                background-color: #1f538d;
                color: #ffffff;
            }
        """)
        action_send = menu.addAction(self.mw.get_send_to_ct_text())
        action_send.triggered.connect(self.mw.send_to_ct_images_cmd)
        menu.exec(self.mw.pacs_table.viewport().mapToGlobal(pos))
