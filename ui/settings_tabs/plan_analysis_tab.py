# -*- coding: utf-8 -*-
"""Plan Analysis Settings Tab."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, 
                             QFormLayout, QSpinBox, QDoubleSpinBox, QFrame,
                             QPushButton)

from ui.toggle_switch import ToggleSwitch
from ui.plan_analysis_help_dialog import show_plan_analysis_help
from core.locale_utils import tr_ui


def build_plan_analysis_tab(dialog):
    tab_widget = QWidget()
    tab_layout = QVBoxLayout(tab_widget)
    tab_layout.setContentsMargins(15, 15, 15, 15)
    tab_layout.setSpacing(10)

    form = QFormLayout()
    form.setSpacing(8)

    # 1. Master toggle: Enable context menu item
    dialog.plan_analyzer_context_menu_cb = ToggleSwitch()
    dialog.plan_analyzer_context_menu_cb.setChecked(
        dialog.config.get('plan_analyzer_context_menu_enabled', 'False').lower() == 'true'
    )
    dialog.lbl_plan_analyzer_context_menu = QLabel()
    form.addRow(dialog.lbl_plan_analyzer_context_menu, dialog.plan_analyzer_context_menu_cb)

    # Separator
    sep_line1 = QFrame()
    sep_line1.setFrameShape(QFrame.Shape.HLine)
    sep_line1.setFrameShadow(QFrame.Shadow.Sunken)
    sep_line1.setStyleSheet("background-color: #2d2d2d; margin-top: 4px; margin-bottom: 4px;")
    form.addRow(sep_line1)

    spin_style = (
        "QSpinBox, QDoubleSpinBox { background-color: #1e1e1e; color: #ffffff; border: 1px solid #2d2d2d; padding: 4px 6px; border-radius: 4px; font-weight: 600; font-size: 12px; }"
        "QSpinBox:focus, QDoubleSpinBox:focus { border: 1px solid #007acc; }"
        "QSpinBox:disabled, QDoubleSpinBox:disabled { background-color: #141414; color: #666666; border: 1px solid #1c1c1c; }"
    )

    # --- Section 1: Red Zone (Critical Risk / Fault) ---
    dialog.lbl_plan_red_zone_header = QLabel()
    dialog.lbl_plan_red_zone_header.setStyleSheet("font-size: 12px; font-weight: bold; color: #ef4444; margin-top: 2px;")
    form.addRow(dialog.lbl_plan_red_zone_header)

    # Red Dose rate threshold
    dialog.plan_min_dose_rate_spin = QSpinBox()
    dialog.plan_min_dose_rate_spin.setRange(20, 300)
    dialog.plan_min_dose_rate_spin.setSingleStep(5)
    dialog.plan_min_dose_rate_spin.setValue(int(dialog.config.get('plan_min_dose_rate', 60)))
    dialog.plan_min_dose_rate_spin.setFixedWidth(120)
    dialog.plan_min_dose_rate_spin.setStyleSheet(spin_style)
    dialog.lbl_plan_min_dose_rate = QLabel()
    form.addRow(dialog.lbl_plan_min_dose_rate, dialog.plan_min_dose_rate_spin)

    # Red Dose density threshold (MU/deg)
    dialog.plan_min_mu_per_deg_spin = QDoubleSpinBox()
    dialog.plan_min_mu_per_deg_spin.setRange(0.010, 1.000)
    dialog.plan_min_mu_per_deg_spin.setDecimals(3)
    dialog.plan_min_mu_per_deg_spin.setSingleStep(0.005)
    dialog.plan_min_mu_per_deg_spin.setValue(float(dialog.config.get('plan_min_mu_per_deg', 0.165)))
    dialog.plan_min_mu_per_deg_spin.setFixedWidth(120)
    dialog.plan_min_mu_per_deg_spin.setStyleSheet(spin_style)
    dialog.lbl_plan_min_mu_per_deg = QLabel()
    form.addRow(dialog.lbl_plan_min_mu_per_deg, dialog.plan_min_mu_per_deg_spin)

    # Red Modulation jump factor threshold
    dialog.plan_max_modulation_factor_spin = QDoubleSpinBox()
    dialog.plan_max_modulation_factor_spin.setRange(2.0, 50.0)
    dialog.plan_max_modulation_factor_spin.setDecimals(1)
    dialog.plan_max_modulation_factor_spin.setSingleStep(0.5)
    dialog.plan_max_modulation_factor_spin.setValue(float(dialog.config.get('plan_max_modulation_factor', 10.0)))
    dialog.plan_max_modulation_factor_spin.setFixedWidth(120)
    dialog.plan_max_modulation_factor_spin.setStyleSheet(spin_style)
    dialog.lbl_plan_max_modulation_factor = QLabel()
    form.addRow(dialog.lbl_plan_max_modulation_factor, dialog.plan_max_modulation_factor_spin)

    # Separator
    sep_line2 = QFrame()
    sep_line2.setFrameShape(QFrame.Shape.HLine)
    sep_line2.setFrameShadow(QFrame.Shadow.Sunken)
    sep_line2.setStyleSheet("background-color: #2d2d2d; margin-top: 4px; margin-bottom: 4px;")
    form.addRow(sep_line2)

    # --- Section 2: Yellow Zone (Warning / High Complexity) ---
    dialog.lbl_plan_yellow_zone_header = QLabel()
    dialog.lbl_plan_yellow_zone_header.setStyleSheet("font-size: 12px; font-weight: bold; color: #f59e0b; margin-top: 2px;")
    form.addRow(dialog.lbl_plan_yellow_zone_header)

    # Yellow Dose rate threshold
    dialog.plan_warn_dose_rate_spin = QSpinBox()
    dialog.plan_warn_dose_rate_spin.setRange(25, 400)
    dialog.plan_warn_dose_rate_spin.setSingleStep(5)
    dialog.plan_warn_dose_rate_spin.setValue(int(dialog.config.get('plan_warn_dose_rate', 75)))
    dialog.plan_warn_dose_rate_spin.setFixedWidth(120)
    dialog.plan_warn_dose_rate_spin.setStyleSheet(spin_style)
    dialog.lbl_plan_warn_dose_rate = QLabel()
    form.addRow(dialog.lbl_plan_warn_dose_rate, dialog.plan_warn_dose_rate_spin)

    # Yellow Dose density threshold (MU/deg)
    dialog.plan_warn_mu_per_deg_spin = QDoubleSpinBox()
    dialog.plan_warn_mu_per_deg_spin.setRange(0.020, 1.500)
    dialog.plan_warn_mu_per_deg_spin.setDecimals(3)
    dialog.plan_warn_mu_per_deg_spin.setSingleStep(0.005)
    dialog.plan_warn_mu_per_deg_spin.setValue(float(dialog.config.get('plan_warn_mu_per_deg', 0.200)))
    dialog.plan_warn_mu_per_deg_spin.setFixedWidth(120)
    dialog.plan_warn_mu_per_deg_spin.setStyleSheet(spin_style)
    dialog.lbl_plan_warn_mu_per_deg = QLabel()
    form.addRow(dialog.lbl_plan_warn_mu_per_deg, dialog.plan_warn_mu_per_deg_spin)

    # Yellow Modulation jump factor threshold
    dialog.plan_warn_modulation_factor_spin = QDoubleSpinBox()
    dialog.plan_warn_modulation_factor_spin.setRange(1.5, 40.0)
    dialog.plan_warn_modulation_factor_spin.setDecimals(1)
    dialog.plan_warn_modulation_factor_spin.setSingleStep(0.5)
    dialog.plan_warn_modulation_factor_spin.setValue(float(dialog.config.get('plan_warn_modulation_factor', 8.0)))
    dialog.plan_warn_modulation_factor_spin.setFixedWidth(120)
    dialog.plan_warn_modulation_factor_spin.setStyleSheet(spin_style)
    dialog.lbl_plan_warn_modulation_factor = QLabel()
    form.addRow(dialog.lbl_plan_warn_modulation_factor, dialog.plan_warn_modulation_factor_spin)

    tab_layout.addLayout(form)

    tab_layout.addStretch()

    # Bottom bar with question button
    bottom_row = QHBoxLayout()
    bottom_row.setContentsMargins(0, 4, 0, 0)
    bottom_row.addStretch()

    dialog.btn_plan_methodology_help = QPushButton("?")
    dialog.btn_plan_methodology_help.setFixedSize(48, 48)
    dialog.btn_plan_methodology_help.setCursor(Qt.CursorShape.PointingHandCursor)
    dialog.btn_plan_methodology_help.setStyleSheet(
        "QPushButton { "
        "  background-color: #27272a; color: #38bdf8; border: 2px solid #38bdf8; "
        "  border-radius: 24px; font-size: 22px; font-weight: bold; font-family: 'Segoe UI', sans-serif; "
        "  padding: 0px; margin: 0px; "
        "} "
        "QPushButton:hover { "
        "  background-color: #0284c7; color: #ffffff; border: 2px solid #38bdf8; "
        "} "
        "QPushButton:pressed { "
        "  background-color: #0369a1; color: #ffffff; border: 2px solid #0284c7; "
        "}"
    )
    dialog.btn_plan_methodology_help.clicked.connect(
        lambda: show_plan_analysis_help(
            dialog,
            is_ru=(dialog.config.get('interface_lang', 'en') == 'ru')
        )
    )
    bottom_row.addWidget(dialog.btn_plan_methodology_help)
    tab_layout.addLayout(bottom_row)

    return tab_widget


def retranslate_plan_analysis_tab(dialog):
    is_ru = (dialog.config.get('interface_lang', 'en') == 'ru')

    def get_tr(key, ru_txt, en_txt):
        val = tr_ui(key)
        if val == key:
            return ru_txt if is_ru else en_txt
        return val

    dialog.lbl_plan_analyzer_context_menu.setText(
        get_tr("settings_plan_analyzer_context_menu",
               "Пункт «Проверить план Monaco» в контекстном меню:",
               "Show 'Check Monaco Plan' in context menu:")
    )
    dialog.lbl_plan_analyzer_context_menu.setToolTip(
        get_tr("tooltip_plan_analyzer_context_menu",
               "Включает отображение пункта запуска анализатора кинематики плана в контекстных меню таблиц пациентов.",
               "Enables the Monaco plan kinematics check option in patient tables context menus.")
    )
    dialog.plan_analyzer_context_menu_cb.setToolTip(dialog.lbl_plan_analyzer_context_menu.toolTip())

    # --- Section: Red Zone ---
    dialog.lbl_plan_red_zone_header.setText(
        get_tr("settings_plan_red_zone_header",
               "🔴 Красная зона (Критический риск / Сбой):",
               "🔴 Red Zone (Critical Risk / Fault):")
    )

    dialog.lbl_plan_min_dose_rate.setText(
        get_tr("settings_plan_min_dose_rate",
               "Порог мощности дозы:",
               "Minimum dose rate threshold:")
    )
    dialog.plan_min_dose_rate_spin.setSuffix(
        get_tr("settings_unit_mu_min", " MU/мин", " MU/min")
    )
    dialog.plan_min_dose_rate_spin.setToolTip(
        get_tr("tooltip_plan_min_dose_rate",
               "Минимальная стабильная мощность дозы излучателя (по умолчанию 60 MU/мин). Падение ниже порога отмечает сектор сбоем.",
               "Minimum stable PRF dose rate output (default 60 MU/min). Drop below this threshold flags sector as critical fault.")
    )

    dialog.lbl_plan_min_mu_per_deg.setText(
        get_tr("settings_plan_min_mu_per_deg",
               "Порог плотности дозы:",
               "Minimum dose density threshold:")
    )
    dialog.plan_min_mu_per_deg_spin.setSuffix(
        get_tr("settings_unit_mu_deg", " MU/deg", " MU/deg")
    )
    dialog.plan_min_mu_per_deg_spin.setToolTip(
        get_tr("tooltip_plan_min_mu_per_deg",
               "Минимальная плотность дозы дуги (по умолчанию 0.165 MU/deg, эквивалент 60 MU/мин при 6.0°/с).",
               "Minimum arc dose density limit (default 0.165 MU/deg, corresponds to 60 MU/min at 6.0 deg/s).")
    )

    dialog.lbl_plan_max_modulation_factor.setText(
        get_tr("settings_plan_max_modulation_factor",
               "Критический перепад плотности:",
               "Critical modulation jump threshold:")
    )
    dialog.plan_max_modulation_factor_spin.setSuffix(" ×")
    dialog.plan_max_modulation_factor_spin.setToolTip(
        get_tr("tooltip_plan_max_modulation_factor",
               "Порог кратности резкого скачка между соседними активными точками (по умолчанию 10×).",
               "Threshold factor for sudden jumps between adjacent active control points (default 10×).")
    )

    # --- Section: Yellow Zone ---
    dialog.lbl_plan_yellow_zone_header.setText(
        get_tr("settings_plan_yellow_zone_header",
               "🟡 Жёлтая зона (Предупреждение / Повышенная сложность):",
               "🟡 Yellow Zone (Warning / High Complexity):")
    )

    dialog.lbl_plan_warn_dose_rate.setText(
        get_tr("settings_plan_warn_dose_rate",
               "Предупреждение по мощности дозы:",
               "Warning dose rate threshold:")
    )
    dialog.plan_warn_dose_rate_spin.setSuffix(
        get_tr("settings_unit_mu_min", " MU/мин", " MU/min")
    )
    dialog.plan_warn_dose_rate_spin.setToolTip(
        get_tr("tooltip_plan_warn_dose_rate",
               "Порог предупреждения по мощности дозы (по умолчанию 75 MU/мин). Падение ниже этого порога, но выше порога сбоя, отмечает сектор желтым.",
               "Warning dose rate threshold (default 75 MU/min). Drop below this threshold, but above critical fault, flags sector as warning.")
    )

    dialog.lbl_plan_warn_mu_per_deg.setText(
        get_tr("settings_plan_warn_mu_per_deg",
               "Предупреждение по плотности дозы:",
               "Warning dose density threshold:")
    )
    dialog.plan_warn_mu_per_deg_spin.setSuffix(
        get_tr("settings_unit_mu_deg", " MU/deg", " MU/deg")
    )
    dialog.plan_warn_mu_per_deg_spin.setToolTip(
        get_tr("tooltip_plan_warn_mu_per_deg",
               "Порог предупреждения по плотности дозы (по умолчанию 0.200 MU/deg). Значения ниже этого уровня отмечаются желтым.",
               "Warning dose density threshold (default 0.200 MU/deg). Values below this level are flagged as warning.")
    )

    dialog.lbl_plan_warn_modulation_factor.setText(
        get_tr("settings_plan_warn_modulation_factor",
               "Предупреждение по перепаду плотности:",
               "Warning modulation jump threshold:")
    )
    dialog.plan_warn_modulation_factor_spin.setSuffix(" ×")
    dialog.plan_warn_modulation_factor_spin.setToolTip(
        get_tr("tooltip_plan_warn_modulation_factor",
               "Порог предупреждения по резкому перепаду плотности дозы (по умолчанию 8.0×).",
               "Warning modulation jump threshold factor (default 8.0×).")
    )
    if hasattr(dialog, 'btn_plan_methodology_help'):
        dialog.btn_plan_methodology_help.setToolTip(
            get_tr("tooltip_plan_methodology_help",
                   "Методика кинематического анализа и обоснование физических порогов",
                   "Kinematics Analysis Methodology & Physical Limits")
        )
