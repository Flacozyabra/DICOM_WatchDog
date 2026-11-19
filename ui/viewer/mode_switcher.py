from __future__ import annotations

import math

from PyQt6.QtCore import (
    QEasingCurve,
    QEvent,
    QPointF,
    QPropertyAnimation,
    QRectF,
    Qt,
    pyqtProperty,
)
from PyQt6.QtGui import (
    QColor,
    QFont,
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
_RENDER_TEXT_AA = (
    QPainter.RenderHint.TextAntialiasing
    if hasattr(QPainter, "RenderHint")
    else QPainter.TextAntialiasing
)
_ALIGN_CENTER = (
    Qt.AlignmentFlag.AlignCenter
    if hasattr(Qt, "AlignmentFlag")
    else Qt.AlignCenter
)
_NO_PEN = Qt.PenStyle.NoPen if hasattr(Qt, "PenStyle") else Qt.NoPen
_NO_BRUSH = Qt.BrushStyle.NoBrush if hasattr(Qt, "BrushStyle") else Qt.NoBrush
_EASE_LINEAR = QEasingCurve.Type.Linear if hasattr(QEasingCurve, "Type") else QEasingCurve.Linear
_FONT_DEMIBOLD = (
    QFont.Weight.DemiBold
    if hasattr(QFont, "Weight")
    else 63
)


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)


def _ease_front(t: float) -> float:
    """Fast front edge that settles with a damped liquid oscillation."""
    t = _clamp01(t)
    if t < 0.68:
        u = t / 0.68
        inv = 1.0 - u
        return 1.0 - inv * inv * inv
    # Damped harmonic overshoot & settle after landing
    p = (t - 0.68) / 0.32
    return 1.0 + 0.055 * math.sin(p * math.pi * 2.2) * math.exp(-3.4 * p)


def _ease_back(t: float) -> float:
    """Viscous trailing edge that clings briefly before flowing into target."""
    t = _clamp01(t)
    if t < 0.14:
        return 0.015 * (t / 0.14)
    if t < 0.78:
        u = (t - 0.14) / 0.64
        return 0.015 + 0.985 * (u * u * (3.0 - 2.0 * u))
    p = (t - 0.78) / 0.22
    return 1.0 - 0.032 * math.sin(p * math.pi * 1.8) * math.exp(-3.0 * p)


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
    pinch = max(-1.5, min(r * 0.44, pinch_px))
    k = 0.55228475

    path.moveTo(inner_l, top)

    span = inner_r - inner_l
    if span > 0.5:
        if abs(pinch) > 0.04:
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

    # Right semi-circle cap
    path.cubicTo(inner_r + cap_r * k, top, right, mid_y - r * k, right, mid_y)
    path.cubicTo(right, mid_y + r * k, inner_r + cap_r * k, bot, inner_r, bot)

    # Bottom edge
    if span > 0.5:
        if abs(pinch) > 0.04:
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

    # Left semi-circle cap
    path.cubicTo(inner_l - cap_r * k, bot, left, mid_y + r * k, left, mid_y)
    path.cubicTo(left, mid_y - r * k, inner_l - cap_r * k, top, inner_l, top)

    path.closeSubpath()
    return path


class LiquidModeBar(QWidget):
    """
    Liquid-glass segmented mode bar: keeps native QPushButtons for layout, clicks,
    translations and context menus while rendering refracted water optics, optical
    text magnification under the droplet, caustic floor lighting, cursor glow,
    tactile press squish, and trailing satellite micro-droplets.
    """

    def __init__(self, buttons: list[QPushButton], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._buttons: list[QPushButton] = list(buttons)
        self._pad = 4
        self._btn_h = 28
        self._accent = QColor("#3B82F6")
        self._accent_dark = QColor("#1D4ED8")
        self._text_muted = QColor("#94A3B8")

        self._hover_pos: QPointF | None = None
        self._hover_btn_idx: int = -1
        self._press_depth: float = 0.0

        self._btn_group = QButtonGroup(self)
        self._btn_group.setExclusive(True)

        self.setMouseTracking(True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(self._pad, self._pad, self._pad, self._pad)
        layout.setSpacing(4)

        btn_font = QFont("Segoe UI")
        btn_font.setPixelSize(12)
        btn_font.setWeight(_FONT_DEMIBOLD)

        for idx, btn in enumerate(self._buttons):
            btn.setParent(self)
            btn.setFlat(True)
            btn.setFont(btn_font)
            btn.setFixedHeight(self._btn_h)
            btn.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            btn.setMouseTracking(True)
            btn.installEventFilter(self)
            layout.addWidget(btn)
            self._btn_group.addButton(btn, idx)
            btn.toggled.connect(lambda checked, b=btn: self._on_btn_toggled(b, checked))

        self.setFixedHeight(self._btn_h + self._pad * 2)
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
        self._anim.setDuration(440)
        self._anim.setEasingCurve(_EASE_LINEAR)

        self._press_anim = QPropertyAnimation(self, b"press_depth", self)
        self._press_anim.setDuration(110)

        self._apply_button_styles()

    def set_theme_colors(self, accent: str, accent_dark: str, text_muted: str = "#9CA3AF") -> None:
        self._accent = QColor(accent or "#3B82F6")
        self._accent_dark = QColor(accent_dark or "#1D4ED8")
        self._text_muted = QColor(text_muted or "#94A3B8")
        self._apply_button_styles()
        self.update()

    def _apply_button_styles(self) -> None:
        # Text is rendered cleanly by LiquidModeBar (base + lens-magnified clip inside droplet)
        btn_style = """
            QPushButton {
                background: transparent;
                border: none;
                border-radius: 14px;
                color: transparent;
                padding: 0px 15px;
            }
            QPushButton:hover {
                background: transparent;
                color: transparent;
            }
            QPushButton:pressed {
                background: transparent;
                color: transparent;
            }
            QPushButton:checked {
                background: transparent;
                color: transparent;
            }
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

    @pyqtProperty(float)
    def press_depth(self) -> float:
        return self._press_depth

    @press_depth.setter
    def press_depth(self, value: float) -> None:
        self._press_depth = float(value)
        self.update()

    def _animate_press(self, pressed: bool) -> None:
        self._press_anim.stop()
        self._press_anim.setStartValue(self._press_depth)
        self._press_anim.setEndValue(1.0 if pressed else 0.0)
        self._press_anim.start()

    def eventFilter(self, obj, event) -> bool:
        if obj in self._buttons:
            et = event.type()
            enter_t = getattr(QEvent.Type, "Enter", getattr(QEvent, "Enter", None))
            hover_t = getattr(QEvent.Type, "HoverMove", getattr(QEvent, "HoverMove", None))
            mouse_move_t = getattr(QEvent.Type, "MouseMove", getattr(QEvent, "MouseMove", None))
            leave_t = getattr(QEvent.Type, "Leave", getattr(QEvent, "Leave", None))
            press_t = getattr(QEvent.Type, "MouseButtonPress", getattr(QEvent, "MouseButtonPress", None))
            release_t = getattr(QEvent.Type, "MouseButtonRelease", getattr(QEvent, "MouseButtonRelease", None))

            idx = self._buttons.index(obj)
            if et in (enter_t, hover_t, mouse_move_t):
                self._hover_btn_idx = idx
                if hasattr(event, "position"):
                    local_pt = event.position()
                elif hasattr(event, "pos"):
                    local_pt = QPointF(event.pos())
                else:
                    local_pt = QPointF(obj.width() * 0.5, obj.height() * 0.5)
                mapped = obj.mapTo(self, local_pt.toPoint())
                self._hover_pos = QPointF(mapped)
                self.update()
            elif et == leave_t:
                if self._hover_btn_idx == idx:
                    self._hover_btn_idx = -1
                if not self.rect().contains(self.mapFromGlobal(self.cursor().pos())):
                    self._hover_pos = None
                self.update()
            elif et == press_t:
                l_btn = Qt.MouseButton.LeftButton if hasattr(Qt, "MouseButton") else Qt.LeftButton
                if event.button() == l_btn:
                    self._animate_press(True)
            elif et == release_t:
                self._animate_press(False)
        return super().eventFilter(obj, event)

    def leaveEvent(self, event) -> None:
        self._hover_pos = None
        self._hover_btn_idx = -1
        self._animate_press(False)
        super().leaveEvent(event)

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

    def _draw_droplet_optics(
        self,
        painter: QPainter,
        droplet_path: QPainterPath,
        left: float,
        right: float,
        top: float,
        height: float,
        pinch_px: float,
    ) -> None:
        cur_w = max(8.0, right - left)

        # Soft ambient glow underneath droplet
        glow_path = build_liquid_droplet_path(left - 2.0, right + 2.0, top + 0.8, height + 0.8, max(0.0, pinch_px - 0.5))
        glow_col = QColor(self._accent)
        glow_col.setAlpha(72)
        painter.setPen(_NO_PEN)
        painter.setBrush(glow_col)
        painter.drawPath(glow_path)

        # Contact shadow on the glass track directly below the droplet
        shadow_path = build_liquid_droplet_path(left + 1.0, right - 1.0, top + 1.6, height, pinch_px)
        painter.setBrush(QColor(0, 0, 0, 95))
        painter.drawPath(shadow_path)

        # Translucent water-glass body: lighter aquamarine tint at crown -> deep sapphire rim below
        c_top = QColor(56, 189, 248, 175)
        c_mid = QColor(self._accent)
        c_mid.setAlpha(155)
        c_bot = QColor(self._accent_dark)
        c_bot.setAlpha(215)

        body_grad = QLinearGradient(0.0, top, 0.0, top + height)
        body_grad.setColorAt(0.0, c_top)
        body_grad.setColorAt(0.42, c_mid)
        body_grad.setColorAt(1.0, c_bot)
        painter.setBrush(body_grad)
        painter.drawPath(droplet_path)

        # Inner volumetric Fresnel crescent & bottom caustic clipped strictly to droplet shape
        painter.save()
        painter.setClipPath(droplet_path)

        # Center translucency lift (bright water core so magnified text pops cleanly)
        core_center = QPointF((left + right) * 0.5, top + height * 0.45)
        core_grad = QRadialGradient(core_center, max(18.0, cur_w * 0.55))
        core_grad.setColorAt(0.0, QColor(96, 165, 250, 75))
        core_grad.setColorAt(0.75, QColor(37, 99, 235, 30))
        core_grad.setColorAt(1.0, QColor(30, 64, 175, 115))
        painter.setBrush(core_grad)
        painter.drawRect(QRectF(left - 2.0, top - 2.0, cur_w + 4.0, height + 4.0))

        # Curved top Fresnel specular arc built by subtracting shifted inner droplet path
        inner_cut = build_liquid_droplet_path(
            left + 2.5,
            right - 2.5,
            top + 3.2,
            max(6.0, height - 3.6),
            pinch_px * 0.85,
        )
        crescent_path = droplet_path.subtracted(inner_cut)
        crescent_grad = QLinearGradient(0.0, top, 0.0, top + height * 0.55)
        crescent_grad.setColorAt(0.0, QColor(255, 255, 255, 175))
        crescent_grad.setColorAt(0.55, QColor(186, 230, 253, 65))
        crescent_grad.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setBrush(crescent_grad)
        painter.drawPath(crescent_path)

        # Tiny specular crown reflection near leading meniscus edge
        gleam_x = right - min(16.0, cur_w * 0.22) if self._moving_right else left + min(16.0, cur_w * 0.22)
        gleam_grad = QRadialGradient(QPointF(gleam_x, top + 3.5), 14.0)
        gleam_grad.setColorAt(0.0, QColor(255, 255, 255, 130))
        gleam_grad.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setBrush(gleam_grad)
        painter.drawRect(QRectF(left, top, cur_w, height * 0.5))

        # Bottom refractive cyan glint inside lower meniscus
        low_glint_center = QPointF((left + right) * 0.5, top + height - 1.0)
        low_grad = QRadialGradient(low_glint_center, max(16.0, cur_w * 0.42))
        low_grad.setColorAt(0.0, QColor(125, 211, 252, 110))
        low_grad.setColorAt(1.0, QColor(59, 130, 246, 0))
        painter.setBrush(low_grad)
        painter.drawRect(QRectF(left, top + height * 0.55, cur_w, height * 0.5))

        painter.restore()

        # Crisp liquid edge rim (bright cyan-white crown -> soft sapphire base)
        rim_grad = QLinearGradient(0.0, top, 0.0, top + height)
        rim_grad.setColorAt(0.0, QColor(224, 242, 254, 225))
        rim_grad.setColorAt(0.45, QColor(125, 211, 252, 135))
        rim_grad.setColorAt(1.0, QColor(56, 189, 248, 175))
        painter.setBrush(_NO_BRUSH)
        painter.setPen(QPen(rim_grad, 1.1))
        painter.drawPath(droplet_path)

    def paintEvent(self, event) -> None:
        if self.width() <= 4 or self.height() <= 4:
            return

        painter = QPainter(self)
        painter.setRenderHint(_RENDER_AA, True)
        painter.setRenderHint(_RENDER_TEXT_AA, True)

        # Ensure geometry initialized before painting
        if not self._geom_initialized or self._cur_right <= self._cur_left:
            span = self._target_span_for(self._active_button())
            if span is None:
                return
            self._cur_left, self._cur_right = span
            self._start_left, self._start_right = span
            self._target_left, self._target_right = span
            self._geom_initialized = True

        # 1. Outer dark translucent glass track
        track_rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        track_r = track_rect.height() * 0.5
        track_grad = QLinearGradient(0.0, track_rect.top(), 0.0, track_rect.bottom())
        track_grad.setColorAt(0.0, QColor(15, 23, 42, 235))
        track_grad.setColorAt(1.0, QColor(8, 13, 24, 242))
        painter.setBrush(track_grad)
        painter.setPen(QPen(QColor(148, 163, 184, 58), 1.0))
        painter.drawRoundedRect(track_rect, track_r, track_r)

        # Track subtle top glass reflection
        track_path = QPainterPath()
        track_path.addRoundedRect(track_rect, track_r, track_r)
        painter.save()
        painter.setClipPath(track_path)

        # Interactive cursor light follower under the glass track
        if self._hover_pos is not None:
            h_grad = QRadialGradient(self._hover_pos, 48.0)
            h_grad.setColorAt(0.0, QColor(186, 230, 253, 42))
            h_grad.setColorAt(1.0, QColor(59, 130, 246, 0))
            painter.setPen(_NO_PEN)
            painter.setBrush(h_grad)
            painter.drawRect(track_rect)

        # Compute droplet deformation: stretch during flight, damped wobble on landing, press squish
        span_dist = abs(self._target_left - self._start_left)
        cur_w = max(12.0, self._cur_right - self._cur_left)
        rest_w = max(12.0, self._target_right - self._target_left)
        extra_stretch = max(0.0, cur_w - max(rest_w, self._start_right - self._start_left))
        t = self._anim_progress

        if 0.0 < t < 0.72:
            env = math.sin(math.pi * (t / 0.72))
            pinch_px = min(4.5, (extra_stretch / max(24.0, span_dist or 40.0)) * 5.0 * env + 1.3 * env)
            bulge_y = -0.35 * env
        elif 0.72 <= t < 1.0:
            p = (t - 0.72) / 0.28
            osc = math.sin(p * math.pi * 2.2) * math.exp(-3.2 * p)
            pinch_px = -0.9 * osc
            bulge_y = 0.75 * osc
        else:
            pinch_px = 0.0
            bulge_y = 0.0

        press_x = 1.6 * self._press_depth
        press_y = -1.1 * self._press_depth
        d_left = max(float(self._pad) - 1.0, self._cur_left - press_x)
        d_right = min(float(self.width() - self._pad) + 1.0, self._cur_right + press_x)
        d_top = float(self._pad) - press_y - bulge_y * 0.5
        d_h = float(self._btn_h) + press_y * 2.0 + bulge_y

        # Light focused through water lens onto the dark track floor directly beneath the droplet
        caustic_floor = QRadialGradient(
            QPointF((d_left + d_right) * 0.5, d_top + d_h * 0.78),
            max(22.0, (d_right - d_left) * 0.62),
        )
        caustic_floor.setColorAt(0.0, QColor(56, 189, 248, 82 + int(35 * self._press_depth)))
        caustic_floor.setColorAt(0.65, QColor(37, 99, 235, 28))
        caustic_floor.setColorAt(1.0, QColor(15, 23, 42, 0))
        painter.setPen(_NO_PEN)
        painter.setBrush(caustic_floor)
        painter.drawRect(track_rect)

        # Subtle wet-glass trail shimmer beneath text when leaping across buttons
        if span_dist > 20.0 and 0.08 < t < 0.65:
            trail_alpha = int(52 * math.sin(math.pi * ((t - 0.08) / 0.57)))
            trail_cx = (self._start_left + self._start_right) * 0.5
            trail_r = max(24.0, (self._start_right - self._start_left) * 0.55)
            trail_grad = QRadialGradient(QPointF(trail_cx, d_top + d_h * 0.5), trail_r)
            trail_grad.setColorAt(0.0, QColor(96, 165, 250, trail_alpha))
            trail_grad.setColorAt(1.0, QColor(37, 99, 235, 0))
            painter.setBrush(trail_grad)
            painter.drawRect(track_rect)

        painter.restore()

        droplet_path = build_liquid_droplet_path(d_left, d_right, d_top, d_h, pinch_px)

        # 2. Layer A: Muted labels strictly OUTSIDE the droplet (prevents ghosting under refraction)
        painter.save()
        painter.setClipPath(track_path.subtracted(droplet_path))
        for idx, btn in enumerate(self._buttons):
            painter.setFont(btn.font())
            base_col = QColor("#E2E8F0") if idx == self._hover_btn_idx else self._text_muted
            painter.setPen(base_col)
            painter.drawText(QRectF(btn.geometry()), _ALIGN_CENTER, btn.text())
        painter.restore()

        # 3. Main liquid-glass droplet body + Fresnel specular highlights
        self._draw_droplet_optics(painter, droplet_path, d_left, d_right, d_top, d_h, pinch_px)

        # 5. Layer B: Optically magnified crisp white text clipped strictly inside the droplet lens
        painter.save()
        painter.setClipPath(droplet_path)
        lens_cx = (d_left + d_right) * 0.5
        lens_cy = d_top + d_h * 0.5
        for btn in self._buttons:
            rect = QRectF(btn.geometry())
            btn_cx = rect.center().x()
            # Refract slightly outward toward droplet optical center and magnify ~1.055x
            refract_shift = max(-2.2, min(2.2, (lens_cx - btn_cx) * 0.035))
            painter.save()
            painter.translate(btn_cx + refract_shift, lens_cy)
            painter.scale(1.055 - 0.025 * self._press_depth, 1.055 - 0.025 * self._press_depth)
            painter.translate(-btn_cx, -lens_cy)
            painter.setFont(btn.font())
            # Subtle dark halo behind white letters inside glass for effortless legibility
            painter.setPen(QColor(8, 25, 65, 95))
            painter.drawText(rect.translated(0.0, 0.6), _ALIGN_CENTER, btn.text())
            painter.setPen(QColor("#FFFFFF"))
            painter.drawText(rect, _ALIGN_CENTER, btn.text())
            painter.restore()
        painter.restore()
