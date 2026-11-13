from __future__ import annotations

import math
import numpy as np
from concurrent.futures import ThreadPoolExecutor

from PyQt6.QtCore import Qt, pyqtSignal, QPointF, QRectF
from PyQt6.QtWidgets import (
    QWidget, QGridLayout, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QFrame, QSizePolicy
)
from PyQt6.QtGui import (
    QPainter, QPen, QColor, QFont, QPixmap, QImage,
    QBrush, QPolygonF
)

from core.locale_utils import tr_ui
from .parsers import safe_dcmread


# Safe Qt constants for both PyQt6 and PyQt5
ALIGN_CENTER = Qt.AlignmentFlag.AlignCenter if hasattr(Qt, "AlignmentFlag") else Qt.AlignCenter
ALIGN_LEFT = Qt.AlignmentFlag.AlignLeft if hasattr(Qt, "AlignmentFlag") else Qt.AlignLeft
ALIGN_RIGHT = Qt.AlignmentFlag.AlignRight if hasattr(Qt, "AlignmentFlag") else Qt.AlignRight
CURSOR_CROSS = Qt.CursorShape.CrossCursor if hasattr(Qt, "CursorShape") else Qt.CrossCursor
CURSOR_HAND = Qt.CursorShape.PointingHandCursor if hasattr(Qt, "CursorShape") else Qt.PointingHandCursor
BTN_LEFT = Qt.MouseButton.LeftButton if hasattr(Qt, "MouseButton") else Qt.LeftButton
PEN_SOLID = Qt.PenStyle.SolidLine if hasattr(Qt, "PenStyle") else Qt.SolidLine
PEN_DASH = Qt.PenStyle.DashLine if hasattr(Qt, "PenStyle") else Qt.DashLine

# Projection Plane Constants
PLANE_AXIAL = "axial"       # XY plane (indexed by Z)
PLANE_CORONAL = "coronal"   # XZ plane (indexed by Y)
PLANE_SAGITTAL = "sagittal" # YZ plane (indexed by X)

# Color Scheme for MPR Orthogonal Crosshairs
COLOR_AXIAL = QColor("#38BDF8")     # Cyan/Blue (Z plane)
COLOR_CORONAL = QColor("#4ADE80")   # Green (Y plane)
COLOR_SAGITTAL = QColor("#FB923C")  # Orange/Coral (X plane)


def build_ct_volume(sorted_files: list) -> tuple[np.ndarray, list[float], tuple[float, float, float]] | None:
    """
    Constructs 3D HU volume from sorted CT DICOM slice files.
    Returns (vol, ipp0, (dy, dx, dz)) or None on failure.
    vol shape: (n_z, rows, cols) float32 in Hounsfield Units.
    """
    if not sorted_files:
        return None

    try:
        def _read_slice(item):
            fp = item[0] if isinstance(item, tuple) else item
            try:
                ds = safe_dcmread(fp)
                if (hasattr(ds, "ImagePositionPatient") and hasattr(ds, "pixel_array") and 
                        getattr(ds, "Modality", "CT") == "CT" and ds.pixel_array.ndim == 2):
                    return ds
            except Exception:
                pass
            return None

        # Fast parallel reading of slice pixel arrays
        slices_ds = []
        max_workers = min(8, max(1, len(sorted_files)))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for ds in executor.map(_read_slice, sorted_files):
                if ds is not None:
                    slices_ds.append(ds)

        if not slices_ds:
            return None

        slices_ds.sort(key=lambda s: float(s.ImagePositionPatient[2]))
        n_z = len(slices_ds)
        rows = int(slices_ds[0].Rows)
        cols = int(slices_ds[0].Columns)

        vol = np.zeros((n_z, rows, cols), dtype=np.float32)
        for i, s in enumerate(slices_ds):
            slope = float(getattr(s, "RescaleSlope", 1.0) or 1.0)
            intercept = float(getattr(s, "RescaleIntercept", 0.0) or 0.0)
            vol[i] = s.pixel_array.astype(np.float32) * slope + intercept

        ipp0 = [float(x) for x in slices_ds[0].ImagePositionPatient]
        ipp_last = [float(x) for x in slices_ds[-1].ImagePositionPatient]
        spacing = [float(x) for x in slices_ds[0].PixelSpacing]
        dy, dx = float(spacing[0]), float(spacing[1])
        dz = abs(float(ipp_last[2] - ipp0[2])) / (n_z - 1) if n_z > 1 else 3.0
        if dz <= 0:
            dz = 3.0

        return vol, ipp0, (dy, dx, dz)
    except Exception:
        return None


class MprSliceWidget(QWidget):
    """
    Individual viewport for rendering one orthogonal plane (Axial, Coronal, or Sagittal).
    Maintains physical pixel aspect ratio, crosshair overlay, and interactive navigation.
    """
    cursor_moved = pyqtSignal(int, int, int) # (cur_x, cur_y, cur_z)
    slice_scrolled = pyqtSignal(int, int, int)
    double_clicked = pyqtSignal(str) # plane_type

    def __init__(self, plane_type: str, parent: QWidget = None) -> None:
        super().__init__(parent)
        self.plane_type = plane_type
        self.vol = None
        self.ipp0 = [0.0, 0.0, 0.0]
        self.spacing = (1.0, 1.0, 1.0) # (dy, dx, dz)

        self.cur_x = 0
        self.cur_y = 0
        self.cur_z = 0

        self.window_width = 400.0
        self.window_center = 40.0

        self.current_pixmap = None
        self._target_rect = QRectF()
        self._is_dragging = False

        self.setMouseTracking(True)
        self.setCursor(CURSOR_CROSS)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet("background-color: #0A0A0C; border: 1px solid #1F2937;")

    def set_volume_data(
        self,
        vol: np.ndarray,
        ipp0: list[float],
        spacing: tuple[float, float, float],
        cur_x: int,
        cur_y: int,
        cur_z: int,
        window_width: float,
        window_center: float
    ) -> None:
        self.vol = vol
        self.ipp0 = list(ipp0)
        self.spacing = tuple(spacing)
        self.cur_x = cur_x
        self.cur_y = cur_y
        self.cur_z = cur_z
        self.window_width = window_width
        self.window_center = window_center
        self.refresh_slice()

    def set_cursor(self, cur_x: int, cur_y: int, cur_z: int) -> None:
        need_slice_refresh = False
        if self.plane_type == PLANE_AXIAL and cur_z != self.cur_z:
            need_slice_refresh = True
        elif self.plane_type == PLANE_CORONAL and cur_y != self.cur_y:
            need_slice_refresh = True
        elif self.plane_type == PLANE_SAGITTAL and cur_x != self.cur_x:
            need_slice_refresh = True

        self.cur_x = cur_x
        self.cur_y = cur_y
        self.cur_z = cur_z

        if need_slice_refresh:
            self.refresh_slice()
        else:
            self.update()

    def set_window_params(self, width: float, center: float) -> None:
        if self.window_width != width or self.window_center != center:
            self.window_width = width
            self.window_center = center
            self.refresh_slice()

    def refresh_slice(self) -> None:
        if self.vol is None:
            self.current_pixmap = None
            self.update()
            return

        n_z, rows, cols = self.vol.shape
        self.cur_z = max(0, min(n_z - 1, self.cur_z))
        self.cur_y = max(0, min(rows - 1, self.cur_y))
        self.cur_x = max(0, min(cols - 1, self.cur_x))

        try:
            if self.plane_type == PLANE_AXIAL:
                # XY slice at z: shape (rows, cols)
                slice_hu = self.vol[self.cur_z, :, :]
            elif self.plane_type == PLANE_CORONAL:
                # XZ slice at y: head is superior (vol[::-1, y, :])
                slice_hu = self.vol[::-1, self.cur_y, :]
            elif self.plane_type == PLANE_SAGITTAL:
                # YZ slice at x: head is superior, anterior is left (vol[::-1, :, x])
                slice_hu = self.vol[::-1, :, self.cur_x]
            else:
                return

            ww = max(1.0, float(self.window_width))
            wc = float(self.window_center)
            v_min = wc - ww / 2.0
            
            scaled = np.clip((slice_hu - v_min) / ww * 255.0, 0, 255).astype(np.uint8)
            h, w = scaled.shape
            
            fmt = getattr(QImage.Format, "Format_Grayscale8", getattr(QImage, "Format_Grayscale8", 24))
            # QImage requires a contiguous bytes buffer
            c_data = np.ascontiguousarray(scaled).data
            qimg = QImage(c_data, w, h, w, fmt)
            self.current_pixmap = QPixmap.fromImage(qimg)
        except Exception:
            self.current_pixmap = None

        self.update()

    def _get_physical_dims(self) -> tuple[float, float, int, int]:
        """Returns (phys_w_mm, phys_h_mm, img_w, img_h)."""
        if self.vol is None:
            return 1.0, 1.0, 1, 1
        n_z, rows, cols = self.vol.shape
        dy, dx, dz = self.spacing

        if self.plane_type == PLANE_AXIAL:
            return cols * dx, rows * dy, cols, rows
        elif self.plane_type == PLANE_CORONAL:
            return cols * dx, n_z * dz, cols, n_z
        else: # PLANE_SAGITTAL
            return rows * dy, n_z * dz, rows, n_z

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#0A0A0C"))

        if self.vol is None or self.current_pixmap is None:
            painter.setPen(QColor("#6B7280"))
            painter.setFont(QFont("Segoe UI", 11))
            painter.drawText(self.rect(), ALIGN_CENTER, "No Volume Data")
            return

        w_width = float(self.width())
        w_height = float(self.height())
        phys_w, phys_h, img_w, img_h = self._get_physical_dims()

        # Compute aspect-preserving fit rectangle
        scale = min(w_width / phys_w, w_height / phys_h)
        draw_w = phys_w * scale
        draw_h = phys_h * scale
        draw_x = (w_width - draw_w) / 2.0
        draw_y = (w_height - draw_h) / 2.0
        self._target_rect = QRectF(draw_x, draw_y, draw_w, draw_h)

        # Draw slice pixmap scaled
        painter.drawPixmap(self._target_rect.toRect(), self.current_pixmap)

        # Draw Crosshair
        n_z, rows, cols = self.vol.shape
        pen_horiz = QPen()
        pen_horiz.setWidth(1)
        pen_horiz.setStyle(PEN_DASH)

        pen_vert = QPen()
        pen_vert.setWidth(1)
        pen_vert.setStyle(PEN_DASH)

        if self.plane_type == PLANE_AXIAL:
            # Horizontal: Coronal (cur_y), Vertical: Sagittal (cur_x)
            pen_horiz.setColor(COLOR_CORONAL)
            pen_vert.setColor(COLOR_SAGITTAL)
            cx_img = self.cur_x / max(1, cols)
            cy_img = self.cur_y / max(1, rows)
            cx_px = draw_x + cx_img * draw_w
            cy_px = draw_y + cy_img * draw_h

        elif self.plane_type == PLANE_CORONAL:
            # Horizontal: Axial (cur_z), Vertical: Sagittal (cur_x)
            pen_horiz.setColor(COLOR_AXIAL)
            pen_vert.setColor(COLOR_SAGITTAL)
            cx_img = self.cur_x / max(1, cols)
            cy_img = ((n_z - 1) - self.cur_z) / max(1, n_z)
            cx_px = draw_x + cx_img * draw_w
            cy_px = draw_y + cy_img * draw_h

        else: # PLANE_SAGITTAL
            # Horizontal: Axial (cur_z), Vertical: Coronal (cur_y)
            pen_horiz.setColor(COLOR_AXIAL)
            pen_vert.setColor(COLOR_CORONAL)
            cx_img = self.cur_y / max(1, rows)
            cy_img = ((n_z - 1) - self.cur_z) / max(1, n_z)
            cx_px = draw_x + cx_img * draw_w
            cy_px = draw_y + cy_img * draw_h

        # Draw cross lines with small center gap
        gap = 4.0
        # Horizontal lines (left and right of cursor)
        painter.setPen(pen_horiz)
        if draw_x <= cx_px - gap:
            painter.drawLine(QPointF(draw_x, cy_px), QPointF(cx_px - gap, cy_px))
        if cx_px + gap <= draw_x + draw_w:
            painter.drawLine(QPointF(cx_px + gap, cy_px), QPointF(draw_x + draw_w, cy_px))

        # Vertical lines (top and bottom of cursor)
        painter.setPen(pen_vert)
        if draw_y <= cy_px - gap:
            painter.drawLine(QPointF(cx_px, draw_y), QPointF(cx_px, cy_px - gap))
        if cy_px + gap <= draw_y + draw_h:
            painter.drawLine(QPointF(cx_px, cy_px + gap), QPointF(cx_px, draw_y + draw_h))

        # OSD Header Tag (Plane Name + Index)
        self._paint_osd(painter, n_z, rows, cols)

    def _paint_osd(self, painter: QPainter, n_z: int, rows: int, cols: int) -> None:
        font = QFont("Segoe UI", 9, QFont.Weight.Bold if hasattr(QFont, "Weight") else 75)
        painter.setFont(font)

        if self.plane_type == PLANE_AXIAL:
            title = tr_ui("viewer_mpr_axial") or "Axial"
            badge_color = COLOR_AXIAL
            slice_text = f"{self.cur_z + 1} / {n_z}"
            mm_val = self.ipp0[2] + self.cur_z * self.spacing[2]
            coord_text = f"Z: {mm_val:.1f} mm"
        elif self.plane_type == PLANE_CORONAL:
            title = tr_ui("viewer_mpr_coronal") or "Coronal"
            badge_color = COLOR_CORONAL
            slice_text = f"{self.cur_y + 1} / {rows}"
            mm_val = self.ipp0[1] + self.cur_y * self.spacing[0]
            coord_text = f"Y: {mm_val:.1f} mm"
        else:
            title = tr_ui("viewer_mpr_sagittal") or "Sagittal"
            badge_color = COLOR_SAGITTAL
            slice_text = f"{self.cur_x + 1} / {cols}"
            mm_val = self.ipp0[0] + self.cur_x * self.spacing[1]
            coord_text = f"X: {mm_val:.1f} mm"

        # Badge pill at top-left
        painter.setPen(Qt.PenStyle.NoPen if hasattr(Qt, "PenStyle") else Qt.NoPen)
        b_brush = QBrush(QColor(badge_color.red(), badge_color.green(), badge_color.blue(), 50))
        painter.setBrush(b_brush)
        painter.drawRoundedRect(QRectF(8, 8, 120, 22), 4, 4)

        painter.setPen(badge_color)
        painter.drawText(QRectF(14, 8, 110, 22), ALIGN_LEFT | Qt.AlignmentFlag.AlignVCenter, title)

        # Slice info at bottom-left
        painter.setPen(QColor("#D1D5DB"))
        painter.drawText(QRectF(10, self.height() - 40, 160, 16), ALIGN_LEFT, slice_text)
        painter.setPen(QColor("#9CA3AF"))
        painter.drawText(QRectF(10, self.height() - 22, 160, 16), ALIGN_LEFT, coord_text)

    def mousePressEvent(self, event) -> None:
        if event.button() == BTN_LEFT:
            self._is_dragging = True
            self._update_cursor_from_pos(event.pos())

    def mouseMoveEvent(self, event) -> None:
        if self._is_dragging:
            self._update_cursor_from_pos(event.pos())

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == BTN_LEFT:
            self._is_dragging = False

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == BTN_LEFT:
            self.double_clicked.emit(self.plane_type)

    def wheelEvent(self, event) -> None:
        delta = event.angleDelta().y()
        if delta == 0:
            return
        step = 1 if delta > 0 else -1

        if self.vol is None:
            return
        n_z, rows, cols = self.vol.shape

        new_x = self.cur_x
        new_y = self.cur_y
        new_z = self.cur_z

        if self.plane_type == PLANE_AXIAL:
            new_z = max(0, min(n_z - 1, self.cur_z + step))
        elif self.plane_type == PLANE_CORONAL:
            new_y = max(0, min(rows - 1, self.cur_y + step))
        else: # PLANE_SAGITTAL
            new_x = max(0, min(cols - 1, self.cur_x + step))

        self.set_cursor(new_x, new_y, new_z)
        self.cursor_moved.emit(new_x, new_y, new_z)
        self.slice_scrolled.emit(new_x, new_y, new_z)

    def _update_cursor_from_pos(self, pt) -> None:
        if self.vol is None or self._target_rect.isEmpty():
            return
        n_z, rows, cols = self.vol.shape

        fx = (pt.x() - self._target_rect.left()) / max(1.0, self._target_rect.width())
        fy = (pt.y() - self._target_rect.top()) / max(1.0, self._target_rect.height())

        fx = max(0.0, min(1.0, fx))
        fy = max(0.0, min(1.0, fy))

        new_x = self.cur_x
        new_y = self.cur_y
        new_z = self.cur_z

        if self.plane_type == PLANE_AXIAL:
            new_x = int(round(fx * (cols - 1)))
            new_y = int(round(fy * (rows - 1)))
        elif self.plane_type == PLANE_CORONAL:
            new_x = int(round(fx * (cols - 1)))
            new_z = int(round((1.0 - fy) * (n_z - 1)))
        else: # PLANE_SAGITTAL
            new_y = int(round(fx * (rows - 1)))
            new_z = int(round((1.0 - fy) * (n_z - 1)))

        self.set_cursor(new_x, new_y, new_z)
        self.cursor_moved.emit(new_x, new_y, new_z)


class MprPanelWidget(QWidget):
    """
    2x2 MPR Container widget:
    - (0, 0): Axial View
    - (0, 1): Coronal View
    - (1, 0): Sagittal View
    - (1, 1): 3D Metadata & Orientation Scout Card
    Coordinates, slices, and W/L parameters are coordinated across views.
    """
    cursor_changed = pyqtSignal(int, int, int) # (x, y, z)

    def __init__(self, parent: QWidget = None) -> None:
        super().__init__(parent)
        self.vol = None
        self.ipp0 = [0.0, 0.0, 0.0]
        self.spacing = (1.0, 1.0, 1.0)
        self.cur_x = 0
        self.cur_y = 0
        self.cur_z = 0
        self.window_width = 400.0
        self.window_center = 40.0
        self._maximized_plane = None

        self._setup_ui()

    def _setup_ui(self) -> None:
        self.grid_layout = QGridLayout(self)
        self.grid_layout.setContentsMargins(2, 2, 2, 2)
        self.grid_layout.setSpacing(4)

        # 3 Orthogonal viewports
        self.view_axial = MprSliceWidget(PLANE_AXIAL, self)
        self.view_coronal = MprSliceWidget(PLANE_CORONAL, self)
        self.view_sagittal = MprSliceWidget(PLANE_SAGITTAL, self)

        for v in (self.view_axial, self.view_coronal, self.view_sagittal):
            v.cursor_moved.connect(self._on_subview_cursor_moved)
            v.double_clicked.connect(self._toggle_maximize_plane)

        self.grid_layout.addWidget(self.view_axial, 0, 0)
        self.grid_layout.addWidget(self.view_coronal, 0, 1)
        self.grid_layout.addWidget(self.view_sagittal, 1, 0)

        # 4th Quadrant: 3D Metadata & Orientation Card
        self.info_card = QFrame(self)
        self.info_card.setStyleSheet("""
            QFrame {
                background-color: #111827;
                border: 1px solid #1F2937;
                border-radius: 6px;
                padding: 8px;
            }
        """)
        info_layout = QVBoxLayout(self.info_card)
        info_layout.setContentsMargins(12, 10, 12, 10)
        info_layout.setSpacing(6)

        title_lbl = QLabel(tr_ui("viewer_mpr_3d_info") or "3D Position", self.info_card)
        title_lbl.setStyleSheet("font-size: 13px; font-weight: bold; color: #F3F4F6; border: none;")
        info_layout.addWidget(title_lbl)

        self.lbl_coord_x = QLabel("X: -- mm", self.info_card)
        self.lbl_coord_x.setStyleSheet("font-size: 11px; font-weight: bold; color: #FB923C; border: none;")
        info_layout.addWidget(self.lbl_coord_x)

        self.lbl_coord_y = QLabel("Y: -- mm", self.info_card)
        self.lbl_coord_y.setStyleSheet("font-size: 11px; font-weight: bold; color: #4ADE80; border: none;")
        info_layout.addWidget(self.lbl_coord_y)

        self.lbl_coord_z = QLabel("Z: -- mm", self.info_card)
        self.lbl_coord_z.setStyleSheet("font-size: 11px; font-weight: bold; color: #38BDF8; border: none;")
        info_layout.addWidget(self.lbl_coord_z)

        info_layout.addSpacing(4)
        self.lbl_hu_density = QLabel("HU: --", self.info_card)
        self.lbl_hu_density.setStyleSheet("font-size: 12px; font-weight: bold; color: #FBBF24; border: none;")
        info_layout.addWidget(self.lbl_hu_density)

        info_layout.addStretch()

        self.btn_reset_center = QPushButton("Reset Crosshair", self.info_card)
        self.btn_reset_center.setFixedHeight(26)
        self.btn_reset_center.setCursor(CURSOR_HAND)
        self.btn_reset_center.setStyleSheet("""
            QPushButton {
                background-color: #1F2937;
                border: 1px solid #374151;
                border-radius: 4px;
                color: #E5E7EB;
                font-size: 11px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #374151;
                color: #FFFFFF;
            }
        """)
        self.btn_reset_center.clicked.connect(self.reset_to_center)
        info_layout.addWidget(self.btn_reset_center)

        self.grid_layout.addWidget(self.info_card, 1, 1)

    def set_volume_data(
        self,
        vol: np.ndarray,
        ipp0: list[float],
        spacing: tuple[float, float, float],
        window_width: float,
        window_center: float,
        initial_z: int = None
    ) -> None:
        self.vol = vol
        self.ipp0 = list(ipp0)
        self.spacing = tuple(spacing)
        self.window_width = window_width
        self.window_center = window_center

        n_z, rows, cols = vol.shape
        self.cur_x = cols // 2
        self.cur_y = rows // 2
        self.cur_z = (n_z // 2) if initial_z is None else max(0, min(n_z - 1, initial_z))

        for v in (self.view_axial, self.view_coronal, self.view_sagittal):
            v.set_volume_data(
                vol=self.vol,
                ipp0=self.ipp0,
                spacing=self.spacing,
                cur_x=self.cur_x,
                cur_y=self.cur_y,
                cur_z=self.cur_z,
                window_width=self.window_width,
                window_center=self.window_center
            )

        self._update_info_card()

    def set_cursor(self, cur_x: int, cur_y: int, cur_z: int) -> None:
        self.cur_x = cur_x
        self.cur_y = cur_y
        self.cur_z = cur_z

        for v in (self.view_axial, self.view_coronal, self.view_sagittal):
            v.set_cursor(cur_x, cur_y, cur_z)

        self._update_info_card()

    def set_window_params(self, width: float, center: float) -> None:
        self.window_width = width
        self.window_center = center
        for v in (self.view_axial, self.view_coronal, self.view_sagittal):
            v.set_window_params(width, center)

    def reset_to_center(self) -> None:
        if self.vol is None:
            return
        n_z, rows, cols = self.vol.shape
        cx = cols // 2
        cy = rows // 2
        cz = n_z // 2
        self.set_cursor(cx, cy, cz)
        self.cursor_changed.emit(cx, cy, cz)

    def _on_subview_cursor_moved(self, x: int, y: int, z: int) -> None:
        self.cur_x = x
        self.cur_y = y
        self.cur_z = z
        # Propagate to sibling subviews
        for v in (self.view_axial, self.view_coronal, self.view_sagittal):
            if v != self.sender():
                v.set_cursor(x, y, z)
        self._update_info_card()
        self.cursor_changed.emit(x, y, z)

    def _update_info_card(self) -> None:
        if self.vol is None:
            return
        n_z, rows, cols = self.vol.shape
        dy, dx, dz = self.spacing
        x0, y0, z0 = self.ipp0

        mm_x = x0 + self.cur_x * dx
        mm_y = y0 + self.cur_y * dy
        mm_z = z0 + self.cur_z * dz

        hu_val = float(self.vol[self.cur_z, self.cur_y, self.cur_x])

        self.lbl_coord_x.setText(f"X (Sagittal): {mm_x:.1f} mm  [{self.cur_x + 1}/{cols}]")
        self.lbl_coord_y.setText(f"Y (Coronal):  {mm_y:.1f} mm  [{self.cur_y + 1}/{rows}]")
        self.lbl_coord_z.setText(f"Z (Axial):    {mm_z:.1f} mm  [{self.cur_z + 1}/{n_z}]")

        # Contextual tissue density tag
        tissue = "Soft Tissue"
        if hu_val < -500:
            tissue = "Air / Cavity"
        elif hu_val < -30:
            tissue = "Adipose / Fat"
        elif hu_val <= 100:
            tissue = "Muscle / Tissue"
        elif hu_val <= 1000:
            tissue = "Trabecular Bone"
        elif hu_val <= 2000:
            tissue = "Cortical Bone"
        else:
            tissue = "Metal Artifact / Implant"

        self.lbl_hu_density.setText(f"HU: {hu_val:+.0f} ({tissue})")

    def _toggle_maximize_plane(self, plane_type: str) -> None:
        if self._maximized_plane == plane_type:
            # Restore 2x2 layout
            self._maximized_plane = None
            self.view_axial.show()
            self.view_coronal.show()
            self.view_sagittal.show()
            self.info_card.show()
        else:
            # Maximize clicked plane
            self._maximized_plane = plane_type
            for p, v in [
                (PLANE_AXIAL, self.view_axial),
                (PLANE_CORONAL, self.view_coronal),
                (PLANE_SAGITTAL, self.view_sagittal),
            ]:
                if p == plane_type:
                    v.show()
                else:
                    v.hide()
            self.info_card.hide()
