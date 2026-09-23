# -*- coding: utf-8 -*-
"""
Monaco Plan Kinematics & Deliverability Analyzer Dialog.

Main dialog orchestrating beam kinematics inspection, polar arc delivery trajectory,
modulation timeline graph, and control point sequence tables.
"""

import os
from typing import Optional, Dict, Any, List
import pydicom

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QTabWidget, QFrame, QSplitter, QMessageBox
)

from core.logger import log_message
from ui.plan_analysis.kinematics import PlanKinematicsAnalyzer
from ui.plan_analysis.polar_arc_widget import PolarArcWidget
from ui.plan_analysis.timeline_widget import ModulationTimelineWidget
from ui.plan_analysis.help_dialog import show_plan_analysis_help
from ui.plan_analysis.utils import apply_dark_title_bar


class MonacoPlanAnalyzerDialog(QDialog):
    """Main window for Monaco plan deliverability inspection."""

    def __init__(self, parent=None, plan_path: str = "", plan_paths: list = None, config: Optional[Dict[str, Any]] = None):
        super().__init__(parent)
        self.config = config or getattr(parent, 'config', {}) or {}
        if not self.config:
            try:
                from core.config_utils import get_config_path
                import json
                cp = get_config_path()
                if os.path.exists(cp):
                    with open(cp, "r", encoding="utf-8") as f:
                        self.config = json.load(f)
            except Exception:
                self.config = {}

        self.thresholds = {
            'min_dose_rate': float(self.config.get('plan_min_dose_rate', 60.0)),
            'min_mu_per_deg': float(self.config.get('plan_min_mu_per_deg', 0.165)),
            'max_modulation_factor': float(self.config.get('plan_max_modulation_factor', 10.0)),
            'warn_dose_rate': float(self.config.get('plan_warn_dose_rate', 75.0)),
            'warn_mu_per_deg': float(self.config.get('plan_warn_mu_per_deg', 0.200)),
            'warn_modulation_factor': float(self.config.get('plan_warn_modulation_factor', 8.0)),
        }
        self.is_ru = (self.config.get('interface_lang', 'en') == 'ru')
        self.plan_path = plan_path
        self.plan_paths: list = plan_paths if plan_paths else ([plan_path] if plan_path else [])
        self.analyzer = PlanKinematicsAnalyzer(plan_path, thresholds=self.thresholds, is_ru=self.is_ru)

        if not self.analyzer.is_monaco:
            _pfx = ("НЕ MONACO — РЕЗУЛЬТАТ НЕ БУДЕТ СООТВЕТСТВОВАТЬ ДЕЙСТВИТЕЛЬНОСТИ" if self.is_ru
                    else "NOT MONACO — RESULTS MAY NOT REFLECT REALITY")
            _ttl = "Анализ плана" if self.is_ru else "Plan Analysis"
            self.setWindowTitle(f"[{_pfx}] {_ttl} — {self.analyzer.patient_name} [{self.analyzer.patient_id}]")
        else:
            _ttl = "Анализ плана Monaco" if self.is_ru else "Monaco Plan Analysis"
            self.setWindowTitle(f"{_ttl} — {self.analyzer.patient_name} [{self.analyzer.patient_id}]")
        self.resize(1200, 800)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowMinimizeButtonHint
        )
        self._already_maximized = False
        self.setStyleSheet("""
            QDialog {
                background-color: #141414;
                color: #ffffff;
                font-family: 'Segoe UI', Arial, sans-serif;
            }
            QLabel {
                color: #e5e5ea;
            }
            QComboBox {
                background-color: #242426;
                color: #ffffff;
                border: 1px solid #38383a;
                border-radius: 4px;
                padding: 5px 10px;
                font-size: 13px;
            }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
            QTabWidget::tab-bar {
                alignment: left;
            }
            QTabWidget::pane {
                border: 1px solid #2c2c2e;
                background-color: #1a1a1c;
                border-radius: 4px;
                margin-top: 6px;
            }
            QTabBar {
                qproperty-drawBase: 0;
                background: transparent;
                border: none;
                margin: 0px;
                padding: 0px;
            }
            QTabBar::tab {
                background-color: #242426;
                color: #8e8e93;
                padding: 6px 16px;
                margin-right: 4px;
                border: 1px solid #2c2c2e;
                border-radius: 4px;
                font-size: 12px;
            }
            QTabBar::tab:hover {
                background-color: #2c2c2e;
                color: #ffffff;
            }
            QTabBar::tab:selected {
                background-color: #1a1a1c;
                color: #38bdf8;
                font-weight: bold;
                border: 1px solid #38bdf8;
            }
            QTableWidget {
                background-color: #1a1a1c;
                color: #ffffff;
                border: none;
                gridline-color: #2c2c2e;
                selection-background-color: #1e3a8a;
                font-size: 12px;
            }
            QHeaderView::section {
                background-color: #242426;
                color: #a1a1aa;
                padding: 5px;
                border: none;
                border-right: 1px solid #2c2c2e;
                font-weight: 600;
            }
            QPushButton {
                background-color: #2c2c2e;
                color: #ffffff;
                border: 1px solid #3a3a3c;
                border-radius: 4px;
                padding: 6px 16px;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #3a3a3c;
            }
        """)

        apply_dark_title_bar(self)
        self._init_ui()

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, '_already_maximized', False):
            self._already_maximized = True
            QTimer.singleShot(0, self._apply_initial_layout)

    def _apply_initial_layout(self):
        self.showMaximized()
        QTimer.singleShot(50, self._center_splitter)

    def _center_splitter(self):
        if hasattr(self, "body_splitter"):
            w = self.body_splitter.width()
            if w > 100:
                half = w // 2
                self.body_splitter.setSizes([half, half])

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # 1. Non-Monaco Warning Banner (if applicable)
        if not self.analyzer.is_monaco:
            warn_banner = QFrame(self)
            warn_banner.setStyleSheet("""
                QFrame {
                    background-color: #3d1c06;
                    border: 2px solid #ea580c;
                    border-radius: 6px;
                    padding: 8px;
                }
            """)
            warn_layout = QHBoxLayout(warn_banner)
            warn_layout.setContentsMargins(12, 8, 12, 8)
            warn_layout.setSpacing(10)

            icon_lbl = QLabel("⚠️", warn_banner)
            icon_lbl.setStyleSheet("font-size: 24px;")
            warn_layout.addWidget(icon_lbl)

            if self.is_ru:
                msg_text = (
                    f"<b style='font-size: 13px; color: #ffedd5;'>ВНИМАНИЕ: План рассчитан НЕ в системе Monaco!</b><br>"
                    f"<span style='color: #fed7aa; font-size: 12px; line-height: 1.35;'>"
                    f"Обнаруженная система: <b>{self.analyzer.tps_name}</b>.<br>"
                    f"Кинематическая модель и расчёт рисков сбоя откалиброваны исключительно под алгоритмы Monaco и линейные ускорители Elekta. "
                    f"Для сторонних систем (Varian Eclipse, RayStation и др.) <b>РЕЗУЛЬТАТ АНАЛИЗА НЕ БУДЕТ СООТВЕТСТВОВАТЬ ДЕЙСТВИТЕЛЬНОСТИ!</b>"
                    f"</span>"
                )
            else:
                msg_text = (
                    f"<b style='font-size: 13px; color: #ffedd5;'>WARNING: Plan was NOT calculated in Monaco!</b><br>"
                    f"<span style='color: #fed7aa; font-size: 12px; line-height: 1.35;'>"
                    f"Detected system: <b>{self.analyzer.tps_name}</b>.<br>"
                    f"The kinematics model and failure risk analysis are calibrated exclusively for Monaco algorithms and Elekta linacs. "
                    f"For third-party systems (Varian Eclipse, RayStation, etc.) <b>ANALYSIS RESULTS WILL NOT REFLECT REALITY!</b>"
                    f"</span>"
                )
            text_lbl = QLabel(msg_text, warn_banner)
            text_lbl.setWordWrap(True)
            warn_layout.addWidget(text_lbl, 1)
            layout.addWidget(warn_banner)

        tps_info = (
            f"<span style='color: #4ade80; font-weight: bold;'>{self.analyzer.tps_name}</span>"
            if self.analyzer.is_monaco else
            f"<span style='color: #fb923c; font-weight: bold;'>{self.analyzer.tps_name} [{'Не Monaco' if self.is_ru else 'Not Monaco'}]</span>"
        )
        self._tps_info_template = tps_info

        # Main Body Splitter: Left (Polar Arc) + Right (Verdict & Analysis)
        self.body_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.body_splitter.setStyleSheet("QSplitter::handle { background: #2c2c2e; width: 1px; }")
        body_splitter = self.body_splitter

        # Left Panel: Polar Diagram + Legend
        left_panel = QWidget(body_splitter)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 8, 0)
        left_layout.setSpacing(8)

        self.polar_widget = PolarArcWidget(left_panel, is_ru=self.is_ru)
        self.polar_widget.intervalHovered.connect(self._on_interval_hovered)
        self.polar_widget.intervalClicked.connect(self._on_interval_clicked)
        left_layout.addWidget(self.polar_widget, 1)

        # Connect / populate embedded plan & beam selectors
        self.plan_combo = self.polar_widget.plan_combo
        for pp in self.plan_paths:
            try:
                ds_tmp = pydicom.dcmread(pp, stop_before_pixels=True, force=True,
                    specific_tags=['RTPlanLabel', 'RTPlanName'])
                lbl = str(getattr(ds_tmp, 'RTPlanLabel', '') or getattr(ds_tmp, 'RTPlanName', '') or os.path.basename(pp))
            except Exception:
                lbl = os.path.basename(pp)
            self.plan_combo.addItem(lbl, pp)
        cur_idx = self.plan_combo.findData(self.plan_path)
        if cur_idx >= 0:
            self.plan_combo.setCurrentIndex(cur_idx)
        self.plan_combo.currentIndexChanged.connect(self._on_plan_changed)

        self.beam_combo = self.polar_widget.beam_combo
        self._populate_beam_combo()
        self.beam_combo.currentIndexChanged.connect(self._on_beam_changed)

        # Legend — inline rows under polar widget with help '?' button
        legend_box = QFrame(left_panel)
        legend_box.setStyleSheet("QFrame { background: transparent; border: none; }")
        leg_outer_l = QHBoxLayout(legend_box)
        leg_outer_l.setContentsMargins(2, 4, 2, 0)
        leg_outer_l.setSpacing(6)

        leg_l = QVBoxLayout()
        leg_l.setContentsMargins(0, 0, 0, 0)
        leg_l.setSpacing(2)

        def make_leg_row(color_hex, text):
            row = QHBoxLayout()
            dot = QLabel("●")
            dot.setStyleSheet(f"color: {color_hex}; font-size: 12px;")
            lbl = QLabel(text)
            lbl.setWordWrap(True)
            lbl.setStyleSheet("font-size: 10px; color: #6b7280;")
            row.addWidget(dot)
            row.addWidget(lbl, 1)
            return row

        min_dr = self.thresholds['min_dose_rate']
        min_mpd = self.thresholds['min_mu_per_deg']
        max_jump = self.thresholds['max_modulation_factor']
        warn_dr = self.thresholds.get('warn_dose_rate', 75.0)
        warn_mpd = self.thresholds.get('warn_mu_per_deg', 0.200)
        warn_jump = self.thresholds.get('warn_modulation_factor', 8.0)
        leg_l.addLayout(make_leg_row("#30d158", "Безопасный отпуск (стабильная мощность и скорость)" if self.is_ru else "Safe delivery (stable dose rate and speeds)"))
        leg_l.addLayout(make_leg_row("#ffd60a", f"Повышенная сложность (< {warn_dr:.0f} MU/мин, < {warn_mpd:.3f} MU/deg или перепад > {warn_jump:.1f}×)" if self.is_ru else f"High complexity (< {warn_dr:.0f} MU/min, < {warn_mpd:.3f} MU/deg or jump > {warn_jump:.1f}×)"))
        leg_l.addLayout(make_leg_row("#ff453a", f"КРИТИЧЕСКИЙ РИСК СБОЯ 'DOSE RATE MON' (< {min_dr:.0f} MU/мин, < {min_mpd:.3f} MU/deg или перепад > {max_jump:.0f}×)" if self.is_ru else f"CRITICAL RISK OF 'DOSE RATE MON' FAULT (< {min_dr:.0f} MU/min, < {min_mpd:.3f} MU/deg or jump > {max_jump:.0f}×)"))

        self.multitrack_leg_label = QLabel(legend_box)
        self.multitrack_leg_label.setStyleSheet("font-size: 10px; color: #38bdf8; font-weight: 600; margin-top: 3px;")
        self.multitrack_leg_label.setText("Двойная дуга: Внутренний трек — Проход 1 (↻ CW) | Внешний трек — Проход 2 (↺ CCW)" if self.is_ru else "Dual arc: Inner track — Pass 1 (↻ CW) | Outer track — Pass 2 (↺ CCW)")
        self.multitrack_leg_label.hide()
        leg_l.addWidget(self.multitrack_leg_label)

        leg_outer_l.addLayout(leg_l, 1)

        # Question mark button for methodology and parameter origins
        self.btn_methodology_help = QPushButton("?", legend_box)
        self.btn_methodology_help.setFixedSize(48, 48)
        self.btn_methodology_help.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_methodology_help.setToolTip(
            "Методика кинематического анализа и обоснование физических порогов" if self.is_ru
            else "Kinematics Analysis Methodology & Physical Limits"
        )
        self.btn_methodology_help.setStyleSheet(
            "QPushButton { "
            "  background-color: #1a1a1c; color: #38bdf8; border: 1px solid #38bdf8; "
            "  border-radius: 4px; font-size: 22px; font-weight: bold; font-family: 'Segoe UI', sans-serif; "
            "  padding: 0px; margin: 0px; "
            "} "
            "QPushButton:hover { "
            "  background-color: #27272a; color: #7dd3fc; border: 1px solid #38bdf8; "
            "} "
            "QPushButton:pressed { "
            "  background-color: #0f172a; color: #38bdf8; border: 1px solid #0284c7; "
            "}"
        )
        self.btn_methodology_help.clicked.connect(self._open_methodology_help)
        leg_outer_l.addWidget(self.btn_methodology_help, 0, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)

        left_layout.addWidget(legend_box)

        body_splitter.addWidget(left_panel)

        # Right Panel: Patient info header + Verdict Banner + Metrics Cards + Detailed Tabs
        right_panel = QWidget(body_splitter)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(10, 0, 0, 0)
        right_layout.setSpacing(6)

        # Compact patient info — plain row, no box
        patient_frame = QFrame(right_panel)
        patient_frame.setFixedHeight(26)
        patient_frame.setStyleSheet("QFrame { background: transparent; border: none; }")
        pf_layout = QHBoxLayout(patient_frame)
        pf_layout.setContentsMargins(2, 0, 2, 0)
        pf_layout.setSpacing(10)

        self.lbl_patient = QLabel(
            f"<b style='color: #ffffff;'>{self.analyzer.patient_name}</b>"
            f"<span style='color: #6b7280;'> ({self.analyzer.patient_id})</span>",
            patient_frame
        )
        self.lbl_patient.setTextFormat(Qt.TextFormat.RichText)
        self.lbl_patient.setStyleSheet("font-size: 13px;")
        pf_layout.addWidget(self.lbl_patient)

        _sep = QLabel("|", patient_frame)
        _sep.setStyleSheet("color: #3a3a3c;")
        pf_layout.addWidget(_sep)

        self.lbl_tps = QLabel(self._tps_info_template, patient_frame)
        self.lbl_tps.setTextFormat(Qt.TextFormat.RichText)
        self.lbl_tps.setStyleSheet("font-size: 12px;")
        pf_layout.addWidget(self.lbl_tps)

        pf_layout.addStretch()

        right_layout.addWidget(patient_frame)

        # Verdict Card
        self.verdict_card = QFrame(right_panel)
        self.verdict_card.setObjectName("verdictCard")
        self.verdict_layout = QVBoxLayout(self.verdict_card)
        self.verdict_layout.setContentsMargins(14, 10, 14, 10)
        right_layout.addWidget(self.verdict_card)

        # Summary Metrics — no box border, just background
        metrics_frame = QFrame(right_panel)
        metrics_frame.setStyleSheet("QFrame { background: transparent; border: none; border-top: 1px solid #2c2c2e; }")
        m_layout = QHBoxLayout(metrics_frame)
        m_layout.setContentsMargins(4, 6, 4, 2)

        self.lbl_metric_mu = QLabel(metrics_frame)
        self.lbl_metric_dr = QLabel(metrics_frame)
        self.lbl_metric_mpd = QLabel(metrics_frame)

        for lbl in (self.lbl_metric_mu, self.lbl_metric_dr, self.lbl_metric_mpd):
            lbl.setTextFormat(Qt.TextFormat.RichText)
            m_layout.addWidget(lbl)
            m_layout.addStretch()

        right_layout.addWidget(metrics_frame)

        # Tabs: Critical Points / Graph / Full Sequence
        self.tabs = QTabWidget(right_panel)

        # Tab 1: Critical Control Points Table
        self.critical_table = QTableWidget(self.tabs)
        self._setup_table_headers(self.critical_table)
        self.critical_table.itemSelectionChanged.connect(self._on_table_selection_changed)
        self.tabs.addTab(self.critical_table, "Критические точки" if self.is_ru else "Critical Points")

        # Tab 2: Linear Graph
        self.graph_widget = ModulationTimelineWidget(self.tabs, is_ru=self.is_ru)
        self.graph_widget.intervalClicked.connect(self._on_interval_clicked)
        self.graph_widget.intervalHovered.connect(self._on_interval_hovered)
        self.tabs.addTab(self.graph_widget, "График модуляции (MU/deg)" if self.is_ru else "Modulation Graph (MU/deg)")

        # Tab 3: All Control Points Table
        self.all_table = QTableWidget(self.tabs)
        self._setup_table_headers(self.all_table)
        self.all_table.itemSelectionChanged.connect(self._on_table_selection_changed)
        self.tabs.addTab(self.all_table, "Все точки (Sequence)" if self.is_ru else "All Points (Sequence)")

        right_layout.addWidget(self.tabs, 1)

        body_splitter.addWidget(right_panel)
        body_splitter.setStretchFactor(0, 1)
        body_splitter.setStretchFactor(1, 1)
        layout.addWidget(body_splitter, 1)

        # 4. Bottom Button Bar
        bottom_bar = QHBoxLayout()
        self.status_lbl = QLabel("", self)
        self.status_lbl.setStyleSheet("color: #8e8e93; font-size: 12px;")
        bottom_bar.addWidget(self.status_lbl)
        bottom_bar.addStretch()
        layout.addLayout(bottom_bar)

        # Initial render for first beam
        if self.analyzer.beams:
            self._on_beam_changed(0)

    def _setup_table_headers(self, table: QTableWidget, is_vmat: bool = True):
        table.setColumnCount(7)
        if is_vmat:
            table.setHorizontalHeaderLabels([
                "CP",
                "Сектор гентри" if self.is_ru else "Gantry Sector",
                "Δ Гентри" if self.is_ru else "Δ Gantry",
                "Δ MU", "MU/deg",
                "Расч. мощность" if self.is_ru else "Est. Dose Rate",
                "Диагностика риска" if self.is_ru else "Risk Diagnosis"
            ])
        else:
            table.setHorizontalHeaderLabels([
                "CP",
                "Угол гентри" if self.is_ru else "Gantry Angle",
                "Δ Гентри" if self.is_ru else "Δ Gantry",
                "Δ MU",
                "Тип доставки" if self.is_ru else "Delivery Type",
                "Расч. мощность" if self.is_ru else "Est. Dose Rate",
                "Статус сегмента" if self.is_ru else "Segment Status"
            ])
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)

    def _populate_beam_combo(self) -> None:
        """Fill beam_combo from current analyzer beams."""
        self.beam_combo.blockSignals(True)
        self.beam_combo.clear()
        for b in self.analyzer.beams:
            if b['is_vmat']:
                kind = f"VMAT Arc ({b['total_gantry_travel']:.0f}°, {b['total_mu']:.0f} MU)"
            else:
                kind = f"{b['beam_mode']} {b['fixed_gantry_angle']:.0f}° ({b['num_control_points']} CP, {b['total_mu']:.0f} MU)"
            self.beam_combo.addItem(f"Beam #{b['beam_number']}: {b['beam_name']} — {kind}", b)
        self.beam_combo.blockSignals(False)

    def _on_plan_changed(self, index: int) -> None:
        """Reload analyzer when a different RTPLAN is selected."""
        if index < 0 or index >= len(self.plan_paths):
            return
        new_path = self.plan_combo.itemData(index)
        if not new_path or new_path == self.plan_path:
            return
        try:
            self.plan_path = new_path
            self.analyzer = PlanKinematicsAnalyzer(new_path, thresholds=self.thresholds, is_ru=self.is_ru)
        except Exception as e:
            log_message(None, f"Ошибка загрузки плана: {e}")
            return

        # Update header labels
        tps_info = (
            f"<span style='color: #4ade80; font-weight: bold;'>{self.analyzer.tps_name}</span>"
            if self.analyzer.is_monaco else
            f"<span style='color: #fb923c; font-weight: bold;'>{self.analyzer.tps_name} [{'Не Monaco' if self.is_ru else 'Not Monaco'}]</span>"
        )
        self.lbl_patient.setText(
            f"<b style='font-size: 14px; color: #ffffff;'>{self.analyzer.patient_name}</b> "
            f"<span style='color: #8e8e93;'>({self.analyzer.patient_id})</span>"
        )
        self.lbl_tps.setText(tps_info)

        # Update window title
        if not self.analyzer.is_monaco:
            _pfx2 = "НЕ MONACO" if self.is_ru else "NOT MONACO"
            _ttl2 = "Анализ плана" if self.is_ru else "Plan Analysis"
            self.setWindowTitle(f"[{_pfx2}] {_ttl2} — {self.analyzer.patient_name} [{self.analyzer.patient_id}]")
        else:
            _ttl2 = "Анализ плана Monaco" if self.is_ru else "Monaco Plan Analysis"
            self.setWindowTitle(f"{_ttl2} — {self.analyzer.patient_name} [{self.analyzer.patient_id}]")

        # Repopulate beams and render first beam
        self._populate_beam_combo()
        if self.analyzer.beams:
            self._on_beam_changed(0)
        else:
            self.polar_widget.set_beam_data(None)
            self.graph_widget.set_beam_data(None)

    def _on_beam_changed(self, index: int):
        if index < 0 or index >= len(self.analyzer.beams):
            return

        b = self.analyzer.beams[index]
        self.polar_widget.set_beam_data(b)
        self.graph_widget.set_beam_data(b)

        passes_info = b.get('passes_info', [])
        if b.get('num_passes', 1) > 1 and len(passes_info) >= 2:
            p1 = passes_info[0]
            p2 = passes_info[1]
            d1_sym = '↻ CW' if p1.get('direction', 'CW') == 'CW' else '↺ CCW'
            d2_sym = '↻ CW' if p2.get('direction', 'CW') == 'CW' else '↺ CCW'
            if self.is_ru:
                txt = (f"Двойная дуга: Внутренний трек — Проход 1 ({p1['start_angle']:.1f}° → {p1['end_angle']:.1f}°, {d1_sym}) | "
                       f"Внешний трек — Проход 2 ({p2['start_angle']:.1f}° → {p2['end_angle']:.1f}°, {d2_sym})")
            else:
                txt = (f"Dual arc: Inner track — Pass 1 ({p1['start_angle']:.1f}° → {p1['end_angle']:.1f}°, {d1_sym}) | "
                       f"Outer track — Pass 2 ({p2['start_angle']:.1f}° → {p2['end_angle']:.1f}°, {d2_sym})")
            self.multitrack_leg_label.setText(txt)
            self.multitrack_leg_label.show()
        else:
            self.multitrack_leg_label.hide()

        # Update Verdict Card
        verdict = b['verdict']
        crit_count = b['critical_count']
        warn_count = b['warning_count']

        # Clear old verdict layout
        while self.verdict_layout.count():
            item = self.verdict_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if verdict == 'CRITICAL':
            self.verdict_card.setStyleSheet("""
                #verdictCard {
                    background-color: #2a1010;
                    border: none;
                    border-left: 3px solid #ef4444;
                    border-radius: 4px;
                }
                #verdictCard QLabel {
                    border: none;
                    background: transparent;
                }
            """)
            min_dr = self.thresholds['min_dose_rate']
            min_mpd = self.thresholds['min_mu_per_deg']
            max_jump = self.thresholds['max_modulation_factor']
            title = QLabel(
                "🔴 ВЫСОКИЙ РИСК СБОЯ АППАРАТА (DOSE RATE MON)" if self.is_ru
                else "🔴 HIGH RISK OF MACHINE FAULT (DOSE RATE MON)",
                self.verdict_card
            )
            title.setStyleSheet("font-size: 13px; font-weight: bold; color: #fca5a5;")
            desc = QLabel(
                (f"В пучке обнаружено <b>{crit_count} критических секторов</b> с падением мощности/плотности дозы ниже порога Elekta (&lt; {min_dr:.0f} MU/мин, &lt; {min_mpd:.3f} MU/deg) "
                 f"или экстремальным перепадом модуляции (&gt; {max_jump:.0f}×). Аппарат с высокой вероятностью выдаст ошибку <code>DOSE RATE MON</code> при отпуске.")
                if self.is_ru else
                (f"Beam has <b>{crit_count} critical sectors</b> with dose rate/density below Elekta limits (&lt; {min_dr:.0f} MU/min, &lt; {min_mpd:.3f} MU/deg) "
                 f"or extreme modulation jump (&gt; {max_jump:.0f}×). Machine will very likely trigger <code>DOSE RATE MON</code> fault during delivery."),
                self.verdict_card
            )
            desc.setWordWrap(True)
            desc.setStyleSheet("font-size: 12px; color: #fee2e2; margin-top: 2px;")
            self.verdict_layout.addWidget(title)
            self.verdict_layout.addWidget(desc)
        elif verdict == 'WARNING':
            self.verdict_card.setStyleSheet("""
                #verdictCard {
                    background-color: #271c05;
                    border: none;
                    border-left: 3px solid #f59e0b;
                    border-radius: 4px;
                }
                #verdictCard QLabel {
                    border: none;
                    background: transparent;
                }
            """)
            title = QLabel(
                "🟡 ПОВЫШЕННАЯ СЛОЖНОСТЬ ПЛАНА (ТРЕБУЕТ ВНИМАНИЯ)" if self.is_ru
                else "🟡 HIGH PLAN COMPLEXITY (REQUIRES ATTENTION)",
                self.verdict_card
            )
            title.setStyleSheet("font-size: 13px; font-weight: bold; color: #fde68a;")
            desc = QLabel(
                (f"Обнаружено {warn_count} секторов с сильным снижением скорости гентри или высокой модуляцией. "
                 f"План может отпуститься медленнее расчетного времени.")
                if self.is_ru else
                (f"Found {warn_count} sectors with significant gantry speed reduction or high modulation. "
                 f"Plan may be delivered slower than calculated."),
                self.verdict_card
            )
            desc.setWordWrap(True)
            desc.setStyleSheet("font-size: 12px; color: #fef3c7; margin-top: 2px;")
            self.verdict_layout.addWidget(title)
            self.verdict_layout.addWidget(desc)
        elif verdict == 'STATIC':
            self.verdict_card.setStyleSheet("""
                #verdictCard {
                    background-color: #081a2a;
                    border: none;
                    border-left: 3px solid #38bdf8;
                    border-radius: 4px;
                }
                #verdictCard QLabel {
                    border: none;
                    background: transparent;
                }
            """)
            title = QLabel(
                f"ℹ️ {'СТАТИЧЕСКИЙ ПУЧОК' if self.is_ru else 'STATIC BEAM'} ({b['beam_mode']})",
                self.verdict_card
            )
            title.setStyleSheet("font-size: 13px; font-weight: bold; color: #7dd3fc;")
            desc = QLabel(
                (f"Пучок доставляется на фиксированном угле гентри <b>{b['fixed_gantry_angle']:.1f}°</b> "
                 f"({b['num_control_points']} контрольных точек / сегментов). "
                 f"Вращение гентри отсутствует, поэтому ротационные риски VMAT и сбои мощности <code>DOSE RATE MON</code> при прохождении дуги <b>не применимы</b>.")
                if self.is_ru else
                (f"Beam delivered at fixed gantry angle <b>{b['fixed_gantry_angle']:.1f}°</b> "
                 f"({b['num_control_points']} control points / segments). "
                 f"No gantry rotation — VMAT rotational risks and arc-related <code>DOSE RATE MON</code> faults are <b>not applicable</b>."),
                self.verdict_card
            )
            desc.setWordWrap(True)
            desc.setStyleSheet("font-size: 12px; color: #e0f2fe; margin-top: 2px;")
            self.verdict_layout.addWidget(title)
            self.verdict_layout.addWidget(desc)
        else:
            self.verdict_card.setStyleSheet("""
                #verdictCard {
                    background-color: #0a2015;
                    border: none;
                    border-left: 3px solid #22c55e;
                    border-radius: 4px;
                }
                #verdictCard QLabel {
                    border: none;
                    background: transparent;
                }
            """)
            title = QLabel(
                "🟢 ПЛАН БЕЗОПАСЕН ДЛЯ ОТПУСКА" if self.is_ru else "🟢 PLAN IS SAFE FOR DELIVERY",
                self.verdict_card
            )
            title.setStyleSheet("font-size: 13px; font-weight: bold; color: #86efac;")
            desc = QLabel(
                "Все параметры мощности дозы, скорости вращения гентри и движения лепестков укладываются в штатные лимиты Elekta."
                if self.is_ru else
                "All dose rate, gantry rotation speed, and leaf motion parameters are within Elekta standard limits.",
                self.verdict_card
            )
            desc.setWordWrap(True)
            desc.setStyleSheet("font-size: 12px; color: #dcfce7;")
            self.verdict_layout.addWidget(title)
            self.verdict_layout.addWidget(desc)

        # Append non-Monaco warning in verdict card if plan is from external TPS
        if not self.analyzer.is_monaco:
            _nm_title = (
                f"⚠️ ВНИМАНИЕ: План рассчитан не в системе Monaco ({self.analyzer.tps_name})!" if self.is_ru
                else f"⚠️ WARNING: Plan was not calculated in Monaco ({self.analyzer.tps_name})!"
            )
            _nm_body = (
                f"Модель кинематики оптимизирована под Elekta/Monaco. Для сторонних систем (Varian/RaySearch и др.) "
                f"<b>результат анализа НЕ БУДЕТ СООТВЕТСТВОВАТЬ ДЕЙСТВИТЕЛЬНОСТИ</b>."
                if self.is_ru else
                f"Kinematics model is calibrated for Elekta/Monaco. For third-party systems (Varian/RaySearch, etc.) "
                f"<b>analysis results WILL NOT REFLECT REALITY</b>."
            )
            non_monaco_notice = QLabel(
                f"<div style='margin-top: 6px; padding: 6px 10px; background-color: rgba(234, 88, 12, 0.25); "
                f"border: 1px solid #ea580c; border-radius: 4px;'>"
                f"<b style='color: #ffedd5; font-size: 11px;'>{_nm_title}</b><br>"
                f"<span style='color: #fed7aa; font-size: 11px;'>{_nm_body}</span></div>",
                self.verdict_card
            )
            non_monaco_notice.setWordWrap(True)
            self.verdict_layout.addWidget(non_monaco_notice)

        # Update Metrics Card
        _lbl_total = "Суммарно" if self.is_ru else "Total MU"
        _lbl_dr = "Мощность дозы" if self.is_ru else "Dose Rate"
        _mu_min = "MU/мин" if self.is_ru else "MU/min"
        self.lbl_metric_mu.setText(f"<span style='color: #8e8e93;'>{_lbl_total}:</span><br><b style='font-size: 13px;'>{b['total_mu']:.1f} MU</b>")
        self.lbl_metric_dr.setText(f"<span style='color: #8e8e93;'>{_lbl_dr}:</span><br><b style='font-size: 13px;'>{b['min_dose_rate']:.0f} – {b['max_dose_rate']:.0f} {_mu_min}</b>")
        if b['is_vmat']:
            _lbl_density = "Плотность дозы" if self.is_ru else "Dose Density"
            self.lbl_metric_mpd.setText(f"<span style='color: #8e8e93;'>{_lbl_density}:</span><br><b style='font-size: 13px;'>{b['min_mu_per_deg']:.2f} – {b['max_mu_per_deg']:.2f} MU/deg</b>")
            _tip_range = "Диапазон плотности дозы" if self.is_ru else "Dose density range"
            _tip_avg = "среднее" if self.is_ru else "avg"
            self.lbl_metric_mpd.setToolTip(f"{_tip_range}: {b['min_mu_per_deg']:.2f} – {b['max_mu_per_deg']:.2f} MU/deg ({_tip_avg}: {b['avg_mu_per_deg']:.2f} MU/deg)")
            self.tabs.setTabText(1, "График модуляции (MU/deg)" if self.is_ru else "Modulation Graph (MU/deg)")
        else:
            _lbl_gantry_ang = "Угол гентри" if self.is_ru else "Gantry Angle"
            _static_word = "статика" if self.is_ru else "static"
            self.lbl_metric_mpd.setText(f"<span style='color: #8e8e93;'>{_lbl_gantry_ang}:</span><br><b style='font-size: 13px;'>{b['fixed_gantry_angle']:.1f}° ({_static_word})</b>")
            self.lbl_metric_mpd.setToolTip("")
            self.tabs.setTabText(1, "График сегментов (ΔMU)" if self.is_ru else "Segment Graph (ΔMU)")

        # Setup Table Headers according to mode
        self._setup_table_headers(self.critical_table, is_vmat=b['is_vmat'])
        self._setup_table_headers(self.all_table, is_vmat=b['is_vmat'])

        # Fill Critical Points Table
        intervals = b.get('intervals', [])
        crit_items = [item for item in intervals if item['risk_level'] in ('CRITICAL', 'WARNING')]
        self._populate_table(self.critical_table, crit_items, is_vmat=b['is_vmat'])
        _crit_tab = "Критические точки" if self.is_ru else "Critical Points"
        self.tabs.setTabText(0, f"{_crit_tab} ({len(crit_items)})")

        # Fill All Points Table
        self._populate_table(self.all_table, intervals, is_vmat=b['is_vmat'])

    def _populate_table(self, table: QTableWidget, items: List[Dict[str, Any]], is_vmat: bool = True):
        table.setRowCount(0)
        table.setRowCount(len(items))

        for row, item in enumerate(items):
            cp_idx = item['index']
            if is_vmat:
                g_str = f"{item['gantry_start']:.1f}° → {item['gantry_end']:.1f}°"
                dg_str = f"{item['delta_gantry']:.1f}°"
                mpd_str = f"{item['mu_per_deg']:.2f}"
                default_ok = "OK"
            else:
                g_str = f"{item['gantry_start']:.1f}°"
                dg_str = "0.0°"
                mpd_str = "Статика" if self.is_ru else "Static"
                default_ok = "OK"

            dmu_str = f"{item['delta_mu']:.2f}"
            dr_val = item['est_dose_rate']
            _mu_min_t = "MU/мин" if self.is_ru else "MU/min"
            if round(dr_val) < 60 and item['delta_mu'] > 0.01:
                dr_str = f"{dr_val:.1f} {_mu_min_t}"
            else:
                dr_str = f"{dr_val:.0f} {_mu_min_t}"
            reason_str = "; ".join(item['reasons']) if item['reasons'] else default_ok

            risk = item['risk_level']
            if risk == 'CRITICAL':
                fg_color = QColor("#ff453a")
            elif risk == 'WARNING':
                fg_color = QColor("#ffd60a")
            else:
                fg_color = QColor("#e5e5ea")

            c_item = QTableWidgetItem(f"{cp_idx:02d}")
            c_item.setData(Qt.ItemDataRole.UserRole, cp_idx)
            g_item = QTableWidgetItem(g_str)
            dg_item = QTableWidgetItem(dg_str)
            dmu_item = QTableWidgetItem(dmu_str)
            mpd_item = QTableWidgetItem(mpd_str)
            dr_item = QTableWidgetItem(dr_str)
            r_item = QTableWidgetItem(reason_str)

            for it in (c_item, g_item, dg_item, dmu_item, mpd_item, dr_item, r_item):
                it.setForeground(fg_color)

            table.setItem(row, 0, c_item)
            table.setItem(row, 1, g_item)
            table.setItem(row, 2, dg_item)
            table.setItem(row, 3, dmu_item)
            table.setItem(row, 4, mpd_item)
            table.setItem(row, 5, dr_item)
            table.setItem(row, 6, r_item)

    def _on_table_selection_changed(self):
        table = self.sender()
        if not isinstance(table, QTableWidget):
            return
        selected = table.selectedItems()
        if not selected:
            return
        row = selected[0].row()
        item0 = table.item(row, 0)
        if item0:
            cp_idx = item0.data(Qt.ItemDataRole.UserRole)
            if cp_idx is not None:
                idx = int(cp_idx)
                self.polar_widget.select_interval(idx)
                self.graph_widget.select_interval(idx)
                if self.polar_widget.beam_data:
                    for cp_info in self.polar_widget.beam_data.get('intervals', []):
                        if cp_info['index'] == idx:
                            self._on_interval_hovered(cp_info)
                            break

    def _on_interval_hovered(self, cp_info: Dict[str, Any]):
        dr = cp_info['est_dose_rate']
        mpd = cp_info['mu_per_deg']
        is_vmat = (cp_info.get('delta_gantry', 0.0) > 0.001)
        num_passes = self.polar_widget.beam_data.get('num_passes', 1) if self.polar_widget.beam_data else 1
        p_idx = cp_info.get('pass_idx', 0)
        rot_dir = cp_info.get('direction', 'CW')
        dir_sym = '↻ CW' if rot_dir == 'CW' else '↺ CCW'
        _pass_word = "Проход" if self.is_ru else "Pass"
        pass_tag = f" [{_pass_word} {p_idx+1}: {dir_sym}]" if num_passes > 1 else ""

        dr_fmt = f"{dr:.1f}" if round(dr) < 60 and cp_info.get('delta_mu', 0.0) > 0.01 else f"{dr:.0f}"
        _mu_min_h = "MU/мин" if self.is_ru else "MU/min"
        _gantry_h = "Гентри" if self.is_ru else "Gantry"
        _power_h = "Мощность" if self.is_ru else "Dose Rate"
        _static_h = "статика" if self.is_ru else "static"
        if is_vmat:
            msg = f"CP {cp_info['index']:02d}{pass_tag}: {_gantry_h} {cp_info['gantry_start']:.1f}° → {cp_info['gantry_end']:.1f}° | ΔMU: {cp_info['delta_mu']:.2f} | MU/deg: {mpd:.2f} | {_power_h}: {dr_fmt} {_mu_min_h}"
        else:
            msg = f"CP {cp_info['index']:02d}: {_gantry_h} {cp_info['gantry_start']:.1f}° ({_static_h}) | ΔMU: {cp_info['delta_mu']:.2f} | {_power_h}: {dr_fmt} {_mu_min_h}"
        if cp_info['reasons']:
            msg += f" — ⚠️ {'; '.join(cp_info['reasons'])}"
        self.status_lbl.setText(msg)

    def _on_interval_clicked(self, cp_idx: int):
        self.polar_widget.select_interval(cp_idx)
        self.graph_widget.select_interval(cp_idx)
        table = self.tabs.currentWidget()
        if isinstance(table, QTableWidget):
            for row in range(table.rowCount()):
                item = table.item(row, 0)
                if item and item.data(Qt.ItemDataRole.UserRole) == cp_idx:
                    table.selectRow(row)
                    table.scrollToItem(item)
                    break
        if self.polar_widget.beam_data:
            for cp_info in self.polar_widget.beam_data.get('intervals', []):
                if cp_info['index'] == cp_idx:
                    self._on_interval_hovered(cp_info)
                    break

    def _open_methodology_help(self):
        show_plan_analysis_help(self, is_ru=self.is_ru)


def open_plan_analyzer(parent, folder_or_plan_path: str, patient_id: str = "", patient_name: str = ""):
    """Helper to find RTPLAN in folder (or direct plan path) and show the MonacoPlanAnalyzerDialog."""
    plan_file = None
    all_plan_files: list = []

    if folder_or_plan_path and os.path.isfile(folder_or_plan_path):
        plan_file = folder_or_plan_path
        folder = os.path.dirname(folder_or_plan_path)
    else:
        folder = folder_or_plan_path

    if folder and os.path.isdir(folder):
        for root, dirs, files in os.walk(folder):
            for f in files:
                fp = os.path.join(root, f)
                try:
                    ds_tmp = pydicom.dcmread(fp, stop_before_pixels=True, force=True, specific_tags=['Modality'])
                    if str(getattr(ds_tmp, 'Modality', '')).upper() == 'RTPLAN':
                        all_plan_files.append(fp)
                except Exception:
                    pass

    if all_plan_files and plan_file is None:
        plan_file = all_plan_files[0]
    elif plan_file and plan_file not in all_plan_files:
        all_plan_files.insert(0, plan_file)

    if not plan_file:
        _cfg_f = getattr(parent, 'config', {}) or {}
        _is_ru_f = _cfg_f.get('interface_lang', 'en') == 'ru'
        QMessageBox.warning(
            parent,
            "План не найден" if _is_ru_f else "Plan Not Found",
            f"В папке пациента {patient_name} ({patient_id}) не обнаружен файл RTPLAN."
            if _is_ru_f else
            f"No RTPLAN file found in patient folder {patient_name} ({patient_id})."
        )
        return

    try:
        dlg = MonacoPlanAnalyzerDialog(parent, plan_file, plan_paths=all_plan_files, config=getattr(parent, 'config', None))
        dlg.showMaximized()
        dlg.exec()
    except Exception as e:
        log_message(getattr(parent, 'output_field', None), f"Monaco plan analysis error: {e}")
        _cfg_e = getattr(parent, 'config', {}) or {}
        _is_ru_e = _cfg_e.get('interface_lang', 'en') == 'ru'
        QMessageBox.critical(
            parent,
            "Ошибка анализа плана" if _is_ru_e else "Plan Analysis Error",
            f"Не удалось проанализировать файл плана:\n{e}" if _is_ru_e else f"Failed to analyze plan file:\n{e}"
        )
