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
        center_r = max(105.0, (radius - 76.0) if num_passes > 1 else (radius - 58.0))

        combo_w = max(130, min(220, int(center_r * 1.35)))
        combo_h = 24

        plan_y = int(cy - 94)
        self.plan_combo.setGeometry(int(cx - combo_w / 2.0), plan_y, combo_w, combo_h)

        beam_y = int(cy + 52)
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
                center_r = max(105.0, radius - 58.0)
                painter.drawEllipse(center, center_r, center_r)

                painter.setPen(QPen(QColor("#ffffff")))
                painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
                painter.drawText(QRectF(center.x() - 80, center.y() - 30, 160, 20), Qt.AlignmentFlag.AlignCenter, "STATIC IMRT")
                painter.setFont(QFont("Segoe UI", 9))
                painter.setPen(QPen(QColor("#38bdf8")))
                painter.drawText(QRectF(center.x() - 80, center.y() - 10, 160, 18), Qt.AlignmentFlag.AlignCenter, f"{'Гентри' if self.is_ru else 'Gantry'}: {g_angle:.1f}°")
                painter.setFont(QFont("Segoe UI", 8))
                painter.setPen(QPen(QColor("#8e8e93")))
                cps_cnt = self.beam_data.get('num_control_points', 0)
                tot_mu = self.beam_data.get('total_mu', 0.0)
                painter.drawText(QRectF(center.x() - 80, center.y() + 10, 160, 18), Qt.AlignmentFlag.AlignCenter, f"{cps_cnt} CP | {tot_mu:.1f} MU")
            else:
                painter.setPen(QPen(QColor("#8e8e93")))
                painter.setFont(QFont("Segoe UI", 10))
                painter.drawText(QRectF(center.x() - 120, center.y() - 30, 240, 60), Qt.AlignmentFlag.AlignCenter, "Нет данных пучка" if self.is_ru else "No beam data")
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
        center_r = max(105.0, (radius - 76.0) if num_passes > 1 else (radius - 58.0))
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
                badge_text = 'КРИТИЧЕСКИЙ РИСК' if self.is_ru else 'CRITICAL RISK'
            elif risk == 'WARNING':
                badge_bg = QColor(245, 158, 11, 40)
                badge_border = QColor('#f59e0b')
                badge_text = 'ПОВЫШЕННАЯ СЛОЖНОСТЬ' if self.is_ru else 'HIGH COMPLEXITY'
            else:
                badge_bg = QColor(34, 197, 94, 40)
                badge_border = QColor('#22c55e')
                badge_text = 'ПАРАМЕТРЫ В НОРМЕ' if self.is_ru else 'NORMAL PARAMETERS'

            # Draw Badge with dynamic width and crisp vector dot
            badge_font = QFont('Segoe UI', 8, QFont.Weight.Bold)
            painter.setFont(badge_font)
            fm = painter.fontMetrics()
            text_w = fm.horizontalAdvance(badge_text)
            content_w = 7.0 + 6.0 + text_w # 7px dot + 6px gap + text
            badge_w = content_w + 18.0
            badge_h = 20.0
            badge_rect = QRectF(center.x() - badge_w / 2.0, center.y() - 64, badge_w, badge_h)

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
            if self.is_ru:
                title_txt = f"CP {cp['index']:02d} → {cp['index']+1:02d} (П{p_idx+1}: {dir_sym})" if num_passes > 1 else f"Сектор CP {cp['index']:02d} → {cp['index']+1:02d}"
            else:
                title_txt = f"CP {cp['index']:02d} → {cp['index']+1:02d} (P{p_idx+1}: {dir_sym})" if num_passes > 1 else f"CP Sector {cp['index']:02d} → {cp['index']+1:02d}"

            painter.setFont(QFont('Segoe UI', 9, QFont.Weight.Bold))
            painter.setPen(QPen(QColor('#ffffff')))
            painter.drawText(QRectF(center.x() - 100, center.y() - 41, 200, 16), Qt.AlignmentFlag.AlignCenter, title_txt)

            # Gantry angle
            painter.setFont(QFont('Segoe UI', 8))
            painter.setPen(QPen(QColor('#a1a1aa')))
            _gantry_word = "Гентри" if self.is_ru else "Gantry"
            painter.drawText(QRectF(center.x() - 90, center.y() - 23, 180, 15), Qt.AlignmentFlag.AlignCenter,
                             f"{_gantry_word}: {cp['gantry_start']:.1f}° → {cp['gantry_end']:.1f}° (Δ {cp['delta_gantry']:.1f}°)")

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

            _mu_min = "MU/мин" if self.is_ru else "MU/min"

            # 1. Dose rate status
            if round(dr) < min_dr:
                dr_col, dr_st = QColor('#ef4444'), ('Сбой' if self.is_ru else 'Fault')
                dr_val_str = f"{dr:.1f} {_mu_min}"
            elif dr < warn_dr:
                dr_col, dr_st = QColor('#f59e0b'), ('Низкая' if self.is_ru else 'Low')
                dr_val_str = f"{dr:.0f} {_mu_min}"
            else:
                dr_col, dr_st = QColor('#22c55e'), ('Норма' if self.is_ru else 'OK')
                dr_val_str = f"{dr:.0f} {_mu_min}"
            draw_param_row(center.y() - 4, ('Мощность:' if self.is_ru else 'Dose Rate:'), dr_val_str, dr_col, dr_st)

            # 2. Dose density status
            if mpd < min_mpd:
                mpd_col, mpd_st = QColor('#ef4444'), ('Провал' if self.is_ru else 'Drop')
            elif mpd > 15.0:
                mpd_col, mpd_st = QColor('#f59e0b'), ('Перегруз' if self.is_ru else 'High')
            elif mpd < warn_mpd:
                mpd_col, mpd_st = QColor('#f59e0b'), ('Низкая' if self.is_ru else 'Low')
            else:
                mpd_col, mpd_st = QColor('#22c55e'), ('Норма' if self.is_ru else 'OK')
            draw_param_row(center.y() + 13, ('Плотность:' if self.is_ru else 'Density:'), f"{mpd:.2f} MU/deg", mpd_col, mpd_st)

            # 3. Delta jump status
            if active_idx > 0:
                if factor >= max_jump:
                    jump_col, jump_st = QColor('#ef4444'), ('Шок' if self.is_ru else 'Shock')
                elif factor >= warn_jump:
                    jump_col, jump_st = QColor('#f59e0b'), ('Перепад' if self.is_ru else 'Jump')
                else:
                    jump_col, jump_st = QColor('#22c55e'), ('Норма' if self.is_ru else 'OK')
                d_sign = '+' if delta_mpd >= 0 else ''
                draw_param_row(center.y() + 30, ('Перепад:' if self.is_ru else 'Jump:'), f"{factor:.1f}× ({d_sign}{delta_mpd:.2f})", jump_col, jump_st)
            else:
                draw_param_row(center.y() + 30, ('Перепад:' if self.is_ru else 'Jump:'), ("— (старт)" if self.is_ru else "— (start)"), QColor('#9ca3af'), ('Старт' if self.is_ru else 'Start'))

        else:
            # Neutral Standby State
            painter.setPen(QPen(QColor('#38bdf8')))
            painter.setFont(QFont('Segoe UI', 9, QFont.Weight.Bold))
            _beam_label = f"Пучок #{self.beam_data.get('beam_number', 1)}" if self.is_ru else f"Beam #{self.beam_data.get('beam_number', 1)}"
            painter.drawText(QRectF(center.x() - 80, center.y() - 35, 160, 20), Qt.AlignmentFlag.AlignCenter, _beam_label)

            painter.setPen(QPen(QColor('#ffffff')))
            painter.setFont(QFont('Segoe UI', 8))
            _cps_word = "контрольных точек" if self.is_ru else "control points"
            painter.drawText(QRectF(center.x() - 90, center.y() - 15, 180, 16), Qt.AlignmentFlag.AlignCenter,
                             f"{len(intervals)} {_cps_word}")

            painter.setFont(QFont('Segoe UI', 8))
            painter.setPen(QPen(QColor('#8e8e93')))
            painter.drawText(QRectF(center.x() - 90, center.y() + 5, 180, 16), Qt.AlignmentFlag.AlignCenter,
                             f"Σ {self.beam_data.get('total_mu', 0.0):.1f} MU")

            painter.setFont(QFont('Segoe UI', 8))
            painter.setPen(QPen(QColor('#6b7280')))
            _hint_hover = "Наведите на сектор дуги" if self.is_ru else "Hover over an arc sector"
            painter.drawText(QRectF(center.x() - 95, center.y() + 25, 190, 16), Qt.AlignmentFlag.AlignCenter,
                             _hint_hover)

    def _get_interval_at_pos(self, pos: QPointF) -> Optional[int]:
        if not self.beam_data or not self.beam_data.get('is_vmat'):
            return None

        w = self.width()
        h = self.height()
        center = QPointF(w / 2.0, h / 2.0)
        dx = pos.x() - center.x()
        dy = center.y() - pos.y()  # Invert screen Y to standard cartesian Y
        dist = math.hypot(dx, dy)
        radius = min(w, h) / 2.0 - 24.0

        num_passes = self.beam_data.get('num_passes', 1)
        min_r = max(105.0, (radius - 76.0) if num_passes > 1 else (radius - 58.0))

        if min_r <= dist <= radius + 25:
            math_ang = math.degrees(math.atan2(dy, dx)) % 360.0
            gantry_ang = (90.0 - math_ang) % 360.0

            target_pass = 0
            if num_passes > 1:
                r_split = radius - 38.0
                target_pass = 0 if dist < r_split else 1

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
                        return item['index']
                else: # CC
                    diff = (g1 - g2) % 360.0
                    rel = (g1 - gantry_ang) % 360.0
                    if rel <= diff:
                        return item['index']
        return None

    def mouseMoveEvent(self, event):
        matched_idx = self._get_interval_at_pos(event.position())
        if matched_idx != self.hovered_interval_idx:
            self.hovered_interval_idx = matched_idx
            self.update()
            if matched_idx is not None and self.beam_data:
                intervals = self.beam_data.get('intervals', [])
                if matched_idx < len(intervals):
                    self.intervalHovered.emit(intervals[matched_idx])

    def leaveEvent(self, event):
        if self.hovered_interval_idx is not None:
            self.hovered_interval_idx = None
            self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        idx = self._get_interval_at_pos(event.position())
        if idx is not None:
            self.selected_interval_idx = idx
            self.intervalClicked.emit(self.selected_interval_idx)
            self.update()
