from __future__ import annotations

from PyQt6.QtCore import QCoreApplication, QEasingCurve, QEvent, QPointF, QRectF, QSize, QTimer, QVariantAnimation, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QTabBar


_RENDER_AA = QPainter.RenderHint.Antialiasing if hasattr(QPainter, "RenderHint") else QPainter.Antialiasing
_RENDER_TEXT_AA = (
    QPainter.RenderHint.TextAntialiasing if hasattr(QPainter, "RenderHint") else QPainter.TextAntialiasing
)
_ALIGN_CENTER = Qt.AlignmentFlag.AlignCenter if hasattr(Qt, "AlignmentFlag") else Qt.AlignCenter
_BTN_RIGHT = QTabBar.ButtonPosition.RightSide if hasattr(QTabBar, "ButtonPosition") else QTabBar.RightSide
_EASE_OUT_CUBIC = QEasingCurve.Type.OutCubic if hasattr(QEasingCurve, "Type") else QEasingCurve.OutCubic
_FONT_DEMIBOLD = QFont.Weight.DemiBold if hasattr(QFont, "Weight") else 63
_CURVE_POINTING = Qt.CursorShape.PointingHandCursor if hasattr(Qt, "CursorShape") else Qt.PointingHandCursor
_EVT_FONT_CHANGE = getattr(QEvent.Type, "FontChange", getattr(QEvent, "FontChange", 97))


def _lerp_color(c0: QColor, c1: QColor, t: float) -> QColor:
    u = 0.0 if t < 0.0 else (1.0 if t > 1.0 else float(t))
    return QColor(
        int(round(c0.red() + (c1.red() - c0.red()) * u)),
        int(round(c0.green() + (c1.green() - c0.green()) * u)),
        int(round(c0.blue() + (c1.blue() - c0.blue()) * u)),
        int(round(c0.alpha() + (c1.alpha() - c0.alpha()) * u)),
    )


class AnimatedSegmentedTabBar(QTabBar):
    """
    Clean dark segmented QTabBar with a crisp sliding active pill and subtle hover fade.
    Preserves native QTabWidget tab lifecycle, right-side TabBadge widgets, and context menus.
    """

    TRACK_HEIGHT = 30

    def __init__(self, parent=None) -> None:
        self._font = QFont("Segoe UI")
        self._font.setPixelSize(12)
        self._font.setWeight(_FONT_DEMIBOLD)

        self._slider_x: float = 0.0
        self._slider_w: float = 0.0
        self._start_x: float = 0.0
        self._start_w: float = 0.0
        self._target_x: float = 0.0
        self._target_w: float = 0.0
        self._slider_initialized: bool = False
        self._pressed_index: int = -1
        self._hover_index: int = -1
        self._hover_levels: list[float] = []

        super().__init__(parent)

        self._slide_anim = QVariantAnimation(self)
        self._slide_anim.setDuration(190)
        self._slide_anim.setEasingCurve(_EASE_OUT_CUBIC)
        self._slide_anim.setStartValue(0.0)
        self._slide_anim.setEndValue(1.0)
        self._slide_anim.valueChanged.connect(self._on_slide_step)

        self._hover_timer = QTimer(self)
        self._hover_timer.setInterval(16)
        self._hover_timer.timeout.connect(self._step_hover_fade)

        self.setObjectName("mainTabBar")
        self.setDrawBase(False)
        self.setExpanding(False)
        self.setUsesScrollButtons(False)
        self.setMouseTracking(True)
        self.setCursor(_CURVE_POINTING)
        self.setFont(self._font)

        self.currentChanged.connect(self._on_current_changed)

    def _text_width(self, text: str) -> int:
        fm = QFontMetrics(self._font)
        if hasattr(fm, "horizontalAdvance"):
            return fm.horizontalAdvance(text)
        return fm.width(text)

    def _badge_widget(self, index: int):
        if 0 <= index < self.count():
            w = self.tabButton(index, _BTN_RIGHT)
            if w is not None and not w.isHidden():
                return w
        return None

    def tabSizeHint(self, index: int) -> QSize:
        if index < 0 or index >= self.count():
            return QSize(72, self.TRACK_HEIGHT)
        tw = self._text_width(self.tabText(index))
        badge = self._badge_widget(index)
        if badge is not None:
            bw = max(badge.width(), badge.sizeHint().width())
            # Balanced left breathing room + text + right-side badge pill (includes its margins)
            w = max(88, 13 + tw + bw + 3)
        else:
            w = max(72, 16 + tw + 16)
        return QSize(w, self.TRACK_HEIGHT)

    def refresh_tab_sizes(self) -> None:
        """Invalidate QTabBar's internal tab size cache when a badge digit count changes."""
        QCoreApplication.sendEvent(self, QEvent(_EVT_FONT_CHANGE))
        self.updateGeometry()
        state_cls = getattr(QVariantAnimation, "State", QVariantAnimation)
        if hasattr(self, "_slide_anim") and self._slide_anim.state() != getattr(state_cls, "Running", 2):
            self._sync_slider_immediate()
            self.update()

    def minimumTabSizeHint(self, index: int) -> QSize:
        return self.tabSizeHint(index)

    def _ensure_hover_len(self) -> None:
        n = self.count()
        if len(self._hover_levels) != n:
            old = self._hover_levels
            self._hover_levels = [old[i] if i < len(old) else 0.0 for i in range(n)]

    def _sync_slider_immediate(self) -> None:
        idx = self.currentIndex()
        if 0 <= idx < self.count():
            r = self.tabRect(idx)
            if r.width() > 0:
                self._slider_x = float(r.left())
                self._slider_w = float(r.width())
                self._start_x = self._slider_x
                self._start_w = self._slider_w
                self._target_x = self._slider_x
                self._target_w = self._slider_w
                self._slider_initialized = True
                self._notify_badges()

    def _notify_badges(self) -> None:
        for i in range(self.count()):
            badge = self.tabButton(i, _BTN_RIGHT)
            if badge is not None:
                badge.update()

    def active_ratio_for_index(self, index: int) -> float:
        """Return how much of tab[index]'s inner pill span is covered by the sliding indicator [0..1]."""
        if index < 0 or index >= self.count():
            return 0.0
        if not self._slider_initialized or self._slider_w <= 0.0:
            return 1.0 if self.currentIndex() == index else 0.0
        r = self.tabRect(index)
        if r.width() <= 0:
            return 0.0
        slider_l = self._slider_x + 2.0
        slider_r = slider_l + max(12.0, self._slider_w - 4.0)
        pill_span = max(1.0, float(r.width()) - 4.0)
        tab_l = float(r.left()) + 2.0
        tab_r = tab_l + pill_span
        overlap = max(0.0, min(tab_r, slider_r) - max(tab_l, slider_l))
        return max(0.0, min(1.0, overlap / pill_span))

    def _on_current_changed(self, index: int) -> None:
        if index < 0 or index >= self.count():
            return
        r = self.tabRect(index)
        if r.width() <= 0:
            return
        if not self._slider_initialized or not self.isVisible() or self._slider_w <= 0.0:
            self._sync_slider_immediate()
            self.update()
            return

        new_x = float(r.left())
        new_w = float(r.width())
        if abs(new_x - self._slider_x) < 0.5 and abs(new_w - self._slider_w) < 0.5:
            self._notify_badges()
            self.update()
            return

        self._slide_anim.stop()
        self._start_x = self._slider_x
        self._start_w = self._slider_w
        self._target_x = new_x
        self._target_w = new_w
        self._slide_anim.start()
        self._notify_badges()

    def _is_sliding(self) -> bool:
        state_cls = getattr(QVariantAnimation, "State", QVariantAnimation)
        return hasattr(self, "_slide_anim") and self._slide_anim.state() == getattr(state_cls, "Running", 2)

    def _sync_target_to_current(self) -> None:
        idx = self.currentIndex()
        if 0 <= idx < self.count():
            r = self.tabRect(idx)
            if r.width() > 0:
                if self._is_sliding():
                    self._target_x = float(r.left())
                    self._target_w = float(r.width())
                elif (
                    not self._slider_initialized
                    or abs(self._slider_x - float(r.left())) > 0.5
                    or abs(self._slider_w - float(r.width())) > 0.5
                ):
                    self._sync_slider_immediate()

    def _on_slide_step(self, progress) -> None:
        self._sync_target_to_current()
        p = float(progress)
        self._slider_x = self._start_x + (self._target_x - self._start_x) * p
        self._slider_w = self._start_w + (self._target_w - self._start_w) * p
        self._notify_badges()
        self.update()

    def _step_hover_fade(self) -> None:
        self._ensure_hover_len()
        changed = False
        any_active = False
        cur = self.currentIndex()
        for i in range(len(self._hover_levels)):
            target = 1.0 if (i == self._hover_index and i != cur) else 0.0
            cur_v = self._hover_levels[i]
            if abs(cur_v - target) > 0.01:
                step = 0.18 if target > cur_v else -0.14
                nxt = max(0.0, min(1.0, cur_v + step))
                self._hover_levels[i] = nxt
                changed = True
                if nxt > 0.01:
                    any_active = True
            elif cur_v > 0.01:
                any_active = True
        if not any_active and self._hover_index < 0:
            self._hover_timer.stop()
        if changed:
            self.update()

    def tabLayoutChange(self) -> None:
        super().tabLayoutChange()
        if not hasattr(self, "_slide_anim"):
            return
        self._ensure_hover_len()
        self._sync_target_to_current()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if not hasattr(self, "_slide_anim"):
            return
        self._sync_target_to_current()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._slider_initialized:
            self._sync_slider_immediate()

    def mouseMoveEvent(self, event) -> None:
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        idx = self.tabAt(pos)
        if idx != self._hover_index:
            self._hover_index = idx
            if not self._hover_timer.isActive():
                self._hover_timer.start()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        if self._hover_index != -1:
            self._hover_index = -1
            if not self._hover_timer.isActive():
                self._hover_timer.start()
        self._pressed_index = -1
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        l_btn = Qt.MouseButton.LeftButton if hasattr(Qt, "MouseButton") else Qt.LeftButton
        if event.button() == l_btn:
            pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
            self._pressed_index = self.tabAt(pos)
            self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._pressed_index != -1:
            self._pressed_index = -1
            self.update()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:
        w, h = float(self.width()), float(self.height())
        if w <= 4.0 or h <= 4.0 or self.count() == 0:
            return

        self._sync_target_to_current()
        self._ensure_hover_len()

        p = QPainter(self)
        p.setRenderHint(_RENDER_AA, True)
        p.setRenderHint(_RENDER_TEXT_AA, True)

        # 1. Neutral mid-grey container track matching original #464646 theme cue
        track_rect = QRectF(0.5, 0.5, w - 1.0, h - 1.0)
        track_grad = QLinearGradient(0.0, 0.0, 0.0, h)
        track_grad.setColorAt(0.0, QColor("#4a4a4a"))
        track_grad.setColorAt(1.0, QColor("#414141"))
        p.setPen(QPen(QColor("#5a5a5a"), 1.0))
        p.setBrush(QBrush(track_grad))
        p.drawRoundedRect(track_rect, 7.0, 7.0)

        slider_l = self._slider_x + 2.0
        slider_r = slider_l + max(12.0, self._slider_w - 4.0)

        # 2. Hover pills for inactive tabs
        for i in range(self.count()):
            hv = self._hover_levels[i] if i < len(self._hover_levels) else 0.0
            if hv <= 0.01 or i == self.currentIndex():
                continue
            r = self.tabRect(i)
            hr = QRectF(r).adjusted(2.0, 3.0, -2.0, -3.0)
            p.setPen(Qt.PenStyle.NoPen if hasattr(Qt, "PenStyle") else Qt.NoPen)
            p.setBrush(QBrush(QColor(255, 255, 255, int(round(34.0 * hv)))))
            p.drawRoundedRect(hr, 5.0, 5.0)

        # 3. Subtle vertical dividers between inactive neighbors away from active pill
        for i in range(self.count() - 1):
            r_cur = self.tabRect(i)
            sep_x = float(r_cur.right()) + 0.5
            # Hide divider if slider or strong hover touches either adjacent tab
            near_slider = (slider_l - 6.0) <= sep_x <= (slider_r + 6.0)
            hv_l = self._hover_levels[i] if i < len(self._hover_levels) else 0.0
            hv_r = self._hover_levels[i + 1] if (i + 1) < len(self._hover_levels) else 0.0
            fade = max(0.0, 1.0 - max(hv_l, hv_r))
            if not near_slider and fade > 0.05:
                p.setPen(QPen(QColor(255, 255, 255, int(round(32.0 * fade))), 1.0))
                p.drawLine(QPointF(sep_x, 8.0), QPointF(sep_x, h - 8.0))

        # 4. Crisp sliding active indicator pill
        if self._slider_w > 4.0:
            press_inset = 0.5 if self._pressed_index == self.currentIndex() else 0.0
            pill_rect = QRectF(
                slider_l + press_inset,
                3.0 + press_inset,
                max(10.0, self._slider_w - 4.0 - press_inset * 2.0),
                max(8.0, h - 6.0 - press_inset * 2.0),
            )
            pill_grad = QLinearGradient(0.0, pill_rect.top(), 0.0, pill_rect.bottom())
            pill_grad.setColorAt(0.0, QColor("#2562a6"))
            pill_grad.setColorAt(1.0, QColor("#1c4c82"))
            p.setPen(QPen(QColor("#397cc4"), 1.0))
            p.setBrush(QBrush(pill_grad))
            p.drawRoundedRect(pill_rect, 5.0, 5.0)

            # Fine top highlight hair-line inside active pill for crisp tactile depth
            p.setPen(QPen(QColor(175, 215, 255, 72), 1.0))
            p.drawLine(
                QPointF(pill_rect.left() + 5.0, pill_rect.top() + 1.0),
                QPointF(pill_rect.right() - 5.0, pill_rect.top() + 1.0),
            )

        # 5. Tab labels smoothly cross-faded as the active slider sweeps across tabs
        p.setFont(self._font)
        col_muted = QColor("#e2e5ea")
        col_hover = QColor("#ffffff")
        col_active = QColor("#ffffff")

        for i in range(self.count()):
            r = self.tabRect(i)
            badge = self._badge_widget(i)
            if badge is not None:
                badge_left = float(badge.geometry().left())
                text_w = max(10.0, badge_left - float(r.left()) - 8.0)
                text_rect = QRectF(float(r.left()) + 10.0, float(r.top()), text_w, float(r.height()))
            else:
                text_rect = QRectF(float(r.left()) + 6.0, float(r.top()), max(10.0, float(r.width()) - 12.0), float(r.height()))

            active_ratio = self.active_ratio_for_index(i)
            hv = self._hover_levels[i] if i < len(self._hover_levels) else 0.0

            base_col = _lerp_color(col_muted, col_hover, hv)
            text_col = _lerp_color(base_col, col_active, min(1.0, active_ratio * 1.15))
            p.setPen(text_col)
            p.drawText(text_rect, _ALIGN_CENTER, self.tabText(i))
