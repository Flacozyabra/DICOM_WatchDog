# -*- coding: utf-8 -*-
"""Plan Delivery & Kinematics Analysis Help Dialog."""

import sys
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QTextBrowser
)


def apply_dark_title_bar(widget: QWidget):
    """Enable native Windows 10/11 dark title bar."""
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


class PlanAnalysisHelpDialog(QDialog):
    """Dialog window displaying plan deliverability analysis methodology and linac physics."""

    def __init__(self, parent=None, is_ru: bool = True):
        super().__init__(parent)
        self.is_ru = is_ru
        self.setWindowTitle(
            "Методика кинематического анализа планов Elekta / Monaco" if is_ru 
            else "Elekta / Monaco Plan Kinematics Methodology & Linac Physics"
        )
        self.resize(780, 640)
        self.setStyleSheet("background-color: #141416; color: #f4f4f5; font-family: 'Segoe UI', sans-serif;")
        apply_dark_title_bar(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Header card
        header_frame = QFrame(self)
        header_frame.setObjectName("helpHeaderFrame")
        header_frame.setStyleSheet(
            "QFrame#helpHeaderFrame { background-color: #1a1a1e; border: 1px solid #27272a; border-radius: 8px; } "
            "QLabel { border: none; background: transparent; }"
        )
        h_layout = QVBoxLayout(header_frame)
        h_layout.setContentsMargins(14, 12, 14, 12)
        h_layout.setSpacing(4)

        lbl_title = QLabel(
            "ℹ️ Кинематический анализ отпуска планов Monaco (Elekta VMAT)" if is_ru 
            else "ℹ️ Monaco Plan Kinematics & Delivery Analysis (Elekta VMAT)"
        )
        lbl_title.setStyleSheet("font-size: 15px; font-weight: bold; color: #38bdf8;")
        h_layout.addWidget(lbl_title)

        lbl_sub = QLabel(
            "Физическая природа интерлока DOSE RATE MON и обоснование порогов по умолчанию" if is_ru
            else "Linac DOSE RATE MON physics and derivation of default thresholds"
        )
        lbl_sub.setStyleSheet("font-size: 11px; color: #9ca3af;")
        h_layout.addWidget(lbl_sub)
        layout.addWidget(header_frame)

        # Rich text content browser
        browser = QTextBrowser(self)
        browser.setStyleSheet(
            "QTextBrowser { "
            "  background-color: #18181b; color: #e4e4e7; border: 1px solid #27272a; "
            "  border-radius: 8px; padding: 14px; font-family: 'Segoe UI', -apple-system, sans-serif; "
            "} "
            "QScrollBar:vertical { background: #141416; width: 10px; } "
            "QScrollBar::handle:vertical { background: #3f3f46; border-radius: 5px; min-height: 24px; } "
            "QScrollBar::handle:vertical:hover { background: #52525b; }"
        )
        browser.setOpenExternalLinks(True)
        browser.setHtml(self._get_html_content(is_ru))
        layout.addWidget(browser, 1)

        # Bottom close button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_close = QPushButton("Закрыть" if is_ru else "Close", self)
        btn_close.setFixedWidth(110)
        btn_close.setStyleSheet(
            "QPushButton { "
            "  background-color: #27272a; color: #ffffff; border: 1px solid #3f3f46; "
            "  border-radius: 6px; padding: 6px 14px; font-weight: 600; font-size: 12px; "
            "} "
            "QPushButton:hover { background-color: #3f3f46; border-color: #52525b; } "
            "QPushButton:pressed { background-color: #18181b; }"
        )
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

    def _get_html_content(self, is_ru: bool) -> str:
        if is_ru:
            return """
<style>
  body { color: #e4e4e7; font-size: 13px; line-height: 1.6; margin: 4px; }
  h2 { color: #38bdf8; font-size: 15px; margin-top: 16px; margin-bottom: 6px; border-bottom: 1px solid #27272a; padding-bottom: 4px; }
  p { margin-top: 4px; margin-bottom: 8px; }
  .card { background-color: #1f1f23; border: 1px solid #2d2d32; border-radius: 6px; padding: 10px 14px; margin: 8px 0; }
  .badge-red { color: #ef4444; font-weight: bold; }
  .badge-yellow { color: #f59e0b; font-weight: bold; }
  .badge-green { color: #22c55e; font-weight: bold; }
  code { background-color: #27272a; color: #fbbf24; padding: 1px 5px; border-radius: 3px; font-family: Consolas, monospace; font-size: 12px; }
  ul { margin-top: 4px; margin-bottom: 8px; padding-left: 18px; }
  li { margin-bottom: 4px; }
  table { width: 100%; border-collapse: collapse; margin: 10px 0; }
  th { background-color: #1e1e24; color: #9ca3af; text-align: left; padding: 7px 10px; border: 1px solid #2d2d32; font-size: 12px; }
  td { padding: 7px 10px; border: 1px solid #27272a; font-size: 12px; vertical-align: top; }
</style>

<h2>1. Назначение методики</h2>
<p>
  Анализатор кинематики оценивает реализуемость (deliverability) ротационных планов <b>VMAT</b>, созданных в СППР <b>Elekta Monaco</b>, на линейных ускорителях <b>Elekta</b> (Synergy, Infinity, Versa HD) с многолепестковым коллиматором <b>Agility</b>.
</p>
<p>
  Методика позволяет ещё на этапе дозиметрического планирования выявить потенциально аварийные сектора, провоцирующие аппаратный останов пучка интерлоком <code>DOSE RATE MON</code> или сбой слежения сервоприводов гентри. Для статических полей (Static IMRT / 3D-CRT) гентри неподвижен во время облучения, поэтому ротационные риски и расчёт плотности дозы (MU/deg) к ним не применяются.
</p>

<h2>2. Физическая природа интерлока DOSE RATE MON</h2>
<div class="card">
  <p>
    Мощность дозы на ускорителях Elekta регулируется изменением частоты следования импульсов (PRF — Pulse Repetition Frequency) генератора СВЧ-колебаний (магнетрона или клистрона).
  </p>
  <ul>
    <li><b>Диапазон модуляции PRF:</b> от ~25 Гц (соответствует мощности 60 MU/мин) до ~400 Гц (номинал 600 MU/мин для стандартного 6 MV).</li>
    <li><b>Нижний предел отсечки (60 MU/мин):</b> при попытке снизить мощность ниже 60 MU/мин импульсная генерация становится нестабильной, пропуски импульсов приводят к дефициту заряда в ионизационных камерах монитора дозы (IC1/IC2).</li>
    <li><b>Срабатывание интерлока:</b> дозиметрический контур системы управления Integrity/Desktop фиксирует провал мощности и немедленно выбивает ошибку <code>DOSE RATE MON</code>, прерывая отпуск фракции.</li>
  </ul>
</div>

<h2>3. Ключевые параметры и откуда взяты значения по умолчанию</h2>
<table>
  <tr>
    <th style="width: 28%;">Параметр</th>
    <th style="width: 18%;">По умолчанию</th>
    <th>Физическое обоснование и источник</th>
  </tr>
  <tr>
    <td><span class="badge-red">Порог мощности дозы (сбой)</span></td>
    <td><code>60 MU/мин</code></td>
    <td>Заводской паспорт линейных ускорителей Elekta и аппаратный предел в шаблонах машин Monaco TPS (<i>Min dose rate cut-off</i>). Ниже 60 MU/мин пучок на flattened-энергиях становится нестабильным.</td>
  </tr>
  <tr>
    <td><span class="badge-red">Порог плотности дозы (провал)</span></td>
    <td><code>0.165 MU/deg</code></td>
    <td>
      <b>Физико-математический вывод:</b><br>
      Максимальная угловая скорость вращения гентри Elekta равна <b>6.0°/с</b> (1.0 об/мин).<br>
      При мощности 60 MU/мин темп отпуска дозы составляет <b>1.0 MU/с</b> (60 MU / 60 с).<br>
      Минимально допустимая плотность дозы на градус дуги:<br>
      <code>Плотность = 1.0 MU/с ÷ 6.0°/с ≈ 0.1667 MU/deg ≈ 0.165 MU/deg</code>.<br>
      Если плотность дозы ниже 0.165 MU/deg, гентри даже на предельной скорости вращения (6.0°/с) получает избыточную дозу на градус, что вынуждает ускоритель опускать мощность ниже стабильного предела 60 MU/мин и вызывает сбой <code>DOSE RATE MON</code>.
    </td>
  </tr>
  <tr>
    <td><span class="badge-red">Критический перепад плотности</span></td>
    <td><code>10.0×</code></td>
    <td>Определяется динамическим диапазоном сервоприводов гентри и лепестков Agility. Скачок плотности более чем в 10 раз между соседними 2–3° дуги (например, с 0.2 до 2.0 MU/deg) требует 10-кратного резкого торможения гентри. Механическая инерция и дозиметрия не успевают перестроиться, провоцируя затыкание или ошибку синхронизации.</td>
  </tr>
  <tr>
    <td><span class="badge-yellow">Жёлтая зона: Мощность дозы</span></td>
    <td><code>75 MU/мин</code></td>
    <td>Превентивный буфер безопасности (+25% от порога сбоя). Предупреждает о работе магнетрона вблизи нижней границы устойчивости.</td>
  </tr>
  <tr>
    <td><span class="badge-yellow">Жёлтая зона: Плотность дозы</span></td>
    <td><code>0.200 MU/deg</code></td>
    <td>Эквивалентно мощности ~72 MU/мин при максимальной скорости гентри.</td>
  </tr>
  <tr>
    <td><span class="badge-yellow">Жёлтая зона: Перепад плотности</span></td>
    <td><code>8.0×</code></td>
    <td>Предупреждает о высокой кинематической нагрузке и затягивании отпуска дуги.</td>
  </tr>
</table>

<h2>4. Особенности алгоритмов Monaco и сторонние СППР</h2>
<p>
  Анализатор откалиброван строго под дискретизацию ротационных дуг Monaco (шаг контрольных точек 2–3° с переменной скоростью гентри). В сторонних системах (Varian Eclipse, RayStation) используются иные кинематические модели, поэтому для не-Monaco планов программа выводит предупреждение о нерелевантности оценки.
</p>
"""
        else:
            return """
<style>
  body { color: #e4e4e7; font-size: 13px; line-height: 1.6; margin: 4px; }
  h2 { color: #38bdf8; font-size: 15px; margin-top: 16px; margin-bottom: 6px; border-bottom: 1px solid #27272a; padding-bottom: 4px; }
  p { margin-top: 4px; margin-bottom: 8px; }
  .card { background-color: #1f1f23; border: 1px solid #2d2d32; border-radius: 6px; padding: 10px 14px; margin: 8px 0; }
  .badge-red { color: #ef4444; font-weight: bold; }
  .badge-yellow { color: #f59e0b; font-weight: bold; }
  .badge-green { color: #22c55e; font-weight: bold; }
  code { background-color: #27272a; color: #fbbf24; padding: 1px 5px; border-radius: 3px; font-family: Consolas, monospace; font-size: 12px; }
  ul { margin-top: 4px; margin-bottom: 8px; padding-left: 18px; }
  li { margin-bottom: 4px; }
  table { width: 100%; border-collapse: collapse; margin: 10px 0; }
  th { background-color: #1e1e24; color: #9ca3af; text-align: left; padding: 7px 10px; border: 1px solid #2d2d32; font-size: 12px; }
  td { padding: 7px 10px; border: 1px solid #27272a; font-size: 12px; vertical-align: top; }
</style>

<h2>1. Purpose of Methodology</h2>
<p>
  This kinematics analyzer evaluates the deliverability of rotational <b>VMAT</b> plans generated by <b>Elekta Monaco TPS</b> on <b>Elekta linear accelerators</b> (Synergy, Infinity, Versa HD) equipped with the <b>Agility</b> 160-leaf MLC.
</p>
<p>
  Its primary objective is pre-treatment detection of subtle kinematic bottlenecks that trigger <code>DOSE RATE MON</code> interlocks or gantry servo stalling. For static fields (Static IMRT / 3D-CRT), the gantry remains stationary during radiation delivery, so rotational kinematics and MU/deg metrics are not applicable.
</p>

<h2>2. Linac Physics of DOSE RATE MON Interlock</h2>
<div class="card">
  <p>
    Dose rate on Elekta accelerators is controlled by modulating the Pulse Repetition Frequency (PRF) of the microwave generator (magnetron/klystron).
  </p>
  <ul>
    <li><b>Nominal PRF Range:</b> from ~25 Hz (60 MU/min minimum) up to ~400 Hz (600 MU/min nominal for standard 6 MV).</li>
    <li><b>Cut-off Threshold (60 MU/min):</b> when the delivery control system demands a dose rate below 60 MU/min, pulse triggering becomes irregular, resulting in insufficient ion collection in monitor chambers (IC1/IC2).</li>
    <li><b>Interlock Trigger:</b> Integrity/Desktop R&V detects the dose rate drop below safe tolerances and immediately raises the <code>DOSE RATE MON</code> interlock, terminating beam delivery.</li>
  </ul>
</div>

<h2>3. Key Parameters & Derivation of Default Values</h2>
<table>
  <tr>
    <th style="width: 28%;">Parameter</th>
    <th style="width: 18%;">Default Value</th>
    <th>Physical Basis & Source</th>
  </tr>
  <tr>
    <td><span class="badge-red">Dose rate threshold (fault)</span></td>
    <td><code>60 MU/min</code></td>
    <td>Factory linac specification and Monaco TPS machine limits (<i>Min dose rate cut-off</i>). Below 60 MU/min, flattened beam ionization currents become unstable.</td>
  </tr>
  <tr>
    <td><span class="badge-red">Dose density threshold (drop)</span></td>
    <td><code>0.165 MU/deg</code></td>
    <td>
      <b>Mathematical Derivation:</b><br>
      Max Elekta gantry rotation speed is <b>6.0 deg/s</b> (1.0 RPM).<br>
      At 60 MU/min, dose delivery rate is <b>1.0 MU/s</b> (60 MU / 60 s).<br>
      Minimum viable dose density per gantry degree:<br>
      <code>Density = 1.0 MU/s ÷ 6.0 deg/s ≈ 0.1667 MU/deg ≈ 0.165 MU/deg</code>.<br>
      Below 0.165 MU/deg, even at max speed (6 deg/s) the linac delivers too much dose, forcing output below 60 MU/min and causing a <code>DOSE RATE MON</code> fault.
    </td>
  </tr>
  <tr>
    <td><span class="badge-red">Critical modulation jump</span></td>
    <td><code>10.0×</code></td>
    <td>Derived from the dynamic response bandwidth of gantry and leaf drive servos. A density jump > 10× between adjacent 2–3° control points requires instantaneous 10-fold gantry deceleration, risking mechanical stalling.</td>
  </tr>
  <tr>
    <td><span class="badge-yellow">Yellow Zone: Dose Rate</span></td>
    <td><code>75 MU/min</code></td>
    <td>Preemptive safety margin (+25% above cut-off), warning against near-threshold magnetron operation.</td>
  </tr>
  <tr>
    <td><span class="badge-yellow">Yellow Zone: Dose Density</span></td>
    <td><code>0.200 MU/deg</code></td>
    <td>Corresponds to ~72 MU/min at maximum gantry speed.</td>
  </tr>
  <tr>
    <td><span class="badge-yellow">Yellow Zone: Jump Factor</span></td>
    <td><code>8.0×</code></td>
    <td>Warns of elevated modulation and prolonged delivery time.</td>
  </tr>
</table>

<h2>4. Monaco Algorithm Specifics & Third-Party TPS Notice</h2>
<p>
  This model is calibrated specifically for Monaco arc sequencing (2–3° control point spacing with variable gantry speed). Other systems (Varian Eclipse, RayStation) utilize different delivery physics (such as constant gantry speed with wide-range dose rate modulation). Hence, evaluation for non-Monaco plans is strictly informative.
</p>
"""


def show_plan_analysis_help(parent=None, is_ru: bool = True):
    """Convenience function to open the help dialog."""
    dlg = PlanAnalysisHelpDialog(parent, is_ru=is_ru)
    return dlg.exec()
