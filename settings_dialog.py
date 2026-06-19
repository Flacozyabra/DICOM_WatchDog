import os
import sys
from PyQt6.QtCore import Qt, QRect, QPoint, QPropertyAnimation, pyqtProperty
from PyQt6.QtGui import QColor, QFont, QPainter, QBrush, QPen
from PyQt6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
                             QLineEdit, QPushButton, QFileDialog, QFormLayout, 
                             QSpinBox, QCheckBox, QDialogButtonBox, QMessageBox,
                             QComboBox)

class ToggleSwitch(QCheckBox):
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._bg_color = QColor("#555555")
        self._active_color = QColor("#1f538d")
        self._knob_color = QColor("#ffffff")
        self._knob_position = 18 if self.isChecked() else 2
        self.setFixedHeight(22)

    @pyqtProperty(int)
    def knob_position(self):
        return self._knob_position

    @knob_position.setter
    def knob_position(self, pos):
        self._knob_position = pos
        self.update()

    def hitButton(self, pos: QPoint) -> bool:
        return self.rect().contains(pos)

    def nextCheckState(self):
        super().nextCheckState()
        end_value = 18 if self.isChecked() else 2
        self.anim = QPropertyAnimation(self, b"knob_position")
        self.anim.setDuration(120)
        self.anim.setEndValue(end_value)
        self.anim.start()

    def setChecked(self, state):
        super().setChecked(state)
        self._knob_position = 18 if state else 2
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Рисуем дорожку слайдера
        track_rect = QRect(0, 3, 32, 16)
        if self.isChecked():
            p.setBrush(QBrush(self._active_color))
        else:
            p.setBrush(QBrush(self._bg_color))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(track_rect, 8, 8)
        
        # Рисуем бегунок (круг)
        p.setBrush(QBrush(self._knob_color))
        p.drawEllipse(self._knob_position, 5, 12, 12)
        
        # Рисуем текст метки
        if self.text():
            p.setPen(QPen(QColor("#ffffff")))
            font_metrics = p.fontMetrics()
            y_offset = (self.height() - font_metrics.height()) // 2 + font_metrics.ascent()
            p.drawText(38, y_offset, self.text())
            
        p.end()

class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройки")
        self.setMinimumWidth(650)
        
        # Темная рамка окна Windows
        if sys.platform == "win32":
            import ctypes
            try:
                hwnd = int(self.winId())
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, 20, ctypes.byref(ctypes.c_int(1)), ctypes.sizeof(ctypes.c_int)
                )
            except Exception:
                try:
                    ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        hwnd, 19, ctypes.byref(ctypes.c_int(1)), ctypes.sizeof(ctypes.c_int)
                    )
                except Exception:
                    pass

        self.config = self.load_config()
        self.init_ui()

    def load_config(self):
        config = {
            'ct_images_dir': '',
            'archive_dir': '',
            'fix_switch_value': 'True',
            'client_dir': '',
            'archive_slice': 2,
            'x': 1000,
            'y': 600,
            'dx': 350,
            'dy': 100,
            'log_font_size': 12,
            'folder_scan_time': 10000,
            'notification_is': 'on',
            'icon_path': '',
            'pacs_scan_time': 10000,
            'auto_update_is': 'on',
            'patient_font_size': 14,
            'patient_weight': 'Regular'
        }
        if os.path.exists("config.txt"):
            try:
                with open("config.txt", "r", encoding="utf-8") as f:
                    lines = f.readlines()
                    if len(lines) > 0: config['ct_images_dir'] = lines[0].strip()
                    if len(lines) > 1: config['archive_dir'] = lines[1].strip()
                    if len(lines) > 4: config['fix_switch_value'] = lines[4].strip()
                    if len(lines) > 7: config['client_dir'] = lines[7].strip()
                    if len(lines) > 10: config['archive_slice'] = int(lines[10].strip() or "2")
                    if len(lines) > 16:
                         config['x'] = int(lines[13].strip() or "1000")
                         config['y'] = int(lines[14].strip() or "600")
                         config['dx'] = int(lines[15].strip() or "350")
                         config['dy'] = int(lines[16].strip() or "100")
                    if len(lines) > 19: config['log_font_size'] = int(lines[19].strip() or "12")
                    if len(lines) > 22: config['folder_scan_time'] = int(lines[22].strip() or "10000")
                    if len(lines) > 25: config['notification_is'] = lines[25].strip()
                    if len(lines) > 28: config['icon_path'] = lines[28].strip()
                    if len(lines) > 31: config['pacs_scan_time'] = int(lines[31].strip() or "10000")
                    if len(lines) > 34: config['auto_update_is'] = lines[34].strip()
                    if len(lines) > 37: config['patient_font_size'] = int(lines[37].strip() or "14")
                    if len(lines) > 40: config['patient_weight'] = lines[40].strip()
            except Exception as e:
                print(f"Error loading config.txt: {e}")
        return config

    def save_config(self):
        lines = ["" for _ in range(41)]
        lines[0] = f"{self.config['ct_images_dir']}\n"
        lines[1] = f"{self.config['archive_dir']}\n"
        lines[2] = "\n"
        lines[3] = "fix_switch_value:\n"
        lines[4] = f"{self.config['fix_switch_value']}\n"
        lines[5] = "\n"
        lines[6] = "client_dir:\n"
        lines[7] = f"{self.config['client_dir']}\n"
        lines[8] = "\n"
        lines[9] = "archive_slice:\n"
        lines[10] = f"{self.config['archive_slice']}\n"
        lines[11] = "\n"
        lines[12] = "window_config(x, y, dx, dy):\n"
        lines[13] = f"{self.config['x']}\n"
        lines[14] = f"{self.config['y']}\n"
        lines[15] = f"{self.config['dx']}\n"
        lines[16] = f"{self.config['dy']}\n"
        lines[17] = "\n"
        lines[18] = "log_font_size:\n"
        lines[19] = f"{self.config['log_font_size']}\n"
        lines[20] = "\n"
        lines[21] = "folder_scan_time(msec):\n"
        lines[22] = f"{self.config['folder_scan_time']}\n"
        lines[23] = "\n"
        lines[24] = "notification_is:\n"
        lines[25] = f"{self.config['notification_is']}\n"
        lines[26] = "\n"
        lines[27] = "icon_path:\n"
        lines[28] = f"{self.config['icon_path']}\n"
        lines[29] = "\n"
        lines[30] = "pacs_scan_time(msec):\n"
        lines[31] = f"{self.config['pacs_scan_time']}\n"
        lines[32] = "\n"
        lines[33] = "auto_update_is:\n"
        lines[34] = f"{self.config['auto_update_is']}\n"
        lines[35] = "\n"
        lines[36] = "patient_font_size:\n"
        lines[37] = f"{self.config['patient_font_size']}\n"
        lines[38] = "\n"
        lines[39] = "patient_weight:\n"
        lines[40] = f"{self.config.get('patient_weight', 'Regular')}\n"

        try:
            with open("config.txt", "w", encoding="utf-8") as f:
                f.writelines(lines)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить конфигурацию: {e}")

    def init_ui(self):
        layout = QVBoxLayout()
        form = QFormLayout()

        # Archive Dir
        self.archive_edit = QLineEdit(self.config['archive_dir'])
        archive_btn = QPushButton("Обзор...")
        archive_btn.clicked.connect(lambda: self.browse_folder(self.archive_edit, "Выберите папку архива"))
        h_layout2 = QHBoxLayout()
        h_layout2.addWidget(self.archive_edit)
        h_layout2.addWidget(archive_btn)
        form.addRow("Папка CT Archive:", h_layout2)


        # Folder Scan Interval (sec)
        self.folder_scan_spin = QSpinBox()
        self.folder_scan_spin.setRange(1, 300)
        self.folder_scan_spin.setValue(self.config['folder_scan_time'] // 1000)
        form.addRow("Интервал сканирования папок (сек):", self.folder_scan_spin)

        # PACS Scan Interval (sec)
        self.pacs_scan_spin = QSpinBox()
        self.pacs_scan_spin.setRange(1, 300)
        self.pacs_scan_spin.setValue(self.config['pacs_scan_time'] // 1000)
        form.addRow("Интервал сканирования PACS (сек):", self.pacs_scan_spin)

        # Archive Slice (Max visible rows)
        self.archive_slice_spin = QSpinBox()
        self.archive_slice_spin.setRange(1, 1000)
        self.archive_slice_spin.setValue(self.config['archive_slice'])
        form.addRow("Лимит строк архива:", self.archive_slice_spin)

        # Font size (logs)
        self.font_size_spin = QSpinBox()
        self.font_size_spin.setRange(8, 24)
        self.font_size_spin.setValue(self.config['log_font_size'])
        form.addRow("Размер шрифта логов:", self.font_size_spin)

        # Patient Font Size
        self.patient_font_spin = QSpinBox()
        self.patient_font_spin.setRange(8, 36)
        self.patient_font_spin.setValue(self.config.get('patient_font_size', 14))
        form.addRow("Размер шрифта пациентов:", self.patient_font_spin)

        # Patient Font Weight
        self.patient_weight_combo = QComboBox()
        self.patient_weight_combo.addItems(["Regular", "Semibold", "Bold"])
        self.patient_weight_combo.setCurrentText(self.config.get('patient_weight', 'Regular'))
        form.addRow("Толщина шрифта пациентов:", self.patient_weight_combo)

        # Notifications
        self.notify_cb = ToggleSwitch()
        self.notify_cb.setChecked(self.config['notification_is'].lower() == 'on')
        form.addRow("Включить уведомления:", self.notify_cb)

        # Auto Update
        self.auto_update_cb = ToggleSwitch()
        self.auto_update_cb.setChecked(self.config.get('auto_update_is', 'on').lower() == 'on')
        form.addRow("Автообновление (Auto update):", self.auto_update_cb)

        # Fix Switch value
        self.fix_cb = ToggleSwitch()
        self.fix_cb.setChecked(self.config['fix_switch_value'].lower() == 'true')
        form.addRow("Разрешить Fix Files (исправление):", self.fix_cb)

        layout.addLayout(form)

        # Dialog Buttons (OK / Cancel)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept_settings)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.setLayout(layout)

    def browse_folder(self, line_edit, title):
        dir_path = QFileDialog.getExistingDirectory(self, title, line_edit.text())
        if dir_path:
            line_edit.setText(os.path.normpath(dir_path))

    def accept_settings(self):
        # Update config dictionary
        self.config['archive_dir'] = self.archive_edit.text()
        self.config['folder_scan_time'] = self.folder_scan_spin.value() * 1000
        self.config['pacs_scan_time'] = self.pacs_scan_spin.value() * 1000
        self.config['archive_slice'] = self.archive_slice_spin.value()
        self.config['log_font_size'] = self.font_size_spin.value()
        self.config['patient_font_size'] = self.patient_font_spin.value()
        self.config['patient_weight'] = self.patient_weight_combo.currentText()
        self.config['notification_is'] = 'on' if self.notify_cb.isChecked() else 'off'
        self.config['auto_update_is'] = 'on' if self.auto_update_cb.isChecked() else 'off'
        self.config['fix_switch_value'] = 'True' if self.fix_cb.isChecked() else 'False'

        # Save to file
        self.save_config()
        self.accept()
