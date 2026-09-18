# -*- coding: utf-8 -*-
"""
Monaco Plan Kinematics & Deliverability Analyzer.

Standalone module for evaluating RTPLAN deliverability on Elekta linear accelerators,
detecting dose rate drop-outs, extreme MU/degree modulation, and gantry/MLC kinematic
conflicts that cause 'DOSE RATE MON' and related interlocks.
"""

import os
import sys
import math
from typing import Optional, Dict, Any, List

from PyQt6.QtCore import Qt, QPointF, QRectF, pyqtSignal
from PyQt6.QtGui import (
    QPainter, QPen, QBrush, QColor, QFont, QPolygonF,
    QPainterPath, QLinearGradient, QRadialGradient, QIcon
)
from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QTabWidget, QFrame, QSplitter, QScrollArea,
    QToolTip
)

import pydicom

from core.locale_utils import tr_ui
from core.logger import log_message


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


_plan_file_cache: Dict[str, Any] = {}


def find_rtplan_file(folder_path: str) -> Optional[str]:
    """Find the first valid RTPLAN file in a patient study folder with fast caching."""
    if not folder_path or not os.path.isdir(folder_path):
        return None

    try:
        mtime = os.path.getmtime(folder_path)
    except Exception:
        mtime = 0.0

    if folder_path in _plan_file_cache:
        cached_mtime, cached_result = _plan_file_cache[folder_path]
        if cached_mtime == mtime:
            return cached_result

    candidates = []
    others = []
    for root, dirs, files in os.walk(folder_path):
        for f in files:
            fp = os.path.join(root, f)
            fl = f.lower()
            if 'plan' in fl or 'rp' in fl or 'rtp' in fl or 'srt' in fl:
                candidates.append(fp)
            elif fl.endswith('.dcm') or '.' not in fl:
                others.append(fp)

    found_plan = None
    for fp in candidates + others:
        try:
            ds = pydicom.dcmread(fp, stop_before_pixels=True, force=True, specific_tags=['Modality'])
            if str(getattr(ds, 'Modality', '')).upper() == 'RTPLAN':
                found_plan = fp
                break
        except Exception:
            pass

    _plan_file_cache[folder_path] = (mtime, found_plan)
    return found_plan


class PlanKinematicsAnalyzer:
    """Parses and simulates machine kinematics for an RTPLAN file."""

    def __init__(self, plan_path: str):
        self.plan_path = plan_path
        self.ds = pydicom.dcmread(plan_path, force=True)
        self.plan_label = str(getattr(self.ds, 'RTPlanLabel', '') or getattr(self.ds, 'RTPlanName', 'Unnamed Plan'))
        self.plan_date = str(getattr(self.ds, 'RTPlanDate', ''))
        self.patient_name = str(getattr(self.ds, 'PatientName', 'Unknown'))
        self.patient_id = str(getattr(self.ds, 'PatientID', 'Unknown'))

        # Software and Manufacturer detection
        manufacturer = str(getattr(self.ds, 'Manufacturer', ''))
        software = str(getattr(self.ds, 'SoftwareVersions', ''))
        model = str(getattr(self.ds, 'ManufacturerModelName', ''))
        desc = str(getattr(self.ds, 'RTPlanDescription', ''))

        self.manufacturer = manufacturer
        self.software_version = software
        combined_text = f"{manufacturer} {software} {model} {desc}".upper()

        self.is_monaco = ('CMS' in combined_text) or ('MONACO' in combined_text)
        if self.is_monaco:
            ver_str = software if software else "5.x"
            self.tps_name = f"Elekta Monaco ({ver_str})"
        else:
            name_parts = [p for p in (manufacturer, software) if p]
            self.tps_name = " ".join(name_parts) if name_parts else "Unknown TPS"

        # Prescription & Fractions
        self.num_fractions = 1
        self.beam_mu_map: Dict[int, float] = {}
        frac_group = self.ds.FractionGroupSequence[0] if 'FractionGroupSequence' in self.ds and self.ds.FractionGroupSequence else None
        if frac_group:
            self.num_fractions = int(getattr(frac_group, 'NumberOfFractionsPlanned', 1) or 1)
            for ref_beam in getattr(frac_group, 'ReferencedBeamSequence', []):
                b_num = getattr(ref_beam, 'ReferencedBeamNumber', None)
                mu = getattr(ref_beam, 'BeamMeterset', None)
                if b_num is not None and mu is not None:
                    try:
                        self.beam_mu_map[int(b_num)] = float(mu)
                    except ValueError:
                        pass

        # Parse beams
        self.beams: List[Dict[str, Any]] = []
        for b in getattr(self.ds, 'BeamSequence', []):
            analyzed_beam = self._analyze_beam(b)
            if analyzed_beam:
                self.beams.append(analyzed_beam)

    def _analyze_beam(self, b) -> Optional[Dict[str, Any]]:
        b_num = int(getattr(b, 'BeamNumber', 0))
        b_name = str(getattr(b, 'BeamName', '') or f"Beam {b_num}")
        b_desc = str(getattr(b, 'BeamDescription', ''))
        b_type = str(getattr(b, 'BeamType', 'STATIC')).upper()
        rad_type = str(getattr(b, 'RadiationType', 'PHOTON'))
        machine_name = str(getattr(b, 'TreatmentMachineName', 'ELEKTA'))

        cps = getattr(b, 'ControlPointSequence', [])
        if not cps:
            return None

        cp0 = cps[0]
        energy = getattr(cp0, 'NominalBeamEnergy', '6.0')
        final_weight = float(getattr(b, 'FinalCumulativeMetersetWeight', 1.0) or 1.0)
        total_mu = self.beam_mu_map.get(b_num, final_weight)

        # Jaw Positions from CP0
        jaws_x = None
        jaws_y = None
        for bld in getattr(cp0, 'BeamLimitingDevicePositionSequence', []):
            dev_type = str(getattr(bld, 'RTBeamLimitingDeviceType', '')).upper()
            pos = [float(p) for p in getattr(bld, 'LeafJawPositions', [])]
            if 'X' in dev_type and 'MLC' not in dev_type:
                jaws_x = pos
            elif 'Y' in dev_type:
                jaws_y = pos

        # Calculate rotation span to check if it's VMAT
        # Calculate rotation span to check if it's VMAT
        total_gantry_travel = 0.0
        for i in range(len(cps) - 1):
            g1 = float(getattr(cps[i], 'GantryAngle', 0.0))
            g2 = float(getattr(cps[i+1], 'GantryAngle', 0.0))
            dg = abs(g2 - g1)
            if dg > 180:
                dg = 360 - dg
            total_gantry_travel += dg

        # A beam is an arc (VMAT) only if the gantry actually rotates
        g_rot_dir = str(getattr(cp0, 'GantryRotationDirection', 'NONE')).upper()
        is_vmat = (total_gantry_travel > 5.0) and (g_rot_dir in ('CW', 'CCW', 'CC') or total_gantry_travel >= 10.0)
        fixed_gantry_angle = float(getattr(cp0, 'GantryAngle', 0.0))
        beam_mode = 'VMAT' if is_vmat else ('Static IMRT' if len(cps) > 2 else 'Static 3D-CRT')

        # Control points evaluation
        intervals: List[Dict[str, Any]] = []
        critical_count = 0
        warning_count = 0

        # Elekta physical parameters
        max_gantry_speed = 6.0   # deg/s (1.0 RPM)
        safe_leaf_speed = 35.0   # mm/s continuous leaf speed for Agility
        max_dose_rate = 600.0    # MU/min nominal for flattened 6 MV
        min_stable_dose_rate = 45.0  # MU/min minimum stable PRF output

        mu_per_deg_list = []
        est_dose_rates = []

        prev_gantry_speed = 0.0

        for i in range(len(cps) - 1):
            cp_curr = cps[i]
            cp_next = cps[i+1]

            w_curr = float(cp_curr.CumulativeMetersetWeight) / final_weight * total_mu
            w_next = float(cp_next.CumulativeMetersetWeight) / final_weight * total_mu
            d_mu = max(0.0, w_next - w_curr)

            g_curr = float(cp_curr.GantryAngle)
            g_next = float(cp_next.GantryAngle)
            diff_g = abs(g_next - g_curr)
            if diff_g > 180:
                diff_g = 360 - diff_g

            # MU per degree (meaningful only for active rotational VMAT)
            if is_vmat and diff_g > 0.001:
                mu_per_deg = d_mu / diff_g
                if d_mu > 0.01:
                    mu_per_deg_list.append(mu_per_deg)
            else:
                mu_per_deg = 0.0

            # Max MLC displacement
            mlc_curr = []
            mlc_next = []
            for bld in getattr(cp_curr, 'BeamLimitingDevicePositionSequence', []):
                if 'MLC' in str(getattr(bld, 'RTBeamLimitingDeviceType', '')).upper():
                    mlc_curr = [float(p) for p in bld.LeafJawPositions]
                    break
            for bld in getattr(cp_next, 'BeamLimitingDevicePositionSequence', []):
                if 'MLC' in str(getattr(bld, 'RTBeamLimitingDeviceType', '')).upper():
                    mlc_next = [float(p) for p in bld.LeafJawPositions]
                    break

            max_leaf_disp = 0.0
            if mlc_curr and mlc_next and len(mlc_curr) == len(mlc_next):
                max_leaf_disp = max([abs(p2 - p1) for p1, p2 in zip(mlc_curr, mlc_next)] or [0.0])

            # Timing and kinematics
            if is_vmat and diff_g > 0.01:
                time_gantry = diff_g / max_gantry_speed
                time_leaf = max_leaf_disp / safe_leaf_speed
                step_time = max(time_gantry, time_leaf, 0.1)

                # Raw required dose rate
                raw_dr = (d_mu / step_time) * 60.0

                # Linac cannot exceed max_dose_rate
                if raw_dr > max_dose_rate:
                    step_time = (d_mu / max_dose_rate) * 60.0
                    est_dr = max_dose_rate
                    gantry_speed = diff_g / step_time if step_time > 0 else 0.0
                else:
                    est_dr = raw_dr
                    gantry_speed = diff_g / step_time
            else:
                # Static segment or beam-off turnaround
                time_leaf = max_leaf_disp / safe_leaf_speed
                step_time = max(d_mu / max_dose_rate * 60.0 if d_mu > 0 else 0.0, time_leaf, 0.1)
                est_dr = max_dose_rate if d_mu > 0 else 0.0
                gantry_speed = 0.0

            if d_mu > 0.01:
                est_dose_rates.append(est_dr)

            # Gantry acceleration demand (informative, smoothed by linac controller)
            gantry_accel = 0.0
            if i > 0 and step_time > 0:
                gantry_accel = abs(gantry_speed - prev_gantry_speed) / step_time
            prev_gantry_speed = gantry_speed

            # Risk classification
            reasons = []
            risk_level = 'OK'

            if is_vmat:
                # When beam is OFF (e.g. dual-arc turnaround or pause), no deliverability error
                if d_mu < 0.01:
                    pass
                else:
                    # 1. Low Dose Rate Drop-out (Causes DOSE RATE MON on Elekta below 45 MU/min)
                    if est_dr < min_stable_dose_rate or mu_per_deg < 0.14:
                        reasons.append(f"Мощность дозы ({est_dr:.0f} MU/мин, {mu_per_deg:.2f} MU/deg) ниже предела стабильности (< {min_stable_dose_rate:.0f} MU/мин)")
                        risk_level = 'CRITICAL'
                    elif est_dr < 55.0 or mu_per_deg < 0.20:
                        reasons.append(f"Пониженная плотность дозы ({mu_per_deg:.2f} MU/deg, ~{est_dr:.0f} MU/мин)")
                        if risk_level != 'CRITICAL':
                            risk_level = 'WARNING'

                    # 2. Extreme Modulation Shock (Severe jumps between adjacent active CPs)
                    if len(mu_per_deg_list) > 1:
                        prev_mpd = mu_per_deg_list[-2]
                        if prev_mpd > 0.01 and mu_per_deg > 0.01:
                            factor = max(mu_per_deg, prev_mpd) / min(mu_per_deg, prev_mpd)
                            if factor > 10.0 and (mu_per_deg > 14.0 or prev_mpd > 14.0):
                                reasons.append(f"Экстремальный перепад модуляции в {factor:.1f}× ({prev_mpd:.2f} → {mu_per_deg:.2f} MU/deg)")
                                risk_level = 'CRITICAL'
                            elif factor > 6.0 and (mu_per_deg > 10.0 or prev_mpd > 10.0):
                                reasons.append(f"Резкий перепад модуляции в {factor:.1f}× ({prev_mpd:.2f} → {mu_per_deg:.2f} MU/deg)")
                                if risk_level != 'CRITICAL':
                                    risk_level = 'WARNING'

                    # 3. High leaf movement speed during active beam
                    leaf_speed = max_leaf_disp / step_time if step_time > 0 else 0
                    if leaf_speed > 65.0:
                        reasons.append(f"Конфликт MLC: скорость лепестков {leaf_speed:.0f} мм/с превышает лимит Agility (65 мм/с)")
                        risk_level = 'CRITICAL'
                    elif leaf_speed > 48.0:
                        reasons.append(f"Высокая скорость движения лепестков ({leaf_speed:.0f} мм/с)")
                        if risk_level != 'CRITICAL':
                            risk_level = 'WARNING'

                    # 4. Heavy MU/deg modulation (gantry deceleration)
                    if mu_per_deg >= 15.0:
                        reasons.append(f"Высокая плотность дозы ({mu_per_deg:.1f} MU/deg, замедление гентри до {gantry_speed:.1f}°/с)")
                        if risk_level != 'CRITICAL':
                            risk_level = 'WARNING'

            if risk_level == 'CRITICAL':
                critical_count += 1
            elif risk_level == 'WARNING':
                warning_count += 1

            intervals.append({
                'index': i,
                'gantry_start': g_curr,
                'gantry_end': g_next,
                'delta_gantry': diff_g,
                'mu_start': w_curr,
                'mu_end': w_next,
                'delta_mu': d_mu,
                'mu_per_deg': mu_per_deg,
                'max_leaf_disp_mm': max_leaf_disp,
                'est_step_time_s': step_time,
                'est_dose_rate': est_dr,
                'gantry_speed': gantry_speed,
                'gantry_accel': gantry_accel,
                'risk_level': risk_level,
                'reasons': reasons
            })

        # Overall beam verdict
        if not is_vmat:
            verdict = 'STATIC'
        elif critical_count >= 1:
            verdict = 'CRITICAL'
        elif warning_count >= 4:
            verdict = 'WARNING'
        else:
            verdict = 'OK'

        return {
            'beam_number': b_num,
            'beam_name': b_name,
            'beam_description': b_desc,
            'beam_type': b_type,
            'radiation_type': rad_type,
            'machine_name': machine_name,
            'energy_mv': energy,
            'total_mu': total_mu,
            'num_control_points': len(cps),
            'total_gantry_travel': total_gantry_travel,
            'fixed_gantry_angle': fixed_gantry_angle,
            'beam_mode': beam_mode,
            'is_vmat': is_vmat,
            'jaws_x': jaws_x,
            'jaws_y': jaws_y,
            'intervals': intervals,
            'min_mu_per_deg': min(mu_per_deg_list) if (is_vmat and mu_per_deg_list) else 0.0,
            'max_mu_per_deg': max(mu_per_deg_list) if (is_vmat and mu_per_deg_list) else 0.0,
            'avg_mu_per_deg': (sum(mu_per_deg_list) / len(mu_per_deg_list)) if (is_vmat and mu_per_deg_list) else 0.0,
            'min_dose_rate': min(est_dose_rates) if est_dose_rates else 0.0,
            'max_dose_rate': max(est_dose_rates) if est_dose_rates else 0.0,
            'critical_count': critical_count,
            'warning_count': warning_count,
            'verdict': verdict
        }


class PolarArcWidget(QWidget):
    """
    Renders an interactive polar circular arc diagram conforming to IEC 61217
    with gantry rotation trajectory and color-coded risk levels.
    """
    intervalHovered = pyqtSignal(dict)
    intervalClicked = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(360, 360)
        self.setMouseTracking(True)
        self.beam_data: Optional[Dict[str, Any]] = None
        self.hovered_interval_idx: Optional[int] = None
        self.selected_interval_idx: Optional[int] = None

    def set_beam_data(self, beam_data: Optional[Dict[str, Any]]):
        self.beam_data = beam_data
        self.hovered_interval_idx = None
        self.selected_interval_idx = None
        self.update()

    def select_interval(self, index: int):
        self.selected_interval_idx = index
        self.update()

    def _gantry_to_math_angle(self, gantry_deg: float) -> float:
        """Convert IEC 61217 gantry angle (0=top, 90=right, CW) to Qt/Math angle (0=right, 90=top, CCW)."""
        return (90.0 - gantry_deg) % 360.0

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        center = QPointF(w / 2.0, h / 2.0)
        radius = min(w, h) / 2.0 - 24.0

        # Background fill
        painter.fillRect(self.rect(), QColor("#161616"))

        # Outer reference circle & ticks
        pen_grid = QPen(QColor("#2c2c2e"), 1, Qt.PenStyle.SolidLine)
        painter.setPen(pen_grid)
        painter.drawEllipse(center, radius, radius)
        painter.drawEllipse(center, radius - 45, radius - 45)

        # Draw angle spokes every 30 degrees
        font_ticks = QFont("Segoe UI", 8)
        painter.setFont(font_ticks)

        for deg in range(0, 360, 30):
            math_ang = math.radians(self._gantry_to_math_angle(deg))
            x_outer = center.x() + radius * math.cos(math_ang)
            y_outer = center.y() - radius * math.sin(math_ang)
            x_inner = center.x() + (radius - 8) * math.cos(math_ang)
            y_inner = center.y() - (radius - 8) * math.sin(math_ang)
            painter.setPen(QPen(QColor("#3a3a3c"), 1))
            painter.drawLine(QPointF(x_inner, y_inner), QPointF(x_outer, y_outer))

            # Labels (0, 90, 180, 270)
            if deg in (0, 90, 180, 270):
                lbl_r = radius + 14
                lx = center.x() + lbl_r * math.cos(math_ang)
                ly = center.y() - lbl_r * math.sin(math_ang)
                painter.setPen(QPen(QColor("#a1a1aa")))
                text = f"{deg}°"
                rect = QRectF(lx - 16, ly - 10, 32, 20)
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

        # Draw Arc Segments or Static Beam Vector
        if not self.beam_data or not self.beam_data.get('is_vmat'):
            if self.beam_data:
                g_angle = self.beam_data.get('fixed_gantry_angle', 0.0)
                math_ang = math.radians(self._gantry_to_math_angle(g_angle))

                # Draw incident radiation beam line from gantry perimeter to isocenter
                x_start = center.x() + radius * math.cos(math_ang)
                y_start = center.y() - radius * math.sin(math_ang)

                # Beam line
                painter.setPen(QPen(QColor("#38bdf8"), 2.5))
                painter.drawLine(QPointF(x_start, y_start), center)

                # Arrowhead pointing towards isocenter
                arrow_size = 10.0
                vx = center.x() - x_start
                vy = center.y() - y_start
                v_len = math.hypot(vx, vy)
                if v_len > 0.001:
                    ux = vx / v_len
                    uy = vy / v_len
                    nx = -uy
                    ny = ux
                    p_base = QPointF(center.x() - ux * arrow_size * 2, center.y() - uy * arrow_size * 2)
                    p1 = QPointF(p_base.x() + nx * arrow_size, p_base.y() + ny * arrow_size)
                    p2 = QPointF(p_base.x() - nx * arrow_size, p_base.y() - ny * arrow_size)
                    poly = QPolygonF([center, p1, p2])
                    painter.setBrush(QBrush(QColor("#38bdf8")))
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.drawPolygon(poly)

                # Source Head Marker on perimeter
                painter.setBrush(QBrush(QColor("#f59e0b")))
                painter.setPen(QPen(QColor("#ffffff"), 1.5))
                painter.drawEllipse(QPointF(x_start, y_start), 6, 6)

                # Center Isocenter Circle & Badge
                painter.setBrush(QBrush(QColor("#1f1f21")))
                painter.setPen(QPen(QColor("#38bdf8"), 1.5))
                center_r = radius - 55
                painter.drawEllipse(center, center_r, center_r)

                painter.setPen(QPen(QColor("#ffffff")))
                painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
                painter.drawText(QRectF(center.x() - 80, center.y() - 30, 160, 20), Qt.AlignmentFlag.AlignCenter, "STATIC IMRT")
                painter.setFont(QFont("Segoe UI", 9))
                painter.setPen(QPen(QColor("#38bdf8")))
                painter.drawText(QRectF(center.x() - 80, center.y() - 10, 160, 18), Qt.AlignmentFlag.AlignCenter, f"Гентри: {g_angle:.1f}°")
                painter.setFont(QFont("Segoe UI", 8))
                painter.setPen(QPen(QColor("#8e8e93")))
                cps_cnt = self.beam_data.get('num_control_points', 0)
                tot_mu = self.beam_data.get('total_mu', 0.0)
                painter.drawText(QRectF(center.x() - 80, center.y() + 10, 160, 18), Qt.AlignmentFlag.AlignCenter, f"{cps_cnt} CP | {tot_mu:.1f} MU")
            else:
                painter.setPen(QPen(QColor("#8e8e93")))
                painter.setFont(QFont("Segoe UI", 10))
                painter.drawText(QRectF(center.x() - 120, center.y() - 30, 240, 60), Qt.AlignmentFlag.AlignCenter, "Нет данных пучка")
            return

        intervals = self.beam_data.get('intervals', [])
        r_inner = radius - 40
        r_outer_base = radius - 4

        # Draw each interval as a colored ribbon segment
        for item in intervals:
            idx = item['index']
            g_start = item['gantry_start']
            g_end = item['gantry_end']
            risk = item['risk_level']
            mu_deg = item['mu_per_deg']

            # Height modulation based on MU/deg (clamped)
            extra_h = min(12.0, max(0.0, (mu_deg - 0.5) * 1.5))
            r_outer = r_outer_base + extra_h

            if risk == 'CRITICAL':
                color = QColor("#ff453a") # Neon red
            elif risk == 'WARNING':
                color = QColor("#ffd60a") # Neon yellow/amber
            else:
                color = QColor("#30d158") # Neon green

            is_selected = (idx == self.selected_interval_idx)
            is_hovered = (idx == self.hovered_interval_idx)

            if is_selected:
                color = QColor("#ffffff")
            elif is_hovered:
                color = color.lighter(130)

            # Construct path for arc polygon
            a1 = self._gantry_to_math_angle(g_start)
            a2 = self._gantry_to_math_angle(g_end)

            # Qt drawArc uses angles in 1/16th of a degree, counter-clockwise
            # Span angle calculation:
            diff = a2 - a1
            # Ensure shortest directional span
            if diff > 180:
                diff -= 360
            elif diff < -180:
                diff += 360

            path = QPainterPath()
            path.arcMoveTo(QRectF(center.x() - r_outer, center.y() - r_outer, 2 * r_outer, 2 * r_outer), a1)
            path.arcTo(QRectF(center.x() - r_outer, center.y() - r_outer, 2 * r_outer, 2 * r_outer), a1, diff)
            path.arcTo(QRectF(center.x() - r_inner, center.y() - r_inner, 2 * r_inner, 2 * r_inner), a1 + diff, -diff)
            path.closeSubpath()

            painter.setBrush(QBrush(color))
            painter.setPen(QPen(QColor("#161616"), 0.5))
            painter.drawPath(path)

        # Center orientation and info
        painter.setBrush(QBrush(QColor("#1f1f21")))
        painter.setPen(QPen(QColor("#3a3a3c"), 1.5))
        center_r = radius - 60
        painter.drawEllipse(center, center_r, center_r)

        # Center text: summary or hovered CP info
        painter.setPen(QPen(QColor("#ffffff")))
        if self.hovered_interval_idx is not None and 0 <= self.hovered_interval_idx < len(intervals):
            cp_info = intervals[self.hovered_interval_idx]
            painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            painter.drawText(QRectF(center.x() - 70, center.y() - 32, 140, 20), Qt.AlignmentFlag.AlignCenter,
                             f"CP {cp_info['index']:02d} → {cp_info['index']+1:02d}")
            painter.setFont(QFont("Segoe UI", 8))
            painter.setPen(QPen(QColor("#b0b0b5")))
            painter.drawText(QRectF(center.x() - 70, center.y() - 14, 140, 18), Qt.AlignmentFlag.AlignCenter,
                             f"{cp_info['gantry_start']:.1f}° → {cp_info['gantry_end']:.1f}°")
            painter.setPen(QPen(QColor("#38bdf8")))
            painter.drawText(QRectF(center.x() - 70, center.y() + 4, 140, 18), Qt.AlignmentFlag.AlignCenter,
                             f"{cp_info['mu_per_deg']:.2f} MU/deg")
            dr_color = QColor("#ff453a") if cp_info['est_dose_rate'] < 50 else QColor("#30d158")
            painter.setPen(QPen(dr_color))
            painter.drawText(QRectF(center.x() - 70, center.y() + 20, 140, 18), Qt.AlignmentFlag.AlignCenter,
                             f"~{cp_info['est_dose_rate']:.0f} MU/мин")
        else:
            painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
            painter.drawText(QRectF(center.x() - 70, center.y() - 20, 140, 20), Qt.AlignmentFlag.AlignCenter, "VMAT ARC")
            painter.setFont(QFont("Segoe UI", 8))
            painter.setPen(QPen(QColor("#8e8e93")))
            span = self.beam_data.get('total_gantry_travel', 0.0)
            mu = self.beam_data.get('total_mu', 0.0)
            painter.drawText(QRectF(center.x() - 70, center.y() + 2, 140, 18), Qt.AlignmentFlag.AlignCenter, f"{span:.0f}° | {mu:.1f} MU")

    def mouseMoveEvent(self, event):
        if not self.beam_data or not self.beam_data.get('is_vmat'):
            return

        w = self.width()
        h = self.height()
        center = QPointF(w / 2.0, h / 2.0)
        dx = event.position().x() - center.x()
        dy = -(event.position().y() - center.y()) # invert Y to cartesian
        dist = math.hypot(dx, dy)
        radius = min(w, h) / 2.0 - 24.0

        if radius - 45 <= dist <= radius + 15:
            # Calculate angle in math coords
            math_ang = math.degrees(math.atan2(dy, dx)) % 360.0
            # Convert to gantry angle (0=top, CW)
            gantry_ang = (90.0 - math_ang) % 360.0

            # Find matching interval
            matched_idx = None
            intervals = self.beam_data.get('intervals', [])
            for item in intervals:
                g1 = item['gantry_start']
                g2 = item['gantry_end']
                # Check if angle lies in [g1, g2] taking wrap into account
                diff = (g2 - g1) % 360.0
                rel = (gantry_ang - g1) % 360.0
                if rel <= diff:
                    matched_idx = item['index']
                    break

            if matched_idx != self.hovered_interval_idx:
                self.hovered_interval_idx = matched_idx
                self.update()
                if matched_idx is not None and matched_idx < len(intervals):
                    self.intervalHovered.emit(intervals[matched_idx])
        else:
            if self.hovered_interval_idx is not None:
                self.hovered_interval_idx = None
                self.update()

    def mousePressEvent(self, event):
        if self.hovered_interval_idx is not None:
            self.selected_interval_idx = self.hovered_interval_idx
            self.intervalClicked.emit(self.selected_interval_idx)
            self.update()


class ModulationTimelineWidget(QWidget):
    """Linear graph displaying MU/degree across gantry angle with safety bounds."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(140)
        self.beam_data: Optional[Dict[str, Any]] = None

    def set_beam_data(self, beam_data: Optional[Dict[str, Any]]):
        self.beam_data = beam_data
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        margin_l, margin_r = 50, 20
        margin_t, margin_b = 20, 30
        plot_w = w - margin_l - margin_r
        plot_h = h - margin_t - margin_b

        if not self.beam_data:
            painter.setPen(QPen(QColor("#8e8e93")))
            painter.setFont(QFont("Segoe UI", 9))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Нет данных пучка")
            return

        intervals = self.beam_data.get('intervals', [])
        if not intervals:
            return

        is_vmat = self.beam_data.get('is_vmat', False)

        if not is_vmat:
            # Render Segment Dose (ΔMU per CP) for static IMRT
            max_dmu = max([it['delta_mu'] for it in intervals] or [1.0])
            max_val = max(5.0, max_dmu * 1.25)

            def val_to_y_static(val):
                ratio = min(1.0, max(0.0, val / max_val))
                return margin_t + plot_h * (1.0 - ratio)

            # Draw grid & Y labels
            painter.setFont(QFont("Segoe UI", 8))
            painter.setPen(QPen(QColor("#2c2c2e"), 1, Qt.PenStyle.DashLine))

            grid_steps = [0.0, max_val * 0.25, max_val * 0.5, max_val * 0.75, max_val]
            for v in grid_steps:
                y = val_to_y_static(v)
                painter.drawLine(margin_l, int(y), w - margin_r, int(y))
                painter.setPen(QPen(QColor("#8e8e93")))
                painter.drawText(QRectF(0, y - 8, margin_l - 6, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{v:.1f}")
                painter.setPen(QPen(QColor("#2c2c2e"), 1, Qt.PenStyle.DashLine))

            # Y axis Title
            painter.setPen(QPen(QColor("#38bdf8")))
            painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            painter.drawText(margin_l, margin_t - 4, "Доза сегментов IMRT (ΔMU на контрольную точку)")

            # Plot bars for each segment
            n = len(intervals)
            step_px = plot_w / float(n)
            base_y = margin_t + plot_h

            for i, item in enumerate(intervals):
                dmu = item['delta_mu']
                x = margin_l + i * step_px
                y = val_to_y_static(dmu)
                painter.setPen(QPen(QColor("#38bdf8"), 1))
                painter.setBrush(QBrush(QColor(56, 189, 248, 120)))
                painter.drawRect(QRectF(x, y, max(1.0, step_px - 1), base_y - y))

            # X Axis labels
            painter.setPen(QPen(QColor("#8e8e93")))
            painter.setFont(QFont("Segoe UI", 8))
            painter.drawText(margin_l, h - 10, f"CP 00 (0.0 MU)")
            painter.drawText(w - margin_r - 90, h - 10, f"CP {n:02d} ({self.beam_data.get('total_mu', 0.0):.1f} MU)")
            return

        # Determine scale: max MU/deg capped at 25 for display
        max_val = max(18.0, min(25.0, self.beam_data.get('max_mu_per_deg', 15.0) * 1.1))

        # Y scale line helpers
        def val_to_y(val):
            ratio = min(1.0, max(0.0, val / max_val))
            return margin_t + plot_h * (1.0 - ratio)

        # Draw grid & Y labels
        painter.setFont(QFont("Segoe UI", 8))
        painter.setPen(QPen(QColor("#2c2c2e"), 1, Qt.PenStyle.DashLine))

        for v in [0.0, 5.0, 10.0, 15.0, 20.0]:
            if v <= max_val:
                y = val_to_y(v)
                painter.drawLine(margin_l, int(y), w - margin_r, int(y))
                painter.setPen(QPen(QColor("#8e8e93")))
                painter.drawText(QRectF(0, y - 8, margin_l - 6, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"{v:.0f}")
                painter.setPen(QPen(QColor("#2c2c2e"), 1, Qt.PenStyle.DashLine))

        # Plot Bars / Stepped Curve
        n = len(intervals)
        step_px = plot_w / float(n)
        base_y = margin_t + plot_h

        for i, item in enumerate(intervals):
            mpd = item['mu_per_deg']
            risk = item['risk_level']
            x = margin_l + i * step_px
            y = val_to_y(mpd)

            if risk == 'CRITICAL':
                color = QColor("#ff453a")
            elif risk == 'WARNING':
                color = QColor("#ffd60a")
            else:
                color = QColor("#30d158")

            painter.setPen(QPen(color, 1))
            painter.setBrush(QBrush(QColor(color.red(), color.green(), color.blue(), 100)))
            painter.drawRect(QRectF(x, y, max(1.0, step_px - 1), base_y - y))

        # Draw Safety Thresholds and descriptive labels on top of bars
        # 1. Red line for low dose rate danger (< 0.20 MU/deg)
        y_low = val_to_y(0.20)
        painter.setPen(QPen(QColor("#ff453a"), 1, Qt.PenStyle.DashLine))
        painter.drawLine(margin_l, int(y_low), w - margin_r, int(y_low))

        text_low = " 0.20 MU/deg — риск DOSE RATE MON (< 45 MU/мин) "
        painter.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
        fm = painter.fontMetrics()
        w_low = fm.horizontalAdvance(text_low)
        badge_low_x = w - margin_r - w_low - 12
        badge_low_rect = QRectF(badge_low_x, y_low - 15, w_low, 14)
        painter.setPen(QPen(QColor("#ff453a"), 1))
        painter.setBrush(QBrush(QColor("#1a1a1c")))
        painter.drawRoundedRect(badge_low_rect, 3, 3)
        painter.setPen(QPen(QColor("#ff8585")))
        painter.drawText(badge_low_rect, Qt.AlignmentFlag.AlignCenter, text_low)

        # 2. Yellow line for heavy modulation (>= 15.0 MU/deg)
        y_high = val_to_y(15.0)
        painter.setPen(QPen(QColor("#ffd60a"), 1, Qt.PenStyle.DashLine))
        painter.drawLine(margin_l, int(y_high), w - margin_r, int(y_high))

        text_high = " 15.0 MU/deg — замедление гентри (< 0.7°/с) "
        w_high = fm.horizontalAdvance(text_high)
        badge_high_x = w - margin_r - w_high - 12
        badge_high_rect = QRectF(badge_high_x, y_high - 15, w_high, 14)
        painter.setPen(QPen(QColor("#ffd60a"), 1))
        painter.setBrush(QBrush(QColor("#1a1a1c")))
        painter.drawRoundedRect(badge_high_rect, 3, 3)
        painter.setPen(QPen(QColor("#ffd60a")))
        painter.drawText(badge_high_rect, Qt.AlignmentFlag.AlignCenter, text_high)

        # X Axis labels
        painter.setPen(QPen(QColor("#8e8e93")))
        painter.setFont(QFont("Segoe UI", 8))
        painter.drawText(margin_l, h - 10, f"CP 00 ({intervals[0]['gantry_start']:.0f}°)")
        painter.drawText(w - margin_r - 80, h - 10, f"CP {n-1:02d} ({intervals[-1]['gantry_end']:.0f}°)")


class MonacoPlanAnalyzerDialog(QDialog):
    """Main window for Monaco plan deliverability inspection."""

    def __init__(self, parent=None, plan_path: str = "", plan_paths: list = None):
        super().__init__(parent)
        self.plan_path = plan_path
        # All available RTPLAN files for this patient
        self.plan_paths: list = plan_paths if plan_paths else ([plan_path] if plan_path else [])
        self.analyzer = PlanKinematicsAnalyzer(plan_path)

        if not self.analyzer.is_monaco:
            self.setWindowTitle(f"[НЕ MONACO — РЕЗУЛЬТАТ НЕ БУДЕТ СООТВЕТСТВОВАТЬ ДЕЙСТВИТЕЛЬНОСТИ] Анализ плана — {self.analyzer.patient_name} [{self.analyzer.patient_id}]")
        else:
            self.setWindowTitle(f"Анализ плана Monaco — {self.analyzer.patient_name} [{self.analyzer.patient_id}]")
        self.resize(1200, 800)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowMinimizeButtonHint
        )
        self.setWindowState(Qt.WindowState.WindowMaximized)
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

            msg_text = (
                f"<b style='font-size: 13px; color: #ffedd5;'>ВНИМАНИЕ: План рассчитан НЕ в системе Monaco!</b><br>"
                f"<span style='color: #fed7aa; font-size: 12px; line-height: 1.35;'>"
                f"Обнаруженная система: <b>{self.analyzer.tps_name}</b>.<br>"
                f"Кинематическая модель и расчёт рисков сбоя откалиброваны исключительно под алгоритмы Monaco и линейные ускорители Elekta. "
                f"Для сторонних систем (Varian Eclipse, RayStation и др.) <b>РЕЗУЛЬТАТ АНАЛИЗА НЕ БУДЕТ СООТВЕТСТВОВАТЬ ДЕЙСТВИТЕЛЬНОСТИ!</b>"
                f"</span>"
            )
            text_lbl = QLabel(msg_text, warn_banner)
            text_lbl.setWordWrap(True)
            warn_layout.addWidget(text_lbl, 1)
            layout.addWidget(warn_banner)

        # (patient info header will be placed in the right panel above the verdict card)
        tps_info = (
            f"<span style='color: #4ade80; font-weight: bold;'>{self.analyzer.tps_name}</span>"
            if self.analyzer.is_monaco else
            f"<span style='color: #fb923c; font-weight: bold;'>{self.analyzer.tps_name} [Не Monaco]</span>"
        )
        # Build patient label text (stored for later update via _on_plan_changed)
        self._tps_info_template = tps_info

        # Main Body Splitter: Left (Polar Arc) + Right (Verdict & Analysis)
        body_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        body_splitter.setStyleSheet("QSplitter::handle { background: #2c2c2e; width: 1px; }")

        # Left Panel: Polar Diagram + Legend
        left_panel = QWidget(body_splitter)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 8, 0)
        left_layout.setSpacing(8)

        # Plan + Beam selectors — single row, no border
        plan_ctrl_frame = QFrame(left_panel)
        plan_ctrl_frame.setStyleSheet("QFrame { background: transparent; border: none; }")
        ctrl_row = QHBoxLayout(plan_ctrl_frame)
        ctrl_row.setContentsMargins(0, 0, 0, 4)
        ctrl_row.setSpacing(8)

        lbl_plan_sel = QLabel("План:", plan_ctrl_frame)
        lbl_plan_sel.setStyleSheet("font-size: 11px; font-weight: 700; color: #8e8e93;")
        ctrl_row.addWidget(lbl_plan_sel)

        self.plan_combo = QComboBox(plan_ctrl_frame)
        for pp in self.plan_paths:
            try:
                ds_tmp = __import__('pydicom').dcmread(pp, stop_before_pixels=True, force=True,
                    specific_tags=['RTPlanLabel', 'RTPlanName'])
                lbl = str(getattr(ds_tmp, 'RTPlanLabel', '') or getattr(ds_tmp, 'RTPlanName', '') or os.path.basename(pp))
            except Exception:
                lbl = os.path.basename(pp)
            self.plan_combo.addItem(lbl, pp)
        cur_idx = self.plan_combo.findData(self.plan_path)
        if cur_idx >= 0:
            self.plan_combo.setCurrentIndex(cur_idx)
        self.plan_combo.currentIndexChanged.connect(self._on_plan_changed)
        ctrl_row.addWidget(self.plan_combo, 1)

        lbl_beam_sel = QLabel("Пучок:", plan_ctrl_frame)
        lbl_beam_sel.setStyleSheet("font-size: 11px; font-weight: 700; color: #8e8e93;")
        ctrl_row.addWidget(lbl_beam_sel)

        self.beam_combo = QComboBox(plan_ctrl_frame)
        self._populate_beam_combo()
        self.beam_combo.currentIndexChanged.connect(self._on_beam_changed)
        ctrl_row.addWidget(self.beam_combo, 2)

        left_layout.addWidget(plan_ctrl_frame)

        self.polar_widget = PolarArcWidget(left_panel)
        self.polar_widget.intervalHovered.connect(self._on_interval_hovered)
        self.polar_widget.intervalClicked.connect(self._on_interval_clicked)
        left_layout.addWidget(self.polar_widget, 1)

        # Legend — no box, just inline labels under polar widget
        legend_box = QFrame(left_panel)
        legend_box.setStyleSheet("QFrame { background: transparent; border: none; }")
        leg_l = QVBoxLayout(legend_box)
        leg_l.setContentsMargins(2, 4, 2, 0)
        leg_l.setSpacing(2)

        def make_leg_row(color_hex, text):
            row = QHBoxLayout()
            dot = QLabel("●")
            dot.setStyleSheet(f"color: {color_hex}; font-size: 12px;")
            lbl = QLabel(text)
            lbl.setStyleSheet("font-size: 10px; color: #6b7280;")
            row.addWidget(dot)
            row.addWidget(lbl, 1)
            return row

        leg_l.addLayout(make_leg_row("#30d158", "Безопасный отпуск (стабильная мощность и скорость)"))
        leg_l.addLayout(make_leg_row("#ffd60a", "Повышенная модуляция (замедление гентри / быстрый MLC)"))
        leg_l.addLayout(make_leg_row("#ff453a", "КРИТИЧЕСКИЙ РИСК СБОЯ 'DOSE RATE MON' (< 45 MU/мин или скачок > 10×)"))
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
        self.tabs.addTab(self.critical_table, "Критические точки")

        # Tab 2: Linear Graph
        self.graph_widget = ModulationTimelineWidget(self.tabs)
        self.tabs.addTab(self.graph_widget, "График модуляции (MU/deg)")

        # Tab 3: All Control Points Table
        self.all_table = QTableWidget(self.tabs)
        self._setup_table_headers(self.all_table)
        self.all_table.itemSelectionChanged.connect(self._on_table_selection_changed)
        self.tabs.addTab(self.all_table, "Все точки (Sequence)")

        right_layout.addWidget(self.tabs, 1)

        body_splitter.addWidget(right_panel)
        body_splitter.setStretchFactor(0, 4)
        body_splitter.setStretchFactor(1, 6)
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
                "CP", "Сектор гентри", "Δ Гентри", "Δ MU", "MU/deg", "Расч. мощность", "Диагностика риска"
            ])
        else:
            table.setHorizontalHeaderLabels([
                "CP", "Угол гентри", "Δ Гентри", "Δ MU", "Тип доставки", "Расч. мощность", "Статус сегмента"
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
            self.analyzer = PlanKinematicsAnalyzer(new_path)
        except Exception as e:
            log_message(None, f"Ошибка загрузки плана: {e}")
            return

        # Update header labels
        tps_info = (
            f"<span style='color: #4ade80; font-weight: bold;'>{self.analyzer.tps_name}</span>"
            if self.analyzer.is_monaco else
            f"<span style='color: #fb923c; font-weight: bold;'>{self.analyzer.tps_name} [Не Monaco]</span>"
        )
        self.lbl_patient.setText(
            f"<b style='font-size: 14px; color: #ffffff;'>{self.analyzer.patient_name}</b> "
            f"<span style='color: #8e8e93;'>({self.analyzer.patient_id})</span>"
        )
        self.lbl_tps.setText(tps_info)

        # Update window title
        if not self.analyzer.is_monaco:
            self.setWindowTitle(f"[НЕ MONACO] Анализ плана — {self.analyzer.patient_name} [{self.analyzer.patient_id}]")
        else:
            self.setWindowTitle(f"Анализ плана Monaco — {self.analyzer.patient_name} [{self.analyzer.patient_id}]")

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
            title = QLabel("🔴 ВЫСОКИЙ РИСК СБОЯ АППАРАТА (DOSE RATE MON)", self.verdict_card)
            title.setStyleSheet("font-size: 13px; font-weight: bold; color: #fca5a5;")
            desc = QLabel(
                f"В пучке обнаружено <b>{crit_count} критических секторов</b> с падением мощности дозы ниже порога стабильности Elekta (&lt; 45 MU/мин) "
                f"или резким торможением гентри. Аппарат с высокой вероятностью выдаст ошибку <code>DOSE RATE MON</code> при отпуске.",
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
            title = QLabel("🟡 ПОВЫШЕННАЯ СЛОЖНОСТЬ ПЛАНА (ТРЕБУЕТ ВНИМАНИЯ)", self.verdict_card)
            title.setStyleSheet("font-size: 13px; font-weight: bold; color: #fde68a;")
            desc = QLabel(
                f"Обнаружено {warn_count} секторов с сильным снижением скорости гентри или высокой модуляцией. "
                f"План может отпуститься медленнее расчетного времени.",
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
            title = QLabel(f"ℹ️ СТАТИЧЕСКИЙ ПУЧОК ({b['beam_mode']})", self.verdict_card)
            title.setStyleSheet("font-size: 13px; font-weight: bold; color: #7dd3fc;")
            desc = QLabel(
                f"Пучок доставляется на фиксированном угле гентри <b>{b['fixed_gantry_angle']:.1f}°</b> "
                f"({b['num_control_points']} контрольных точек / сегментов). "
                f"Вращение гентри отсутствует, поэтому ротационные риски VMAT и сбои мощности <code>DOSE RATE MON</code> при прохождении дуги <b>не применимы</b>.",
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
            title = QLabel("🟢 ПЛАН БЕЗОПАСЕН ДЛЯ ОТПУСКА", self.verdict_card)
            title.setStyleSheet("font-size: 13px; font-weight: bold; color: #86efac;")
            desc = QLabel("Все параметры мощности дозы, скорости вращения гентри и движения лепестков укладываются в штатные лимиты Elekta.", self.verdict_card)
            desc.setStyleSheet("font-size: 12px; color: #dcfce7;")
            self.verdict_layout.addWidget(title)
            self.verdict_layout.addWidget(desc)

        # Append non-Monaco warning in verdict card if plan is from external TPS
        if not self.analyzer.is_monaco:
            non_monaco_notice = QLabel(
                f"<div style='margin-top: 6px; padding: 6px 10px; background-color: rgba(234, 88, 12, 0.25); "
                f"border: 1px solid #ea580c; border-radius: 4px;'>"
                f"<b style='color: #ffedd5; font-size: 11px;'>⚠️ ВНИМАНИЕ: План рассчитан не в системе Monaco ({self.analyzer.tps_name})!</b><br>"
                f"<span style='color: #fed7aa; font-size: 11px;'>"
                f"Модель кинематики оптимизирована под Elekta/Monaco. Для сторонних систем (Varian/RaySearch и др.) "
                f"<b>результат анализа НЕ БУДЕТ СООТВЕТСТВОВАТЬ ДЕЙСТВИТЕЛЬНОСТИ</b>."
                f"</span></div>",
                self.verdict_card
            )
            non_monaco_notice.setWordWrap(True)
            self.verdict_layout.addWidget(non_monaco_notice)

        # Update Metrics Card
        self.lbl_metric_mu.setText(f"<span style='color: #8e8e93;'>Суммарно:</span><br><b style='font-size: 13px;'>{b['total_mu']:.1f} MU</b>")
        self.lbl_metric_dr.setText(f"<span style='color: #8e8e93;'>Мощность дозы:</span><br><b style='font-size: 13px;'>{b['min_dose_rate']:.0f} – {b['max_dose_rate']:.0f} MU/мин</b>")
        if b['is_vmat']:
            self.lbl_metric_mpd.setText(f"<span style='color: #8e8e93;'>Плотность дозы:</span><br><b style='font-size: 13px;'>{b['min_mu_per_deg']:.2f} – {b['max_mu_per_deg']:.2f} MU/deg</b>")
            self.lbl_metric_mpd.setToolTip(f"Диапазон плотности дозы: {b['min_mu_per_deg']:.2f} – {b['max_mu_per_deg']:.2f} MU/deg (среднее: {b['avg_mu_per_deg']:.2f} MU/deg)")
            self.tabs.setTabText(1, "График модуляции (MU/deg)")
        else:
            self.lbl_metric_mpd.setText(f"<span style='color: #8e8e93;'>Угол гентри:</span><br><b style='font-size: 13px;'>{b['fixed_gantry_angle']:.1f}° (статика)</b>")
            self.lbl_metric_mpd.setToolTip("")
            self.tabs.setTabText(1, "График сегментов (ΔMU)")



        # Setup Table Headers according to mode
        self._setup_table_headers(self.critical_table, is_vmat=b['is_vmat'])
        self._setup_table_headers(self.all_table, is_vmat=b['is_vmat'])

        # Fill Critical Points Table
        intervals = b.get('intervals', [])
        crit_items = [item for item in intervals if item['risk_level'] in ('CRITICAL', 'WARNING')]
        self._populate_table(self.critical_table, crit_items, is_vmat=b['is_vmat'])
        self.tabs.setTabText(0, f"Критические точки ({len(crit_items)})")

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
                mpd_str = "Статика"
                default_ok = "OK"

            dmu_str = f"{item['delta_mu']:.2f}"
            dr_str = f"{item['est_dose_rate']:.0f} MU/мин"
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
                self.polar_widget.select_interval(int(cp_idx))

    def _on_interval_hovered(self, cp_info: Dict[str, Any]):
        dr = cp_info['est_dose_rate']
        mpd = cp_info['mu_per_deg']
        is_vmat = (cp_info.get('delta_gantry', 0.0) > 0.001)
        if is_vmat:
            msg = f"CP {cp_info['index']:02d}: Гентри {cp_info['gantry_start']:.1f}° → {cp_info['gantry_end']:.1f}° | ΔMU: {cp_info['delta_mu']:.2f} | MU/deg: {mpd:.2f} | Мощность: {dr:.0f} MU/мин"
        else:
            msg = f"CP {cp_info['index']:02d}: Гентри {cp_info['gantry_start']:.1f}° (статика) | ΔMU: {cp_info['delta_mu']:.2f} | Мощность: {dr:.0f} MU/мин"
        if cp_info['reasons']:
            msg += f" — ⚠️ {'; '.join(cp_info['reasons'])}"
        self.status_lbl.setText(msg)

    def _on_interval_clicked(self, cp_idx: int):
        # Select row in active table if exists
        table = self.tabs.currentWidget()
        if isinstance(table, QTableWidget):
            for row in range(table.rowCount()):
                item = table.item(row, 0)
                if item and item.data(Qt.ItemDataRole.UserRole) == cp_idx:
                    table.selectRow(row)
                    table.scrollToItem(item)
                    break


def open_plan_analyzer(parent, folder_or_plan_path: str, patient_id: str = "", patient_name: str = ""):
    """Helper to find RTPLAN in folder (or direct plan path) and show the MonacoPlanAnalyzerDialog."""
    plan_file = None
    all_plan_files: list = []

    if folder_or_plan_path and os.path.isfile(folder_or_plan_path):
        plan_file = folder_or_plan_path
        # Also look for other plans in the same folder
        folder = os.path.dirname(folder_or_plan_path)
    else:
        folder = folder_or_plan_path

    # Collect all RTPLAN files from patient folder
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
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.warning(parent, "План не найден", f"В папке пациента {patient_name} ({patient_id}) не обнаружен файл RTPLAN.")
        return

    try:
        dlg = MonacoPlanAnalyzerDialog(parent, plan_file, plan_paths=all_plan_files)
        dlg.showMaximized()
        dlg.exec()
    except Exception as e:
        log_message(getattr(parent, 'output_field', None), f"Ошибка анализа плана Monaco: {e}")
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.critical(parent, "Ошибка анализа плана", f"Не удалось проанализировать файл плана:\n{e}")

