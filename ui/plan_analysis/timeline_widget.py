# -*- coding: utf-8 -*-
"""
Modulation Timeline Graph Widget.

Linear stepped bar graph displaying MU/degree across gantry angles with safety thresholds,
adaptive vertical scaling, and bidirectional selection synchronization.
"""

from typing import Optional, Dict, Any, List

from PyQt6.QtCore import Qt, QPointF, QRectF, pyqtSignal
from PyQt6.QtGui import (
    QPainter, QPen, QBrush, QColor, QFont
)
from PyQt6.QtWidgets import QWidget


class ModulationTimelineWidget(QWidget):
    """Linear graph displaying MU/degree across gantry angle with safety bounds."""

    intervalHovered = pyqtSignal(dict)
    intervalClicked = pyqtSignal(int)

    def __init__(self, parent=None, is_ru: bool = True):
        super().__init__(parent)
        self.is_ru = is_ru
        self.setMinimumHeight(140)
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

    def _get_interval_index_at(self, pos: QPointF) -> Optional[int]:
        if not self.beam_data:
            return None
        intervals = self.beam_data.get('intervals', [])
        if not intervals:
            return None
        margin_l, margin_r = 50, 20
        margin_t, margin_b = 20, 30
        plot_w = self.width() - margin_l - margin_r
        plot_h = self.height() - margin_t - margin_b
        x = pos.x()
        y = pos.y()
        if not (margin_l <= x <= self.width() - margin_r and margin_t <= y <= margin_t + plot_h):
            return None
        step_px = plot_w / float(len(intervals))
        idx = int((x - margin_l) / step_px)
        if 0 <= idx < len(intervals):
            return idx
        return None

    def mouseMoveEvent(self, event):
        idx = self._get_interval_index_at(event.position())
        if idx != self.hovered_interval_idx:
            self.hovered_interval_idx = idx
            self.update()
            if idx is not None:
                intervals = self.beam_data.get('intervals', [])
                if 0 <= idx < len(intervals):
                    self.intervalHovered.emit(intervals[idx])
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if self.hovered_interval_idx is not None:
            self.hovered_interval_idx = None
            self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        idx = self._get_interval_index_at(event.position())
        if idx is not None:
            self.selected_interval_idx = idx
            self.intervalClicked.emit(idx)
            self.update()
        super().mousePressEvent(event)

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
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Нет данных пучка" if self.is_ru else "No beam data")
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
            painter.drawText(margin_l, margin_t - 4, "Доза сегментов IMRT (ΔMU на контрольную точку)" if self.is_ru else "IMRT Segment Dose (ΔMU per CP)")

            # Plot bars for each segment
            n = len(intervals)
            step_px = plot_w / float(n)
            base_y = margin_t + plot_h

            selected_bar_tuple = None
            for i, item in enumerate(intervals):
                dmu = item['delta_mu']
                x = margin_l + i * step_px
                y = val_to_y_static(dmu)
                is_selected = (i == self.selected_interval_idx)
                is_hovered = (i == self.hovered_interval_idx)
                bar_rect = QRectF(x, y, max(1.0, step_px - 1), base_y - y)
                if is_selected:
                    selected_bar_tuple = (bar_rect, QColor(56, 189, 248))
                else:
                    color = QColor(56, 189, 248)
                    alpha = 180 if is_hovered else 120
                    painter.setPen(QPen(color.lighter(120) if is_hovered else color, 1))
                    painter.setBrush(QBrush(QColor(color.red(), color.green(), color.blue(), alpha)))
                    painter.drawRect(bar_rect)

            if selected_bar_tuple:
                s_rect, s_color = selected_bar_tuple
                painter.setPen(QPen(QColor("#ffffff"), 2.0))
                painter.setBrush(QBrush(QColor(s_color.red(), s_color.green(), s_color.blue(), 230)))
                painter.drawRect(s_rect)

            # X Axis labels
            painter.setPen(QPen(QColor("#8e8e93")))
            painter.setFont(QFont("Segoe UI", 8))
            painter.drawText(margin_l, h - 10, f"CP 00 (0.0 MU)")
            painter.drawText(w - margin_r - 90, h - 10, f"CP {n:02d} ({self.beam_data.get('total_mu', 0.0):.1f} MU)")
            return

        # Determine scale dynamically based on actual plan data
        actual_max_mpd = max([it['mu_per_deg'] for it in intervals] or [1.0])

        if actual_max_mpd >= 12.0:
            max_val = max(18.0, min(25.0, actual_max_mpd * 1.15))
            grid_values = [0.0, 5.0, 10.0, 15.0, 20.0]
            if max_val > 20.0:
                grid_values.append(25.0)
        elif actual_max_mpd > 8.0:
            max_val = 12.0
            grid_values = [0.0, 3.0, 6.0, 9.0, 12.0]
        elif actual_max_mpd > 5.0:
            max_val = 8.0
            grid_values = [0.0, 2.0, 4.0, 6.0, 8.0]
        elif actual_max_mpd > 3.0:
            max_val = 5.0
            grid_values = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
        else:
            max_val = 3.0
            grid_values = [0.0, 1.0, 2.0, 3.0]

        # Y scale line helpers
        def val_to_y(val):
            ratio = min(1.0, max(0.0, val / max_val))
            return margin_t + plot_h * (1.0 - ratio)

        # Draw grid & Y labels
        painter.setFont(QFont("Segoe UI", 8))
        painter.setPen(QPen(QColor("#2c2c2e"), 1, Qt.PenStyle.DashLine))

        for v in grid_values:
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

        selected_bar_tuple = None
        for i, item in enumerate(intervals):
            mpd = item['mu_per_deg']
            risk = item['risk_level']
            x = margin_l + i * step_px
            y = val_to_y(mpd)

            is_selected = (i == self.selected_interval_idx)
            is_hovered = (i == self.hovered_interval_idx)

            if risk == 'CRITICAL':
                color = QColor("#ff453a")
            elif risk == 'WARNING':
                color = QColor("#ffd60a")
            else:
                color = QColor("#30d158")

            bar_rect = QRectF(x, y, max(1.0, step_px - 1), base_y - y)

            if is_selected:
                selected_bar_tuple = (bar_rect, color)
            else:
                c_draw = color.lighter(125) if is_hovered else color
                alpha = 160 if is_hovered else 100
                painter.setPen(QPen(c_draw, 1))
                painter.setBrush(QBrush(QColor(c_draw.red(), c_draw.green(), c_draw.blue(), alpha)))
                painter.drawRect(bar_rect)

        # Draw selected bar on top with crisp white outline
        if selected_bar_tuple:
            s_rect, s_color = selected_bar_tuple
            painter.setPen(QPen(QColor("#ffffff"), 2.0))
            painter.setBrush(QBrush(QColor(s_color.red(), s_color.green(), s_color.blue(), 230)))
            painter.drawRect(s_rect)

        th = self.beam_data.get('thresholds', {}) if self.beam_data else {}
        min_dr = float(th.get('min_dose_rate', 60.0))
        min_mpd = float(th.get('min_mu_per_deg', 0.165))

        # Draw Safety Thresholds and descriptive labels on top of bars
        # 1. Red line for low dose rate danger (< min_mpd MU/deg)
        y_low = val_to_y(min_mpd)
        painter.setPen(QPen(QColor("#ff453a"), 1, Qt.PenStyle.DashLine))
        painter.drawLine(margin_l, int(y_low), w - margin_r, int(y_low))

        text_low = (
            f" {min_mpd:.3f} MU/deg — порог DOSE RATE MON (< {min_dr:.0f} MU/мин) "
            if self.is_ru else
            f" {min_mpd:.3f} MU/deg — DOSE RATE MON threshold (< {min_dr:.0f} MU/min) "
        )
        painter.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
        fm = painter.fontMetrics()
        w_low = fm.horizontalAdvance(text_low)
        badge_low_x = w - margin_r - w_low - 12
        badge_low_rect = QRectF(badge_low_x, max(float(margin_t), y_low - 15), float(w_low), 14.0)
        painter.setPen(QPen(QColor("#ff453a"), 1))
        painter.setBrush(QBrush(QColor("#1a1a1c")))
        painter.drawRoundedRect(badge_low_rect, 3, 3)
        painter.setPen(QPen(QColor("#ff8585")))
        painter.drawText(badge_low_rect, Qt.AlignmentFlag.AlignCenter, text_low)

        # 2. Yellow line for heavy modulation (>= 15.0 MU/deg) — only shown when scale reaches it
        if 15.0 <= max_val:
            y_high = val_to_y(15.0)
            painter.setPen(QPen(QColor("#ffd60a"), 1, Qt.PenStyle.DashLine))
            painter.drawLine(margin_l, int(y_high), w - margin_r, int(y_high))

            text_high = (
                " 15.0 MU/deg — замедление гентри (< 0.7°/с) "
                if self.is_ru else
                " 15.0 MU/deg — gantry slowdown (< 0.7°/s) "
            )
            w_high = fm.horizontalAdvance(text_high)
            badge_high_x = w - margin_r - w_high - 12
            badge_high_rect = QRectF(badge_high_x, float(y_high - 15), float(w_high), 14.0)
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
