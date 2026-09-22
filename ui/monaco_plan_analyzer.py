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

from PyQt6.QtCore import Qt, QPointF, QRectF, pyqtSignal, QTimer
from PyQt6.QtGui import (
    QPainter, QPen, QBrush, QColor, QFont, QPolygonF,
    QPainterPath, QLinearGradient, QRadialGradient, QIcon
)
from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QTabWidget, QFrame, QSplitter, QScrollArea,
    QToolTip, QTextBrowser
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

    def __init__(self, plan_path: str, thresholds: Optional[Dict[str, float]] = None):
        self.plan_path = plan_path
        self.ds = pydicom.dcmread(plan_path, force=True)

        if thresholds is None:
            thresholds = {}
            try:
                from core.config_utils import get_config_path
                import json
                cp = get_config_path()
                if os.path.exists(cp):
                    with open(cp, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                        thresholds['min_dose_rate'] = float(cfg.get('plan_min_dose_rate', 60.0))
                        thresholds['min_mu_per_deg'] = float(cfg.get('plan_min_mu_per_deg', 0.165))
                        thresholds['max_modulation_factor'] = float(cfg.get('plan_max_modulation_factor', 10.0))
                        thresholds['warn_dose_rate'] = float(cfg.get('plan_warn_dose_rate', 75.0))
                        thresholds['warn_mu_per_deg'] = float(cfg.get('plan_warn_mu_per_deg', 0.200))
                        thresholds['warn_modulation_factor'] = float(cfg.get('plan_warn_modulation_factor', 8.0))
            except Exception:
                pass

        self.min_dose_rate = float(thresholds.get('min_dose_rate', 60.0))
        self.min_mu_per_deg = float(thresholds.get('min_mu_per_deg', 0.165))
        self.max_modulation_factor = float(thresholds.get('max_modulation_factor', 10.0))
        self.warn_dose_rate = float(thresholds.get('warn_dose_rate', 75.0))
        self.warn_mu_per_deg = float(thresholds.get('warn_mu_per_deg', 0.200))
        self.warn_modulation_factor = float(thresholds.get('warn_modulation_factor', 8.0))

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
        min_stable_dose_rate = self.min_dose_rate
        min_density_limit = self.min_mu_per_deg
        max_mod_jump = self.max_modulation_factor
        warn_dose_rate = self.warn_dose_rate
        warn_density_limit = self.warn_mu_per_deg
        warn_mod_jump = self.warn_modulation_factor

        mu_per_deg_list = []
        est_dose_rates = []

        prev_gantry_speed = 0.0
        pass_idx = 0
        prev_dir = None

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

            # Gantry direction and multi-pass (reversal) detection
            rot_dir = getattr(cp_curr, 'GantryRotationDirection', '')
            if not rot_dir or rot_dir == 'NONE':
                d_raw = (g_next - g_curr) % 360.0
                if 0.001 < d_raw <= 180.0:
                    rot_dir = 'CW'
                elif d_raw > 180.0 and (360.0 - d_raw) > 0.001:
                    rot_dir = 'CC'
                else:
                    rot_dir = prev_dir or 'CW'

            if prev_dir is not None and rot_dir in ('CW', 'CC') and prev_dir in ('CW', 'CC') and rot_dir != prev_dir:
                pass_idx += 1
            prev_dir = rot_dir

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
                    # 1. Low Dose Rate Drop-out (Causes DOSE RATE MON on Elekta strictly below min_stable_dose_rate)
                    if round(est_dr) < min_stable_dose_rate or mu_per_deg < (min_density_limit - 0.005):
                        reasons.append(f"Мощность дозы ({est_dr:.1f} MU/мин, {mu_per_deg:.2f} MU/deg) ниже порога стабильности (< {min_stable_dose_rate:.0f} MU/мин, < {min_density_limit:.3f} MU/deg)")
                        risk_level = 'CRITICAL'
                    elif est_dr < warn_dose_rate or mu_per_deg < warn_density_limit:
                        reasons.append(f"Пониженная плотность/мощность дозы ({mu_per_deg:.2f} MU/deg, ~{est_dr:.0f} MU/мин)")
                        if risk_level != 'CRITICAL':
                            risk_level = 'WARNING'

                    # 2. Extreme Modulation Shock (Severe jumps between adjacent active CPs)
                    if len(mu_per_deg_list) > 1:
                        prev_mpd = mu_per_deg_list[-2]
                        if prev_mpd > 0.01 and mu_per_deg > 0.01:
                            factor = max(mu_per_deg, prev_mpd) / min(mu_per_deg, prev_mpd)
                            if factor >= max_mod_jump:
                                reasons.append(f"Экстремальный перепад плотности дозы в {factor:.1f}× ({prev_mpd:.2f} → {mu_per_deg:.2f} MU/deg)")
                                risk_level = 'CRITICAL'
                            elif factor >= warn_mod_jump:
                                reasons.append(f"Резкий перепад плотности дозы в {factor:.1f}× ({prev_mpd:.2f} → {mu_per_deg:.2f} MU/deg)")
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
                'reasons': reasons,
                'pass_idx': pass_idx,
                'direction': rot_dir
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

        # Collect summary per pass
        passes_info = []
        if is_vmat and intervals:
            for p_i in range(pass_idx + 1):
                p_items = [it for it in intervals if it.get('pass_idx') == p_i]
                if p_items:
                    passes_info.append({
                        'pass_idx': p_i,
                        'start_angle': p_items[0]['gantry_start'],
                        'end_angle': p_items[-1]['gantry_end'],
                        'direction': p_items[0].get('direction', 'CW')
                    })

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
            'num_passes': pass_idx + 1 if is_vmat else 1,
            'passes_info': passes_info,
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
            'verdict': verdict,
            'thresholds': {
                'min_dose_rate': self.min_dose_rate,
                'min_mu_per_deg': self.min_mu_per_deg,
                'max_modulation_factor': self.max_modulation_factor,
                'warn_dose_rate': self.warn_dose_rate,
                'warn_mu_per_deg': self.warn_mu_per_deg,
                'warn_modulation_factor': self.warn_modulation_factor,
            }
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
        num_passes = self.beam_data.get('num_passes', 1)
        r_inner_single = radius - 55.0
        min_thickness = 7.0
        max_extra = 57.0

        # Draw each interval as a colored ribbon segment
        selected_to_draw_on_top = None
        for item in intervals:
            idx = item['index']
            g_start = item['gantry_start']
            g_end = item['gantry_end']
            risk = item['risk_level']
            mu_deg = item['mu_per_deg']
            p_idx = item.get('pass_idx', 0)
            rot_dir = item.get('direction', 'CW')

            # Dynamic height scaling based on MU/deg (contrast power curve)
            norm = min(1.0, max(0.0, mu_deg / 16.0))

            if num_passes > 1:
                # Multi-track concentric orbits (expanded dynamic range: 3.5px to 36.5px)
                if p_idx == 0:
                    # Inner track (Pass 1)
                    r_inner = radius - 76.0
                    thick = 3.5 + 33.0 * (norm ** 0.58)
                else:
                    # Outer track (Pass 2)
                    r_inner = radius - 37.0
                    thick = 3.5 + 33.0 * (norm ** 0.58)
            else:
                # Single-track mode
                r_inner = r_inner_single
                thick = min_thickness + max_extra * (norm ** 0.65)

            is_selected = (idx == self.selected_interval_idx)
            is_hovered = (idx == self.hovered_interval_idx)

            if is_selected:
                thick += 4.0 if num_passes > 1 else 6.0
            r_outer = r_inner + thick

            if risk == 'CRITICAL':
                color = QColor("#ff453a") # Neon red
            elif risk == 'WARNING':
                color = QColor("#ffd60a") # Neon yellow/amber
            else:
                color = QColor("#30d158") # Neon green

            if is_selected:
                color = color.lighter(115)
            elif is_hovered:
                color = color.lighter(130)

            # Construct path for arc polygon
            a1 = self._gantry_to_math_angle(g_start)
            a2 = self._gantry_to_math_angle(g_end)

            diff = a2 - a1
            if rot_dir == 'CW':
                while diff > 0: diff -= 360
                while diff < -360: diff += 360
            else:
                while diff < 0: diff += 360
                while diff > 360: diff -= 360

            path = QPainterPath()
            path.arcMoveTo(QRectF(center.x() - r_outer, center.y() - r_outer, 2 * r_outer, 2 * r_outer), a1)
            path.arcTo(QRectF(center.x() - r_outer, center.y() - r_outer, 2 * r_outer, 2 * r_outer), a1, diff)
            path.arcTo(QRectF(center.x() - r_inner, center.y() - r_inner, 2 * r_inner, 2 * r_inner), a1 + diff, -diff)
            path.closeSubpath()

            painter.setBrush(QBrush(color))
            if is_selected:
                painter.setPen(QPen(QColor("#ffffff"), 2.0))
                selected_to_draw_on_top = (path, color)
            else:
                painter.setPen(QPen(QColor("#161616"), 0.5))
            painter.drawPath(path)

        # Track separator dashed line if multi-track
        if num_passes > 1:
            sep_r = radius - 38.5
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#3a3a3c"), 1, Qt.PenStyle.DashLine))
            painter.drawEllipse(center, sep_r, sep_r)

        # Draw selected sector on top so white contour is crisp and unclipped
        if selected_to_draw_on_top:
            sel_path, sel_color = selected_to_draw_on_top
            painter.setBrush(QBrush(sel_color))
            painter.setPen(QPen(QColor("#ffffff"), 2.0))
            painter.drawPath(sel_path)

        # Center orientation and info
        painter.setBrush(QBrush(QColor("#1f1f21")))
        painter.setPen(QPen(QColor("#3a3a3c"), 1.5))
        center_r = (radius - 78.0) if num_passes > 1 else (radius - 68.0)
        painter.drawEllipse(center, center_r, center_r)

        active_idx = self.hovered_interval_idx if self.hovered_interval_idx is not None else self.selected_interval_idx

        if active_idx is not None and 0 <= active_idx < len(intervals):
            cp = intervals[active_idx]
            risk = cp['risk_level']
            mpd = cp['mu_per_deg']
            dr = cp['est_dose_rate']

            # Dose density jump (delta)
            if active_idx > 0:
                prev_mpd = intervals[active_idx - 1]['mu_per_deg']
                delta_mpd = mpd - prev_mpd
                factor = max(mpd, prev_mpd) / max(0.01, min(mpd, prev_mpd)) if min(mpd, prev_mpd) > 0.01 else 1.0
            else:
                delta_mpd = 0.0
                factor = 1.0

            # Status badge
            if risk == 'CRITICAL':
                badge_bg = QColor(239, 68, 68, 40)
                badge_border = QColor('#ef4444')
                badge_text = 'КРИТИЧЕСКИЙ РИСК'
            elif risk == 'WARNING':
                badge_bg = QColor(245, 158, 11, 40)
                badge_border = QColor('#f59e0b')
                badge_text = 'ПОВЫШЕННАЯ СЛОЖНОСТЬ'
            else:
                badge_bg = QColor(34, 197, 94, 40)
                badge_border = QColor('#22c55e')
                badge_text = 'ПАРАМЕТРЫ В НОРМЕ'

            # Draw Badge with dynamic width and crisp vector dot
            badge_font = QFont('Segoe UI', 8, QFont.Weight.Bold)
            painter.setFont(badge_font)
            fm = painter.fontMetrics()
            text_w = fm.horizontalAdvance(badge_text)
            content_w = 7.0 + 6.0 + text_w # 7px dot + 6px gap + text
            badge_w = content_w + 18.0
            badge_h = 20.0
            badge_rect = QRectF(center.x() - badge_w / 2.0, center.y() - 66, badge_w, badge_h)

            painter.setBrush(QBrush(badge_bg))
            painter.setPen(QPen(badge_border, 1.0))
            painter.drawRoundedRect(badge_rect, 4, 4)

            # Vector status dot inside badge
            start_x = center.x() - content_w / 2.0
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(badge_border))
            painter.drawEllipse(QPointF(start_x + 3.5, badge_rect.center().y()), 3.5, 3.5)

            # Badge text
            painter.setFont(badge_font)
            painter.setPen(QPen(badge_border))
            text_rect = QRectF(start_x + 13.0, badge_rect.top(), text_w + 4.0, badge_h)
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, badge_text)

            # CP Sector Title with Pass info if multi-track
            p_idx = cp.get('pass_idx', 0)
            rot_dir = cp.get('direction', 'CW')
            dir_sym = '↻ CW' if rot_dir == 'CW' else '↺ CCW'
            title_txt = f"CP {cp['index']:02d} → {cp['index']+1:02d} (П{p_idx+1}: {dir_sym})" if num_passes > 1 else f"Сектор CP {cp['index']:02d} → {cp['index']+1:02d}"

            painter.setFont(QFont('Segoe UI', 9, QFont.Weight.Bold))
            painter.setPen(QPen(QColor('#ffffff')))
            painter.drawText(QRectF(center.x() - 100, center.y() - 42, 200, 18), Qt.AlignmentFlag.AlignCenter, title_txt)

            # Gantry angle
            painter.setFont(QFont('Segoe UI', 8))
            painter.setPen(QPen(QColor('#a1a1aa')))
            painter.drawText(QRectF(center.x() - 90, center.y() - 24, 180, 16), Qt.AlignmentFlag.AlignCenter,
                             f"Гентри: {cp['gantry_start']:.1f}° → {cp['gantry_end']:.1f}° (Δ {cp['delta_gantry']:.1f}°)")

            # Parameter rows with graphic status dots
            def draw_param_row(y, label, val_str, status_color, status_text):
                row_rect = QRectF(center.x() - 85, y, 170, 15)
                # Indicator Dot
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(status_color))
                painter.drawEllipse(QPointF(row_rect.left() + 6, y + 7.5), 3.5, 3.5)
                # Label
                painter.setFont(QFont('Segoe UI', 8))
                painter.setPen(QPen(QColor('#d4d4d8')))
                painter.drawText(QRectF(row_rect.left() + 16, y, 90, 15), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)
                # Value
                painter.setFont(QFont('Segoe UI', 8, QFont.Weight.DemiBold))
                painter.setPen(QPen(status_color))
                painter.drawText(QRectF(row_rect.right() - 75, y, 75, 15), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, val_str)

            th = self.beam_data.get('thresholds', {}) if self.beam_data else {}
            min_dr = float(th.get('min_dose_rate', 60.0))
            min_mpd = float(th.get('min_mu_per_deg', 0.165))
            max_jump = float(th.get('max_modulation_factor', 10.0))
            warn_dr = float(th.get('warn_dose_rate', 75.0))
            warn_mpd = float(th.get('warn_mu_per_deg', 0.200))
            warn_jump = float(th.get('warn_modulation_factor', 8.0))

            # 1. Dose rate status
            if round(dr) < min_dr:
                dr_col, dr_st = QColor('#ef4444'), 'Сбой'
                dr_val_str = f"{dr:.1f} MU/мин"
            elif dr < warn_dr:
                dr_col, dr_st = QColor('#f59e0b'), 'Низкая'
                dr_val_str = f"{dr:.0f} MU/мин"
            else:
                dr_col, dr_st = QColor('#22c55e'), 'Норма'
                dr_val_str = f"{dr:.0f} MU/мин"
            draw_param_row(center.y() - 3, 'Мощность:', dr_val_str, dr_col, dr_st)

            # 2. Dose density status
            if mpd < min_mpd:
                mpd_col, mpd_st = QColor('#ef4444'), 'Провал'
            elif mpd > 15.0 or mpd < warn_mpd:
                mpd_col, mpd_st = QColor('#f59e0b'), 'Перегруз' if mpd > 15 else 'Низкая'
            else:
                mpd_col, mpd_st = QColor('#22c55e'), 'Норма'
            draw_param_row(center.y() + 15, 'Плотность:', f"{mpd:.2f} MU/deg", mpd_col, mpd_st)

            # 3. Delta jump status
            if active_idx > 0:
                if factor >= max_jump:
                    jump_col, jump_st = QColor('#ef4444'), 'Шок'
                elif factor >= warn_jump:
                    jump_col, jump_st = QColor('#f59e0b'), 'Перепад'
                else:
                    jump_col, jump_st = QColor('#22c55e'), 'Норма'
                d_sign = '+' if delta_mpd >= 0 else ''
                draw_param_row(center.y() + 33, 'Перепад:', f"{factor:.1f}× ({d_sign}{delta_mpd:.2f})", jump_col, jump_st)
            else:
                draw_param_row(center.y() + 33, 'Перепад:', "— (старт)", QColor('#9ca3af'), 'Старт')

        else:
            # Default center summary
            painter.setFont(QFont('Segoe UI', 10, QFont.Weight.Bold))
            painter.setPen(QPen(QColor('#ffffff')))
            painter.drawText(QRectF(center.x() - 70, center.y() - 20, 140, 20), Qt.AlignmentFlag.AlignCenter, "VMAT ARC")
            painter.setFont(QFont('Segoe UI', 8))
            painter.setPen(QPen(QColor('#8e8e93')))
            span = self.beam_data.get('total_gantry_travel', 0.0)
            mu = self.beam_data.get('total_mu', 0.0)
            painter.drawText(QRectF(center.x() - 70, center.y() + 2, 140, 18), Qt.AlignmentFlag.AlignCenter, f"{span:.0f}° | {mu:.1f} MU")
            painter.setFont(QFont('Segoe UI', 7))
            painter.setPen(QPen(QColor('#52525b')))
            painter.drawText(QRectF(center.x() - 80, center.y() + 22, 160, 16), Qt.AlignmentFlag.AlignCenter, "Наведите или выберите сектор")

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

        num_passes = self.beam_data.get('num_passes', 1)
        min_r = (radius - 78.0) if num_passes > 1 else (radius - 68.0)

        if min_r <= dist <= radius + 25:
            # Calculate angle in math coords
            math_ang = math.degrees(math.atan2(dy, dx)) % 360.0
            # Convert to gantry angle (0=top, CW)
            gantry_ang = (90.0 - math_ang) % 360.0

            target_pass = 0
            if num_passes > 1:
                r_split = radius - 38.0
                target_pass = 0 if dist < r_split else 1

            # Find matching interval
            matched_idx = None
            intervals = self.beam_data.get('intervals', [])
            for item in intervals:
                if num_passes > 1 and item.get('pass_idx', 0) != target_pass:
                    continue
                g1 = item['gantry_start']
                g2 = item['gantry_end']
                rot = item.get('direction', 'CW')
                if rot == 'CW':
                    diff = (g2 - g1) % 360.0
                    rel = (gantry_ang - g1) % 360.0
                    if rel <= diff:
                        matched_idx = item['index']
                        break
                else: # CC
                    diff = (g1 - g2) % 360.0
                    rel = (g1 - gantry_ang) % 360.0
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

    def leaveEvent(self, event):
        if self.hovered_interval_idx is not None:
            self.hovered_interval_idx = None
            self.update()
        super().leaveEvent(event)

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

        th = self.beam_data.get('thresholds', {}) if self.beam_data else {}
        min_dr = float(th.get('min_dose_rate', 60.0))
        min_mpd = float(th.get('min_mu_per_deg', 0.165))

        # Draw Safety Thresholds and descriptive labels on top of bars
        # 1. Red line for low dose rate danger (< min_mpd MU/deg)
        y_low = val_to_y(min_mpd)
        painter.setPen(QPen(QColor("#ff453a"), 1, Qt.PenStyle.DashLine))
        painter.drawLine(margin_l, int(y_low), w - margin_r, int(y_low))

        text_low = f" {min_mpd:.3f} MU/deg — порог DOSE RATE MON (< {min_dr:.0f} MU/мин) "
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
        header_frame.setStyleSheet("background-color: #1a1a1e; border: 1px solid #27272a; border-radius: 8px;")
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
            "Физическая природа интерлока DOSE RATE MON, ограничения коллиматора Agility и обоснование порогов по умолчанию" if is_ru
            else "Linac DOSE RATE MON physics, Agility MLC constraints, and derivation of default thresholds"
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
  Анализатор кинематики оценивает реализуемость (deliverability) планов VMAT и IMRT, созданных в СППР <b>Elekta Monaco</b>, на линейных ускорителях <b>Elekta</b> (Synergy, Infinity, Versa HD) с многолепестковым коллиматором <b>Agility</b>.
</p>
<p>
  Методика позволяет ещё на этапе дозиметрического планирования выявить потенциально аварийные сектора, провоцирующие аппаратный останов пучка интерлоком <code>DOSE RATE MON</code> или сбой слежения сервоприводов гентри.
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
      При мощности 60 MU/мин расход дозы составляет <b>1.0 MU/с</b> (60 MU / 60 c).<br>
      Минимально допустимая плотность дозы на градус дуги:<br>
      <code>Плотность = 1.0 MU/с ÷ 6.0°/с ≈ 0.1667 MU/deg ≈ 0.165 MU/deg</code>.<br>
      Если плотность дозы ниже 0.165 MU/deg, гентри даже на предельной скорости вращения (6.0°/с) не успевает "растянуть" дозу, что вынуждает linac опускать мощность ниже 60 MU/мин и вызывает <code>DOSE RATE MON</code>.
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

<h2>4. Ограничения многолепесткового коллиматора Agility (MLC)</h2>
<ul>
  <li><b>Непрерывная скорость движения лепестков:</b> до <code>35 мм/с</code> (штатный безызносный режим для 160 лепестков шириной 5 мм).</li>
  <li><b>Максимальная пиковая скорость:</b> до <code>65 мм/с</code>.</li>
  <li><b>Алгоритм Monaco:</b> если лепесткам необходимо пройти большое расстояние за сектор, Monaco принудительно замедляет гентри (до 1.0–2.0°/с). Плотность дозы (MU/deg) при этом возрастает.</li>
</ul>

<h2>5. Особенности алгоритмов Monaco и сторонние СППР</h2>
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
  This kinematics analyzer evaluates the deliverability of VMAT and IMRT plans generated by <b>Elekta Monaco TPS</b> on <b>Elekta linear accelerators</b> (Synergy, Infinity, Versa HD) equipped with the <b>Agility</b> 160-leaf MLC.
</p>
<p>
  Its primary objective is pre-treatment detection of subtle kinematic bottlenecks that trigger <code>DOSE RATE MON</code> interlocks or gantry servo stalling.
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

<h2>4. Elekta Agility MLC Kinematics</h2>
<ul>
  <li><b>Continuous Leaf Speed:</b> up to <code>35 mm/s</code> (standard continuous operation for 160 leaves of 5 mm resolution).</li>
  <li><b>Maximum Peak Speed:</b> up to <code>65 mm/s</code>.</li>
  <li><b>Monaco Behavior:</b> when leaves must travel large distances within a sector, Monaco automatically decelerates the gantry, raising the local MU/deg.</li>
</ul>

<h2>5. Monaco Algorithm Specifics & Third-Party TPS Notice</h2>
<p>
  This model is calibrated specifically for Monaco arc sequencing (2–3° control point spacing with variable gantry speed). Other systems (Varian Eclipse, RayStation) utilize different delivery physics (such as constant gantry speed with wide-range dose rate modulation). Hence, evaluation for non-Monaco plans is strictly informative.
</p>
"""


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
        # All available RTPLAN files for this patient
        self.plan_paths: list = plan_paths if plan_paths else ([plan_path] if plan_path else [])
        self.analyzer = PlanKinematicsAnalyzer(plan_path, thresholds=self.thresholds)

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
            QTimer.singleShot(0, self.showMaximized)

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
        self.btn_methodology_help.setFixedSize(24, 24)
        self.btn_methodology_help.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_methodology_help.setToolTip(
            "Методика кинематического анализа и обоснование физических порогов" if self.is_ru
            else "Kinematics Analysis Methodology & Physical Limits"
        )
        self.btn_methodology_help.setStyleSheet(
            "QPushButton { "
            "  background-color: #27272a; color: #a1a1aa; border: 1px solid #3f3f46; "
            "  border-radius: 12px; font-size: 13px; font-weight: bold; font-family: 'Segoe UI', sans-serif; "
            "  padding: 0px; margin: 0px; "
            "} "
            "QPushButton:hover { "
            "  background-color: #38bdf8; color: #09090b; border: 1px solid #38bdf8; "
            "} "
            "QPushButton:pressed { "
            "  background-color: #0284c7; color: #ffffff; border: 1px solid #0284c7; "
            "}"
        )
        self.btn_methodology_help.clicked.connect(self._open_methodology_help)
        leg_outer_l.addWidget(self.btn_methodology_help, 0, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignRight)

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
            self.analyzer = PlanKinematicsAnalyzer(new_path, thresholds=self.thresholds)
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

        passes_info = b.get('passes_info', [])
        if b.get('num_passes', 1) > 1 and len(passes_info) >= 2:
            p1 = passes_info[0]
            p2 = passes_info[1]
            d1_sym = '↻ CW' if p1.get('direction', 'CW') == 'CW' else '↺ CCW'
            d2_sym = '↻ CW' if p2.get('direction', 'CW') == 'CW' else '↺ CCW'
            txt = (f"Двойная дуга: Внутренний трек — Проход 1 ({p1['start_angle']:.1f}° → {p1['end_angle']:.1f}°, {d1_sym}) | "
                   f"Внешний трек — Проход 2 ({p2['start_angle']:.1f}° → {p2['end_angle']:.1f}°, {d2_sym})")
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
            title = QLabel("🔴 ВЫСОКИЙ РИСК СБОЯ АППАРАТА (DOSE RATE MON)", self.verdict_card)
            title.setStyleSheet("font-size: 13px; font-weight: bold; color: #fca5a5;")
            desc = QLabel(
                f"В пучке обнаружено <b>{crit_count} критических секторов</b> с падением мощности/плотности дозы ниже порога Elekta (&lt; {min_dr:.0f} MU/мин, &lt; {min_mpd:.3f} MU/deg) "
                f"или экстремальным перепадом модуляции (&gt; {max_jump:.0f}×). Аппарат с высокой вероятностью выдаст ошибку <code>DOSE RATE MON</code> при отпуске.",
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
            desc.setWordWrap(True)
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
            dr_val = item['est_dose_rate']
            if round(dr_val) < 60 and item['delta_mu'] > 0.01:
                dr_str = f"{dr_val:.1f} MU/мин"
            else:
                dr_str = f"{dr_val:.0f} MU/мин"
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
        pass_tag = f" [Проход {p_idx+1}: {dir_sym}]" if num_passes > 1 else ""

        dr_fmt = f"{dr:.1f}" if round(dr) < 60 and cp_info.get('delta_mu', 0.0) > 0.01 else f"{dr:.0f}"
        if is_vmat:
            msg = f"CP {cp_info['index']:02d}{pass_tag}: Гентри {cp_info['gantry_start']:.1f}° → {cp_info['gantry_end']:.1f}° | ΔMU: {cp_info['delta_mu']:.2f} | MU/deg: {mpd:.2f} | Мощность: {dr_fmt} MU/мин"
        else:
            msg = f"CP {cp_info['index']:02d}: Гентри {cp_info['gantry_start']:.1f}° (статика) | ΔMU: {cp_info['delta_mu']:.2f} | Мощность: {dr_fmt} MU/мин"
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

    def _open_methodology_help(self):
        dlg = PlanAnalysisHelpDialog(self, is_ru=self.is_ru)
        dlg.exec()


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
        dlg = MonacoPlanAnalyzerDialog(parent, plan_file, plan_paths=all_plan_files, config=getattr(parent, 'config', None))
        dlg.showMaximized()
        dlg.exec()
    except Exception as e:
        log_message(getattr(parent, 'output_field', None), f"Ошибка анализа плана Monaco: {e}")
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.critical(parent, "Ошибка анализа плана", f"Не удалось проанализировать файл плана:\n{e}")

