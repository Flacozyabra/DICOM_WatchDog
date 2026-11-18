from __future__ import annotations

import math

from PyQt6.QtCore import Qt, QEvent, QRectF, QPointF, QPropertyAnimation, QEasingCurve, pyqtProperty
from PyQt6.QtGui import (
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PyQt6.QtWidgets import QButtonGroup, QHBoxLayout, QPushButton, QSizePolicy, QWidget


_RENDER_AA = (
    QPainter.RenderHint.Antialiasing
    if hasattr(QPainter, "RenderHint")
    else QPainter.Antialiasing
)
_NO_PEN = Qt.PenStyle.NoPen if hasattr(Qt, "PenStyle") else Qt.NoPen
_NO_BRUSH = Qt.BrushStyle.NoBrush if hasattr(Qt, "BrushStyle") else Qt.NoBrush
_EASE_LINEAR = QEasingCurve.Type.Linear if hasattr(QEasingCurve, "Type") else QEasingCurve.Linear


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)


def _ease_front(t: float) -> float:
    """Fast front-edge curve with subtle settle overshoot inside track bounds."""
    t = _clamp01(t)
    inv = 1.0 - t
    base = 1.0 - inv * inv * inv
    # Slight elastic bulge around t=0.72..0.95 that settles cleanly to 1.0
    if t > 0.55:
        phase = (t - 0.55) / 0.45
        base += 0.035 * math.sin(math.pi * phase) * (1.0 - phase * 0.4)
    return base


def _ease_back(t: float) -> float:
    """Delayed viscous tail curve so the droplet stretches before snapping to target."""
    t = _clamp01(t)
    if t <= 0.14:
        return 0.02 * (t / 0.14)
    u = _clamp01((t - 0.14) / 0.86)
    return 0.02 + 0.98 * (u * u * (3.0 - 2.0 * u))


def build_liquid_droplet_path(
    left: float,
    right: float,
    top: float,
    height: float,
    pinch_px: float = 0.0,
) -> QPainterPath:
    """Build a C1-continuous capsule / pinched-waist liquid droplet QPainterPath."""
    path = QPainterPath()
    w = max(4.0, right - left)
    h = max(4.0, height)
    r = h * 0.5
    cap_r = min(r, w * 0.48)
    bot = top + h
    mid_y = top + r
    inner_l = left + cap_r
    inner_r = right - cap_r
    pinch = max(0.0, min(r * 0.42, pinch_px))
    k = 0.55228475

    path.moveTo(inner_l, top)

    # Top edge (with optional inward meniscus pinch when stretched between buttons)
    span = inner_r - inner_l
    if span > 0.5:
        if pinch > 0.05:
            cx = (inner_l + inner_r) * 0.5
            dx_h = span * 0.5
            path.cubicTo(
                inner_l + dx_h * 0.45, top,
                cx - dx_h * 0.45, top + pinch,
                cx, top + pinch,
            )
            path.cubicTo(
                cx + dx_h * 0.45, top + pinch,
                inner_r - dx_h * 0.45, top,
                inner_r, top,
            )
        else:
            path.lineTo(inner_r, top)

    # Right cap
    path.cubicTo(inner_r + cap_r * k, top, right, mid_y - r * k, right, mid_y)
    path.cubicTo(right, mid_y + r * k, inner_r + cap_r * k, bot, inner_r, bot)

    # Bottom edge
    if span > 0.5:
        if pinch > 0.05:
            cx = (inner_l + inner_r) * 0.5
            dx_h = span * 0.5
            path.cubicTo(
                inner_r - dx_h * 0.45, bot,
                cx + dx_h * 0.45, bot - pinch,
                cx, bot - pinch,
            )
            path.cubicTo(
                cx - dx_h * 0.45, bot - pinch,
                inner_l + dx_h * 0.45, bot,
                inner_l, bot,
            )
        else:
            path.lineTo(inner_l, bot)

    # Left cap
    path.cubicTo(inner_l - cap_r * k, bot, left, mid_y + r * k, left, mid_y)
    path.cubicTo(left, mid_y - r * k, inner_l - cap_r * k, top, inner_l, top)

    path.closeSubpath()
    return path


class LiquidModeBar(QWidget):
    """
    Glassmorphic segmented track hosting checkable QPushButtons while painting
    an animated liquid-glass water droplet that flows smoothly between modes.
    """

    def __init__(self, buttons: list[QPushButton], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._buttons: list[QPushButton] = list(buttons)
        self._pad = 3
        self._accent = QColor("#3B82F6")
        self._accent_dark = QColor("#1D4ED8")
        self._text_muted = "#9CA3AF"

        self._btn_group = QButtonGroup(self)
        self._btn_group.setExclusive(True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(self._pad, self._pad, self._pad, self._pad)
        layout.setSpacing(4)
        for idx, btn in enumerate(self._buttons):
            btn.setParent(self)
            btn.setFlat(True)
            btn.setFixedHeight(27)
            btn.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            layout.addWidget(btn)
            self._btn_group.addButton(btn, idx)
            btn.toggled.connect(lambda checked, b=btn: self._on_btn_toggled(b, checked))

        self.setFixedHeight(27 + self._pad * 2)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

        self._cur_left = 0.0
        self._cur_right = 0.0
        self._start_left = 0.0
        self._start_right = 0.0
        self._target_left = 0.0
        self._target_right = 0.0
        self._moving_right = True
        self._anim_progress = 1.0
        self._geom_initialized = False

        self._anim = QPropertyAnimation(self, b"anim_progress", self)
        self._anim.setDuration(340)
        self._anim.setEasingCurve(_EASE_LINEAR)

        self._apply_button_styles()

    def set_theme_colors(self, accent: str, accent_dark: str, text_muted: str = "#9CA3AF") -> None:
        self._accent = QColor(accent or "#3B82F6")
        self._accent_dark = QColor(accent_dark or "#1D4ED8")
        self._text_muted = text_muted or "#9CA3AF"
        self._apply_button_styles()
        self.update()

    def _apply_button_styles(self) -> None:
        btn_style = f"""
            QPushButton {{
                background: transparent;
                border: none;
                border-radius: 14px;
                color: {self._text_muted};
                padding: 0px 14px;
                font-size: 11px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background: rgba(255, 255, 255, 18);
                color: #F3F4F6;
            }}
            QPushButton:checked {{
                background: transparent;
                color: #FFFFFF;
            }}
        """
        for btn in self._buttons:
            btn.setStyleSheet(btn_style)

    @pyqtProperty(float)
    def anim_progress(self) -> float:
        return self._anim_progress

    @anim_progress.setter
    def anim_progress(self, value: float) -> None:
        self._anim_progress = float(value)
        t_f = _ease_front(self._anim_progress)
        t_b = _ease_back(self._anim_progress)
        max_r = max(float(self._pad), float(self.width() - self._pad))
        min_l = float(self._pad)

        if self._moving_right:
            raw_r = self._start_right + (self._target_right - self._start_right) * t_f
            raw_l = self._start_left + (self._target_left - self._start_left) * t_b
        else:
            raw_l = self._start_left + (self._target_left - self._start_left) * t_f
            raw_r = self._start_right + (self._target_right - self._start_right) * t_b

        self._cur_left = max(min_l, min(max_r - 12.0, raw_l))
        self._cur_right = min(max_r, max(self._cur_left + 12.0, raw_r))
        self.update()

    def _active_button(self) -> QPushButton | None:
        for btn in self._buttons:
            if btn.isChecked():
                return btn
        return self._buttons[0] if self._buttons else None

    def _target_span_for(self, btn: QPushButton | None) -> tuple[float, float] | None:
        if btn is None or btn.width() <= 0:
            return None
        g = btn.geometry()
        return float(g.left()), float(g.left() + g.width())

    def snap_to_checked(self) -> None:
        """Immediately snap the droplet to the active button without animation."""
        self._anim.stop()
        self._anim_progress = 1.0
        span = self._target_span_for(self._active_button())
        if span is not None:
            self._cur_left, self._cur_right = span
            self._start_left, self._start_right = span
            self._target_left, self._target_right = span
            self._geom_initialized = True
        self.update()

    def animate_to_button(self, btn: QPushButton | None) -> None:
        span = self._target_span_for(btn)
        if span is None:
            return
        if not self._geom_initialized or not self.isVisible():
            self._cur_left, self._cur_right = span
            self._start_left, self._start_right = span
            self._target_left, self._target_right = span
            self._geom_initialized = True
            self.update()
            return

        new_l, new_r = span
        if abs(new_l - self._target_left) < 0.5 and abs(new_r - self._target_right) < 0.5 and self._anim_progress >= 1.0:
            return

        self._anim.stop()
        self._start_left = self._cur_left
        self._start_right = self._cur_right
        self._target_left = new_l
        self._target_right = new_r
        cur_cx = (self._cur_left + self._cur_right) * 0.5
        tgt_cx = (new_l + new_r) * 0.5
        self._moving_right = tgt_cx >= cur_cx

        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()

    def _on_btn_toggled(self, btn: QPushButton, checked: bool) -> None:
        if checked:
            self.animate_to_button(btn)

    def _is_animating(self) -> bool:
        state_cls = getattr(QPropertyAnimation, "State", QPropertyAnimation)
        return self._anim.state() == getattr(state_cls, "Running", 2)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._geom_initialized:
            self.snap_to_checked()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if not self._is_animating():
            self.snap_to_checked()

    def event(self, event) -> bool:
        handled = super().event(event)
        layout_req = getattr(QEvent.Type, "LayoutRequest", getattr(QEvent, "LayoutRequest", None))
        if layout_req is not None and event.type() == layout_req and not self._is_animating():
            self.snap_to_checked()
        return handled

    def paintEvent(self, event) -> None:
        if self.width() <= 4 or self.height() <= 4:
            return

        painter = QPainter(self)
        painter.setRenderHint(_RENDER_AA, True)

        # 1. Outer translucent glass capsule track
        track_rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        track_r = track_rect.height() * 0.5
        track_grad = QLinearGradient(0.0, track_rect.top(), 0.0, track_rect.bottom())
        track_grad.setColorAt(0.0, QColor(24, 31, 46, 235))
        track_grad.setColorAt(1.0, QColor(12, 17, 28, 240))
        painter.setBrush(track_grad)
        painter.setPen(QPen(QColor(148, 163, 184, 52), 1.0))
        painter.drawRoundedRect(track_rect, track_r, track_r)

        # Subtle bottom rim glint inside outer glass
        painter.setBrush(_NO_BRUSH)
        painter.setPen(QPen(QColor(255, 255, 255, 16), 1.0))
        inner_track = track_rect.adjusted(1.5, 1.5, -1.5, -1.5)
        painter.drawRoundedRect(inner_track, track_r - 1.5, track_r - 1.5)

        # Ensure initial span even if resize happened right before first paint
        if not self._geom_initialized or self._cur_right <= self._cur_left:
            span = self._target_span_for(self._active_button())
            if span is None:
                return
            self._cur_left, self._cur_right = span
            self._start_left, self._start_right = span
            self._target_left, self._target_right = span
            self._geom_initialized = True

        # 2. Compute liquid stretch & waist pinch during droplet flow
        span_dist = abs(self._target_left - self._start_left)
        cur_w = max(12.0, self._cur_right - self._cur_left)
        rest_w = max(12.0, self._target_right - self._target_left)
        extra_stretch = max(0.0, cur_w - max(rest_w, self._start_right - self._start_left))
        t = self._anim_progress
        flow_envelope = math.sin(math.pi * _clamp01(t)) if 0.0 < t < 1.0 else 0.0
        pinch_px = min(4.2, (extra_stretch / max(24.0, span_dist or 40.0)) * 5.2 * flow_envelope + 1.4 * flow_envelope)

        top = float(self._pad)
        h = float(self.height() - self._pad * 2)
        droplet_path = build_liquid_droplet_path(
            left=self._cur_left,
            right=self._cur_right,
            top=top,
            height=h,
            pinch_px=pinch_px,
        )

        # 3. Soft outer cyan/blue liquid glow underneath the droplet
        glow_path = build_liquid_droplet_path(
            left=self._cur_left - 1.5,
            right=self._cur_right + 1.5,
            top=top + 0.8,
            height=h + 0.5,
            pinch_px=max(0.0, pinch_px - 0.5),
        )
        glow_col = QColor(self._accent)
        glow_col.setAlpha(68)
        painter.setPen(_NO_PEN)
        painter.setBrush(glow_col)
        painter.drawPath(glow_path)

        # 4. Main translucent water-droplet body gradient (deep refracted blue for high text contrast)
        c_top = self._accent.lighter(115)
        c_top.setAlpha(230)
        c_mid = QColor(self._accent_dark)
        c_mid.setAlpha(232)
        c_bot = self._accent_dark.darker(135)
        c_bot.setAlpha(245)

        body_grad = QLinearGradient(0.0, top, 0.0, top + h)
        body_grad.setColorAt(0.0, c_top)
        body_grad.setColorAt(0.50, c_mid)
        body_grad.setColorAt(1.0, c_bot)
        painter.setBrush(body_grad)
        painter.setPen(_NO_PEN)
        painter.drawPath(droplet_path)

        # 5. Specular water highlight & caustic reflection clipped cleanly inside droplet
        painter.save()
        painter.setClipPath(droplet_path)

        # Crisp upper meniscus highlight arc kept above glyph center
        gloss_rect = QRectF(self._cur_left + 5.0, top + 1.0, max(8.0, cur_w - 10.0), h * 0.34)
        gloss_grad = QLinearGradient(0.0, gloss_rect.top(), 0.0, gloss_rect.bottom())
        gloss_grad.setColorAt(0.0, QColor(255, 255, 255, 98))
        gloss_grad.setColorAt(0.55, QColor(224, 242, 254, 28))
        gloss_grad.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setBrush(gloss_grad)
        painter.drawRoundedRect(gloss_rect, h * 0.17, h * 0.17)

        # Subtle localized specular sheen near the leading crown of the droplet
        gleam_x = self._cur_right - min(18.0, cur_w * 0.25) if self._moving_right else self._cur_left + min(18.0, cur_w * 0.25)
        gleam_grad = QRadialGradient(QPointF(gleam_x, top + 4.0), 16.0)
        gleam_grad.setColorAt(0.0, QColor(255, 255, 255, 65))
        gleam_grad.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setBrush(gleam_grad)
        painter.drawRect(QRectF(self._cur_left, top, cur_w, h * 0.55))

        # Soft caustic glow at the bottom edge of the droplet (light refracting out)
        caustic_center = QPointF((self._cur_left + self._cur_right) * 0.5, top + h - 1.5)
        caustic_grad = QRadialGradient(caustic_center, max(16.0, cur_w * 0.45))
        caustic_grad.setColorAt(0.0, QColor(147, 197, 253, 88))
        caustic_grad.setColorAt(1.0, QColor(59, 130, 246, 0))
        painter.setBrush(caustic_grad)
        painter.drawRect(QRectF(self._cur_left, top + h * 0.55, cur_w, h * 0.50))

        painter.restore()

        # 6. Crisp liquid meniscus rim stroke (brighter at top, deeper at bottom)
        rim_grad = QLinearGradient(0.0, top, 0.0, top + h)
        rim_grad.setColorAt(0.0, QColor(224, 242, 254, 195))
        rim_grad.setColorAt(0.5, QColor(147, 197, 253, 120))
        rim_grad.setColorAt(1.0, QColor(30, 64, 175, 170))
        painter.setBrush(_NO_BRUSH)
        painter.setPen(QPen(rim_grad, 1.15))
        painter.drawPath(droplet_path)
