from __future__ import annotations

import math
import numpy as np

from PyQt6.QtCore import (
    QEasingCurve,
    QEvent,
    QPointF,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
    pyqtProperty,
)
from PyQt6.QtGui import (
    QColor,
    QFont,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
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
_RENDER_SMOOTH = (
    QPainter.RenderHint.SmoothPixmapTransform
    if hasattr(QPainter, "RenderHint")
    else QPainter.SmoothPixmapTransform
)
_ALIGN_CENTER = (
    Qt.AlignmentFlag.AlignCenter
    if hasattr(Qt, "AlignmentFlag")
    else Qt.AlignCenter
)
_FMT_ARGB32 = (
    QImage.Format.Format_ARGB32
    if hasattr(QImage, "Format")
    else QImage.Format_ARGB32
)
_FMT_RGBA8888 = (
    QImage.Format.Format_RGBA8888
    if hasattr(QImage, "Format")
    else QImage.Format_RGBA8888
)
_EASE_LINEAR = QEasingCurve.Type.Linear if hasattr(QEasingCurve, "Type") else QEasingCurve.Linear
_FONT_DEMIBOLD = QFont.Weight.DemiBold if hasattr(QFont, "Weight") else 63
_QSP_PREF = QSizePolicy.Policy.Preferred if hasattr(QSizePolicy, "Policy") else QSizePolicy.Preferred
_QSP_FIXED = QSizePolicy.Policy.Fixed if hasattr(QSizePolicy, "Policy") else QSizePolicy.Fixed
_QSP_MAX = QSizePolicy.Policy.Maximum if hasattr(QSizePolicy, "Policy") else QSizePolicy.Maximum


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)


def _ease_front(t: float) -> float:
    """Fast front edge surge with clean elastic bounce on target landing."""
    t = _clamp01(t)
    if t < 0.56:
        u = t / 0.56
        inv = 1.0 - u
        return 1.0 - inv * inv * inv
    p = (t - 0.56) / 0.44
    return 1.0 + 0.065 * math.sin(p * math.pi * 2.4) * math.exp(-2.8 * p)


def _ease_back(t: float) -> float:
    """Viscous trailing edge that clings cleanly so even adjacent moves stretch deeply."""
    t = _clamp01(t)
    if t < 0.24:
        # Hold tail briefly at origin to stretch droplet waist dramatically
        return 0.03 * (t / 0.24) ** 2
    if t < 0.78:
        u = (t - 0.24) / 0.54
        return 0.03 + 0.97 * (u * u * (3.0 - 2.0 * u))
    p = (t - 0.78) / 0.22
    return 1.0 - 0.055 * math.sin(p * math.pi * 2.0) * math.exp(-2.6 * p)


def _sd_capsule_vec(
    gx: np.ndarray,
    gy: np.ndarray,
    left: float,
    right: float,
    cy: float,
    radius: float,
) -> np.ndarray:
    """Exact vectorized 2D signed distance to a horizontal stadium capsule."""
    r = max(2.0, float(radius))
    ax = min((left + right) * 0.5, float(left) + r)
    bx = max((left + right) * 0.5, float(right) - r)
    dx = np.maximum(0.0, np.maximum(ax - gx, gx - bx)).astype(np.float32)
    dy = (gy - np.float32(cy)).astype(np.float32)
    return np.sqrt(dx * dx + dy * dy) - np.float32(r)


def _smin_poly(a: np.ndarray, b: np.ndarray, k: float) -> np.ndarray:
    """Polynomial smooth-minimum (Inigo Quilez) for viscous metaball droplet blending."""
    kf = np.float32(max(0.5, k))
    h = np.clip(np.float32(0.5) + np.float32(0.5) * (b - a) / kf, np.float32(0.0), np.float32(1.0))
    return b * (np.float32(1.0) - h) + a * h - kf * h * (np.float32(1.0) - h)


def _qimage_to_rgba_float(img: QImage) -> np.ndarray:
    """Convert QImage into float32 (H, W, 4) in [0..1] RGBA order."""
    rgba_img = img.convertToFormat(_FMT_RGBA8888)
    w, h = rgba_img.width(), rgba_img.height()
    ptr = rgba_img.constBits()
    ptr.setsize(h * rgba_img.bytesPerLine())
    arr = np.frombuffer(ptr, dtype=np.uint8).reshape(h, rgba_img.bytesPerLine() // 4, 4)[:, :w, :]
    return arr.astype(np.float32) * np.float32(1.0 / 255.0)


def _sample_bilinear_rgb_dispersed(
    tex: np.ndarray,
    gx: np.ndarray,
    gy: np.ndarray,
    shift_x: np.ndarray,
    shift_y: np.ndarray,
    mag_dx: np.ndarray,
    mag_dy: np.ndarray,
) -> np.ndarray:
    """Refract backdrop through liquid lens with visible magnification & R/G/B dispersion."""
    h, w, _ = tex.shape
    sh, sw = gx.shape
    out = np.empty((sh, sw, 3), dtype=np.float32)
    etas = (np.float32(0.76), np.float32(1.00), np.float32(1.30))
    for ch, eta in enumerate(etas):
        sx = np.clip(gx - mag_dx + shift_x * eta, np.float32(0.0), np.float32(w - 1.001))
        sy = np.clip(gy - mag_dy + shift_y * eta, np.float32(0.0), np.float32(h - 1.001))
        x0 = np.floor(sx).astype(np.intp)
        y0 = np.floor(sy).astype(np.intp)
        x1 = np.minimum(x0 + 1, w - 1)
        y1 = np.minimum(y0 + 1, h - 1)
        fx = sx - x0
        fy = sy - y0
        omx = np.float32(1.0) - fx
        omy = np.float32(1.0) - fy
        plane = tex[..., ch]
        out[..., ch] = (
            plane[y0, x0] * (omx * omy)
            + plane[y0, x1] * (fx * omy)
            + plane[y1, x0] * (omx * fy)
            + plane[y1, x1] * (fx * fy)
        )
    return out


class LiquidModeBar(QWidget):
    """
    Real-time 2D Shallow-Water Wave & Liquid Glass mode switcher:
    - Isotropic 2D damped wave equation eta(y, x) running at ~60 FPS only while active
      so pointer clicks, drags, and collisions send concentric ripples across the lens
      and asymmetrically deflect top/bottom meniscus edges.
    - Viscous metaball break-up & coalescence (smin satellite droplet on long jumps),
      inertial top-ridge slosh, and interactive drag-to-slide gesture.
    - Native DPR rendering (1x crisp glyph clarity on 1080p, 2x on HiDPI)
      with capped optical magnification, R/G/B dispersion, Fresnel & Blinn-Phong speculars.
    """

    def __init__(self, buttons: list[QPushButton], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._buttons: list[QPushButton] = list(buttons)
        self._pad = 4
        self._btn_h = 28
        self._accent = QColor("#3B82F6")
        self._accent_dark = QColor("#1D4ED8")
        self._text_muted = QColor("#8B9BB4")

        self._hover_pos: QPointF | None = None
        self._hover_btn_idx: int = -1
        self._press_depth: float = 0.0
        self._ss = 1

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
            btn.setSizePolicy(_QSP_PREF, _QSP_FIXED)
            btn.setMouseTracking(True)
            btn.installEventFilter(self)
            layout.addWidget(btn)
            self._btn_group.addButton(btn, idx)
            btn.toggled.connect(lambda checked, b=btn: self._on_btn_toggled(b, checked))

        self.setFixedHeight(self._btn_h + self._pad * 2)
        self.setSizePolicy(_QSP_MAX, _QSP_FIXED)

        self._cur_left = 0.0
        self._cur_right = 0.0
        self._start_left = 0.0
        self._start_right = 0.0
        self._target_left = 0.0
        self._target_right = 0.0
        self._moving_right = True
        self._anim_progress = 1.0
        self._geom_initialized = False
        self._impact_injected = True
        self._satellite_merged = True

        # Interactive drag-to-slide and volume slosh inertia state
        self._press_btn: QPushButton | None = None
        self._press_start_pos: QPointF | None = None
        self._press_in_droplet = False
        self._dragging = False
        self._drag_offset_x = 0.0
        self._drag_width = 0.0
        self._last_drag_x = 0.0
        self._slosh_inertia = 0.0
        self._prev_mid_x = 0.0

        # 2D shallow-water height & velocity fields (sh, sw) across droplet surface
        self._wave_eta: np.ndarray | None = None
        self._wave_vel: np.ndarray | None = None

        # Cached shader grids & backdrops
        self._cache_key: tuple | None = None
        self._gx: np.ndarray | None = None
        self._gy: np.ndarray | None = None
        self._track_sd: np.ndarray | None = None
        self._track_cov: np.ndarray | None = None
        self._bg_muted: np.ndarray | None = None
        self._bg_bright: np.ndarray | None = None

        self._anim = QPropertyAnimation(self, b"anim_progress", self)
        self._anim.setDuration(420)
        self._anim.setEasingCurve(_EASE_LINEAR)

        self._press_anim = QPropertyAnimation(self, b"press_depth", self)
        self._press_anim.setDuration(95)

        self._wave_timer = QTimer(self)
        self._wave_timer.setInterval(16)
        self._wave_timer.timeout.connect(self._step_shallow_water_wave)

        self._apply_button_styles()

    def set_theme_colors(self, accent: str, accent_dark: str, text_muted: str = "#8B9BB4") -> None:
        self._accent = QColor(accent or "#3B82F6")
        self._accent_dark = QColor(accent_dark or "#1D4ED8")
        self._text_muted = QColor(text_muted or "#8B9BB4")
        self._invalidate_backdrop_cache()
        self._apply_button_styles()
        self.update()

    def _invalidate_backdrop_cache(self) -> None:
        self._cache_key = None

    def _apply_button_styles(self) -> None:
        btn_style = """
            QPushButton {
                background: transparent;
                border: none;
                border-radius: 14px;
                color: transparent;
                padding: 0px 15px;
            }
            QPushButton:hover, QPushButton:pressed, QPushButton:checked {
                background: transparent;
                color: transparent;
            }
        """
        for btn in self._buttons:
            btn.setStyleSheet(btn_style)

    def _ensure_wave_buffers(self, sw: int, sh: int) -> None:
        if (
            self._wave_eta is None
            or self._wave_vel is None
            or self._wave_eta.shape != (sh, sw)
        ):
            self._wave_eta = np.zeros((sh, sw), dtype=np.float32)
            self._wave_vel = np.zeros((sh, sw), dtype=np.float32)

    def _inject_wave_impulse(
        self,
        center_x_px: float,
        amplitude: float,
        sigma_px: float = 10.0,
        center_y_px: float | None = None,
    ) -> None:
        """Inject a localized 2D radial wave impulse into the water surface solver."""
        ss = self._ss
        sw = max(16, self.width() * ss)
        sh = max(8, self.height() * ss)
        self._ensure_wave_buffers(sw, sh)

        xs = np.arange(sw, dtype=np.float32) + np.float32(0.5)
        ys = np.arange(sh, dtype=np.float32) + np.float32(0.5)
        cx = np.float32(center_x_px * ss)
        cy = np.float32((center_y_px if center_y_px is not None else self.height() * 0.5) * ss)
        sig = np.float32(max(2.5, sigma_px * ss))
        inv_2sig2 = np.float32(-1.0) / (np.float32(2.0) * sig * sig)

        dx2 = (xs - cx) ** 2
        dy2 = (ys - cy) ** 2
        pulse = np.float32(amplitude) * np.exp((dy2[:, None] + dx2[None, :]) * inv_2sig2)
        self._wave_vel += pulse
        if not self._wave_timer.isActive() and self.isVisible():
            self._wave_timer.start()

    def _step_shallow_water_wave(self) -> None:
        """Advance damped 2D isotropic shallow-water wave PDE inside current droplet."""
        if self._wave_eta is None or self._wave_vel is None or not self.isVisible():
            self._wave_timer.stop()
            return

        eta = self._wave_eta
        vel = self._wave_vel
        sh, sw = eta.shape
        ss = self._ss
        l_idx = max(1, min(sw - 4, int(self._cur_left * ss)))
        r_idx = max(l_idx + 3, min(sw - 1, int(self._cur_right * ss)))
        t_idx = max(1, min(sh // 2 - 2, int(max(1.0, self._pad * 0.5) * ss)))
        b_idx = max(t_idx + 3, min(sh - 1, sh - int(max(1.0, self._pad * 0.5) * ss)))

        # Decay ripples cleanly outside moving droplet domain
        if l_idx > 0:
            eta[:, :l_idx] *= np.float32(0.45)
            vel[:, :l_idx] *= np.float32(0.45)
        if r_idx < sw:
            eta[:, r_idx:] *= np.float32(0.45)
            vel[:, r_idx:] *= np.float32(0.45)
        if t_idx > 0:
            eta[:t_idx, :] *= np.float32(0.55)
            vel[:t_idx, :] *= np.float32(0.55)
        if b_idx < sh:
            eta[b_idx:, :] *= np.float32(0.55)
            vel[b_idx:, :] *= np.float32(0.55)

        c2 = np.float32(0.14)
        damp = np.float32(0.962)
        ym = slice(t_idx, b_idx)
        yp = slice(t_idx + 1, b_idx + 1)
        yn = slice(t_idx - 1, b_idx - 1)
        xm = slice(l_idx, r_idx)
        xp = slice(l_idx + 1, r_idx + 1)
        xn = slice(l_idx - 1, r_idx - 1)

        for _ in range(2):
            # 9-point isotropic Laplacian gives circular ripples rather than diamond waves
            lap = (
                np.float32(0.20) * (eta[yn, xm] + eta[yp, xm] + eta[ym, xn] + eta[ym, xp])
                + np.float32(0.05) * (eta[yn, xn] + eta[yn, xp] + eta[yp, xn] + eta[yp, xp])
                - eta[ym, xm]
            ) * np.float32(4.0)
            vel[ym, xm] = vel[ym, xm] * damp + c2 * lap
            eta[ym, xm] += vel[ym, xm]
            # Reflective boundary conditions at top/bottom, left/right meniscus walls & corners
            eta[ym, l_idx - 1] = eta[ym, l_idx]
            if r_idx < sw:
                eta[ym, r_idx] = eta[ym, r_idx - 1]
            hi_x = min(sw, r_idx + 1)
            eta[t_idx - 1, l_idx - 1:hi_x] = eta[t_idx, l_idx - 1:hi_x]
            if b_idx < sh:
                eta[b_idx, l_idx - 1:hi_x] = eta[b_idx - 1, l_idx - 1:hi_x]

        np.clip(eta, np.float32(-1.1), np.float32(1.1), out=eta)
        # Decay slosh inertia gently toward zero when settling
        if not self._dragging and not self._is_animating():
            self._slosh_inertia *= 0.80
        energy = float(np.max(np.abs(eta)) + np.max(np.abs(vel)))
        if energy < 2.2e-3 and abs(self._slosh_inertia) < 0.02 and not self._is_animating() and not self._dragging:
            eta.fill(0.0)
            vel.fill(0.0)
            self._slosh_inertia = 0.0
            self._wave_timer.stop()
        self.update()

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

        cur_mid = (self._cur_left + self._cur_right) * 0.5
        if self._prev_mid_x > 0.0:
            delta_mid = cur_mid - self._prev_mid_x
            self._slosh_inertia = max(-1.0, min(1.0, self._slosh_inertia * 0.72 + delta_mid * 0.14))
        self._prev_mid_x = cur_mid

        # Trigger 2D landing splash wave as leading front strikes target edge
        if not self._impact_injected and self._anim_progress >= 0.52:
            self._impact_injected = True
            hit_x = (self._target_right - 10.0) if self._moving_right else (self._target_left + 10.0)
            self._inject_wave_impulse(hit_x, amplitude=0.28, sigma_px=14.0)

        # Trigger secondary coalesce ripple when trailing metaball satellite catches up
        if not self._satellite_merged and self._anim_progress >= 0.68:
            self._satellite_merged = True
            if abs(self._target_left - self._start_left) > max(48.0, float(self.width()) * 0.28):
                merge_x = (self._cur_left + 10.0) if self._moving_right else (self._cur_right - 10.0)
                self._inject_wave_impulse(merge_x, amplitude=0.22, sigma_px=9.5)

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

    def _event_pos_in_self(self, src_obj, event) -> QPointF:
        if hasattr(event, "position"):
            local_pt = event.position()
        elif hasattr(event, "pos"):
            local_pt = QPointF(event.pos())
        else:
            local_pt = QPointF(src_obj.width() * 0.5, src_obj.height() * 0.5)
        if src_obj is self:
            return QPointF(local_pt)
        return QPointF(src_obj.mapTo(self, local_pt.toPoint()))

    def _nearest_button_for_x(self, cx: float) -> QPushButton | None:
        best_btn = None
        best_dist = float("inf")
        for btn in self._buttons:
            if btn.width() <= 0:
                continue
            d = abs(float(btn.geometry().center().x()) - cx)
            if d < best_dist:
                best_dist = d
                best_btn = btn
        return best_btn

    def _update_drag(self, pt: QPointF) -> None:
        dx = float(pt.x() - self._last_drag_x)
        self._last_drag_x = float(pt.x())
        self._hover_pos = QPointF(pt)
        self._slosh_inertia = max(-1.0, min(1.0, self._slosh_inertia * 0.72 + dx * 0.18))

        desired_cx = float(pt.x()) + self._drag_offset_x
        nearest = self._nearest_button_for_x(desired_cx)
        target_w = float(nearest.width()) if nearest is not None else self._drag_width
        # Smoothly adapt lens width as the user drags across narrow/wide mode labels
        self._drag_width = self._drag_width * 0.80 + target_w * 0.20

        min_l = float(self._pad)
        max_r = max(min_l + 24.0, float(self.width() - self._pad))
        half_w = self._drag_width * 0.5
        clamped_cx = max(min_l + half_w, min(max_r - half_w, desired_cx))

        self._cur_left = clamped_cx - half_w
        self._cur_right = clamped_cx + half_w
        self._prev_mid_x = clamped_cx
        if abs(dx) > 1.0:
            self._inject_wave_impulse(
                pt.x(),
                amplitude=max(-0.20, min(0.20, -abs(dx) * 0.03)),
                sigma_px=8.0,
                center_y_px=pt.y(),
            )
        self.update()

    def _finish_drag(self) -> None:
        if self._dragging:
            try:
                self.releaseMouse()
            except Exception:
                pass
        self._dragging = False
        self._press_in_droplet = False
        self._press_btn = None
        self._press_start_pos = None
        self._animate_press(False)

        cx = (self._cur_left + self._cur_right) * 0.5
        target_btn = self._nearest_button_for_x(cx)
        if target_btn is not None:
            if target_btn.isChecked():
                self.animate_to_button(target_btn)
            else:
                target_btn.setChecked(True)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging:
            self._update_drag(self._event_pos_in_self(self, event))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._dragging:
            self._finish_drag()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def hideEvent(self, event) -> None:
        if self._dragging:
            self._dragging = False
            try:
                self.releaseMouse()
            except Exception:
                pass
        super().hideEvent(event)

    def eventFilter(self, obj, event) -> bool:
        if obj in self._buttons:
            et = event.type()
            enter_t = getattr(QEvent.Type, "Enter", getattr(QEvent, "Enter", None))
            hover_t = getattr(QEvent.Type, "HoverMove", getattr(QEvent, "HoverMove", None))
            mouse_move_t = getattr(QEvent.Type, "MouseMove", getattr(QEvent, "MouseMove", None))
            leave_t = getattr(QEvent.Type, "Leave", getattr(QEvent, "Leave", None))
            press_t = getattr(QEvent.Type, "MouseButtonPress", getattr(QEvent, "MouseButtonPress", None))
            release_t = getattr(QEvent.Type, "MouseButtonRelease", getattr(QEvent, "MouseButtonRelease", None))
            l_btn = Qt.MouseButton.LeftButton if hasattr(Qt, "MouseButton") else Qt.LeftButton

            idx = self._buttons.index(obj)
            if et in (enter_t, hover_t, mouse_move_t):
                self._hover_btn_idx = idx
                pt = self._event_pos_in_self(obj, event)
                if self._press_in_droplet and hasattr(event, "buttons") and (event.buttons() & l_btn):
                    if (
                        not self._dragging
                        and self._press_start_pos is not None
                        and abs(pt.x() - self._press_start_pos.x()) > 4.0
                    ):
                        self._dragging = True
                        self._anim.stop()
                        self._anim_progress = 1.0
                        self._impact_injected = True
                        self._satellite_merged = True
                        if self._press_btn is not None:
                            self._press_btn.setDown(False)
                        self._drag_offset_x = (self._cur_left + self._cur_right) * 0.5 - float(self._press_start_pos.x())
                        self._drag_width = max(36.0, self._cur_right - self._cur_left)
                        self._last_drag_x = float(self._press_start_pos.x())
                        if self.isVisible():
                            self.grabMouse()
                    if self._dragging:
                        self._update_drag(pt)
                        return True

                prev_pt = self._hover_pos
                self._hover_pos = pt
                # Subtle radial 2D ripple when cursor sweeps across the active droplet surface
                if (
                    prev_pt is not None
                    and math.hypot(pt.x() - prev_pt.x(), pt.y() - prev_pt.y()) > 3.5
                    and self._cur_left <= pt.x() <= self._cur_right
                ):
                    self._inject_wave_impulse(pt.x(), amplitude=-0.065, sigma_px=7.0, center_y_px=pt.y())
                self.update()
            elif et == leave_t:
                if self._dragging:
                    return True
                if self._hover_btn_idx == idx:
                    self._hover_btn_idx = -1
                if not self.rect().contains(self.mapFromGlobal(self.cursor().pos())):
                    self._hover_pos = None
                self.update()
            elif et == press_t:
                if event.button() == l_btn:
                    pt = self._event_pos_in_self(obj, event)
                    self._animate_press(True)
                    self._inject_wave_impulse(pt.x(), amplitude=-0.26, sigma_px=10.5, center_y_px=pt.y())
                    if self._cur_left - 2.0 <= pt.x() <= self._cur_right + 2.0:
                        self._press_in_droplet = True
                        self._press_btn = obj
                        self._press_start_pos = pt
                        self._last_drag_x = float(pt.x())
                    else:
                        self._press_in_droplet = False
                        self._press_btn = None
            elif et == release_t:
                self._press_in_droplet = False
                self._press_btn = None
                if self._dragging:
                    self._finish_drag()
                    return True
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

    def _is_animating(self) -> bool:
        state_cls = getattr(QPropertyAnimation, "State", QPropertyAnimation)
        return self._anim.state() == getattr(state_cls, "Running", 2)

    def snap_to_checked(self) -> None:
        """Immediately snap the droplet to the active button and settle wave state."""
        self._anim.stop()
        self._wave_timer.stop()
        if self._dragging:
            self._dragging = False
            try:
                self.releaseMouse()
            except Exception:
                pass
        self._press_in_droplet = False
        self._press_btn = None
        self._anim_progress = 1.0
        self._impact_injected = True
        self._satellite_merged = True
        self._slosh_inertia = 0.0
        if self._wave_eta is not None:
            self._wave_eta.fill(0.0)
        if self._wave_vel is not None:
            self._wave_vel.fill(0.0)
        span = self._target_span_for(self._active_button())
        if span is not None:
            self._cur_left, self._cur_right = span
            self._start_left, self._start_right = span
            self._target_left, self._target_right = span
            self._prev_mid_x = (span[0] + span[1]) * 0.5
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
            self._prev_mid_x = (span[0] + span[1]) * 0.5
            self._geom_initialized = True
            self.update()
            return

        new_l, new_r = span
        if abs(new_l - self._cur_left) < 0.5 and abs(new_r - self._cur_right) < 0.5 and not self._is_animating():
            self._target_left, self._target_right = span
            return

        self._anim.stop()
        self._start_left = self._cur_left
        self._start_right = self._cur_right
        self._target_left = new_l
        self._target_right = new_r
        cur_cx = (self._cur_left + self._cur_right) * 0.5
        tgt_cx = (new_l + new_r) * 0.5
        self._prev_mid_x = cur_cx
        self._moving_right = tgt_cx >= cur_cx
        self._impact_injected = False
        self._satellite_merged = False

        # Launch surge 2D wave impulse from the leaving edge of the droplet
        launch_x = (self._cur_right - 10.0) if self._moving_right else (self._cur_left + 10.0)
        self._inject_wave_impulse(launch_x, amplitude=0.24, sigma_px=13.5)

        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()

    def _on_btn_toggled(self, btn: QPushButton, checked: bool) -> None:
        if checked:
            self.animate_to_button(btn)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._geom_initialized:
            self.snap_to_checked()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._invalidate_backdrop_cache()
        if not self._is_animating():
            self.snap_to_checked()

    def event(self, event) -> bool:
        handled = super().event(event)
        layout_req = getattr(QEvent.Type, "LayoutRequest", getattr(QEvent, "LayoutRequest", None))
        if layout_req is not None and event.type() == layout_req:
            self._invalidate_backdrop_cache()
            if not self._is_animating():
                self.snap_to_checked()
        return handled

    def _current_ss(self) -> int:
        try:
            dpr = float(self.devicePixelRatioF())
        except Exception:
            dpr = 1.0
        return max(1, min(2, int(round(dpr))))

    def _render_backdrop_layer(self, bright: bool, ss: int) -> np.ndarray:
        """Render the DPR-native glass track backdrop (floor ribs + labels)."""
        w, h = self.width(), self.height()
        sw, sh = w * ss, h * ss
        img = QImage(sw, sh, _FMT_ARGB32)
        img.fill(Qt.GlobalColor.transparent if hasattr(Qt, "GlobalColor") else 0)

        p = QPainter(img)
        p.setRenderHint(_RENDER_AA, True)
        p.setRenderHint(_RENDER_TEXT_AA, True)
        if ss != 1:
            p.scale(float(ss), float(ss))

        track_rect = QRectF(0.5, 0.5, w - 1.0, h - 1.0)
        track_r = track_rect.height() * 0.5
        clip = QPainterPath()
        clip.addRoundedRect(track_rect, track_r, track_r)
        p.setClipPath(clip)

        floor_grad = QLinearGradient(0.0, 0.0, 0.0, float(h))
        if bright:
            floor_grad.setColorAt(0.0, QColor(14, 24, 46, 255))
            floor_grad.setColorAt(0.55, QColor(12, 20, 38, 255))
            floor_grad.setColorAt(1.0, QColor(18, 32, 60, 255))
        else:
            floor_grad.setColorAt(0.0, QColor(13, 19, 32, 248))
            floor_grad.setColorAt(1.0, QColor(7, 11, 20, 252))
        p.fillRect(track_rect, floor_grad)

        # Fine optical grooves on glass floor so Snell lens distortion is clearly visible even in gaps
        rib_pen = QPen(QColor(148, 163, 184, 24 if bright else 10), 0.8)
        p.setPen(rib_pen)
        x_pos = 12.0
        while x_pos < w - 10.0:
            p.drawLine(QPointF(x_pos, 3.0), QPointF(x_pos, float(h) - 3.0))
            x_pos += 10.0

        p.setPen(QPen(QColor(186, 230, 253, 18 if bright else 7), 0.8))
        p.drawLine(QPointF(8.0, h * 0.5), QPointF(w - 8.0, h * 0.5))

        for btn in self._buttons:
            p.setFont(btn.font())
            if bright:
                p.setPen(QColor(6, 18, 48, 130))
                p.drawText(QRectF(btn.geometry()).translated(0.0, 0.6), _ALIGN_CENTER, btn.text())
                p.setPen(QColor("#FFFFFF"))
                p.drawText(QRectF(btn.geometry()), _ALIGN_CENTER, btn.text())
            else:
                p.setPen(self._text_muted)
                p.drawText(QRectF(btn.geometry()), _ALIGN_CENTER, btn.text())

        p.end()
        return _qimage_to_rgba_float(img)

    def _ensure_shader_cache(self) -> None:
        w, h = self.width(), self.height()
        ss = self._current_ss()
        key = (
            w,
            h,
            ss,
            tuple((b.text(), b.geometry().x(), b.geometry().width()) for b in self._buttons),
            self._accent.name(),
            self._accent_dark.name(),
            self._text_muted.name(),
        )
        if self._cache_key == key and self._bg_muted is not None:
            return

        self._ss = ss
        sw, sh = w * ss, h * ss
        ys = np.arange(sh, dtype=np.float32) + np.float32(0.5)
        xs = np.arange(sw, dtype=np.float32) + np.float32(0.5)
        self._gx, self._gy = np.meshgrid(xs, ys)

        pad_s = 0.5 * ss
        r_s = (h - 1.0) * 0.5 * ss
        self._track_sd = _sd_capsule_vec(self._gx, self._gy, pad_s, sw - pad_s, sh * 0.5, r_s)
        self._track_cov = np.clip(np.float32(0.5) - self._track_sd * np.float32(0.95 / ss), np.float32(0.0), np.float32(1.0))

        self._bg_muted = self._render_backdrop_layer(bright=False, ss=ss)
        rim_band = np.exp(-((self._track_sd + np.float32(1.0 * ss)) ** 2) / np.float32(1.2 * ss * ss))
        top_bias = np.clip(np.float32(1.0) - (self._gy / np.float32(sh)), np.float32(0.15), np.float32(1.0))
        self._bg_muted[..., :3] = np.clip(
            self._bg_muted[..., :3] + (rim_band * top_bias * np.float32(0.24))[..., None] * np.array([0.75, 0.85, 1.0], dtype=np.float32),
            np.float32(0.0),
            np.float32(1.0),
        )
        self._bg_bright = self._render_backdrop_layer(bright=True, ss=ss)
        self._ensure_wave_buffers(sw, sh)
        self._cache_key = key

    def _satellite_state(self) -> tuple[float, float] | None:
        """Compute detached trailing metaball satellite position and radius during long jumps."""
        if self._dragging:
            return None
        t = float(self._anim_progress)
        span_dist = abs(self._target_left - self._start_left)
        if not (0.16 < t < 0.72) or span_dist <= max(48.0, float(self.width()) * 0.28):
            return None
        t_u = (t - 0.16) / 0.56
        r_sat = 4.6 * (math.sin(math.pi * t_u) ** 0.42)
        if r_sat < 1.5:
            return None
        lag_px = (20.0 if span_dist > 95.0 else 13.0) * (math.sin(math.pi * t_u) ** 1.1)
        if self._moving_right:
            sat_cx = max(float(self._pad) + r_sat + 2.0, (self._cur_left + 9.0) - lag_px)
        else:
            sat_cx = min(float(self.width() - self._pad) - r_sat - 2.0, (self._cur_right - 9.0) + lag_px)
        return sat_cx, r_sat

    def _evaluate_liquid_sdf(self) -> tuple[np.ndarray, float, np.ndarray]:
        """Compute waist-modulated metaball droplet SDF, inertial slosh, and 2D wave surface."""
        ss = self._ss
        gx, gy = self._gx, self._gy
        sh, sw = gx.shape
        h = float(self.height())
        cy = np.float32(h * 0.5 * ss)
        t = float(self._anim_progress)

        press_x = 1.8 * self._press_depth
        press_y = -1.2 * self._press_depth

        # Visible elastic squash-and-stretch on landing (0.56..1.0)
        if 0.56 <= t < 1.0 and not self._dragging:
            p = (t - 0.56) / 0.44
            wobble = math.sin(p * math.pi * 2.4) * math.exp(-2.8 * p)
        else:
            wobble = 0.0

        r_base = (self._btn_h * 0.5 + press_y + 1.35 * wobble) * ss
        l_main = max(float(self._pad) - 1.0, self._cur_left - press_x) * ss
        r_main = min(float(self.width() - self._pad) + 1.0, self._cur_right + press_x) * ss

        span_dist = abs(self._target_left - self._start_left)
        cur_w = max(12.0, self._cur_right - self._cur_left)
        rest_w = max(12.0, self._target_right - self._target_left)
        extra_stretch = max(0.0, cur_w - max(rest_w, self._start_right - self._start_left))

        r_local = np.full(gx.shape, np.float32(r_base), dtype=np.float32)
        if not self._dragging and 0.01 < t < 0.78 and span_dist > 8.0:
            env = math.sin(math.pi * _clamp01(t / 0.78))
            pinch_amp = min(5.4 * ss, (extra_stretch / max(20.0, span_dist)) * 5.2 * ss * env + 1.6 * ss * env)
            mid_x = np.float32((l_main + r_main) * 0.5)
            half_span = np.float32(max(10.0 * ss, (r_main - l_main) * 0.25))
            neck = np.exp(-((gx - mid_x) / half_span) ** 2)
            norm_x = np.clip((gx - np.float32(l_main)) / np.float32(max(1.0, r_main - l_main)), np.float32(0.0), np.float32(1.0))
            head_bias = (norm_x - np.float32(0.5)) * (np.float32(1.25 * ss * env) * (np.float32(1.0) if self._moving_right else np.float32(-1.0)))
            r_local = np.maximum(np.float32(3.5 * ss), r_local - np.float32(pinch_amp) * neck + head_bias)
        elif self._dragging and abs(self._slosh_inertia) > 0.05:
            # Dynamic waist tension while the user pulls the droplet rapidly by mouse
            mid_x = np.float32((l_main + r_main) * 0.5)
            half_span = np.float32(max(12.0 * ss, (r_main - l_main) * 0.35))
            neck = np.exp(-((gx - mid_x) / half_span) ** 2)
            pinch = np.float32(min(2.6 * ss, abs(self._slosh_inertia) * 2.2 * ss))
            r_local = np.maximum(np.float32(4.0 * ss), r_local - pinch * neck)

        # Couple real 2D shallow-water field independently into top & bottom meniscus contours!
        if self._wave_eta is not None and self._wave_eta.shape == gx.shape:
            top_row = max(1, min(sh - 3, int((self._pad + 1.5) * ss)))
            bot_row = max(top_row + 1, min(sh - 2, sh - 1 - int((self._pad + 1.5) * ss)))
            eta_top = self._wave_eta[max(0, top_row - 1):min(sh, top_row + 2), :].mean(axis=0)
            eta_bot = self._wave_eta[max(0, bot_row - 1):min(sh, bot_row + 2), :].mean(axis=0)
            frac_bot = np.clip(
                (gy - np.float32(top_row + 0.5)) / np.float32(max(1, bot_row - top_row)),
                np.float32(0.0),
                np.float32(1.0),
            )
            edge_eta = eta_top[None, :] * (np.float32(1.0) - frac_bot) + eta_bot[None, :] * frac_bot
            r_local = np.clip(
                r_local + edge_eta * np.float32(1.65 * ss),
                np.float32(3.5 * ss),
                np.float32((h * 0.5 - 1.25) * ss),
            )
            wave_2d = self._wave_eta * np.float32(0.28)
        else:
            wave_2d = np.zeros_like(gx)

        # Inertial mass slosh tilts upper fluid ridge smoothly forward in motion direction
        gx_eff = gx
        if abs(self._slosh_inertia) > 0.015:
            tilt = (cy - gy) * np.float32(0.11 * self._slosh_inertia)
            gx_eff = gx - tilt

        ax = min((l_main + r_main) * 0.5, l_main + r_base)
        bx = max((l_main + r_main) * 0.5, r_main - r_base)
        dx = np.maximum(np.float32(0.0), np.maximum(np.float32(ax) - gx_eff, gx_eff - np.float32(bx)))
        dy = gy - cy
        sd = np.sqrt(dx * dx + dy * dy) - r_local

        # Viscous metaball satellite droplet break-up and coalescence on long jumps
        sat = self._satellite_state()
        if sat is not None:
            sat_cx, sat_r = sat
            sd_sat = np.sqrt((gx - np.float32(sat_cx * ss)) ** 2 + dy * dy) - np.float32(sat_r * ss)
            sd = _smin_poly(sd, sd_sat, k=5.5 * ss)

        if self._hover_pos is not None and not self._is_animating() and not self._dragging:
            hx = np.float32(self._hover_pos.x() * ss)
            hy = np.float32(self._hover_pos.y() * ss)
            dist_sq = (gx - hx) ** 2 + (gy - hy) ** 2
            pull = np.float32(1.35 * ss) * np.exp(-dist_sq / np.float32((18.0 * ss) ** 2))
            sd = sd - pull * np.clip(np.float32(1.0) - np.abs(sd) / np.float32(8.0 * ss), np.float32(0.0), np.float32(1.0))

        return sd, wobble, wave_2d

    def paintEvent(self, event) -> None:
        if self.width() <= 8 or self.height() <= 8:
            return

        if not self._geom_initialized or self._cur_right <= self._cur_left:
            span = self._target_span_for(self._active_button())
            if span is None:
                return
            self._cur_left, self._cur_right = span
            self._start_left, self._start_right = span
            self._target_left, self._target_right = span
            self._geom_initialized = True

        self._ensure_shader_cache()
        ss = self._ss
        gx, gy = self._gx, self._gy
        sh, sw = gx.shape

        sd, wobble, wave = self._evaluate_liquid_sdf()

        out_rgb = self._bg_muted[..., :3].copy()
        track_cov = self._track_cov

        if self._hover_pos is not None:
            hx = np.float32(self._hover_pos.x() * ss)
            hy = np.float32(self._hover_pos.y() * ss)
            d2_h = (gx - hx) ** 2 + (gy - hy) ** 2
            hover_glow = np.exp(-d2_h / np.float32((30.0 * ss) ** 2)) * np.float32(0.10)
            out_rgb[..., 0] += hover_glow * np.float32(0.65)
            out_rgb[..., 1] += hover_glow * np.float32(0.85)
            out_rgb[..., 2] += hover_glow * np.float32(1.00)

        left_bound = self._cur_left
        right_bound = self._cur_right
        sat = self._satellite_state()
        if sat is not None:
            left_bound = min(left_bound, sat[0] - sat[1] - 4.0)
            right_bound = max(right_bound, sat[0] + sat[1] + 4.0)

        bx0 = max(0, int((left_bound - 12.0) * ss))
        bx1 = min(sw, int((right_bound + 12.0) * ss))
        if bx1 - bx0 >= 4:
            sd_s = sd[:, bx0:bx1]
            gx_s = gx[:, bx0:bx1]
            gy_s = gy[:, bx0:bx1]
            wave_s = wave[:, bx0:bx1]
            bg_s = out_rgb[:, bx0:bx1, :]

            drop_cov = np.clip(np.float32(0.5) - sd_s * np.float32(0.95 / ss), np.float32(0.0), np.float32(1.0))
            shadow_zone = np.clip(np.float32(1.0) - np.abs(sd_s - np.float32(2.2 * ss)) / np.float32(4.2 * ss), np.float32(0.0), np.float32(1.0))
            caustic_zone = np.exp(-((sd_s - np.float32(1.8 * ss)) ** 2) / np.float32((5.5 * ss) ** 2)) * np.clip(
                (gy_s - np.float32(sh * 0.38)) / np.float32(sh * 0.62), np.float32(0.0), np.float32(1.0)
            )
            bg_s *= (np.float32(1.0) - np.float32(0.42) * shadow_zone * (np.float32(1.0) - drop_cov))[..., None]
            c_col = np.array(
                [self._accent.redF() * 0.45, self._accent.greenF() * 0.72, self._accent.blueF()],
                dtype=np.float32,
            )
            bg_s += (caustic_zone * np.float32(0.32 + 0.14 * self._press_depth))[..., None] * c_col

            # 2. Construct 3D droplet height field + smooth live shallow-water wave elevation
            edge_zone = np.float32(8.0 * ss)
            u = np.clip(-sd_s / edge_zone, np.float32(0.0), np.float32(1.0))
            omu = np.float32(1.0) - u
            wave_fade = np.clip((u - np.float32(0.08)) / np.float32(0.92), np.float32(0.0), np.float32(1.0))
            h_field = np.sqrt(np.clip(np.float32(1.0) - omu * omu, np.float32(0.0), np.float32(1.0))) + wave_s * wave_fade

            gh_x = np.empty_like(h_field)
            gh_x[:, 1:-1] = (h_field[:, 2:] - h_field[:, :-2]) * np.float32(0.5)
            gh_x[:, 0] = h_field[:, 1] - h_field[:, 0]
            gh_x[:, -1] = h_field[:, -1] - h_field[:, -2]

            gh_y = np.empty_like(h_field)
            gh_y[1:-1, :] = (h_field[2:, :] - h_field[:-2, :]) * np.float32(0.5)
            gh_y[0, :] = h_field[1, :] - h_field[0, :]
            gh_y[-1, :] = h_field[-1, :] - h_field[-2, :]

            slope = np.float32(2.05 - 0.35 * self._press_depth + 0.35 * wobble)
            nx_raw = -gh_x * slope * np.float32(ss)
            ny_raw = -gh_y * slope * np.float32(ss)
            inv_len = np.float32(1.0) / np.sqrt(nx_raw * nx_raw + ny_raw * ny_raw + np.float32(1.0))
            nx = nx_raw * inv_len
            ny = ny_raw * inv_len
            nz = inv_len

            # 3. Optical lens magnification tapered at rim and damped across wide flow stretches
            droplet_cx = np.float32((self._cur_left + self._cur_right) * 0.5 * ss)
            droplet_cy = np.float32(sh * 0.5)
            half_span = np.float32(max(24.0 * ss, (self._cur_right - self._cur_left) * 0.5 * ss))
            mag_taper = np.clip((u - np.float32(0.20)) / np.float32(0.80), np.float32(0.0), np.float32(1.0))
            width_damping = np.float32(min(1.0, (42.0 * ss) / float(half_span)))
            mag_scale = np.float32(0.072 - 0.018 * self._press_depth) * mag_taper * h_field * width_damping
            mag_dx = (gx_s - droplet_cx) * mag_scale
            mag_dy = (gy_s - droplet_cy) * mag_scale
            shift_x = nx * np.float32(3.4 * ss)
            shift_y = ny * np.float32(3.8 * ss)

            refracted = _sample_bilinear_rgb_dispersed(
                self._bg_bright, gx_s, gy_s, shift_x, shift_y, mag_dx, mag_dy
            )

            # 4. Water absorption tint + Fresnel reflection + dual Blinn-Phong highlights
            rim_depth = np.float32(1.0) - nz
            water_tint = np.array(
                [
                    0.14 + 0.35 * self._accent.redF(),
                    0.42 + 0.42 * self._accent.greenF(),
                    0.78 + 0.22 * self._accent.blueF(),
                ],
                dtype=np.float32,
            )
            body_depth = np.clip(np.float32(1.0) - h_field, np.float32(0.0), np.float32(1.0))
            tint_strength = (np.float32(0.26) + np.float32(0.38) * rim_depth + np.float32(0.10) * body_depth)[..., None]
            water_body = refracted * (np.float32(1.0) - tint_strength * np.float32(0.60)) + water_tint * (tint_strength * np.float32(0.55))

            fresnel = np.float32(0.05) + np.float32(0.95) * np.power(np.clip(np.float32(1.0) - nz, np.float32(0.0), np.float32(1.0)), np.float32(3.0))
            sky_refl = np.array([0.80, 0.92, 1.0], dtype=np.float32)

            ndoth1 = np.clip(nx * np.float32(-0.16) + ny * np.float32(-0.53) + nz * np.float32(0.83), np.float32(0.0), np.float32(1.0))
            spec1 = np.power(ndoth1, np.float32(40.0)) * np.float32(0.92) + np.power(ndoth1, np.float32(10.0)) * np.float32(0.24)

            ndoth2 = np.clip(nx * np.float32(0.10) + ny * np.float32(0.42) + nz * np.float32(0.90), np.float32(0.0), np.float32(1.0))
            spec2 = np.power(ndoth2, np.float32(18.0)) * np.float32(0.52)

            droplet_rgb = (
                water_body * (np.float32(1.0) - fresnel[..., None] * np.float32(0.65))
                + sky_refl * (fresnel[..., None] * np.float32(0.75))
                + spec1[..., None] * np.array([1.0, 1.0, 1.0], dtype=np.float32)
                + spec2[..., None] * np.array([0.52, 0.86, 1.0], dtype=np.float32)
            )

            out_rgb[:, bx0:bx1, :] = bg_s * (np.float32(1.0) - drop_cov[..., None]) + droplet_rgb * drop_cov[..., None]

        comp_rgb = np.clip(out_rgb, np.float32(0.0), np.float32(1.0))

        rgba_u8 = np.empty((sh, sw, 4), dtype=np.uint8)
        rgba_u8[..., :3] = (comp_rgb * np.float32(255.0)).astype(np.uint8)
        rgba_u8[..., 3] = (np.clip(track_cov * np.float32(0.96), np.float32(0.0), np.float32(1.0)) * np.float32(255.0)).astype(np.uint8)

        qimg = QImage(rgba_u8.data, sw, sh, sw * 4, _FMT_RGBA8888)
        painter = QPainter(self)
        if ss != 1:
            painter.setRenderHint(_RENDER_AA, True)
            painter.setRenderHint(_RENDER_SMOOTH, True)
        painter.drawImage(QRectF(0.0, 0.0, float(self.width()), float(self.height())), qimg)
