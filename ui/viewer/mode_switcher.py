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


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)


def _ease_front(t: float) -> float:
    t = _clamp01(t)
    if t < 0.68:
        u = t / 0.68
        inv = 1.0 - u
        return 1.0 - inv * inv * inv
    p = (t - 0.68) / 0.32
    return 1.0 + 0.045 * math.sin(p * math.pi * 2.1) * math.exp(-3.4 * p)


def _ease_back(t: float) -> float:
    t = _clamp01(t)
    if t < 0.14:
        return 0.015 * (t / 0.14)
    if t < 0.78:
        u = (t - 0.14) / 0.64
        return 0.015 + 0.985 * (u * u * (3.0 - 2.0 * u))
    p = (t - 0.78) / 0.22
    return 1.0 - 0.025 * math.sin(p * math.pi * 1.8) * math.exp(-3.0 * p)


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


def _qimage_to_rgba_float(img: QImage) -> np.ndarray:
    """Convert QImage (RGB/ARGB) into float32 (H, W, 4) in [0..1] RGBA order."""
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
    """Refract backdrop with subtle optical lens magnification and R/G/B chromatic dispersion."""
    h, w, _ = tex.shape
    sh, sw = gx.shape
    out = np.empty((sh, sw, 3), dtype=np.float32)
    etas = (np.float32(0.86), np.float32(1.00), np.float32(1.18))
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
    Physically-inspired Liquid Glass mode switcher using a vectorized NumPy SDF
    metaball shader (2x SSAA): smooth-min droplet fusion, 3D surface normals,
    Snell text/track refraction with RGB dispersion, Fresnel rim reflections,
    Blinn-Phong specular highlights, floor caustics, and interactive hover/press.
    """

    _SS = 2  # 2x supersampling factor for razor-sharp curved meniscus and text

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

        # Cached 2x-supersampled shader grids & pre-rendered backdrops
        self._cache_key: tuple | None = None
        self._gx: np.ndarray | None = None
        self._gy: np.ndarray | None = None
        self._track_sd: np.ndarray | None = None
        self._track_cov: np.ndarray | None = None
        self._bg_muted: np.ndarray | None = None
        self._bg_bright: np.ndarray | None = None

        self._anim = QPropertyAnimation(self, b"anim_progress", self)
        self._anim.setDuration(460)
        self._anim.setEasingCurve(_EASE_LINEAR)

        self._press_anim = QPropertyAnimation(self, b"press_depth", self)
        self._press_anim.setDuration(105)

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
                self._hover_pos = QPointF(obj.mapTo(self, local_pt.toPoint()))
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

    def _is_animating(self) -> bool:
        state_cls = getattr(QPropertyAnimation, "State", QPropertyAnimation)
        return self._anim.state() == getattr(state_cls, "Running", 2)

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

    def _render_backdrop_layer(self, bright: bool) -> np.ndarray:
        """Render the 2x-SSAA glass track backdrop (floor micro-ribs + button labels)."""
        ss = self._SS
        w, h = self.width(), self.height()
        sw, sh = w * ss, h * ss
        img = QImage(sw, sh, _FMT_ARGB32)
        img.fill(Qt.GlobalColor.transparent if hasattr(Qt, "GlobalColor") else 0)

        p = QPainter(img)
        p.setRenderHint(_RENDER_AA, True)
        p.setRenderHint(_RENDER_TEXT_AA, True)
        p.scale(float(ss), float(ss))

        # Deep smoked glass floor with delicate optical ribs so refraction bends visible texture
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

        # Ultra-subtle vertical optical guide grooves on the glass floor (refract softly in the water lens)
        rib_pen = QPen(QColor(148, 163, 184, 18 if bright else 9), 0.7)
        p.setPen(rib_pen)
        x_pos = 12.0
        while x_pos < w - 10.0:
            p.drawLine(QPointF(x_pos, 3.0), QPointF(x_pos, float(h) - 3.0))
            x_pos += 10.0

        # Subtle horizontal meniscus groove across middle of glass track
        p.setPen(QPen(QColor(186, 230, 253, 14 if bright else 6), 0.7))
        p.drawLine(QPointF(8.0, h * 0.5), QPointF(w - 8.0, h * 0.5))

        # Render labels onto the two backdrops
        for btn in self._buttons:
            p.setFont(btn.font())
            if bright:
                # Crisp white with slight deep shadow for high contrast under water refraction
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
        key = (
            w,
            h,
            tuple((b.text(), b.geometry().x(), b.geometry().width()) for b in self._buttons),
            self._accent.name(),
            self._accent_dark.name(),
            self._text_muted.name(),
        )
        if self._cache_key == key and self._bg_muted is not None:
            return

        ss = self._SS
        sw, sh = w * ss, h * ss
        ys = np.arange(sh, dtype=np.float32) + np.float32(0.5)
        xs = np.arange(sw, dtype=np.float32) + np.float32(0.5)
        self._gx, self._gy = np.meshgrid(xs, ys)

        # Signed distance of outer glass track in 2x coordinates
        pad_s = (0.5) * ss
        r_s = (h - 1.0) * 0.5 * ss
        self._track_sd = _sd_capsule_vec(self._gx, self._gy, pad_s, sw - pad_s, sh * 0.5, r_s)
        self._track_cov = np.clip(np.float32(0.5) - self._track_sd * np.float32(0.85), np.float32(0.0), np.float32(1.0))

        self._bg_muted = self._render_backdrop_layer(bright=False)
        # Bake outer glass rim reflection directly into cached backdrop
        rim_band = np.exp(-((self._track_sd + np.float32(1.2)) ** 2) / np.float32(1.4))
        top_bias = np.clip(np.float32(1.0) - (self._gy / np.float32(sh)), np.float32(0.15), np.float32(1.0))
        self._bg_muted[..., :3] = np.clip(
            self._bg_muted[..., :3] + (rim_band * top_bias * np.float32(0.24))[..., None] * np.array([0.75, 0.85, 1.0], dtype=np.float32),
            np.float32(0.0),
            np.float32(1.0),
        )
        self._bg_bright = self._render_backdrop_layer(bright=True)
        self._cache_key = key

    def _evaluate_liquid_sdf(self) -> tuple[np.ndarray, float, np.ndarray]:
        """
        Compute continuous 2D SDF for the flowing liquid droplet using smooth waist
        pinch modulation (no stray floating dots over neighboring labels) plus
        a traveling surface capillary wave on landing.
        """
        ss = self._SS
        gx, gy = self._gx, self._gy
        h = float(self.height())
        cy = np.float32(h * 0.5 * ss)
        t = float(self._anim_progress)

        press_x = 1.4 * self._press_depth
        press_y = -0.9 * self._press_depth

        if 0.68 <= t < 1.0:
            p = (t - 0.68) / 0.32
            wobble = math.sin(p * math.pi * 2.1) * math.exp(-3.2 * p)
        else:
            wobble = 0.0

        l_main = max(float(self._pad) - 1.0, self._cur_left - press_x) * ss
        r_main = min(float(self.width() - self._pad) + 1.0, self._cur_right + press_x) * ss

        # Dynamic waist necking while stretched between buttons + asymmetric meniscus swell
        r_base = (self._btn_h * 0.5 + press_y + 0.55 * wobble) * ss
        span_dist = abs(self._target_left - self._start_left)
        cur_w = max(12.0, self._cur_right - self._cur_left)
        rest_w = max(12.0, self._target_right - self._target_left)
        extra_stretch = max(0.0, cur_w - max(rest_w, self._start_right - self._start_left))

        r_local = np.full(gx.shape, np.float32(r_base), dtype=np.float32)
        if 0.01 < t < 0.72 and span_dist > 8.0:
            env = math.sin(math.pi * _clamp01(t / 0.72))
            pinch_amp = min(4.2 * ss, (extra_stretch / max(24.0, span_dist)) * 4.4 * ss * env + 1.1 * ss * env)
            mid_x = np.float32((l_main + r_main) * 0.5)
            half_span = np.float32(max(12.0 * ss, (r_main - l_main) * 0.28))
            neck = np.exp(-((gx - mid_x) / half_span) ** 2)
            # Subtle directional surge: slightly fuller leading head, sleeker trailing tail
            norm_x = np.clip((gx - np.float32(l_main)) / np.float32(max(1.0, r_main - l_main)), np.float32(0.0), np.float32(1.0))
            head_bias = (norm_x - np.float32(0.5)) * (np.float32(0.85 * ss * env) * (np.float32(1.0) if self._moving_right else np.float32(-1.0)))
            r_local = np.maximum(np.float32(4.0 * ss), r_local - np.float32(pinch_amp) * neck + head_bias)

        # Signed distance to stadium capsule with smoothly varying radius r_local(x)
        cap_end_r = np.float32(r_base)
        ax = min((l_main + r_main) * 0.5, l_main + r_base)
        bx = max((l_main + r_main) * 0.5, r_main - r_base)
        dx = np.maximum(np.float32(0.0), np.maximum(np.float32(ax) - gx, gx - np.float32(bx)))
        dy = gy - cy
        sd = np.sqrt(dx * dx + dy * dy) - r_local

        # Subtle magnetic meniscus pull toward mouse cursor on hover
        if self._hover_pos is not None and not self._is_animating():
            hx = np.float32(self._hover_pos.x() * ss)
            hy = np.float32(self._hover_pos.y() * ss)
            dist_sq = (gx - hx) ** 2 + (gy - hy) ** 2
            pull = np.float32(1.25 * ss) * np.exp(-dist_sq / np.float32((18.0 * ss) ** 2))
            sd = sd - pull * np.clip(np.float32(1.0) - np.abs(sd) / np.float32(8.0 * ss), np.float32(0.0), np.float32(1.0))

        # Traveling capillary surface wave inside the water plateau after landing
        if 0.60 <= t < 0.99:
            wp = (t - 0.60) / 0.39
            dir_s = np.float32(1.0 if self._moving_right else -1.0)
            wave_arg = (gx / np.float32(9.0 * ss)) * dir_s - np.float32(wp * 8.5)
            wave = np.sin(wave_arg) * np.float32(0.055 * math.sin(math.pi * wp) * math.exp(-1.6 * wp))
        else:
            wave = np.zeros_like(gx)

        return sd, wobble, wave

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
        ss = self._SS
        gx, gy = self._gx, self._gy
        sh, sw = gx.shape

        sd, wobble, wave = self._evaluate_liquid_sdf()

        # 1. Start from baked muted backdrop + subtle cursor light follower
        out_rgb = self._bg_muted[..., :3].copy()
        track_cov = self._track_cov

        if self._hover_pos is not None:
            hx = np.float32(self._hover_pos.x() * ss)
            hy = np.float32(self._hover_pos.y() * ss)
            d2_h = (gx - hx) ** 2 + (gy - hy) ** 2
            hover_glow = np.exp(-d2_h / np.float32((30.0 * ss) ** 2)) * np.float32(0.095)
            out_rgb[..., 0] += hover_glow * np.float32(0.65)
            out_rgb[..., 1] += hover_glow * np.float32(0.85)
            out_rgb[..., 2] += hover_glow * np.float32(1.00)

        # Restrict 3D refraction, normals, and Fresnel lighting strictly to the droplet bounding box
        bx0 = max(0, int((self._cur_left - 10.0) * ss))
        bx1 = min(sw, int((self._cur_right + 10.0) * ss))
        if bx1 - bx0 >= 4:
            sd_s = sd[:, bx0:bx1]
            gx_s = gx[:, bx0:bx1]
            gy_s = gy[:, bx0:bx1]
            wave_s = wave[:, bx0:bx1]
            bg_s = out_rgb[:, bx0:bx1, :]

            drop_cov = np.clip(np.float32(0.5) - sd_s * np.float32(0.85), np.float32(0.0), np.float32(1.0))
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

            # 2. Construct 3D droplet height field + traveling capillary wave
            edge_zone = np.float32(7.6 * ss)
            u = np.clip(-sd_s / edge_zone, np.float32(0.0), np.float32(1.0))
            omu = np.float32(1.0) - u
            h_field = np.sqrt(np.clip(np.float32(1.0) - omu * omu, np.float32(0.0), np.float32(1.0))) + wave_s * u

            # Fast central differences in float32 (5x faster than np.gradient)
            gh_x = np.empty_like(h_field)
            gh_x[:, 1:-1] = (h_field[:, 2:] - h_field[:, :-2]) * np.float32(0.5)
            gh_x[:, 0] = h_field[:, 1] - h_field[:, 0]
            gh_x[:, -1] = h_field[:, -1] - h_field[:, -2]

            gh_y = np.empty_like(h_field)
            gh_y[1:-1, :] = (h_field[2:, :] - h_field[:-2, :]) * np.float32(0.5)
            gh_y[0, :] = h_field[1, :] - h_field[0, :]
            gh_y[-1, :] = h_field[-1, :] - h_field[-2, :]

            slope = np.float32(1.85 - 0.35 * self._press_depth + 0.25 * wobble)
            nx_raw = -gh_x * slope * np.float32(ss)
            ny_raw = -gh_y * slope * np.float32(ss)
            inv_len = np.float32(1.0) / np.sqrt(nx_raw * nx_raw + ny_raw * ny_raw + np.float32(1.0))
            nx = nx_raw * inv_len
            ny = ny_raw * inv_len
            nz = inv_len

            # 3. Physically-inspired Snell refraction + chromatic dispersion + optical magnification
            droplet_cx = np.float32((self._cur_left + self._cur_right) * 0.5 * ss)
            droplet_cy = np.float32(sh * 0.5)
            mag_scale = np.float32(0.048 - 0.015 * self._press_depth) * h_field
            mag_dx = (gx_s - droplet_cx) * mag_scale
            mag_dy = (gy_s - droplet_cy) * mag_scale
            shift_x = nx * np.float32(5.8 * ss)
            shift_y = ny * np.float32(5.2 * ss)

            refracted = _sample_bilinear_rgb_dispersed(
                self._bg_bright, gx_s, gy_s, shift_x, shift_y, mag_dx, mag_dy
            )

            # 4. Water absorption tint + Fresnel reflection + dual specular highlights (Blinn-Phong)
            rim_depth = np.float32(1.0) - nz
            water_tint = np.array(
                [
                    0.14 + 0.35 * self._accent.redF(),
                    0.42 + 0.42 * self._accent.greenF(),
                    0.78 + 0.22 * self._accent.blueF(),
                ],
                dtype=np.float32,
            )
            tint_strength = (np.float32(0.28) + np.float32(0.36) * rim_depth + np.float32(0.10) * (np.float32(1.0) - h_field))[..., None]
            water_body = refracted * (np.float32(1.0) - tint_strength * np.float32(0.62)) + water_tint * (tint_strength * np.float32(0.55))

            fresnel = np.float32(0.05) + np.float32(0.95) * np.power(np.clip(np.float32(1.0) - nz, np.float32(0.0), np.float32(1.0)), np.float32(3.2))
            sky_refl = np.array([0.78, 0.91, 1.0], dtype=np.float32)

            ndoth1 = np.clip(nx * np.float32(-0.16) + ny * np.float32(-0.53) + nz * np.float32(0.83), np.float32(0.0), np.float32(1.0))
            spec1 = np.power(ndoth1, np.float32(42.0)) * np.float32(0.85) + np.power(ndoth1, np.float32(10.0)) * np.float32(0.22)

            ndoth2 = np.clip(nx * np.float32(0.10) + ny * np.float32(0.42) + nz * np.float32(0.90), np.float32(0.0), np.float32(1.0))
            spec2 = np.power(ndoth2, np.float32(20.0)) * np.float32(0.48)

            droplet_rgb = (
                water_body * (np.float32(1.0) - fresnel[..., None] * np.float32(0.65))
                + sky_refl * (fresnel[..., None] * np.float32(0.72))
                + spec1[..., None] * np.array([1.0, 1.0, 1.0], dtype=np.float32)
                + spec2[..., None] * np.array([0.52, 0.86, 1.0], dtype=np.float32)
            )

            out_rgb[:, bx0:bx1, :] = bg_s * (np.float32(1.0) - drop_cov[..., None]) + droplet_rgb * drop_cov[..., None]

        comp_rgb = np.clip(out_rgb, np.float32(0.0), np.float32(1.0))

        # Assemble premultiplied-safe RGBA8888 output clipped by outer track alpha
        rgba_u8 = np.empty((sh, sw, 4), dtype=np.uint8)
        rgba_u8[..., :3] = (comp_rgb * np.float32(255.0)).astype(np.uint8)
        rgba_u8[..., 3] = (np.clip(track_cov * np.float32(0.96), np.float32(0.0), np.float32(1.0)) * np.float32(255.0)).astype(np.uint8)

        qimg = QImage(rgba_u8.data, sw, sh, sw * 4, _FMT_RGBA8888)
        painter = QPainter(self)
        painter.setRenderHint(_RENDER_AA, True)
        painter.setRenderHint(_RENDER_SMOOTH, True)
        painter.drawImage(QRectF(0.0, 0.0, float(self.width()), float(self.height())), qimg)
