# -*- coding: utf-8 -*-
"""
Polar Circular Arc Diagram Widget (IEC 61217).

Visualizes VMAT rotational arcs and static IMRT beams with color-coded deliverability
risk levels, multi-pass concentric tracks, and an interactive center HUD.
"""

import math
from typing import Optional, Dict, Any, List

from PyQt6.QtCore import Qt, QPointF, QRectF, pyqtSignal
from PyQt6.QtGui import (
    QPainter, QPen, QBrush, QColor, QFont, QPainterPath
)
from PyQt6.QtWidgets import QWidget, QComboBox


class PolarArcWidget(QWidget):
    """
    Renders an interactive polar circular arc diagram conforming to IEC 61217
    with gantry rotation trajectory, embedded center HUD plan/beam selectors, and color-coded risk levels.
    """
    intervalHovered = pyqtSignal(dict)
    intervalClicked = pyqtSignal(int)

    def __init__(self, parent=None, is_ru: bool = True):
        super().__init__(parent)
        self.is_ru = is_ru
        self.setMinimumSize(360, 360)
        self.setMouseTracking(True)
        self.beam_data: Optional[Dict[str, Any]] = None
        self.hovered_interval_idx: Optional[int] = None
        self.selected_interval_idx: Optional[int] = None

        self._init_embedded_controls()

    def _init_embedded_controls(self):
        combo_style = """
            QComboBox {
                background-color: #242426;
                color: #f4f4f5;
                border: 1px solid #3f3f46;
                border-radius: 4px;
                padding: 2px 6px;
                font-size: 11px;
            }
            QComboBox:hover {
                border-color: #38bdf8;
                background-color: #2a2a2d;
            }
            QComboBox::drop-down {
                border: none;
                width: 16px;
            }
            QComboBox QAbstractItemView {
                background-color: #18181b;
                color: #f4f4f5;
                selection-background-color: #1e3a8a;
                selection-color: #ffffff;
                border: 1px solid #3f3f46;
                min-width: 200px;
            }
        """
        self.plan_combo = QComboBox(self)
        self.plan_combo.setToolTip("Выбор плана" if self.is_ru else "Select plan")
        self.plan_combo.setStyleSheet(combo_style)

        self.beam_combo = QComboBox(self)
        self.beam_combo.setToolTip("Выбор пучка" if self.is_ru else "Select beam")
        self.beam_combo.setStyleSheet(combo_style)

    def _update_controls_geometry(self):
        w = self.width()
        h = self.height()
        cx = w / 2.0
        cy = h / 2.0
        radius = min(w, h) / 2.0 - 24.0
        num_passes = self.beam_data.get('num_passes', 1) if self.beam_data else 1
        center_r = (radius - 78.0) if num_passes > 1 else (radius - 60.0)
        center_r = max(50.0, center_r)

        combo_w = max(130, min(230, int(center_r * 1.42)))
        combo_h = 24

        plan_y = int(cy - center_r * 0.70)
        self.plan_combo.setGeometry(int(cx - combo_w / 2.0), plan_y, combo_w, combo_h)

        beam_y = int(cy + center_r * 0.70 - combo_h)
        self.beam_combo.setGeometry(int(cx - combo_w / 2.0), beam_y, combo_w, combo_h)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_controls_geometry()

    def showEvent(self, event):
        super().showEvent(event)
        self._update_controls_geometry()

    def set_beam_data(self, beam_data: Optional[Dict[str, Any]]):
        self.beam_data = beam_data
        self.hovered_interval_idx = None
        self.selected_interval_idx = None
        self._update_controls_geometry()
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
        cx = w / 2.0
        cy = h / 2.0
        center = QPointF(cx, cy)
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
                    p_left = QPointF(p_base.x() + nx * arrow_size, p_base.y() + ny * arrow_size)
                    p_right = QPointF(p_base.x() - nx * arrow_size, p_base.y() - ny * arrow_size)
                    arrow_path = QPainterPath()
                    arrow_path.moveTo(center)
                    arrow_path.lineTo(p_left)
                    arrow_path.lineTo(p_right)
                    arrow_path.closeSubpath()
                    painter.setBrush(QBrush(QColor("#38bdf8")))
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.drawPath(arrow_path)

                # Source head indicator circle at gantry perimeter
                painter.setBrush(QBrush(QColor("#0284c7")))
                painter.setPen(QPen(QColor("#ffffff"), 1.5))
                painter.drawEllipse(QPointF(x_start, y_start), 6, 6)

                # Center Isocenter Circle & Badge
                painter.setBrush(QBrush(QColor("#1f1f21")))
                painter.setPen(QPen(QColor("#38bdf8"), 1.5))
                center_r = radius - 60.0
                painter.drawEllipse(center, center_r, center_r)

                combo_h = 24
                plan_y = int(cy - center_r * 0.70)
                beam_y = int(cy + center_r * 0.70 - combo_h)

                # Section headers above embedded combo boxes
                painter.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
                painter.setPen(QPen(QColor("#71717a")))
                painter.drawText(QRectF(cx - 60, plan_y - 12, 120, 11), Qt.AlignmentFlag.AlignCenter, "ПЛАН" if self.is_ru else "PLAN")
                painter.drawText(QRectF(cx - 60, beam_y - 12, 120, 11), Qt.AlignmentFlag.AlignCenter, "ПУЧОК" if self.is_ru else "BEAM")

                painter.setPen(QPen(QColor("#ffffff")))
                painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
                painter.drawText(QRectF(cx - 80, cy - 20, 160, 18), Qt.AlignmentFlag.AlignCenter, "STATIC IMRT")
                painter.setFont(QFont("Segoe UI", 8))
                painter.setPen(QPen(QColor("#38bdf8")))
                painter.drawText(QRectF(cx - 80, cy - 3, 160, 16), Qt.AlignmentFlag.AlignCenter, f"{'Гентри' if self.is_ru else 'Gantry'}: {g_angle:.1f}°")
                painter.setFont(QFont("Segoe UI", 8))
                painter.setPen(QPen(QColor("#8e8e93")))
                cps_cnt = self.beam_data.get('num_control_points', 0)
                tot_mu = self.beam_data.get('total_mu', 0.0)
                painter.drawText(QRectF(cx - 80, cy + 13, 160, 16), Qt.AlignmentFlag.AlignCenter, f"{cps_cnt} CP | {tot_mu:.1f} MU")
            else:
                painter.setPen(QPen(QColor("#8e8e93")))
                painter.setFont(QFont("Segoe UI", 9))
                painter.drawText(QRectF(cx - 100, cy - 10, 200, 20), Qt.AlignmentFlag.AlignCenter, "Нет данных пучка" if self.is_ru else "No beam data")
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
                # Multi-track concentric orbits
                if p_idx == 0:
                    r_inner = radius - 76.0
                    thick = 3.5 + 33.0 * (norm ** 0.58)
                else:
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
        center_r = (radius - 78.0) if num_passes > 1 else (radius - 60.0)
        painter.drawEllipse(center, center_r, center_r)

        # Draw section headers above embedded combo boxes inside center circle
        combo_h = 24
        plan_y = int(cy - center_r * 0.70)
        beam_y = int(cy + center_r * 0.70 - combo_h)

        painter.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
        painter.setPen(QPen(QColor("#71717a")))
        painter.drawText(QRectF(cx - 60, plan_y - 12, 120, 11), Qt.AlignmentFlag.AlignCenter, "ПЛАН" if self.is_ru else "PLAN")
        painter.drawText(QRectF(cx - 60, beam_y - 12, 120, 11), Qt.AlignmentFlag.AlignCenter, "ПУЧОК" if self.is_ru else "BEAM")

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

            if risk == 'CRITICAL':
                risk_color = QColor('#ef4444')
                risk_text = 'КРИТИЧЕСКИЙ РИСК' if self.is_ru else 'CRITICAL RISK'
            elif risk == 'WARNING':
                risk_color = QColor('#f59e0b')
                risk_text = 'ПОВЫШЕННАЯ СЛОЖНОСТЬ' if self.is_ru else 'HIGH COMPLEXITY'
            else:
                risk_color = QColor('#22c55e')
                risk_text = 'ПАРАМЕТРЫ В НОРМЕ' if self.is_ru else 'NORMAL'

            p_idx = cp.get('pass_idx', 0)
            rot_dir = cp.get('direction', 'CW')
            dir_sym = '↻' if rot_dir == 'CW' else '↺'
            title_txt = f"CP {cp['index']:02d}→{cp['index']+1:02d} ({dir_sym})" if num_passes > 1 else f"CP {cp['index']:02d} → {cp['index']+1:02d}"

            # Row 1: CP title
            painter.setFont(QFont('Segoe UI', 8, QFont.Weight.Bold))
            painter.setPen(QPen(QColor('#ffffff')))
            painter.drawText(QRectF(cx - 85, cy - 25, 170, 14), Qt.AlignmentFlag.AlignCenter, title_txt)

            # Row 2: Status color dot and risk label
            painter.setFont(QFont('Segoe UI', 8, QFont.Weight.DemiBold))
            painter.setPen(QPen(risk_color))
            painter.drawText(QRectF(cx - 85, cy - 11, 170, 14), Qt.AlignmentFlag.AlignCenter, f"● {risk_text}")

            # Row 3: Gantry angles
            painter.setFont(QFont('Segoe UI', 8))
            painter.setPen(QPen(QColor('#a1a1aa')))
            painter.drawText(QRectF(cx - 85, cy + 3, 170, 14), Qt.AlignmentFlag.AlignCenter,
                             f"{'Гентри' if self.is_ru else 'Gantry'}: {cp['gantry_start']:.0f}° → {cp['gantry_end']:.0f}°")

            # Row 4: Dose density and Jump factor
            painter.setFont(QFont('Segoe UI', 8))
            painter.setPen(QPen(QColor('#38bdf8')))
            painter.drawText(QRectF(cx - 85, cy + 17, 170, 14), Qt.AlignmentFlag.AlignCenter,
                             f"{mpd:.2f} MU/deg | {factor:.1f}×")

        else:
            # Neutral Standby State
            painter.setPen(QPen(QColor('#ffffff')))
            painter.setFont(QFont('Segoe UI', 9, QFont.Weight.Bold))
            _cps_word = "контрольных точек" if self.is_ru else "control points"
            painter.drawText(QRectF(cx - 90, cy - 20, 180, 18), Qt.AlignmentFlag.AlignCenter,
                             f"{len(intervals)} {_cps_word}")

            painter.setFont(QFont('Segoe UI', 8))
            painter.setPen(QPen(QColor('#38bdf8')))
            painter.drawText(QRectF(cx - 80, cy - 3, 160, 16), Qt.AlignmentFlag.AlignCenter,
                             f"Σ {self.beam_data.get('total_mu', 0.0):.1f} MU")

            painter.setFont(QFont('Segoe UI', 8))
            painter.setPen(QPen(QColor('#71717a')))
            _hint_hover = "Наведите на сектор дуги" if self.is_ru else "Hover over an arc sector"
            painter.drawText(QRectF(cx - 90, cy + 13, 180, 16), Qt.AlignmentFlag.AlignCenter,
                             _hint_hover)

    def mouseMoveEvent(self, event):
        if not self.beam_data or not self.beam_data.get('is_vmat'):
            return

        w = self.width()
        h = self.height()
        center = QPointF(w / 2.0, h / 2.0)
        pos = event.position()
        dx = pos.x() - center.x()
        dy = pos.y() - center.y()
        dist = math.hypot(dx, dy)
        radius = min(w, h) / 2.0 - 24.0

        num_passes = self.beam_data.get('num_passes', 1)
        min_r = (radius - 78.0) if num_passes > 1 else (radius - 60.0)

        if min_r <= dist <= radius + 25:
            math_ang = math.degrees(math.atan2(dy, dx)) % 360.0
            gantry_ang = (90.0 - math_ang) % 360.0

            target_pass = 0
            if num_passes > 1:
                r_split = radius - 38.0
                target_pass = 0 if dist < r_split else 1

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
