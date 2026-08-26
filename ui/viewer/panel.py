from __future__ import annotations

import os
import io
import gc
import numpy as np
import pydicom

from PyQt6.QtCore import Qt, pyqtSignal, QSize, QPoint, QRect
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel,
    QPushButton, QComboBox, QSlider, QListWidget, QListWidgetItem,
    QStackedWidget, QButtonGroup, QSizePolicy
)
from PyQt6.QtGui import (
    QIcon, QFont, QPixmap, QBrush, QColor, QImage
)

from ui.toggle_switch import ToggleSwitch
from core.config_utils import get_resource_path
from core.locale_utils import tr_ui

from .parsers import safe_dcmread
from .workers import (
    PatientSeriesLoaderWorker, StructureLoaderWorker,
    DoseLoaderWorker, DRRPrecomputeWorker, BEVStructurePrecomputeWorker
)
from .controls import HUVerticalSlider, DRRProgressDialog
from .canvas import DicomViewerWidget


class DicomViewerPanel(QWidget):
    """Панель управления просмотром DICOM серий с поддержкой RTSTRUCT и RTDOSE/RTPLAN."""
    close_requested = pyqtSignal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.parent_app = parent
        self.sorted_files = []
        self.struct_files = []
        self.dose_files = []
        self.plan_files = []
        self.current_index = -1
        self.is_loading = False
        self.loader_worker = None
        self.struct_worker = None
        self.dose_worker = None
        self.drr_worker = None
        self.bev_struct_worker = None
        self.drr_dialog = None
        self.progress_dialog = None
        self.pixmap_cache = {}

        self.window_width = 400.0
        self.window_center = 40.0
        self.default_wc = 40.0
        self.default_ww = 400.0

        self.setup_ui()

    def setup_ui(self) -> None:
        root_layout = QHBoxLayout(self)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(10)

        # 1. Панель структур и изодоз слева (растянута на всю высоту)
        self.setup_left_panel()
        root_layout.addWidget(self.structures_panel)

        # 2. Правая область (Верхняя панель + Холст со шкалой HU + Слайдер)
        right_layout = QVBoxLayout()
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        # Верхняя панель управления
        top_layout = QHBoxLayout()
        top_layout.setSpacing(6)
        
        self.lbl_info = QLabel(self)
        self.lbl_info.setStyleSheet("font-size: 13px; font-weight: bold; color: #FFFFFF;")
        self.lbl_info.hide()
        top_layout.addWidget(self.lbl_info)

        top_layout.addStretch()

        # Метка и выпадающий список для выбора файла дозы RTDOSE
        self.lbl_dose = QLabel("RTD", self)
        self.lbl_dose.setStyleSheet("font-size: 11px; font-weight: bold; color: #9CA3AF;")
        top_layout.addWidget(self.lbl_dose)

        self.cb_dose = QComboBox(self)
        self.cb_dose.setFixedWidth(160)
        self.cb_dose.setStyleSheet("""
            QComboBox {
                background-color: #2A2A2A;
                border: 1px solid #374151;
                border-radius: 4px;
                color: #FFFFFF;
                padding: 0px 8px;
                font-size: 12px;
                min-height: 28px;
                max-height: 28px;
            }
            QComboBox QAbstractItemView {
                background-color: #1A1A1A;
                border: 1px solid #374151;
                color: #FFFFFF;
                selection-background-color: #3B82F6;
            }
        """)
        self.cb_dose.addItem(tr_ui("viewer_no_dose_available"), None)
        self.cb_dose.setEnabled(False)
        top_layout.addWidget(self.cb_dose)

        # Метка и выпадающий список для выбора набора структур RTSTRUCT
        self.lbl_structures = QLabel("STR", self)
        self.lbl_structures.setStyleSheet("font-size: 11px; font-weight: bold; color: #9CA3AF;")
        top_layout.addWidget(self.lbl_structures)

        self.cb_structures = QComboBox(self)
        self.cb_structures.setFixedWidth(160)
        self.cb_structures.setStyleSheet("""
            QComboBox {
                background-color: #2A2A2A;
                border: 1px solid #374151;
                border-radius: 4px;
                color: #FFFFFF;
                padding: 0px 8px;
                font-size: 12px;
                min-height: 28px;
                max-height: 28px;
            }
            QComboBox QAbstractItemView {
                background-color: #1A1A1A;
                border: 1px solid #374151;
                color: #FFFFFF;
                selection-background-color: #3B82F6;
            }
        """)
        self.cb_structures.addItem(tr_ui("viewer_no_structures_available"), None)
        self.cb_structures.setEnabled(False)
        top_layout.addWidget(self.cb_structures)



        # Метка и выпадающий список пресетов HU
        self.lbl_presets = QLabel("HU", self)
        self.lbl_presets.setStyleSheet("font-size: 11px; font-weight: bold; color: #9CA3AF;")
        top_layout.addWidget(self.lbl_presets)

        self.cb_presets = QComboBox(self)
        self.cb_presets.setFixedWidth(160)
        self.cb_presets.setStyleSheet("""
            QComboBox {
                background-color: #2A2A2A;
                border: 1px solid #374151;
                border-radius: 4px;
                color: #FFFFFF;
                padding: 0px 8px;
                font-size: 12px;
                min-height: 28px;
                max-height: 28px;
            }
            QComboBox QAbstractItemView {
                background-color: #1A1A1A;
                border: 1px solid #374151;
                color: #FFFFFF;
                selection-background-color: #3B82F6;
            }
        """)
        top_layout.addWidget(self.cb_presets)

        # Загружаем иконки
        self.img_dose_point = QIcon(get_resource_path("themes/target.png"))
        self.img_ruler = QIcon(get_resource_path("themes/ruler.png"))
        self.img_hu = QIcon(get_resource_path("themes/hu.png"))
        self.img_osd = QIcon(get_resource_path("themes/eye.png"))
        self.img_close = QIcon(get_resource_path("themes/close.png"))

        # Кнопка отображения пучков на срезах КТ
        self.btn_beams = QPushButton(tr_ui("viewer_beams_btn"), self)
        self.btn_beams.setFixedSize(50, 28)
        self.btn_beams.setToolTip(tr_ui("viewer_beams_tooltip"))
        self.btn_beams.setEnabled(False)
        self.btn_beams.clicked.connect(self.toggle_beams)
        top_layout.addWidget(self.btn_beams)

        # Кнопка "Вид из пучка" (BEV)
        self.btn_bev = QPushButton(tr_ui("viewer_bev_btn"), self)
        self.btn_bev.setFixedSize(36, 28)
        self.btn_bev.setToolTip(tr_ui("viewer_bev_tooltip"))
        self.btn_bev.setEnabled(False)
        self.btn_bev.clicked.connect(self.toggle_bev)
        top_layout.addWidget(self.btn_bev)

        # Кнопка "Доза в точке"
        self.btn_dose_point = QPushButton(self)
        self.btn_dose_point.setIcon(self.img_dose_point)
        self.btn_dose_point.setIconSize(QSize(20, 20))
        self.btn_dose_point.setFixedSize(28, 28)
        self.btn_dose_point.setToolTip(tr_ui("viewer_point_dose"))
        self.btn_dose_point.setEnabled(False)
        self.btn_dose_point.clicked.connect(self.toggle_dose_point)
        top_layout.addWidget(self.btn_dose_point)

        # Кнопка линейки
        self.btn_ruler = QPushButton(self)
        self.btn_ruler.setIcon(self.img_ruler)
        self.btn_ruler.setIconSize(QSize(20, 20))
        self.btn_ruler.setFixedSize(28, 28)
        self.btn_ruler.setToolTip("Линейка")
        self.btn_ruler.clicked.connect(self.toggle_ruler)
        top_layout.addWidget(self.btn_ruler)

        # Кнопка настройки HU
        self.btn_hu = QPushButton(self)
        self.btn_hu.setIcon(self.img_hu)
        self.btn_hu.setIconSize(QSize(20, 20))
        self.btn_hu.setFixedSize(28, 28)
        self.btn_hu.setToolTip("Настройка окна HU")
        self.btn_hu.clicked.connect(self.toggle_hu)
        top_layout.addWidget(self.btn_hu)

        # Кнопка скрытия надписей OSD
        self.btn_osd = QPushButton(self)
        self.btn_osd.setIcon(self.img_osd)
        self.btn_osd.setIconSize(QSize(20, 20))
        self.btn_osd.setFixedSize(28, 28)
        self.btn_osd.setToolTip("Показать/скрыть надписи")
        self.btn_osd.clicked.connect(self.toggle_osd)
        top_layout.addWidget(self.btn_osd)

        # Кнопка закрытия
        self.btn_close = QPushButton(self)
        self.btn_close.setIcon(self.img_close)
        self.btn_close.setIconSize(QSize(20, 20))
        self.btn_close.setFixedSize(28, 28)
        self.btn_close.setToolTip("Закрыть просмотр")
        self.btn_close.clicked.connect(self.close_requested.emit)
        top_layout.addWidget(self.btn_close)
        
        right_layout.addLayout(top_layout)

        # Центральная область (Холст просмотра + шкала HU справа)
        center_layout = QHBoxLayout()
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(10)

        self.viewer = DicomViewerWidget(self)
        self.viewer.slice_scrolled.connect(self.on_slice_scrolled)
        self.viewer.window_changed.connect(self.on_window_changed)
        self.viewer.bev_beam_changed.connect(self._on_bev_beam_changed)
        self.viewer.bev_control_point_changed.connect(self._on_bev_cp_changed)
        self.viewer.drr_precompute_requested.connect(self.start_drr_precompute)
        center_layout.addWidget(self.viewer, stretch=1)

        # Создаем и добавляем шкалу HU справа
        self.setup_hu_panel()
        center_layout.addWidget(self.hu_panel)

        right_layout.addLayout(center_layout, stretch=1)

        # Информационная подпись над слайдером (для BEV режима)
        self.lbl_slider_info = QLabel("", self)
        self.lbl_slider_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_slider_info.setStyleSheet("font-size: 11px; font-weight: bold; color: #93C5FD; padding: 2px 0px;")
        self.lbl_slider_info.hide()
        right_layout.addWidget(self.lbl_slider_info)

        # Горизонтальный слайдер срезов снизу
        self.slider = QSlider(Qt.Orientation.Horizontal, self)
        self.slider.valueChanged.connect(self.on_slider_changed)
        self.slider.setStyleSheet("""
            QSlider::groove:horizontal {
                background: #1F2937;
                height: 6px;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #3B82F6;
                width: 30px;
                margin-top: -5px;
                margin-bottom: -5px;
                border-radius: 6px;
            }
            QSlider::handle:horizontal:hover {
                background: #60A5FA;
            }
        """)
        right_layout.addWidget(self.slider)

        root_layout.addLayout(right_layout, stretch=1)

        self.retranslate_ui()
        self.cb_dose.currentIndexChanged.connect(self.on_dose_file_changed)
        self.cb_structures.currentIndexChanged.connect(self.on_structure_file_changed)
        self.cb_presets.currentIndexChanged.connect(self.apply_preset)
        self.update_buttons_style()

    def setup_left_panel(self) -> None:
        self.structures_panel = QFrame(self)
        self.structures_panel.setFixedWidth(225)
        
        eye_path = get_resource_path("themes/eye.png").replace(os.sep, "/")
        self.structures_panel.setStyleSheet("""
            QFrame {
                background-color: #141414;
                border: 1px solid #282828;
                border-radius: 6px;
            }
        """)
        panel_layout = QVBoxLayout(self.structures_panel)
        panel_layout.setContentsMargins(6, 6, 6, 6)
        panel_layout.setSpacing(8)

        # 1. Сегментированный переключатель вкладок
        self.segmented_frame = QFrame(self.structures_panel)
        self.segmented_frame.setStyleSheet("""
            QFrame {
                background-color: #1A1A1A;
                border: 1px solid #282828;
                border-radius: 6px;
            }
        """)
        seg_layout = QHBoxLayout(self.segmented_frame)
        seg_layout.setContentsMargins(2, 2, 2, 2)
        seg_layout.setSpacing(2)

        self.btn_tab_structs = QPushButton(tr_ui("viewer_tab_structures"), self.segmented_frame)
        self.btn_tab_structs.setCheckable(True)
        self.btn_tab_structs.setChecked(True)
        self.btn_tab_structs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_tab_structs.setFixedHeight(26)
        seg_layout.addWidget(self.btn_tab_structs)

        self.btn_tab_isodoses = QPushButton(tr_ui("viewer_tab_isodoses"), self.segmented_frame)
        self.btn_tab_isodoses.setCheckable(True)
        self.btn_tab_isodoses.setChecked(False)
        self.btn_tab_isodoses.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_tab_isodoses.setFixedHeight(26)
        seg_layout.addWidget(self.btn_tab_isodoses)

        self.tab_btn_group = QButtonGroup(self.structures_panel)
        self.tab_btn_group.setExclusive(True)
        self.tab_btn_group.addButton(self.btn_tab_structs, 0)
        self.tab_btn_group.addButton(self.btn_tab_isodoses, 1)
        self.tab_btn_group.idClicked.connect(self.on_tab_button_clicked)

        panel_layout.addWidget(self.segmented_frame)

        # 2. Стек содержимого вкладок
        self.stack_panel = QStackedWidget(self.structures_panel)
        self.stack_panel.setStyleSheet("QStackedWidget { background: transparent; border: none; }")

        list_style = f"""
            QListWidget {{
                background-color: #0f0f0f;
                border: 1px solid #282828;
                border-radius: 6px;
                color: #FFFFFF;
                outline: 0;
                font-family: "Segoe UI", -apple-system, Roboto, sans-serif;
                font-size: 12px;
            }}
            QListWidget::item {{
                padding: 5px 8px;
                border-radius: 4px;
                margin: 2px 2px;
            }}
            QListWidget::item:hover {{
                background-color: #222222;
            }}
            QListWidget::item:selected {{
                background-color: #1f538d;
                color: #FFFFFF;
            }}
            QListWidget::indicator {{
                width: 14px;
                height: 14px;
                border: 1px solid #3d3d3d;
                border-radius: 3px;
                background-color: #0f0f0f;
            }}
            QListWidget::indicator:hover {{
                border-color: #1f538d;
                background-color: #151515;
            }}
            QListWidget::indicator:checked {{
                image: url({eye_path});
                border: 1px solid #1f538d;
                border-radius: 3px;
                background-color: #1f538d;
            }}
        """

        # 2.1 Страница "Структуры"
        page_structs = QWidget()
        page_structs.setStyleSheet("background: transparent; border: none;")
        struct_layout = QVBoxLayout(page_structs)
        struct_layout.setContentsMargins(0, 2, 0, 0)
        struct_layout.setSpacing(8)

        self.cb_show_structures = ToggleSwitch(tr_ui("viewer_show_structures"), page_structs)
        self.cb_show_structures.setChecked(True)
        self.cb_show_structures.stateChanged.connect(self.on_global_structures_changed)
        struct_layout.addWidget(self.cb_show_structures)

        self.list_structures = QListWidget(page_structs)
        self.list_structures.setStyleSheet(list_style)
        self.list_structures.itemChanged.connect(self.on_structure_item_changed)
        struct_layout.addWidget(self.list_structures)

        self.stack_panel.addWidget(page_structs)

        # 2.2 Страница "Изодозы"
        page_isodoses = QWidget()
        page_isodoses.setStyleSheet("background: transparent; border: none;")
        dose_layout = QVBoxLayout(page_isodoses)
        dose_layout.setContentsMargins(0, 2, 0, 0)
        dose_layout.setSpacing(8)

        self.cb_show_isodoses = ToggleSwitch(tr_ui("viewer_show_isodoses"), page_isodoses)
        self.cb_show_isodoses.setChecked(False)
        self.cb_show_isodoses.stateChanged.connect(self.on_global_isodoses_changed)
        dose_layout.addWidget(self.cb_show_isodoses)

        self.cb_dose_gradient = ToggleSwitch(tr_ui("viewer_show_dose_gradient"), page_isodoses)
        self.cb_dose_gradient.setChecked(True)
        self.cb_dose_gradient.stateChanged.connect(self.on_dose_gradient_changed)
        dose_layout.addWidget(self.cb_dose_gradient)

        self.lbl_dose_info = QLabel(page_isodoses)
        self.lbl_dose_info.setStyleSheet("color: #9CA3AF; font-size: 10px; font-weight: bold; padding: 0px 2px; border: none;")
        self.lbl_dose_info.setWordWrap(True)
        dose_layout.addWidget(self.lbl_dose_info)

        self.list_isodoses = QListWidget(page_isodoses)
        self.list_isodoses.setStyleSheet(list_style)
        self.list_isodoses.itemChanged.connect(self.on_isodose_item_changed)
        dose_layout.addWidget(self.list_isodoses)

        self.stack_panel.addWidget(page_isodoses)

        panel_layout.addWidget(self.stack_panel)
        self.update_tab_buttons_style()

    def on_tab_button_clicked(self, tab_id: int) -> None:
        self.stack_panel.setCurrentIndex(tab_id)
        self.update_tab_buttons_style()

    def on_dose_gradient_changed(self, state: int) -> None:
        self.viewer.show_dose_gradient = (state == 2)
        self.viewer.update()

    def update_tab_buttons_style(self) -> None:
        palette = self.parent_app.THEMES[self.parent_app.current_theme] if hasattr(self.parent_app, "current_theme") and hasattr(self.parent_app, "THEMES") else {
            "ACCENT_COLOR": "#3B82F6",
            "ACCENT_COLOR_DARK": "#2563EB",
            "TEXT_COLOR": "#FFFFFF",
            "TEXT_MUTED": "#9CA3AF",
            "BUTTON_BG": "#374151"
        }
        accent = palette.get("ACCENT_COLOR", "#3B82F6")
        accent_dark = palette.get("ACCENT_COLOR_DARK", "#2563EB")
        
        style_active = f"""
            QPushButton {{
                background-color: {accent_dark};
                color: #FFFFFF;
                border: none;
                border-radius: 4px;
                font-size: 11px;
                font-weight: bold;
                padding: 4px 0px;
            }}
        """
        style_inactive = f"""
            QPushButton {{
                background-color: transparent;
                color: {palette.get('TEXT_MUTED', '#9CA3AF')};
                border: none;
                border-radius: 4px;
                font-size: 11px;
                font-weight: bold;
                padding: 4px 0px;
            }}
            QPushButton:hover {{
                background-color: {palette.get('BUTTON_BG', '#374151')};
                color: #FFFFFF;
            }}
        """
        self.btn_tab_structs.setStyleSheet(style_active if self.btn_tab_structs.isChecked() else style_inactive)
        self.btn_tab_isodoses.setStyleSheet(style_active if self.btn_tab_isodoses.isChecked() else style_inactive)

    def on_global_structures_changed(self, state: int) -> None:
        is_on = (state == 2)
        self.viewer.show_structures_globally = is_on

        self.list_structures.blockSignals(True)
        if not is_on:
            if self.viewer.enabled_structures:
                self.saved_enabled_structures = set(self.viewer.enabled_structures)
            self.viewer.enabled_structures.clear()
            for i in range(self.list_structures.count()):
                item = self.list_structures.item(i)
                if item:
                    item.setCheckState(Qt.CheckState.Unchecked)
        else:
            to_restore = getattr(self, "saved_enabled_structures", None)
            all_names = {self.list_structures.item(i).text() for i in range(self.list_structures.count()) if self.list_structures.item(i)}
            if not to_restore:
                to_restore = all_names

            self.viewer.enabled_structures.clear()
            for i in range(self.list_structures.count()):
                item = self.list_structures.item(i)
                if item:
                    if item.text() in to_restore:
                        item.setCheckState(Qt.CheckState.Checked)
                        self.viewer.enabled_structures.add(item.text())
                    else:
                        item.setCheckState(Qt.CheckState.Unchecked)
        self.list_structures.blockSignals(False)

        self.viewer.rebuild_contour_index()
        self.viewer.update()

    def on_structure_item_changed(self, item: QListWidgetItem) -> None:
        name = item.text()
        checked = (item.checkState() == Qt.CheckState.Checked)
        if checked:
            self.viewer.enabled_structures.add(name)
        else:
            self.viewer.enabled_structures.discard(name)

        has_any = len(self.viewer.enabled_structures) > 0
        if has_any != self.cb_show_structures.isChecked():
            self.cb_show_structures.blockSignals(True)
            self.cb_show_structures.setChecked(has_any)
            self.viewer.show_structures_globally = has_any
            self.cb_show_structures.blockSignals(False)

        self.viewer.rebuild_contour_index()
        self.viewer.update()

    def apply_structures(self, parsed: dict) -> None:
        self.viewer.structures.clear()
        self.viewer.enabled_structures.clear()
        self.viewer.bev_struct_cache.clear()
        
        self.list_structures.blockSignals(True)
        self.list_structures.clear()
        
        for num, data in parsed.items():
            self.viewer.structures[num] = data
            self.viewer.enabled_structures.add(data["name"])
            
        for num, data in self.viewer.structures.items():
            item = QListWidgetItem(data["name"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            
            color = data["color"]
            item.setForeground(QBrush(color))
            self.list_structures.addItem(item)
            
        self.list_structures.blockSignals(False)
        self.viewer.rebuild_contour_index()
        self.viewer.update()

    def on_structure_file_changed(self, index: int) -> None:
        if self.struct_worker is not None and self.struct_worker.isRunning():
            self.struct_worker.quit()
            self.struct_worker.wait()

        self.viewer.structures.clear()
        self.viewer.enabled_structures.clear()
        self.viewer.bev_struct_cache.clear()
        self.viewer.rebuild_contour_index()
        
        self.list_structures.blockSignals(True)
        self.list_structures.clear()
        self.list_structures.blockSignals(False)
        self.viewer.update()
        
        if index >= 0:
            sf_path = self.cb_structures.itemData(index)
            if sf_path and os.path.exists(sf_path):
                self.struct_worker = StructureLoaderWorker(sf_path)
                self.struct_worker.finished_signal.connect(self._on_structure_loaded)
                self.struct_worker.start()

    def _on_structure_loaded(self, sf_path: str, parsed: dict) -> None:
        current_sf = self.cb_structures.currentData()
        if current_sf == sf_path:
            self.apply_structures(parsed)

    def on_global_isodoses_changed(self, state: int) -> None:
        self.viewer.show_isodoses_globally = (state == 2)
        self.viewer.update()

    def on_isodose_item_changed(self, item: QListWidgetItem) -> None:
        name = item.data(Qt.ItemDataRole.UserRole)
        checked = (item.checkState() == Qt.CheckState.Checked)
        if checked:
            self.viewer.enabled_isodose_levels.add(name)
        else:
            self.viewer.enabled_isodose_levels.discard(name)
        self.viewer.update()

    def apply_dose_data(self, dose_data: dict) -> None:
        self.viewer.set_dose_data(dose_data)
        
        self.list_isodoses.blockSignals(True)
        self.list_isodoses.clear()

        has_dose = bool(dose_data and dose_data.get("dose_grid") is not None)
        if hasattr(self, "btn_dose_point"):
            self.btn_dose_point.setEnabled(has_dose)
            if not has_dose and self.viewer.dose_point_active:
                self.viewer.dose_point_active = False
                self.update_buttons_style()
        
        if not dose_data or "levels" not in dose_data:
            self.lbl_dose_info.setText("")
            self.list_isodoses.blockSignals(False)
            return

        rx = dose_data.get("rx_dose", 0.0)
        mx = dose_data.get("max_dose", 0.0)
        units = dose_data.get("dose_units", "Gy")
        self.lbl_dose_info.setText(f"{tr_ui('viewer_rx_dose')}: {rx:.2f} {units} | {tr_ui('viewer_max_dose')}: {mx:.2f} {units}")

        for lvl in dose_data.get("levels", []):
            name = lvl["name"]
            val = lvl["val"]
            col = lvl["color"]

            item_text = f"{name} — {val:.2f} {units}"
            item = QListWidgetItem(item_text)
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if lvl.get("enabled", True) else Qt.CheckState.Unchecked)
            item.setForeground(QBrush(col))
            self.list_isodoses.addItem(item)

        self.list_isodoses.blockSignals(False)

    def on_dose_file_changed(self, index: int) -> None:
        if self.dose_worker is not None and self.dose_worker.isRunning():
            self.dose_worker.quit()
            self.dose_worker.wait()

        self.apply_dose_data({})
        self.viewer.update()

        if index >= 0:
            dose_path = self.cb_dose.itemData(index)
            if dose_path and os.path.exists(dose_path):
                self.dose_worker = DoseLoaderWorker(dose_path, self.plan_files)
                self.dose_worker.finished_signal.connect(self._on_dose_loaded)
                self.dose_worker.start()

    def _on_dose_loaded(self, dose_path: str, parsed: dict) -> None:
        current_path = self.cb_dose.currentData()
        if current_path == dose_path:
            self.apply_dose_data(parsed)

    def setup_hu_panel(self) -> None:
        self.hu_panel = QFrame(self)
        self.hu_panel.setFixedWidth(70)
        self.hu_panel.setStyleSheet("""
            QFrame {
                background-color: #1F2937;
                border: 1px solid #374151;
                border-radius: 6px;
            }
            QLabel {
                border: none;
                background: transparent;
                color: #EF4444;
                font-size: 11px;
                font-weight: bold;
            }
        """)
        panel_layout = QVBoxLayout(self.hu_panel)
        panel_layout.setContentsMargins(4, 10, 4, 10)
        panel_layout.setSpacing(6)

        self.lbl_upper_hu = QLabel("240 HU", self.hu_panel)
        self.lbl_upper_hu.setAlignment(Qt.AlignmentFlag.AlignCenter)
        panel_layout.addWidget(self.lbl_upper_hu)

        self.hu_slider = HUVerticalSlider(self.hu_panel)
        self.hu_slider.values_changed.connect(self.on_vertical_slider_changed)
        panel_layout.addWidget(self.hu_slider, stretch=1)

        self.lbl_lower_hu = QLabel("-160 HU", self.hu_panel)
        self.lbl_lower_hu.setAlignment(Qt.AlignmentFlag.AlignCenter)
        panel_layout.addWidget(self.lbl_lower_hu)

        self.hu_panel.hide()

    def on_vertical_slider_changed(self, lower: float, upper: float) -> None:
        self.window_width = upper - lower
        self.window_center = (upper + lower) / 2.0
        
        self.cb_presets.blockSignals(True)
        self.cb_presets.setCurrentIndex(-1)
        self.cb_presets.blockSignals(False)
        
        self.lbl_upper_hu.setText(f"{int(upper)} HU")
        self.lbl_lower_hu.setText(f"{int(lower)} HU")
        self.update_current_slice_pixels()

    def retranslate_ui(self) -> None:
        self.cb_presets.blockSignals(True)
        cur_idx = self.cb_presets.currentIndex()
        self.cb_presets.clear()
        self.cb_presets.addItem(tr_ui("viewer_preset_default"), "dicom")
        self.cb_presets.addItem(tr_ui("viewer_preset_soft"), "soft")
        self.cb_presets.addItem(tr_ui("viewer_preset_bone"), "bone")
        self.cb_presets.addItem(tr_ui("viewer_preset_lung"), "lung")
        self.cb_presets.addItem(tr_ui("viewer_preset_brain"), "brain")
        if cur_idx >= 0:
            self.cb_presets.setCurrentIndex(cur_idx)
        self.cb_presets.blockSignals(False)

        self.cb_dose.blockSignals(True)
        if not getattr(self, "dose_files", []):
            self.cb_dose.clear()
            self.cb_dose.addItem(tr_ui("viewer_no_dose_available"), None)
        else:
            self.cb_dose.setItemText(0, tr_ui("viewer_no_dose"))
        self.cb_dose.blockSignals(False)

        self.cb_structures.blockSignals(True)
        if not getattr(self, "struct_files", []):
            self.cb_structures.clear()
            self.cb_structures.addItem(tr_ui("viewer_no_structures_available"), None)
        else:
            self.cb_structures.setItemText(0, tr_ui("viewer_no_structures"))
        self.cb_structures.blockSignals(False)

        if hasattr(self, "btn_tab_structs"):
            self.btn_tab_structs.setText(tr_ui("viewer_tab_structures"))
        if hasattr(self, "btn_tab_isodoses"):
            self.btn_tab_isodoses.setText(tr_ui("viewer_tab_isodoses"))

        if hasattr(self, "cb_show_structures"):
            self.cb_show_structures.setText(tr_ui("viewer_show_structures"))
        if hasattr(self, "cb_show_isodoses"):
            self.cb_show_isodoses.setText(tr_ui("viewer_show_isodoses"))
        if hasattr(self, "cb_dose_gradient"):
            self.cb_dose_gradient.setText(tr_ui("viewer_show_dose_gradient"))

        if hasattr(self, "btn_dose_point"):
            self.btn_dose_point.setToolTip(tr_ui("viewer_point_dose"))
        if hasattr(self, "btn_beams"):
            self.btn_beams.setText(tr_ui("viewer_beams_btn"))
            self.btn_beams.setToolTip(tr_ui("viewer_beams_tooltip"))
        if hasattr(self, "btn_bev"):
            self.btn_bev.setText(tr_ui("viewer_bev_btn"))
            self.btn_bev.setToolTip(tr_ui("viewer_bev_tooltip"))
        self.btn_ruler.setToolTip("Линейка")
        self.btn_hu.setToolTip("Настройка окна HU")
        self.btn_osd.setToolTip("Показать/скрыть надписи")
        self.btn_close.setToolTip("Закрыть просмотр")

    def apply_theme(self) -> None:
        if not hasattr(self.parent_app, "current_theme") or not hasattr(self.parent_app, "THEMES"):
            return
        palette = self.parent_app.THEMES[self.parent_app.current_theme]
        
        self.lbl_info.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {palette['TEXT_LIGHT']};")
        
        style_combo = f"""
            QComboBox {{
                background-color: {palette['PANEL_BG']};
                border: 1px solid {palette['BORDER_COLOR_ALT']};
                color: {palette['TEXT_COLOR']};
                selection-background-color: {palette['ACCENT_COLOR']};
                selection-color: #FFFFFF;
            }}
            QComboBox QAbstractItemView {{
                background-color: {palette['PANEL_BG']};
                border: 1px solid {palette['BORDER_COLOR']};
                selection-background-color: {palette['ACCENT_COLOR']};
                selection-color: #FFFFFF;
                outline: none;
            }}
        """
        self.cb_presets.setStyleSheet(style_combo)
        self.cb_dose.setStyleSheet(style_combo)
        self.cb_structures.setStyleSheet(style_combo)
        if hasattr(self, "cb_beam"):
            self.cb_beam.setStyleSheet(style_combo)

        lbl_style = f"font-size: 11px; font-weight: bold; color: {palette.get('TEXT_MUTED', '#9CA3AF')}; background: transparent; border: none;"
        if hasattr(self, "lbl_dose"):
            self.lbl_dose.setStyleSheet(lbl_style)
        if hasattr(self, "lbl_structures"):
            self.lbl_structures.setStyleSheet(lbl_style)
        if hasattr(self, "lbl_presets"):
            self.lbl_presets.setStyleSheet(lbl_style)
        if hasattr(self, "lbl_beam"):
            self.lbl_beam.setStyleSheet(lbl_style)
        
        self.hu_panel.setStyleSheet(f"""
            QFrame {{
                background-color: {palette['PANEL_BG']};
                border: 1px solid {palette['BORDER_COLOR']};
                border-radius: 6px;
            }}
            QLabel {{
                border: none;
                background: transparent;
                color: {palette['TEXT_COLOR']};
            }}
        """)

        self.structures_panel.setStyleSheet(f"""
            QFrame {{
                background-color: {palette['PANEL_BG']};
                border: 1px solid {palette['BORDER_COLOR']};
                border-radius: 6px;
            }}
            QLabel {{
                border: none;
                background: transparent;
                color: {palette['TEXT_COLOR']};
            }}
        """)

        if hasattr(self, "segmented_frame"):
            self.segmented_frame.setStyleSheet(f"""
                QFrame {{
                    background-color: {palette.get('WINDOW_BG', '#111827')};
                    border: 1px solid {palette['BORDER_COLOR']};
                    border-radius: 6px;
                }}
            """)

        eye_path = get_resource_path("themes/eye.png").replace(os.sep, "/")
        list_style = f"""
            QListWidget {{
                background-color: {palette.get('WINDOW_BG', '#111827')};
                border: 1px solid {palette['BORDER_COLOR']};
                border-radius: 6px;
                color: {palette['TEXT_COLOR']};
                outline: 0;
                font-family: "Segoe UI", -apple-system, Roboto, sans-serif;
                font-size: 12px;
            }}
            QListWidget::item {{
                padding: 5px 8px;
                border-radius: 4px;
                margin: 2px 2px;
            }}
            QListWidget::item:hover {{
                background-color: {palette.get('HOVER_BG', '#222222')};
            }}
            QListWidget::item:selected {{
                background-color: {palette['ACCENT_COLOR']};
                color: #FFFFFF;
            }}
            QListWidget::indicator {{
                width: 14px;
                height: 14px;
                border: 1px solid {palette.get('BORDER_COLOR_ALT', '#3d3d3d')};
                border-radius: 3px;
                background-color: {palette.get('WINDOW_BG', '#0f0f0f')};
            }}
            QListWidget::indicator:hover {{
                border-color: {palette['ACCENT_COLOR']};
                background-color: {palette.get('PANEL_BG', '#151515')};
            }}
            QListWidget::indicator:checked {{
                image: url({eye_path});
                border: 1px solid {palette['ACCENT_COLOR']};
                border-radius: 3px;
                background-color: {palette['ACCENT_COLOR']};
            }}
        """
        if hasattr(self, "list_structures"):
            self.list_structures.setStyleSheet(list_style)
        if hasattr(self, "list_isodoses"):
            self.list_isodoses.setStyleSheet(list_style)
        self.update_tab_buttons_style()

        self.slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                background: {palette['BORDER_COLOR_ALT']};
                height: 6px;
                border-radius: 3px;
            }}
            QSlider::handle:horizontal {{
                background: {palette['ACCENT_COLOR']};
                width: 30px;
                margin-top: -5px;
                margin-bottom: -5px;
                border-radius: 6px;
            }}
            QSlider::handle:horizontal:hover {{
                background: {palette['ACCENT_COLOR_DARK']};
            }}
        """)
        
        self.update_buttons_style()

    def update_buttons_style(self) -> None:
        palette = self.parent_app.THEMES[self.parent_app.current_theme] if hasattr(self.parent_app, "current_theme") and hasattr(self.parent_app, "THEMES") else {
            "ACCENT_COLOR": "#3B82F6",
            "ACCENT_COLOR_DARK": "#2563EB",
            "BUTTON_BG": "#374151",
            "BORDER_COLOR_ALT": "#4B5563",
            "BUTTON_HOVER_BG": "#4B5563"
        }
        
        accent_color = palette.get("ACCENT_COLOR", "#3B82F6")
        accent_dark = palette.get("ACCENT_COLOR_DARK", "#2563EB")
        
        btn_bg = palette.get("BUTTON_BG", "#374151")
        btn_border = palette.get("BORDER_COLOR_ALT", "#4B5563")
        btn_hover = palette.get("BUTTON_HOVER_BG", "#4B5563")

        style_ruler_active = f"""
            QPushButton {{
                background-color: {accent_color};
                border: 1px solid {accent_dark};
                border-radius: 4px;
                padding: 0px;
                min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px;
            }}
        """
        style_ruler_inactive = f"""
            QPushButton {{
                background-color: {btn_bg};
                border: 1px solid {btn_border};
                border-radius: 4px;
                padding: 0px;
                min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px;
            }}
            QPushButton:hover {{ background-color: {btn_hover}; }}
        """
        style_hu_active = f"""
            QPushButton {{
                background-color: {accent_color};
                border: 1px solid {accent_dark};
                border-radius: 4px;
                padding: 0px;
                min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px;
            }}
        """
        style_hu_inactive = f"""
            QPushButton {{
                background-color: {btn_bg};
                border: 1px solid {btn_border};
                border-radius: 4px;
                padding: 0px;
                min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px;
            }}
            QPushButton:hover {{ background-color: {btn_hover}; }}
        """
        style_osd_active = f"""
            QPushButton {{
                background-color: {accent_color};
                border: 1px solid {accent_dark};
                border-radius: 4px;
                padding: 0px;
                min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px;
            }}
        """
        style_osd_inactive = f"""
            QPushButton {{
                background-color: {btn_bg};
                border: 1px solid {btn_border};
                border-radius: 4px;
                padding: 0px;
                min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px;
            }}
            QPushButton:hover {{ background-color: {btn_hover}; }}
        """
        style_close = """
            QPushButton {
                background-color: #BE123C;
                border: 1px solid #E11D48;
                border-radius: 4px;
                padding: 0px;
                min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px;
            }
            QPushButton:hover {
                background-color: #E11D48;
                border-color: #F43F5E;
            }
            QPushButton:pressed {
                background-color: #9F1239;
            }
        """

        style_dose_point_active = f"""
            QPushButton {{
                background-color: {accent_color};
                border: 1px solid {accent_dark};
                border-radius: 4px;
                padding: 0px;
                min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px;
            }}
        """
        style_dose_point_inactive = f"""
            QPushButton {{
                background-color: {btn_bg};
                border: 1px solid {btn_border};
                border-radius: 4px;
                padding: 0px;
                min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px;
            }}
            QPushButton:hover {{ background-color: {btn_hover}; }}
            QPushButton:disabled {{ background-color: #1a1a1a; border: 1px solid #333333; }}
        """

        style_bev_active = f"""
            QPushButton {{
                background-color: {accent_color};
                border: 1px solid {accent_dark};
                color: #FFFFFF;
                font-weight: bold;
                border-radius: 4px;
                padding: 0px;
                min-width: 36px; max-width: 36px; min-height: 28px; max-height: 28px;
            }}
        """
        style_bev_inactive = f"""
            QPushButton {{
                background-color: {btn_bg};
                border: 1px solid {btn_border};
                color: #FFFFFF;
                font-weight: bold;
                border-radius: 4px;
                padding: 0px;
                min-width: 36px; max-width: 36px; min-height: 28px; max-height: 28px;
            }}
            QPushButton:hover {{ background-color: {btn_hover}; }}
            QPushButton:disabled {{ background-color: #1a1a1a; border: 1px solid #333333; color: #555555; }}
        """

        style_beams_active = f"""
            QPushButton {{
                background-color: {accent_color};
                border: 1px solid {accent_dark};
                color: #FFFFFF;
                font-weight: bold;
                border-radius: 4px;
                padding: 0px 4px;
                min-width: 48px; max-width: 55px; min-height: 28px; max-height: 28px;
                font-size: 11px;
            }}
        """
        style_beams_inactive = f"""
            QPushButton {{
                background-color: {btn_bg};
                border: 1px solid {btn_border};
                color: #FFFFFF;
                font-weight: bold;
                border-radius: 4px;
                padding: 0px 4px;
                min-width: 48px; max-width: 55px; min-height: 28px; max-height: 28px;
                font-size: 11px;
            }}
            QPushButton:hover {{ background-color: {btn_hover}; }}
            QPushButton:disabled {{ background-color: #1a1a1a; border: 1px solid #333333; color: #555555; }}
        """

        if hasattr(self, "btn_beams"):
            self.btn_beams.setStyleSheet(style_beams_active if self.viewer.show_beams else style_beams_inactive)
        if hasattr(self, "btn_bev"):
            self.btn_bev.setStyleSheet(style_bev_active if self.viewer.bev_active else style_bev_inactive)
        if hasattr(self, "btn_dose_point"):
            self.btn_dose_point.setStyleSheet(style_dose_point_active if self.viewer.dose_point_active else style_dose_point_inactive)
        self.btn_ruler.setStyleSheet(style_ruler_active if self.viewer.ruler_active else style_ruler_inactive)
        self.btn_hu.setStyleSheet(style_hu_active if self.viewer.hu_active else style_hu_inactive)
        self.btn_osd.setStyleSheet(style_osd_active if self.viewer.osd_visible else style_osd_inactive)
        self.btn_close.setStyleSheet(style_close)

    def toggle_beams(self) -> None:
        val = not self.viewer.show_beams
        self.viewer.set_show_beams(val)
        self.update_buttons_style()

    def _on_bev_beam_changed(self, index: int) -> None:
        self._sync_bev_slider()
        self._update_bev_slider_label()
        self.viewer.update()

    def _sync_bev_slider(self) -> None:
        if not self.viewer.bev_active:
            self._update_bev_slider_label()
            return
        beams = self.viewer.plan_data.get("beams", [])
        if not beams:
            self.slider.setEnabled(False)
            self._update_bev_slider_label()
            return
        idx = max(0, min(len(beams) - 1, self.viewer.bev_selected_beam_idx))
        beam = beams[idx]
        cps = beam.get("control_points", [])
        if beam.get("is_dynamic", False) and len(cps) > 1:
            self.slider.blockSignals(True)
            self.slider.setEnabled(True)
            self.slider.setRange(0, len(cps) - 1)
            self.slider.setValue(self.viewer.bev_control_point_idx)
            self.slider.blockSignals(False)
        else:
            self.slider.setEnabled(False)
        self._update_bev_slider_label()

    def _on_bev_cp_changed(self, cp_idx: int) -> None:
        if self.viewer.bev_active:
            self.slider.blockSignals(True)
            self.slider.setValue(cp_idx)
            self.slider.blockSignals(False)
            self._update_bev_slider_label()

    def _update_bev_slider_label(self) -> None:
        if not self.viewer.bev_active:
            self.lbl_slider_info.hide()
            return
        beams = self.viewer.plan_data.get("beams", [])
        if not beams:
            self.lbl_slider_info.hide()
            return
        idx = max(0, min(len(beams) - 1, self.viewer.bev_selected_beam_idx))
        beam = beams[idx]
        cps = beam.get("control_points", [])
        cp_idx = max(0, min(len(cps) - 1, self.viewer.bev_control_point_idx)) if cps else 0
        cp = cps[cp_idx] if cps else {}
        g_angle = float(cp.get("gantry_angle", beam.get("gantry_angle", 0.0)) or 0.0)

        if beam.get("is_dynamic", False) and len(cps) > 1:
            self.lbl_slider_info.setText(f"Control Point {cp_idx + 1} / {len(cps)}   •   Гантри {g_angle:.1f}°")
            self.lbl_slider_info.show()
        else:
            c_angle = float(cp.get("beam_limiting_device_angle", beam.get("collimator_angle", 0.0)) or 0.0)
            self.lbl_slider_info.setText(f"Гантри {g_angle:.1f}°   •   Коллиматор {c_angle:.1f}°")
            self.lbl_slider_info.show()

    def start_bev_struct_precompute(self) -> None:
        beams = self.viewer.plan_data.get("beams", [])
        if not beams or not self.viewer.structures:
            return

        if hasattr(self, "bev_struct_worker") and self.bev_struct_worker is not None and self.bev_struct_worker.isRunning():
            return

        self.viewer.bev_precomputing_status = "BEV: подготовка 3D-проекций..."
        self.bev_struct_worker = BEVStructurePrecomputeWorker(
            self.viewer.structures,
            self.viewer.enabled_structures,
            beams,
            1000.0,
            active_beam_idx=self.viewer.bev_selected_beam_idx
        )
        self.bev_struct_worker.progress_signal.connect(self._on_bev_struct_progress)
        self.bev_struct_worker.item_computed_signal.connect(self._on_bev_struct_item_computed)
        self.bev_struct_worker.finished_signal.connect(self._on_bev_struct_finished)
        self.bev_struct_worker.start()

    def _on_bev_struct_progress(self, cur: int, total: int, b_name: str) -> None:
        self.viewer.bev_precomputing_status = f"BEV: подготовка 3D ({cur}/{total})"
        if self.viewer.bev_active:
            self.viewer.update()

    def _on_bev_struct_item_computed(self, ck: tuple, path, pois: list) -> None:
        self.viewer.bev_struct_cache[ck] = (path, pois)

    def _on_bev_struct_finished(self) -> None:
        self.viewer.bev_precomputing_status = ""
        if self.viewer.bev_active:
            self.viewer.update()

    def toggle_bev(self) -> None:
        active = not self.viewer.bev_active
        self.viewer.bev_active = active
        if active:
            self.viewer.dose_point_active = False
            self.viewer.ruler_active = False
            self.viewer.hu_active = False
            self.hu_panel.hide()

            # Сохраняем текущий набор включенных пользователем структур
            self._pre_bev_enabled_structures = set(self.viewer.enabled_structures)

            # В BEV по умолчанию оставляем включенными только Body, PTV и ICRU / ориентиры
            def is_essential_bev_struct(name: str) -> bool:
                n = name.lower()
                for k in ("body", "тело", "боди", "external", "skin", "ptv", "птв", "icru", "ориентир", "marker", "poi"):
                    if k in n:
                        return True
                return False

            self.list_structures.blockSignals(True)
            self.viewer.enabled_structures.clear()
            for i in range(self.list_structures.count()):
                item = self.list_structures.item(i)
                if item:
                    s_name = item.text()
                    if is_essential_bev_struct(s_name) and s_name in self._pre_bev_enabled_structures:
                        item.setCheckState(Qt.CheckState.Checked)
                        self.viewer.enabled_structures.add(s_name)
                    else:
                        item.setCheckState(Qt.CheckState.Unchecked)
            self.list_structures.blockSignals(False)
            self.viewer.rebuild_contour_index()

            self.start_bev_struct_precompute()
            self._sync_bev_slider()

            if hasattr(self, "lbl_dose"):
                self.lbl_dose.hide()
            self.cb_dose.hide()
            if hasattr(self, "lbl_structures"):
                self.lbl_structures.hide()
            self.cb_structures.hide()
            if hasattr(self, "lbl_presets"):
                self.lbl_presets.hide()
            self.cb_presets.hide()
        else:
            if hasattr(self, "bev_struct_worker") and self.bev_struct_worker is not None and self.bev_struct_worker.isRunning():
                self.bev_struct_worker.cancel()
                self.bev_struct_worker.quit()
                self.bev_struct_worker.wait()
            self.viewer.bev_precomputing_status = ""
            self.viewer.show_drr = False
            self.lbl_slider_info.hide()
            
            # Восстанавливаем слайдер срезов
            self.slider.blockSignals(True)
            self.slider.setEnabled(True)
            self.slider.setRange(0, max(0, len(self.sorted_files) - 1))
            self.slider.setValue(max(0, self.current_index))
            self.slider.blockSignals(False)

            # Восстанавливаем состояние включенных структур, которое было до входа в BEV
            to_restore = getattr(self, "_pre_bev_enabled_structures", None)
            if to_restore is not None:
                self.list_structures.blockSignals(True)
                self.viewer.enabled_structures.clear()
                for i in range(self.list_structures.count()):
                    item = self.list_structures.item(i)
                    if item:
                        s_name = item.text()
                        if s_name in to_restore:
                            item.setCheckState(Qt.CheckState.Checked)
                            self.viewer.enabled_structures.add(s_name)
                        else:
                            item.setCheckState(Qt.CheckState.Unchecked)
                self.list_structures.blockSignals(False)
                self.viewer.rebuild_contour_index()

            if hasattr(self, "lbl_dose"):
                self.lbl_dose.show()
            self.cb_dose.show()
            if hasattr(self, "lbl_structures"):
                self.lbl_structures.show()
            self.cb_structures.show()
            if hasattr(self, "lbl_presets"):
                self.lbl_presets.show()
            self.cb_presets.show()

        self.update_buttons_style()
        self.viewer.update()

    def start_drr_precompute(self) -> None:
        beams = self.viewer.plan_data.get("beams", [])
        if not beams:
            self.viewer.show_drr = True
            self.viewer.update()
            return

        cached_keys = set(self.viewer.drr_cache.keys())
        needs_calc = False
        for b in beams:
            sad = float(b.get("sad", 1000.0) or 1000.0)
            cps = b.get("control_points", [])
            if not cps:
                g_angle = float(b.get("gantry_angle", 0.0))
                iso = b.get("isocenter")
                if iso and len(iso) >= 3:
                    ck = (round(g_angle, 1), round(iso[0], 2), round(iso[1], 2), round(iso[2], 2), round(sad, 1))
                    if ck not in cached_keys:
                        needs_calc = True
                        break
            else:
                step_deg = 3.0 if len(cps) > 30 else 0.5
                last_g = None
                for cp in cps:
                    g_angle = float(cp.get("gantry_angle", b.get("gantry_angle", 0.0)))
                    iso = cp.get("isocenter", b.get("isocenter"))
                    if not iso or len(iso) < 3:
                        continue
                    if last_g is not None and abs(g_angle - last_g) < (step_deg - 0.1):
                        continue
                    ck = (round(g_angle, 1), round(iso[0], 2), round(iso[1], 2), round(iso[2], 2), round(sad, 1))
                    if ck not in cached_keys:
                        needs_calc = True
                        break
                    last_g = g_angle
            if needs_calc:
                break

        if not needs_calc:
            self.viewer.show_drr = True
            self.viewer.update()
            return

        if self.drr_worker is not None and self.drr_worker.isRunning():
            self.drr_worker.cancel()
            self.drr_worker.quit()
            self.drr_worker.wait()

        self.drr_dialog = DRRProgressDialog(self)
        self.drr_worker = DRRPrecomputeWorker(
            self.viewer.sorted_files,
            self.viewer.ct_volume,
            self.viewer.ct_ipp0,
            self.viewer.ct_spacing,
            beams,
            cached_keys
        )
        self.drr_dialog.cancelled.connect(self.drr_worker.cancel)
        self.drr_worker.progress_signal.connect(self.drr_dialog.set_progress)
        self.drr_worker.item_computed_signal.connect(self._on_drr_item_computed)
        self.drr_worker.finished_signal.connect(self._on_drr_worker_finished)
        self.drr_worker.start()
        self.drr_dialog.show()

    def _on_drr_item_computed(self, ck: tuple, q_img: QImage) -> None:
        self.viewer.drr_cache[ck] = q_img

    def _on_drr_worker_finished(self, is_cancelled: bool) -> None:
        if hasattr(self, "drr_dialog") and self.drr_dialog and self.drr_dialog.isVisible():
            self.drr_dialog.close()
        if not is_cancelled:
            self.viewer.show_drr = True
        else:
            self.viewer.show_drr = False
        self.viewer.update()

    def toggle_osd(self) -> None:
        self.viewer.set_osd_visible(not self.viewer.osd_visible)
        self.update_buttons_style()

    def toggle_dose_point(self) -> None:
        active = not self.viewer.dose_point_active
        self.viewer.dose_point_active = active
        if active:
            self.viewer.bev_active = False
            self.slider.setEnabled(True)
            self.viewer.ruler_active = False
            self.viewer.hu_active = False
            self.hu_panel.hide()
        self.update_buttons_style()
        self.viewer.update()

    def toggle_ruler(self) -> None:
        active = not self.viewer.ruler_active
        self.viewer.ruler_active = active
        if active:
            self.viewer.bev_active = False
            self.slider.setEnabled(True)
            self.viewer.dose_point_active = False
            self.viewer.hu_active = False
            self.hu_panel.hide()
        self.update_buttons_style()
        self.viewer.update()

    def toggle_hu(self) -> None:
        active = not self.viewer.hu_active
        self.viewer.hu_active = active
        if active:
            self.viewer.bev_active = False
            self.slider.setEnabled(True)
            self.viewer.dose_point_active = False
            self.viewer.ruler_active = False
            self.hu_panel.show()
        else:
            self.hu_panel.hide()
        self.update_buttons_style()
        self.viewer.update()

    def clear_panel(self) -> None:
        if self.loader_worker is not None and self.loader_worker.isRunning():
            self.loader_worker.quit()
            self.loader_worker.wait()
        if self.struct_worker is not None and self.struct_worker.isRunning():
            self.struct_worker.quit()
            self.struct_worker.wait()
        if self.dose_worker is not None and self.dose_worker.isRunning():
            self.dose_worker.quit()
            self.dose_worker.wait()
        if hasattr(self, "bev_struct_worker") and self.bev_struct_worker is not None and self.bev_struct_worker.isRunning():
            self.bev_struct_worker.cancel()
            self.bev_struct_worker.quit()
            self.bev_struct_worker.wait()

        self.viewer.clear_viewer()
        self.pixmap_cache.clear()
        self.sorted_files.clear()
        self.struct_files.clear()
        self.dose_files.clear()
        self.plan_files.clear()

        self.cb_dose.blockSignals(True)
        self.cb_dose.clear()
        self.cb_dose.blockSignals(False)

        self.cb_structures.blockSignals(True)
        self.cb_structures.clear()
        self.cb_structures.blockSignals(False)

        self.list_structures.blockSignals(True)
        self.list_structures.clear()
        self.list_structures.blockSignals(False)

        self.list_isodoses.blockSignals(True)
        self.list_isodoses.clear()
        self.list_isodoses.blockSignals(False)

        self.lbl_dose_info.setText("")
        self.lbl_info.setText("")
        if hasattr(self, "btn_dose_point"):
            self.btn_dose_point.setEnabled(False)
        if hasattr(self, "btn_bev"):
            self.btn_bev.setEnabled(False)
        if hasattr(self, "btn_beams"):
            self.btn_beams.setEnabled(False)
        self.current_index = -1
        self.is_loading = False
        gc.collect()

    def load_series(self, files: list[str]) -> None:
        self.is_loading = True
        self.sorted_files = []
        self.current_index = -1
        self.pixmap_cache.clear()
        self.viewer.clear_viewer()
        self.hu_panel.hide()
        self.update_buttons_style()

        self.cb_presets.blockSignals(True)
        self.cb_presets.setCurrentIndex(0)
        self.cb_presets.blockSignals(False)

        self.cb_dose.blockSignals(True)
        self.cb_dose.clear()
        self.dose_files = []

        self.cb_structures.blockSignals(True)
        self.cb_structures.clear()
        self.struct_files = []
        self.plan_files = []

        if self.loader_worker is not None and self.loader_worker.isRunning():
            self.loader_worker.quit()
            self.loader_worker.wait()

        from ui.loading_dialog import LoadingProgressDialog
        self.progress_dialog = LoadingProgressDialog(
            self,
            title=tr_ui("loading_viewer_title"),
            show_cancel=True,
            on_cancel=self._on_cancel_load_series
        )
        total_count = len(files) if files else 0
        self.progress_dialog.set_custom_progress(0, 100, tr_ui("loading_dicom_files", 0, total_count))

        self.loader_worker = PatientSeriesLoaderWorker(files)
        self.loader_worker.progress_signal.connect(self._on_load_progress)
        self.loader_worker.finished_signal.connect(self._on_series_loaded)
        self.loader_worker.error_signal.connect(self._on_series_load_error)
        self.loader_worker.start()

        self.progress_dialog.exec()

    def _on_cancel_load_series(self) -> None:
        if self.loader_worker is not None:
            self.loader_worker.cancel()
        self.is_loading = False
        self.lbl_info.setText("Загрузка отменена.")

    def _on_load_progress(self, current: int, total: int, text: str) -> None:
        if self.progress_dialog:
            self.progress_dialog.set_custom_progress(current, total, text)

    def _on_series_loaded(self, result: dict) -> None:
        if self.loader_worker and getattr(self.loader_worker, '_is_cancelled', False):
            return

        self.struct_files = result.get("struct_files", [])
        self.dose_files = result.get("dose_files", [])
        self.plan_files = result.get("plan_files", [])
        
        selected_struct_idx = result.get("selected_struct_idx", -1)
        parsed_structures = result.get("parsed_structures", {})
        
        selected_dose_idx = result.get("selected_dose_idx", -1)
        parsed_dose = result.get("parsed_dose", {})
        
        self.sorted_files = result.get("sorted_files", [])

        # Настройка выпадающего списка RTDOSE
        self.cb_dose.blockSignals(True)
        self.cb_dose.clear()
        if self.dose_files:
            self.cb_dose.addItem(tr_ui("viewer_no_dose"), None)
            for df in self.dose_files:
                display_name = os.path.basename(df)
                if df == parsed_dose.get("filepath") and parsed_dose.get("plan_label"):
                    display_name = f"Dose: {parsed_dose['plan_label']}"
                self.cb_dose.addItem(display_name, df)

            if selected_dose_idx > 0:
                self.cb_dose.setCurrentIndex(selected_dose_idx)
            else:
                self.cb_dose.setCurrentIndex(0)

            self.cb_dose.setEnabled(True)
        else:
            self.cb_dose.addItem(tr_ui("viewer_no_dose_available"), None)
            self.cb_dose.setCurrentIndex(0)
            self.cb_dose.setEnabled(False)
        self.cb_dose.show()
        self.cb_dose.blockSignals(False)

        # Настройка выпадающего списка RTSTRUCT
        self.cb_structures.blockSignals(True)
        self.cb_structures.clear()
        if self.struct_files:
            self.cb_structures.addItem(tr_ui("viewer_no_structures"), None)
            for sf in self.struct_files:
                self.cb_structures.addItem(os.path.basename(sf), sf)

            if selected_struct_idx > 0:
                self.cb_structures.setCurrentIndex(selected_struct_idx)
            else:
                self.cb_structures.setCurrentIndex(0)

            self.cb_structures.setEnabled(True)
        else:
            self.cb_structures.addItem(tr_ui("viewer_no_structures_available"), None)
            self.cb_structures.setCurrentIndex(0)
            self.cb_structures.setEnabled(False)
        self.cb_structures.show()
        self.cb_structures.blockSignals(False)

        self.apply_structures(parsed_structures)
        self.viewer.show_structures_globally = self.cb_show_structures.isChecked()

        self.apply_dose_data(parsed_dose)
        self.viewer.show_isodoses_globally = self.cb_show_isodoses.isChecked()
        self.viewer.show_dose_gradient = self.cb_dose_gradient.isChecked()

        self.viewer.drr_cache.clear()
        self.viewer.ct_volume = None
        self.viewer.ct_ipp0 = None
        self.viewer.ct_spacing = None
        self.viewer.sorted_files = self.sorted_files
        parsed_plan = result.get("parsed_plan", {})
        self.viewer.set_plan_data(parsed_plan)
        has_beams = bool(parsed_plan and parsed_plan.get("beams"))
        if hasattr(self, "btn_bev"):
            self.btn_bev.setEnabled(has_beams)
        if hasattr(self, "btn_beams"):
            self.btn_beams.setEnabled(has_beams)

        if not self.sorted_files:
            if self.progress_dialog:
                self.progress_dialog.accept()
                self.progress_dialog = None
            self.lbl_info.setText("Серия не содержит корректных DICOM файлов.")
            self.viewer.set_slice_info(0, 0)
            self.is_loading = False
            return

        self.slider.setRange(0, len(self.sorted_files) - 1)
        self.is_loading = False
        self.set_current_slice(0)

        if self.progress_dialog:
            self.progress_dialog.accept()
            self.progress_dialog = None

    def _on_series_load_error(self, error_msg: str) -> None:
        if self.loader_worker and getattr(self.loader_worker, '_is_cancelled', False):
            return

        if self.progress_dialog:
            self.progress_dialog.reject()
            self.progress_dialog = None
        self.lbl_info.setText(error_msg)
        self.viewer.set_slice_info(0, 0)
        self.is_loading = False

    def read_truncated_dicom(self, filepath: str):
        ds_meta = safe_dcmread(filepath, stop_before_pixels=True)
        transfer_syntax = ds_meta.file_meta.TransferSyntaxUID
        
        with open(filepath, "rb") as f:
            file_bytes = bytearray(f.read())
            
        is_compressed = transfer_syntax.startswith("1.2.840.10008.1.2.4.") or "rle" in getattr(ds_meta.file_meta, "TransferSyntaxUID_name", "").lower()
        
        if is_compressed:
            has_eoi = file_bytes.endswith(b"\xff\xd9") or b"\xff\xd9" in file_bytes[-20:]
            if not has_eoi:
                file_bytes.extend(b"\xff\xd9")
                
            has_delim = b"\xfe\xff\xdd\xe0" in file_bytes[-20:]
            if not has_delim:
                file_bytes.extend(b"\xfe\xff\xdd\xe0\x00\x00\x00\x00")
        else:
            rows = getattr(ds_meta, "Rows", 512)
            cols = getattr(ds_meta, "Columns", 512)
            bits = getattr(ds_meta, "BitsAllocated", 16)
            expected_pixels = rows * cols * (bits // 8)
            if len(file_bytes) < expected_pixels:
                file_bytes.extend(b"\x00" * (expected_pixels * 2))
                
        bio = io.BytesIO(file_bytes)
        ds = safe_dcmread(bio)
        
        try:
            _ = ds.pixel_array
        except Exception:
            rows = getattr(ds, "Rows", 512)
            cols = getattr(ds, "Columns", 512)
            bits = getattr(ds, "BitsAllocated", 16)
            pixel_repr = getattr(ds, "PixelRepresentation", 0)
            if bits == 16:
                dtype = np.int16 if pixel_repr == 1 else np.uint16
            else:
                dtype = np.int8 if pixel_repr == 1 else np.uint8
            arr = np.zeros((rows, cols), dtype=dtype)
            
            class TruncatedDataset(type(ds)):
                @property
                def pixel_array(self):
                    return getattr(self, "_pixel_array", None)
            
            ds._pixel_array = arr
            ds.__class__ = TruncatedDataset
            
        return ds

    def set_current_slice(self, index: int) -> None:
        if index < 0 or index >= len(self.sorted_files):
            return

        self.current_index = index
        self.slider.setValue(index)

        filepath, frame_idx = self.sorted_files[index]
        cache_key = (filepath, frame_idx, round(self.window_width, 1), round(self.window_center, 1))

        if cache_key in self.pixmap_cache:
            pixmap, ds = self.pixmap_cache[cache_key]
            pat_name = getattr(ds, "PatientName", "Unknown")
            pat_id = getattr(ds, "PatientID", "Unknown")
            study_desc = getattr(ds, "StudyDescription", "")
            series_desc = getattr(ds, "SeriesDescription", "")
            
            info_text = f"{pat_name} [{pat_id}] | {study_desc} | {series_desc}"
            self.lbl_info.setText(info_text)
            self.viewer.set_slice_info(index + 1, len(self.sorted_files))
            self.viewer.set_dicom_image(pixmap, ds)
            self.viewer.set_window_params(self.window_width, self.window_center)
            return

        try:
            try:
                ds = safe_dcmread(filepath)
                if not hasattr(ds, "pixel_array") or len(ds) == 0:
                    raise ValueError("Empty dataset or missing pixel array")
            except Exception:
                ds = self.read_truncated_dicom(filepath)

            ds.current_frame_idx = frame_idx

            if self.current_index == 0:
                self.default_wc = 40.0
                self.default_ww = 400.0
                wc = getattr(ds, "WindowCenter", None)
                ww = getattr(ds, "WindowWidth", None)
                if wc is not None and ww is not None:
                    try:
                        c_val = wc[0] if hasattr(wc, "__iter__") else wc
                        w_val = ww[0] if hasattr(ww, "__iter__") else ww
                        self.default_wc = float(c_val)
                        self.default_ww = float(w_val)
                    except Exception:
                        pass
                
                preset_data = self.cb_presets.currentData()
                if preset_data == "dicom":
                    self.window_center = self.default_wc
                    self.window_width = self.default_ww
                    cache_key = (filepath, frame_idx, round(self.window_width, 1), round(self.window_center, 1))

            pat_name = getattr(ds, "PatientName", "Unknown")
            pat_id = getattr(ds, "PatientID", "Unknown")
            study_desc = getattr(ds, "StudyDescription", "")
            series_desc = getattr(ds, "SeriesDescription", "")
            
            info_text = f"{pat_name} [{pat_id}] | {study_desc} | {series_desc}"
            self.lbl_info.setText(info_text)
            self.viewer.set_slice_info(index + 1, len(self.sorted_files))

            pixmap = self.dicom_to_pixmap(ds, self.window_width, self.window_center)
            if pixmap:
                if len(self.pixmap_cache) > 80:
                    oldest_key = next(iter(self.pixmap_cache))
                    del self.pixmap_cache[oldest_key]
                self.pixmap_cache[cache_key] = (pixmap, ds)
                self.viewer.set_dicom_image(pixmap, ds)
                self.viewer.set_window_params(self.window_width, self.window_center)
            else:
                raise ValueError("Failed to decode pixel array to pixmap")
                
        except Exception as e:
            print(f"[Viewer] Skipping corrupted file {filepath} (frame {frame_idx}): {str(e)}")
            self.sorted_files.pop(index)
            if not self.sorted_files:
                self.lbl_info.setText("Нет доступных изображений в серии.")
                self.viewer.set_slice_info(0, 0)
                self.viewer.clear_viewer()
                return

            self.slider.setRange(0, len(self.sorted_files) - 1)
            new_index = min(index, len(self.sorted_files) - 1)
            self.set_current_slice(new_index)

    def on_slider_changed(self, value: int) -> None:
        if self.viewer.bev_active:
            beams = self.viewer.plan_data.get("beams", [])
            if beams:
                beam = beams[self.viewer.bev_selected_beam_idx]
                cps = beam.get("control_points", [])
                if beam.get("is_dynamic", False) and len(cps) > 1:
                    self.viewer.bev_control_point_idx = max(0, min(len(cps) - 1, value))
                    self._update_bev_slider_label()
                    self.viewer.update()
            return

        if not self.is_loading and value != self.current_index:
            self.set_current_slice(value)

    def on_slice_scrolled(self, step: int) -> None:
        new_index = self.current_index + step
        if 0 <= new_index < len(self.sorted_files):
            self.set_current_slice(new_index)

    def on_window_changed(self, width: float, center: float) -> None:
        self.window_width = width
        self.window_center = center
        
        if self.sender() == self.viewer:
            self.cb_presets.blockSignals(True)
            self.cb_presets.setCurrentIndex(-1)
            self.cb_presets.blockSignals(False)
            
        if hasattr(self, "hu_panel") and hasattr(self, "hu_slider"):
            lower = center - width / 2.0
            upper = center + width / 2.0
            
            self.hu_slider.blockSignals(True)
            self.hu_slider.set_values(lower, upper)
            self.hu_slider.blockSignals(False)
            
            self.lbl_upper_hu.setText(f"{int(upper)} HU")
            self.lbl_lower_hu.setText(f"{int(lower)} HU")
            
        self.update_current_slice_pixels()

    def update_current_slice_pixels(self) -> None:
        ds = self.viewer.current_dataset
        if ds is not None:
            pixmap = self.dicom_to_pixmap(ds, self.window_width, self.window_center)
            if pixmap:
                self.viewer.set_dicom_image(pixmap, ds)
                self.viewer.set_window_params(self.window_width, self.window_center)
            return

        if self.current_index < 0 or self.current_index >= len(self.sorted_files):
            return
        filepath, frame_idx = self.sorted_files[self.current_index]
        try:
            ds = safe_dcmread(filepath)
            ds.current_frame_idx = frame_idx
            pixmap = self.dicom_to_pixmap(ds, self.window_width, self.window_center)
            if pixmap:
                self.viewer.set_dicom_image(pixmap, ds)
                self.viewer.set_window_params(self.window_width, self.window_center)
        except Exception:
            pass

    def apply_preset(self, index: int) -> None:
        preset_type = self.cb_presets.itemData(index)
        
        if preset_type == "dicom":
            self.window_width = self.default_ww
            self.window_center = self.default_wc
        elif preset_type == "soft":
            self.window_width = 400.0
            self.window_center = 40.0
        elif preset_type == "bone":
            self.window_width = 1500.0
            self.window_center = 300.0
        elif preset_type == "lung":
            self.window_width = 1500.0
            self.window_center = -600.0
        elif preset_type == "brain":
            self.window_width = 80.0
            self.window_center = 40.0

        self.on_window_changed(self.window_width, self.window_center)

    def dicom_to_pixmap(self, ds, window_width: float, window_center: float) -> QPixmap | None:
        try:
            if not hasattr(ds, "pixel_array"):
                return None

            original_ts = getattr(ds.file_meta, "TransferSyntaxUID", None)
            if original_ts and not hasattr(ds, "original_transfer_syntax"):
                ds.original_transfer_syntax = original_ts

            try:
                ds.decompress()
            except Exception as e:
                if "already uncompressed" in str(e).lower():
                    ds.file_meta.TransferSyntaxUID = pydicom.uid.ImplicitVRLittleEndian

            pixel_array = ds.pixel_array
            frame_idx = getattr(ds, "current_frame_idx", 0)

            # Определяем размерность массива и извлекаем нужный кадр
            if pixel_array.ndim == 3:
                # Если третья ось имеет размер 3 или 4, это RGB/RGBA изображение (один кадр)
                if pixel_array.shape[2] in (3, 4):
                    if pixel_array.shape[2] == 3:
                        arr = (0.299 * pixel_array[..., 0] + 
                               0.587 * pixel_array[..., 1] + 
                               0.114 * pixel_array[..., 2]).astype(float)
                    else:
                        arr = (0.299 * pixel_array[..., 0] + 
                               0.587 * pixel_array[..., 1] + 
                               0.114 * pixel_array[..., 2]).astype(float)
                else:
                    # Иначе это многокадровое монохромное изображение (Frames, Rows, Columns)
                    f_idx = min(max(0, frame_idx), pixel_array.shape[0] - 1)
                    arr = pixel_array[f_idx].astype(float)
            elif pixel_array.ndim == 4:
                # Многокадровое цветное изображение (Frames, Rows, Columns, Channels)
                f_idx = min(max(0, frame_idx), pixel_array.shape[0] - 1)
                frame_data = pixel_array[f_idx]
                if frame_data.shape[2] in (3, 4):
                    arr = (0.299 * frame_data[..., 0] + 
                           0.587 * frame_data[..., 1] + 
                           0.114 * frame_data[..., 2]).astype(float)
                else:
                    arr = frame_data.astype(float)
            else:
                arr = pixel_array.astype(float)

            slope = float(getattr(ds, "RescaleSlope", 1.0))
            intercept = float(getattr(ds, "RescaleIntercept", 0.0))
            arr = arr * slope + intercept

            min_val = window_center - window_width / 2.0
            max_val = window_center + window_width / 2.0

            arr = np.clip(arr, min_val, max_val)
            arr = ((arr - min_val) / (max_val - min_val) * 255.0).astype(np.uint8)

            height, width = arr.shape
            bytes_per_line = width

            self._temp_arr = np.ascontiguousarray(arr)
            qimg = QImage(self._temp_arr.data, width, height, bytes_per_line, QImage.Format.Format_Grayscale8)
            return QPixmap.fromImage(qimg)
        except Exception:
            return None
