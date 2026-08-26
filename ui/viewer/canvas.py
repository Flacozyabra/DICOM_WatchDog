from __future__ import annotations

import math
from collections import defaultdict
import numpy as np
from PyQt6.QtCore import Qt, pyqtSignal, QPoint, QPointF, QRect, QRectF
from PyQt6.QtWidgets import QWidget, QApplication, QMenu
from PyQt6.QtGui import (
    QFont, QPixmap, QBrush, QColor, QPainter,
    QPen, QImage, QPolygonF, QPainterPath, QTransform
)

from core.locale_utils import tr_ui
from .parsers import (
    safe_dcmread, _convex_hull_2d, get_dose_slice_at_z,
    dose_slice_to_rgba, marching_squares_2d
)


class DicomViewerWidget(QWidget):
    """Виджет для отрисовки DICOM-изображения, линейки и контуров структур RTSTRUCT."""
    slice_scrolled = pyqtSignal(int)
    window_changed = pyqtSignal(float, float)
    bev_beam_changed = pyqtSignal(int)
    bev_control_point_changed = pyqtSignal(int)
    drr_precompute_requested = pyqtSignal()

    def __init__(self, parent: QWidget = None) -> None:
        super().__init__(parent)
        self.current_pixmap = None
        self.current_dataset = None
        self.image_rect = None

        self.current_slice = 0
        self.total_slices = 0

        self.window_width = 400.0
        self.window_center = 40.0

        self.zoom_factor = 1.0
        self.pan_offset = QPointF(0, 0)
        self.last_mouse_pos = None

        self.ruler_active = False
        self.hu_active = False

        self.start_pos = None
        self.current_pos = None
        self.drawing_line = False
        self.ruler_close_rect = None

        self.windowing_active = False
        self.pan_active = False

        self.osd_visible = True

        # Структуры RTSTRUCT
        self.structures = {}
        self.enabled_structures = set()
        self.show_structures_globally = True
        self.contour_sop_index = {}
        self.contour_z_index = []

        # Изодозы RTDOSE
        self.dose_data = {}
        self.enabled_isodose_levels = set()
        self.show_isodoses_globally = False
        self.show_dose_gradient = True
        self.dose_point_active = False
        self.hover_pos = None
        self.pinned_dose_pos = None
        self.dose_point_close_rect = None

        # Данные RTPLAN и режим BEV (Beam's Eye View)
        self.plan_data = {}
        self.show_beams = True
        self.bev_active = False
        self.show_drr = False
        self.bev_selected_beam_idx = 0
        self.bev_control_point_idx = 0
        self.bev_prev_btn_rect = None
        self.bev_next_btn_rect = None
        self.bev_drr_btn_rect = None
        self.bev_cp_slider_rect = None

        # Кэш DRR, объем КТ и кэш 3D-проекций структур BEV
        self.drr_cache = {}
        self.bev_struct_cache = {}
        self.bev_precomputing_status = ""
        self.ct_volume = None
        self.ct_ipp0 = None
        self.ct_spacing = None
        self.sorted_files = []

        self.setMouseTracking(True)
        self.setStyleSheet("background-color: #000000;")

    def clear_viewer(self) -> None:
        self.current_pixmap = None
        self.raw_pixel_array = None
        self.current_sop_uid = ""
        self.current_z = None
        self.structures.clear()
        self.enabled_structures.clear()
        self.contour_sop_index.clear()
        self.contour_z_index.clear()
        self.dose_data.clear()
        self.enabled_isodose_levels.clear()
        self.plan_data.clear()
        self.pinned_dose_pos = None
        self.hover_pos = None
        self.dose_point_active = False
        self.ruler_active = False
        self.hu_active = False
        self.bev_active = False
        self.show_drr = False
        self.bev_selected_beam_idx = 0
        self.bev_control_point_idx = 0
        self.bev_precomputing_status = ""
        self.drr_cache.clear()
        self.bev_struct_cache.clear()
        self.ct_volume = None
        self.ct_ipp0 = None
        self.ct_spacing = None
        self.sorted_files.clear()
        self.update()

    def set_show_beams(self, show: bool) -> None:
        if self.show_beams != show:
            self.show_beams = show
            self.update()

    def set_dose_data(self, dose_data: dict) -> None:
        self.dose_data = dose_data or {}
        self.enabled_isodose_levels = {lvl["name"] for lvl in self.dose_data.get("levels", []) if lvl.get("enabled", True)}
        self.update()

    def set_plan_data(self, plan_data: dict) -> None:
        self.plan_data = plan_data or {}
        self.bev_selected_beam_idx = 0
        self.bev_control_point_idx = 0
        self.drr_cache.clear()
        self.update()

    def rebuild_contour_index(self) -> None:
        self.contour_sop_index = {}
        self.contour_z_index = []

        if not self.structures or not self.show_structures_globally:
            return

        for roi_num, roi_data in self.structures.items():
            name = roi_data["name"]
            color = roi_data["color"]
            if name not in self.enabled_structures:
                continue

            for contour in roi_data["contours"]:
                sop_uid = contour.get("sop_uid")
                z_coord = contour.get("z")
                points = contour.get("points")
                if not points:
                    continue

                if sop_uid:
                    if sop_uid not in self.contour_sop_index:
                        self.contour_sop_index[sop_uid] = []
                    self.contour_sop_index[sop_uid].append((color, points))

                if z_coord is not None:
                    self.contour_z_index.append((z_coord, color, points))

    def set_osd_visible(self, visible: bool) -> None:
        self.osd_visible = visible
        self.update()

    def set_dicom_image(self, pixmap: QPixmap, ds) -> None:
        self.current_pixmap = pixmap
        self.current_dataset = ds
        self.update()

    def set_window_params(self, width: float, center: float) -> None:
        self.window_width = width
        self.window_center = center
        self.update()

    def set_slice_info(self, current: int, total: int) -> None:
        self.current_slice = current
        self.total_slices = total
        self.update()

    def clear_viewer(self) -> None:
        self.current_pixmap = None
        self.current_dataset = None
        self.start_pos = None
        self.current_pos = None
        self.drawing_line = False
        self.ruler_close_rect = None
        self.windowing_active = False
        self.pan_active = False
        self.zoom_factor = 1.0
        self.pan_offset = QPointF(0, 0)
        self.current_slice = 0
        self.total_slices = 0
        self.ruler_active = False
        self.hu_active = False
        self.structures = {}
        self.enabled_structures = set()
        self.show_structures_globally = True
        self.contour_sop_index = {}
        self.contour_z_index = []
        self.dose_data = {}
        self.enabled_isodose_levels.clear()
        self.show_isodoses_globally = False
        self.show_dose_gradient = True
        self.dose_point_active = False
        self.hover_pos = None
        self.pinned_dose_pos = None
        self.dose_point_close_rect = None
        self.plan_data = {}
        self.bev_active = False
        self.bev_selected_beam_idx = 0
        self.bev_control_point_idx = 0
        self.drr_cache.clear()
        self.ct_volume = None
        self.ct_ipp0 = None
        self.ct_spacing = None
        self.sorted_files = []
        self.update()

    def get_or_compute_drr(self, g_angle: float, iso: list[float], sad: float = 1000.0) -> QImage | None:
        """
        Генерирует и кэширует DRR (Digitally Reconstructed Radiograph) проекцию пациента
        для заданного угла гентри, изоцентра пучка и расстояния SAD.
        """
        if not iso or len(iso) < 3:
            return None

        cache_key = (round(float(g_angle), 1), round(float(iso[0]), 2), round(float(iso[1]), 2), round(float(iso[2]), 2), round(float(sad), 1))
        if cache_key in self.drr_cache:
            return self.drr_cache[cache_key]

        # Ищем ближайший рассчитанный угол в пределах 3.05° (для мгновенного плавного скраббинга VMAT)
        cur_angle = float(g_angle)
        best_diff = 3.05
        best_img = None
        for ck, img in self.drr_cache.items():
            if len(ck) >= 4 and ck[1] == cache_key[1] and ck[2] == cache_key[2] and ck[3] == cache_key[3]:
                diff = abs(ck[0] - cur_angle)
                if diff < best_diff:
                    best_diff = diff
                    best_img = img
        if best_img is not None:
            return best_img

        if self.ct_volume is None:
            if not self.sorted_files:
                return None
            try:
                slices_ds = []
                for item in self.sorted_files:
                    f_path = item[0] if isinstance(item, tuple) else item
                    ds = safe_dcmread(f_path)
                    if hasattr(ds, "ImagePositionPatient") and hasattr(ds, "pixel_array") and getattr(ds, "Modality", "CT") == "CT" and ds.pixel_array.ndim == 2:
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
                dz = (ipp_last[2] - ipp0[2]) / (n_z - 1) if n_z > 1 else 5.0
                
                self.ct_volume = vol
                self.ct_ipp0 = ipp0
                self.ct_spacing = (dy, dx, dz)
            except Exception:
                return None

        vol = self.ct_volume
        ipp0 = self.ct_ipp0
        dy, dx, dz = self.ct_spacing
        n_z, rows, cols = vol.shape
        vol_pre = np.maximum(0.0, vol + 500.0)

        try:
            drr_fov = 400.0
            drr_w, drr_h = 160, 160
            sad = float(sad or 1000.0)
            g_rad = math.radians(g_angle)
            sin_g = math.sin(g_rad)
            cos_g = math.cos(g_rad)

            u = np.linspace(-drr_fov / 2.0, drr_fov / 2.0, drr_w, dtype=np.float32)
            v = np.linspace(drr_fov / 2.0, -drr_fov / 2.0, drr_h, dtype=np.float32)
            U, V = np.meshgrid(u, v)

            Sx = iso[0] + sad * sin_g
            Sy = iso[1] - sad * cos_g
            Sz = iso[2]

            P_iso_x = iso[0] + U * cos_g
            P_iso_y = iso[1] + U * sin_g
            P_iso_z = iso[2] + V

            Dx = P_iso_x - Sx
            Dy = P_iso_y - Sy
            Dz = P_iso_z - Sz
            D_len = np.sqrt(Dx * Dx + Dy * Dy + Dz * Dz)
            Dx /= D_len
            Dy /= D_len
            Dz /= D_len

            steps = np.arange(sad - 200.0, sad + 200.0, 6.0, dtype=np.float32)
            drr = np.zeros((drr_h, drr_w), dtype=np.float32)

            for t in steps:
                Px = Sx + Dx * t
                Py = Sy + Dy * t
                Pz = Sz + Dz * t
                ix = np.round((Px - ipp0[0]) / dx).astype(np.int32)
                iy = np.round((Py - ipp0[1]) / dy).astype(np.int32)
                iz = np.round((Pz - ipp0[2]) / dz).astype(np.int32)
                valid = (ix >= 0) & (ix < cols) & (iy >= 0) & (iy < rows) & (iz >= 0) & (iz < n_z)
                drr[valid] += vol_pre[iz[valid], iy[valid], ix[valid]]

            d_min = drr.min()
            d_max = drr.max()
            drr_norm = (drr - d_min) / (d_max - d_min + 1e-5)
            drr_u8 = (drr_norm * 255.0).astype(np.uint8)
            drr_rgba = np.stack([drr_u8, drr_u8, drr_u8, np.full_like(drr_u8, 255)], axis=-1)

            b = drr_rgba.tobytes()
            q_img = QImage(b, drr_w, drr_h, drr_w * 4, QImage.Format.Format_RGBA8888).copy()
            self.drr_cache[cache_key] = q_img
            return q_img
        except Exception:
            return None

    def toggle_drr(self) -> None:
        if self.show_drr:
            self.show_drr = False
            self.update()
        else:
            self.drr_precompute_requested.emit()

    def mousePressEvent(self, event) -> None:
        self.setFocus()
        if self.bev_active:
            if event.button() == Qt.MouseButton.LeftButton:
                pos = event.position().toPoint()
                beams = self.plan_data.get("beams", [])
                if self.bev_prev_btn_rect and self.bev_prev_btn_rect.contains(pos):
                    if beams:
                        self.bev_selected_beam_idx = (self.bev_selected_beam_idx - 1) % len(beams)
                        self.bev_control_point_idx = 0
                        self.bev_beam_changed.emit(self.bev_selected_beam_idx)
                        self.update()
                    return
                elif hasattr(self, "bev_title_btn_rect") and self.bev_title_btn_rect and self.bev_title_btn_rect.contains(pos):
                    if beams:
                        menu = QMenu(self)
                        menu.setStyleSheet("""
                            QMenu {
                                background-color: #0F172A;
                                color: #F8FAFC;
                                border: 1px solid #334155;
                                border-radius: 8px;
                                padding: 4px;
                                font-family: "Segoe UI";
                                font-size: 13px;
                            }
                            QMenu::item {
                                padding: 6px 20px 6px 24px;
                                border-radius: 4px;
                            }
                            QMenu::item:selected {
                                background-color: #2563EB;
                                color: #FFFFFF;
                            }
                        """)
                        for b_idx, b in enumerate(beams):
                            b_name = b.get("display_name", f"Поле {b_idx + 1}")
                            prefix = "✔  " if b_idx == self.bev_selected_beam_idx else "    "
                            action = menu.addAction(f"{prefix}{b_name}")
                            action.setData(b_idx)

                        global_pt = self.mapToGlobal(QPoint(self.bev_title_btn_rect.left(), self.bev_title_btn_rect.bottom() + 4))
                        selected_action = menu.exec(global_pt)
                        if selected_action is not None and selected_action.data() is not None:
                            new_idx = int(selected_action.data())
                            if new_idx != self.bev_selected_beam_idx:
                                self.bev_selected_beam_idx = new_idx
                                self.bev_control_point_idx = 0
                                self.bev_beam_changed.emit(self.bev_selected_beam_idx)
                                self.bev_control_point_changed.emit(0)
                                self.update()
                    return
                elif self.bev_next_btn_rect and self.bev_next_btn_rect.contains(pos):
                    if beams:
                        self.bev_selected_beam_idx = (self.bev_selected_beam_idx + 1) % len(beams)
                        self.bev_control_point_idx = 0
                        self.bev_beam_changed.emit(self.bev_selected_beam_idx)
                        self.bev_control_point_changed.emit(0)
                        self.update()
                    return
                elif self.bev_drr_btn_rect and self.bev_drr_btn_rect.contains(pos):
                    self.toggle_drr()
                    return
            return

        if not self.current_pixmap:
            return

        btn = event.button()
        pos = event.position()

        if btn == Qt.MouseButton.LeftButton and self.ruler_active and self.ruler_close_rect and self.ruler_close_rect.contains(pos.toPoint()):
            self.start_pos = None
            self.current_pos = None
            self.drawing_line = False
            self.ruler_close_rect = None
            self.update()
            return

        if btn == Qt.MouseButton.LeftButton and self.dose_point_active:
            if self.dose_point_close_rect and self.dose_point_close_rect.contains(pos.toPoint()):
                self.pinned_dose_pos = None
                self.dose_point_close_rect = None
                self.update()
                return

            if self.image_rect and self.image_rect.contains(pos.toPoint()):
                self.pinned_dose_pos = pos
                self.update()
                return

        if btn in (Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton):
            self.pan_active = True
            self.last_mouse_pos = event.position()
        elif btn == Qt.MouseButton.LeftButton:
            if self.ruler_active:
                self.start_pos = event.position()
                self.current_pos = event.position()
                self.drawing_line = True
                self.update()
            elif self.hu_active:
                self.windowing_active = True
                self.last_mouse_pos = event.position()

    def mouseMoveEvent(self, event) -> None:

        if self.dose_point_active:
            self.hover_pos = event.position()
            self.update()

        if self.pan_active and self.last_mouse_pos:
            delta = event.position() - self.last_mouse_pos
            self.pan_offset += delta
            self.last_mouse_pos = event.position()
            self.update()
        elif self.drawing_line:
            self.current_pos = event.position()
            self.update()
        elif self.windowing_active and self.last_mouse_pos:
            delta = event.position() - self.last_mouse_pos
            self.last_mouse_pos = event.position()
            self.window_width = max(1.0, self.window_width + delta.x() * 2.0)
            self.window_center = self.window_center + delta.y() * 2.0
            self.window_changed.emit(self.window_width, self.window_center)

    def leaveEvent(self, event) -> None:
        if self.dose_point_active:
            self.hover_pos = None
            self.update()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        btn = event.button()
        if btn in (Qt.MouseButton.MiddleButton, Qt.MouseButton.RightButton):
            self.pan_active = False
        elif btn == Qt.MouseButton.LeftButton:
            if self.drawing_line:
                self.current_pos = event.position()
                self.drawing_line = False
                self.update()
            elif self.windowing_active:
                self.windowing_active = False

    def get_dose_at_point(self, pt_widget: QPointF) -> tuple[float, float, str] | None:
        """
        Вычисляет интерполированную дозу в 3D-точке для заданных экранных координат холста.
        Возвращает (доза_Гр, процент_от_Rx, ед_измерения) или None.
        """
        if not self.dose_data or "dose_grid" not in self.dose_data or not self.current_dataset or not self.image_rect:
            return None

        x_img, y_img = self.to_image_coords(pt_widget)
        ipp = getattr(self.current_dataset, "ImagePositionPatient", None)
        iop = getattr(self.current_dataset, "ImageOrientationPatient", None)
        pixel_spacing = getattr(self.current_dataset, "PixelSpacing", None)

        if not ipp or not iop or not pixel_spacing or len(ipp) < 3 or len(iop) < 6 or len(pixel_spacing) < 2:
            return None

        ipp_x, ipp_y, ipp_z = float(ipp[0]), float(ipp[1]), float(ipp[2])
        xr, yr, zr = float(iop[0]), float(iop[1]), float(iop[2])
        xc, yc, zc = float(iop[3]), float(iop[4]), float(iop[5])
        dy, dx = float(pixel_spacing[0]), float(pixel_spacing[1])

        x_pat = ipp_x + x_img * dx * xr + y_img * dy * xc
        y_pat = ipp_y + x_img * dx * yr + y_img * dy * yc
        z_pat = ipp_z + x_img * dx * zr + y_img * dy * zc

        slice_grid = get_dose_slice_at_z(self.dose_data, z_pat)
        if slice_grid is None:
            return None

        d_ipp = self.dose_data.get("ipp", [0.0, 0.0, 0.0])
        d_iop = self.dose_data.get("iop", [1.0, 0.0, 0.0, 0.0, 1.0, 0.0])
        d_dy, d_dx = self.dose_data.get("pixel_spacing", [1.0, 1.0])

        xr_d, yr_d, zr_d = float(d_iop[0]), float(d_iop[1]), float(d_iop[2])
        xc_d, yc_d, zc_d = float(d_iop[3]), float(d_iop[4]), float(d_iop[5])

        dp_x = x_pat - d_ipp[0]
        dp_y = y_pat - d_ipp[1]
        dp_z = z_pat - d_ipp[2]

        c = (dp_x * xr_d + dp_y * yr_d + dp_z * zr_d) / d_dx
        r = (dp_x * xc_d + dp_y * yc_d + dp_z * zc_d) / d_dy

        rows_d, cols_d = slice_grid.shape
        if 0.0 <= r <= float(rows_d - 1) and 0.0 <= c <= float(cols_d - 1):
            r0 = int(r)
            c0 = int(c)
            r1 = min(rows_d - 1, r0 + 1)
            c1 = min(cols_d - 1, c0 + 1)

            dr = r - r0
            dc = c - c0

            v00 = slice_grid[r0, c0]
            v01 = slice_grid[r0, c1]
            v10 = slice_grid[r1, c0]
            v11 = slice_grid[r1, c1]

            val = float((1.0 - dr) * (1.0 - dc) * v00 + (1.0 - dr) * dc * v01 + dr * (1.0 - dc) * v10 + dr * dc * v11)
        else:
            val = 0.0

        rx = float(self.dose_data.get("rx_dose", 0.0))
        units = str(self.dose_data.get("dose_units", "Gy"))
        pct = (val / rx * 100.0) if rx > 0 else 0.0
        return val, pct, units

    def _draw_dose_probe(self, painter: QPainter, pt: QPointF, is_pinned: bool) -> None:
        dose_info = self.get_dose_at_point(pt)
        if dose_info is None:
            return

        val, pct, units = dose_info
        cx, cy = pt.x(), pt.y()

        color_reticle = QColor("#F59E0B") if is_pinned else QColor("#38BDF8")
        pen_reticle = QPen(color_reticle, 1.5, Qt.PenStyle.SolidLine)
        painter.setPen(pen_reticle)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        painter.drawEllipse(QPointF(cx, cy), 10, 10)
        painter.drawEllipse(QPointF(cx, cy), 3, 3)

        painter.drawLine(QPointF(cx, cy - 16), QPointF(cx, cy - 10))
        painter.drawLine(QPointF(cx, cy + 10), QPointF(cx, cy + 16))
        painter.drawLine(QPointF(cx - 16, cy), QPointF(cx - 10, cy))
        painter.drawLine(QPointF(cx + 10, cy), QPointF(cx + 16, cy))

        text_val = f"{val:.2f} {units}"
        text_pct = f"({pct:.1f}%)" if pct > 0 else ""
        full_text = f"{text_val}  {text_pct}".strip()

        font = QFont("Consolas", 10, QFont.Weight.Bold)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        text_w = metrics.horizontalAdvance(full_text)
        text_h = metrics.height()

        padding_x = 8
        padding_y = 4
        cross_w = 12 if is_pinned else 0
        gap = 6 if is_pinned else 0

        badge_w = text_w + padding_x * 2 + cross_w + gap
        badge_h = text_h + padding_y * 2

        bx = cx + 18
        by = cy - badge_h - 10
        if bx + badge_w > self.width() - 10:
            bx = cx - badge_w - 18
        if by < 10:
            by = cy + 18

        badge_rect = QRectF(bx, by, badge_w, badge_h)

        painter.setPen(QPen(color_reticle, 1.2))
        painter.setBrush(QBrush(QColor(15, 23, 42, 220)))
        painter.drawRoundedRect(badge_rect, 6, 6)

        painter.setPen(QColor("#FFFFFF"))
        text_draw_rect = QRectF(bx + padding_x, by + padding_y, text_w, text_h)
        painter.drawText(text_draw_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, full_text)

        if is_pinned:
            close_x = bx + padding_x + text_w + gap
            close_y = by + (badge_h - cross_w) / 2
            close_rect = QRect(int(close_x), int(close_y), cross_w, cross_w)
            self.dose_point_close_rect = close_rect

            painter.setPen(QPen(QColor("#EF4444"), 1.8))
            margin = 2
            painter.drawLine(
                int(close_x + margin), int(close_y + margin),
                int(close_x + cross_w - margin), int(close_y + cross_w - margin)
            )
            painter.drawLine(
                int(close_x + cross_w - margin), int(close_y + margin),
                int(close_x + margin), int(close_y + cross_w - margin)
            )

    def wheelEvent(self, event) -> None:
        if self.bev_active:
            beams = self.plan_data.get("beams", [])
            if not beams:
                return
            beam = beams[self.bev_selected_beam_idx]
            cps = beam.get("control_points", [])
            delta = event.angleDelta().y()
            if beam.get("is_dynamic", False) and len(cps) > 1:
                step = 1 if delta < 0 else -1
                self.bev_control_point_idx = max(0, min(len(cps) - 1, self.bev_control_point_idx + step))
                self.bev_control_point_changed.emit(self.bev_control_point_idx)
                self.update()
            else:
                step = 1 if delta < 0 else -1
                self.bev_selected_beam_idx = (self.bev_selected_beam_idx + step) % len(beams)
                self.bev_control_point_idx = 0
                self.bev_beam_changed.emit(self.bev_selected_beam_idx)
                self.bev_control_point_changed.emit(0)
                self.update()
            return

        modifiers = QApplication.keyboardModifiers()
        if modifiers == Qt.KeyboardModifier.ControlModifier:
            delta = event.angleDelta().y()
            if delta > 0:
                self.zoom_factor = max(0.1, self.zoom_factor - 0.1)
            elif delta < 0:
                self.zoom_factor = min(10.0, self.zoom_factor + 0.1)
            self.update()
        else:
            delta = event.angleDelta().y()
            if delta > 0:
                self.slice_scrolled.emit(-1)
            elif delta < 0:
                self.slice_scrolled.emit(1)

    def to_image_coords(self, pt: QPointF) -> tuple[float, float]:
        if not self.image_rect or not self.current_pixmap:
            return 0.0, 0.0

        x_w = pt.x()
        y_w = pt.y()

        offset_x = self.image_rect.x()
        offset_y = self.image_rect.y()
        view_w = self.image_rect.width()
        view_h = self.image_rect.height()

        pix_w = self.current_pixmap.width()
        pix_h = self.current_pixmap.height()

        x_img = (x_w - offset_x) * (pix_w / view_w)
        y_img = (y_w - offset_y) * (pix_h / view_h)

        x_img = max(0.0, min(float(pix_w), x_img))
        y_img = max(0.0, min(float(pix_h), y_img))
        return x_img, y_img

    def _paint_bev(self, painter: QPainter) -> None:
        painter.fillRect(self.rect(), QColor("#0B0F19"))
        beams = self.plan_data.get("beams", [])
        if not beams:
            return

        idx = max(0, min(len(beams) - 1, self.bev_selected_beam_idx))
        beam = beams[idx]

        w = self.width()
        h = self.height()
        cx = w / 2.0
        cy = h / 2.0

        cps = beam.get("control_points", [])
        cp_idx = max(0, min(len(cps) - 1, self.bev_control_point_idx)) if cps else 0
        cp = cps[cp_idx] if cps else {}

        g_angle = cp.get("gantry_angle", beam.get("gantry_angle", 0.0))
        c_angle = cp.get("collimator_angle", beam.get("collimator_angle", 0.0))
        couch_angle = cp.get("couch_angle", beam.get("couch_angle", 0.0))
        jaws = cp.get("jaws", beam.get("jaws", {"x": [-100.0, 100.0], "y": [-100.0, 100.0]}))
        mlc = cp.get("mlc_leaves", beam.get("mlc_leaves", []))
        leaf_bounds = cp.get("leaf_boundaries", beam.get("leaf_boundaries", []))
        wedges = beam.get("wedges", [])
        rad_type = beam.get("radiation_type", "PHOTON")
        mach = beam.get("machine_name", "")

        scale = min(w, h - 80) / 440.0
        r_field = 200.0 * scale
        sad = float(beam.get("sad", 1000.0) or 1000.0)
        iso = cp.get("isocenter", beam.get("isocenter", [0.0, 0.0, 0.0]))

        def mm_to_canvas(x_mm, y_mm):
            return QPointF(cx + x_mm * scale, cy - y_mm * scale)

        # 1. Фоновый круг коллиматора
        painter.setPen(QPen(QColor("#1E293B"), 2))
        painter.setBrush(QBrush(QColor("#0F172A")))
        painter.drawEllipse(QPointF(cx, cy), r_field, r_field)

        # 2. Отрисовка DRR и 3D-проекций контуров RTSTRUCT (внутри круглой апертуры)
        painter.save()
        clip_path = QPainterPath()
        clip_path.addEllipse(QPointF(cx, cy), r_field, r_field)
        painter.setClipPath(clip_path)

        # 2.1. DRR (Digitally Reconstructed Radiograph)
        if self.show_drr:
            drr_img = self.get_or_compute_drr(g_angle, iso, sad)
            if drr_img and not drr_img.isNull():
                drr_rect = QRectF(cx - 200.0 * scale, cy - 200.0 * scale, 400.0 * scale, 400.0 * scale)
                painter.drawImage(drr_rect, drr_img)

        # 2.2. Проекция 3D контуров RTSTRUCT на плоскость детектора / изоцентра BEV (непрерывный объемный силуэт)
        if self.show_structures_globally and self.structures and iso and len(iso) >= 3:
            g_rad = math.radians(g_angle)
            sin_g = math.sin(g_rad)
            cos_g = math.cos(g_rad)
            Sx = iso[0] + sad * sin_g
            Sy = iso[1] - sad * cos_g
            Sz = iso[2]

            def project_pt_mm(x, y, z):
                rx = x - Sx
                ry = y - Sy
                rz = z - Sz
                dz_p = -rx * sin_g + ry * cos_g
                if dz_p > 50.0:
                    M = sad / dz_p
                    u_p = (rx * cos_g + ry * sin_g) * M
                    v_p = rz * M
                    return (u_p, -v_p)
                return None

            trans = QTransform()
            trans.translate(cx, cy)
            trans.scale(scale, scale)

            for roi_num, s in self.structures.items():
                name = s.get("name", "")
                if self.enabled_structures and name not in self.enabled_structures:
                    continue
                s_color = s.get("color", QColor("#10B981"))
                if isinstance(s_color, (tuple, list)):
                    s_color = QColor(*s_color)

                contours = s.get("contours", [])
                if not contours:
                    continue

                cache_key = (
                    roi_num,
                    round(float(g_angle) / 2.0) * 2.0,
                    round(float(iso[0]), 1),
                    round(float(iso[1]), 1),
                    round(float(iso[2]), 1),
                    round(float(sad), 1)
                )
                if cache_key in self.bev_struct_cache:
                    struct_path_mm, pois_mm = self.bev_struct_cache[cache_key]
                else:
                    struct_path_mm = QPainterPath()
                    pois_mm = []

                    # Одноточечные ориентиры (POI, например ICRU / реперные точки)
                    if len(contours) == 1 and len(contours[0].get("points", [])) == 1:
                        pt = contours[0]["points"][0]
                        ppt = project_pt_mm(pt[0], pt[1], pt[2])
                        if ppt:
                            pois_mm.append((name, ppt[0], ppt[1]))
                    else:
                        z_map = defaultdict(list)
                        for c in contours:
                            pts = c.get("points", [])
                            if len(pts) >= 3:
                                z_key = round(float(c.get("z", pts[0][2])), 2)
                                z_map[z_key].append(pts)

                        sorted_z = sorted(z_map.keys())
                        if sorted_z:
                            step_z = max(1, len(sorted_z) // 60) if len(sorted_z) > 70 else 1
                            sample_z = sorted_z[::step_z]

                            prev_slice_pts = None
                            for z_val in sample_z:
                                polys_on_z = z_map[z_val]
                                slice_all_pts_2d = []

                                for pts in polys_on_z:
                                    step_p = max(1, len(pts) // 35) if len(pts) > 45 else 1
                                    pts_2d = [project_pt_mm(p[0], p[1], p[2]) for p in pts[::step_p]]
                                    valid_pts = [p for p in pts_2d if p is not None]
                                    if len(valid_pts) >= 3:
                                        slice_all_pts_2d.extend(valid_pts)
                                        p_c = QPainterPath()
                                        p_c.addPolygon(QPolygonF([QPointF(p[0], p[1]) for p in valid_pts]))
                                        p_c.closeSubpath()
                                        struct_path_mm = struct_path_mm.united(p_c) if not struct_path_mm.isEmpty() else p_c

                                if prev_slice_pts and slice_all_pts_2d:
                                    hull = _convex_hull_2d(prev_slice_pts + slice_all_pts_2d)
                                    if len(hull) >= 3:
                                        hull_path = QPainterPath()
                                        hull_path.addPolygon(QPolygonF([QPointF(p[0], p[1]) for p in hull]))
                                        hull_path.closeSubpath()
                                        struct_path_mm = struct_path_mm.united(hull_path)

                                prev_slice_pts = slice_all_pts_2d

                    self.bev_struct_cache[cache_key] = (struct_path_mm, pois_mm)

                # Отрисовка силуэта структуры
                if not struct_path_mm.isEmpty():
                    canvas_path = trans.map(struct_path_mm)
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.setPen(QPen(s_color, 2.0))
                    painter.drawPath(canvas_path)

                # Отрисовка точечных маркеров POI
                for poi_name, poi_u, poi_v in pois_mm:
                    p_canvas = QPointF(cx + poi_u * scale, cy + poi_v * scale)
                    painter.setPen(QPen(s_color, 2.0))
                    painter.setBrush(QBrush(s_color))
                    painter.drawEllipse(p_canvas, 3.5, 3.5)
                    painter.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
                    painter.drawText(int(p_canvas.x() + 6), int(p_canvas.y() + 4), poi_name)

        painter.restore()

        # 3. Сетка коллиматора (неподвижная система отсчета гентри)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(148, 163, 184, 50), 1, Qt.PenStyle.DashLine))
        for r_cm in [5, 10, 15, 20]:
            r_px = r_cm * 10.0 * scale
            if r_px <= r_field:
                painter.drawEllipse(QPointF(cx, cy), r_px, r_px)

        painter.setPen(QPen(QColor(148, 163, 184, 80), 1))
        painter.drawLine(QPointF(cx - r_field, cy), QPointF(cx + r_field, cy))
        painter.drawLine(QPointF(cx, cy - r_field), QPointF(cx, cy + r_field))

        for cm in range(-20, 21):
            if cm == 0:
                continue
            px = cx + cm * 10.0 * scale
            py = cy - cm * 10.0 * scale
            if abs(cm * 10.0 * scale) <= r_field:
                painter.drawLine(QPointF(px, cy - 3), QPointF(px, cy + 3))
                painter.drawLine(QPointF(cx - 3, py), QPointF(cx + 3, py))

        # 4. Аналитическое преобразование коллиматора по IEC 61217 в систему координат BEV:
        c_rad = math.radians(c_angle)
        cos_c, sin_c = math.cos(c_rad), math.sin(c_rad)

        def coll_to_canvas(xc, yc):
            xg = xc * cos_c - yc * sin_c
            yg = xc * sin_c + yc * cos_c
            return QPointF(cx + xg * scale, cy - yg * scale)

        jx1, jx2 = jaws.get("x", [-100.0, 100.0])
        jy1, jy2 = jaws.get("y", [-100.0, 100.0])

        # 4.1. Лепестки MLC и активная апертура
        if mlc and len(mlc) >= 2:
            num_pairs = len(mlc) // 2
            mach = str(beam.get("machine_name", "")).upper()
            if num_pairs == 40 and ("TERABALT" in mach or not leaf_bounds or (leaf_bounds and abs(leaf_bounds[1] - leaf_bounds[0] - 11.0) < 1.5)):
                leaf_bounds = (
                    [-150.0 + i * 10.0 for i in range(10)] +
                    [-50.0 + i * 5.0 for i in range(20)] +
                    [50.0 + i * 10.0 for i in range(11)]
                )
            elif not leaf_bounds or len(leaf_bounds) != num_pairs + 1:
                total_span = 400.0
                step = total_span / num_pairs
                leaf_bounds = [-200.0 + i * step for i in range(num_pairs + 1)]

            # Находим открытые пары лепестков (апертура > 3 мм)
            open_pairs = [i for i in range(num_pairs) if mlc[num_pairs + i] > mlc[i] + 3.0]

            # Тонкие направляющие линии активных лепестков
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(234, 179, 8, 40), 1))
            for i in open_pairs:
                y_c = leaf_bounds[i]
                painter.drawLine(coll_to_canvas(mlc[i], y_c), coll_to_canvas(mlc[num_pairs + i], y_c))

            # Ступенчатый золотой замкнутый контур активной апертуры поля MLC
            painter.setPen(QPen(QColor("#F59E0B"), 2.2))
            if open_pairs:
                poly_pts = []
                for idx_k, i in enumerate(open_pairs):
                    y_bot = leaf_bounds[i]
                    y_top = leaf_bounds[i + 1]
                    pos_a = mlc[i]
                    if idx_k == 0:
                        poly_pts.append(coll_to_canvas(pos_a, y_bot))
                    else:
                        prev_i = open_pairs[idx_k - 1]
                        if prev_i == i - 1:
                            prev_a = mlc[prev_i]
                            if abs(pos_a - prev_a) > 0.1:
                                poly_pts.append(coll_to_canvas(pos_a, y_bot))
                    poly_pts.append(coll_to_canvas(pos_a, y_top))

                for idx_k in range(len(open_pairs) - 1, -1, -1):
                    i = open_pairs[idx_k]
                    y_bot = leaf_bounds[i]
                    y_top = leaf_bounds[i + 1]
                    pos_b = mlc[num_pairs + i]
                    if idx_k == len(open_pairs) - 1:
                        poly_pts.append(coll_to_canvas(pos_b, y_top))
                    else:
                        next_i = open_pairs[idx_k + 1]
                        if next_i == i + 1:
                            next_b = mlc[num_pairs + next_i]
                            if abs(pos_b - next_b) > 0.1:
                                poly_pts.append(coll_to_canvas(pos_b, y_top))
                    poly_pts.append(coll_to_canvas(pos_b, y_bot))

                poly_pts.append(poly_pts[0])
                painter.drawPolyline(QPolygonF(poly_pts))

        # 4.2. Пунктирная граница шторок Jaws
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor("#38BDF8"), 1.8, Qt.PenStyle.DashLine))
        p_j1 = coll_to_canvas(jx1, jy1)
        p_j2 = coll_to_canvas(jx2, jy1)
        p_j3 = coll_to_canvas(jx2, jy2)
        p_j4 = coll_to_canvas(jx1, jy2)
        painter.drawPolygon(QPolygonF([p_j1, p_j2, p_j3, p_j4]))

        # 5. Центральный перекрест изоцентра
        painter.setPen(QPen(QColor("#EF4444"), 2))
        painter.drawLine(QPointF(cx - 15, cy), QPointF(cx + 15, cy))
        painter.drawLine(QPointF(cx, cy - 15), QPointF(cx, cy + 15))
        painter.drawEllipse(QPointF(cx, cy), 3, 3)

        # 6. Направления осей (неподвижная система отсчета пациента)
        painter.setFont(QFont("Consolas", 9, QFont.Weight.Bold))
        painter.setPen(QColor("#94A3B8"))
        painter.drawText(int(cx + 6), int(cy - r_field + 16), "GUN / TOP (Y+)")
        painter.drawText(int(cx + 6), int(cy + r_field - 6), "TARGET / BOT (Y-)")
        painter.drawText(int(cx - r_field + 6), int(cy - 6), "RIGHT (X-)")
        painter.drawText(int(cx + r_field - 70), int(cy - 6), "LEFT (X+)")

        # 8. Заголовок и селектор полей
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        header_text = beam.get("display_name") or f"Поле {idx + 1}"
        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
        m_head = painter.fontMetrics()
        txt_w = m_head.horizontalAdvance(header_text)

        btn_w, btn_h = 34, 32
        btn_drr_w = 58
        top_y = 15
        box_w = max(240, txt_w + 44)
        gap = 6

        rect_title = QRect(int(cx - box_w / 2), top_y, box_w, btn_h)
        rect_prev = QRect(rect_title.left() - gap - btn_w, top_y, btn_w, btn_h)
        rect_next = QRect(rect_title.right() + gap, top_y, btn_w, btn_h)
        rect_drr = QRect(rect_next.right() + gap, top_y, btn_drr_w, btn_h)

        self.bev_prev_btn_rect = rect_prev
        self.bev_title_btn_rect = rect_title
        self.bev_next_btn_rect = rect_next
        self.bev_drr_btn_rect = rect_drr

        # Кнопка «Назад ◀»
        painter.setPen(QPen(QColor(51, 65, 85, 220), 1.0))
        painter.setBrush(QColor(15, 23, 42, 220))
        painter.drawRoundedRect(rect_prev, 8, 8)
        painter.setPen(QColor("#93C5FD"))
        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        painter.drawText(rect_prev, Qt.AlignmentFlag.AlignCenter, "◀")

        # Выпадающее поле названия «[ DisplayName ▾ ]»
        painter.setPen(QPen(QColor(59, 130, 246, 200), 1.0))
        painter.setBrush(QColor(15, 23, 42, 230))
        painter.drawRoundedRect(rect_title, 8, 8)
        painter.setPen(QColor("#F8FAFC"))
        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
        
        text_rect = rect_title.adjusted(12, 0, -22, 0)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, header_text)
        
        # Стрелочка выпадающего меню ▾
        arrow_rect = QRect(rect_title.right() - 22, top_y, 16, btn_h)
        painter.setPen(QColor("#60A5FA"))
        painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        painter.drawText(arrow_rect, Qt.AlignmentFlag.AlignCenter, "▼")

        # Кнопка «Вперед ▶»
        painter.setPen(QPen(QColor(51, 65, 85, 220), 1.0))
        painter.setBrush(QColor(15, 23, 42, 220))
        painter.drawRoundedRect(rect_next, 8, 8)
        painter.setPen(QColor("#93C5FD"))
        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        painter.drawText(rect_next, Qt.AlignmentFlag.AlignCenter, "▶")

        # Кнопка «DRR»
        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        if self.show_drr:
            painter.setPen(QPen(QColor("#3B82F6"), 1.2))
            painter.setBrush(QColor(37, 99, 235, 240))
            painter.drawRoundedRect(rect_drr, 8, 8)
            painter.setPen(QColor("#FFFFFF"))
        else:
            painter.setPen(QPen(QColor(51, 65, 85, 200), 1.0))
            painter.setBrush(QColor(15, 23, 42, 220))
            painter.drawRoundedRect(rect_drr, 8, 8)
            painter.setPen(QColor("#94A3B8"))
        painter.drawText(rect_drr, Qt.AlignmentFlag.AlignCenter, "DRR")

        # 9. Информационная плашка поля
        lines_specs = [
            f"Gantry: {g_angle:.1f}°",
            f"Collimator: {c_angle:.1f}°",
            f"Couch: {couch_angle:.1f}°",
            f"Jaws X: [{jx1:.1f}, {jx2:.1f}] mm",
            f"Jaws Y: [{jy1:.1f}, {jy2:.1f}] mm",
            f"Machine: {mach}",
            f"Radiation: {rad_type}"
        ]
        if wedges:
            for w_item in wedges:
                lines_specs.append(f"Wedge: {w_item.get('id')} ({w_item.get('angle')}°)")

        painter.setFont(QFont("Consolas", 10, QFont.Weight.Bold))
        metrics_s = painter.fontMetrics()
        y_spec = 15
        for line in lines_specs:
            rect_l = metrics_s.boundingRect(line)
            rect_l.setWidth(rect_l.width() + 14)
            rect_l.moveTopLeft(QPoint(15, y_spec))
            painter.fillRect(rect_l.adjusted(-4, -2, 4, 2), QColor(0, 0, 0, 160))
            painter.setPen(QColor("#E2E8F0"))
            painter.drawText(rect_l, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, line)
            y_spec += rect_l.height() + 4

        self.bev_cp_slider_rect = None
        self.bev_cp_track_rect = None

        # 10. Индикатор фонового предрасчета 3D-проекций BEV
        if getattr(self, "bev_precomputing_status", ""):
            st_text = f"⏳ {self.bev_precomputing_status}"
            painter.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
            st_metrics = painter.fontMetrics()
            st_w = st_metrics.horizontalAdvance(st_text) + 20
            st_rect = QRect(w - st_w - 15, h - 35, st_w, 24)
            painter.fillRect(st_rect, QColor(30, 41, 59, 230))
            painter.setPen(QPen(QColor("#3B82F6"), 1.2))
            painter.drawRoundedRect(st_rect, 4, 4)
            painter.setPen(QColor("#93C5FD"))
            painter.drawText(st_rect, Qt.AlignmentFlag.AlignCenter, st_text)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#000000"))

        if self.bev_active and self.plan_data and self.plan_data.get("beams"):
            self._paint_bev(painter)
            return

        if self.current_pixmap:
            pix_w = self.current_pixmap.width()
            pix_h = self.current_pixmap.height()
            w = self.width()
            h = self.height()

            scale = min(w / pix_w, h / pix_h) * self.zoom_factor
            view_w = int(pix_w * scale)
            view_h = int(pix_h * scale)

            offset_x = (w - view_w) // 2 + int(self.pan_offset.x())
            offset_y = (h - view_h) // 2 + int(self.pan_offset.y())

            self.image_rect = QRect(offset_x, offset_y, view_w, view_h)
            painter.drawPixmap(self.image_rect, self.current_pixmap)

            # Отрисовка цветового градиента дозы (Dose Colorwash)
            if self.show_dose_gradient and self.dose_data:
                ipp = getattr(self.current_dataset, "ImagePositionPatient", None)
                iop = getattr(self.current_dataset, "ImageOrientationPatient", None)
                pixel_spacing = getattr(self.current_dataset, "PixelSpacing", None)
                
                if ipp is not None and iop is not None and pixel_spacing is not None and len(ipp) >= 3 and len(iop) >= 6 and len(pixel_spacing) >= 2:
                    ipp_x, ipp_y, ipp_z = float(ipp[0]), float(ipp[1]), float(ipp[2])
                    xr, yr, zr = float(iop[0]), float(iop[1]), float(iop[2])
                    xc, yc, zc = float(iop[3]), float(iop[4]), float(iop[5])
                    dy, dx = float(pixel_spacing[0]), float(pixel_spacing[1])
                    
                    rows = getattr(self.current_dataset, "Rows", 512)
                    cols = getattr(self.current_dataset, "Columns", 512)
                    
                    scale_x = view_w / cols
                    scale_y = view_h / rows

                    slice_grid = get_dose_slice_at_z(self.dose_data, ipp_z)
                    if slice_grid is not None:
                        rx = float(self.dose_data.get("rx_dose", 0.0))
                        mx = float(self.dose_data.get("max_dose", 0.0))
                        rgba = dose_slice_to_rgba(
                            slice_grid,
                            rx,
                            mx,
                            self.dose_data.get("levels", []),
                            self.enabled_isodose_levels
                        )
                        if rgba is not None:
                            d_ipp = self.dose_data.get("ipp", [0.0, 0.0, 0.0])
                            d_iop = self.dose_data.get("iop", [1.0, 0.0, 0.0, 0.0, 1.0, 0.0])
                            d_dy, d_dx = self.dose_data.get("pixel_spacing", [1.0, 1.0])
                            
                            xr_d, yr_d, zr_d = float(d_iop[0]), float(d_iop[1]), float(d_iop[2])
                            xc_d, yc_d, zc_d = float(d_iop[3]), float(d_iop[4]), float(d_iop[5])
                            
                            rows_d, cols_d = slice_grid.shape
                            
                            # Угловые точки сетки дозы в координатах холста
                            dp_tl_x = d_ipp[0] - ipp_x
                            dp_tl_y = d_ipp[1] - ipp_y
                            dp_tl_z = ipp_z - ipp_z
                            px_tl = (dp_tl_x * xr + dp_tl_y * yr + dp_tl_z * zr) / dx
                            py_tl = (dp_tl_x * xc + dp_tl_y * yc + dp_tl_z * zc) / dy
                            wx_tl = offset_x + px_tl * scale_x
                            wy_tl = offset_y + py_tl * scale_y

                            x_br = d_ipp[0] + cols_d * d_dx * xr_d + rows_d * d_dy * xc_d
                            y_br = d_ipp[1] + cols_d * d_dx * yr_d + rows_d * d_dy * yc_d
                            dp_br_x = x_br - ipp_x
                            dp_br_y = y_br - ipp_y
                            dp_br_z = ipp_z - ipp_z
                            px_br = (dp_br_x * xr + dp_br_y * yr + dp_br_z * zr) / dx
                            py_br = (dp_br_x * xc + dp_br_y * yc + dp_br_z * zc) / dy
                            wx_br = offset_x + px_br * scale_x
                            wy_br = offset_y + py_br * scale_y

                            target_rect = QRectF(QPointF(wx_tl, wy_tl), QPointF(wx_br, wy_br)).normalized()
                            
                            self._temp_dose_rgba = np.ascontiguousarray(rgba)
                            qimg_dose = QImage(self._temp_dose_rgba.data, cols_d, rows_d, cols_d * 4, QImage.Format.Format_RGBA8888)
                            
                            prev_smooth = painter.renderHints() & QPainter.RenderHint.SmoothPixmapTransform
                            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
                            painter.drawImage(target_rect, qimg_dose)
                            if not prev_smooth:
                                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)

            # Отрисовка RTSTRUCT-структур поверх изображения
            if self.show_structures_globally and (self.contour_sop_index or self.contour_z_index):
                ipp = getattr(self.current_dataset, "ImagePositionPatient", None)
                iop = getattr(self.current_dataset, "ImageOrientationPatient", None)
                pixel_spacing = getattr(self.current_dataset, "PixelSpacing", None)
                sop_uid = getattr(self.current_dataset, "SOPInstanceUID", None)
                
                if ipp is not None and iop is not None and pixel_spacing is not None and len(ipp) >= 3 and len(iop) >= 6 and len(pixel_spacing) >= 2:
                    ipp_x, ipp_y, ipp_z = float(ipp[0]), float(ipp[1]), float(ipp[2])
                    xr, yr, zr = float(iop[0]), float(iop[1]), float(iop[2])
                    xc, yc, zc = float(iop[3]), float(iop[4]), float(iop[5])
                    dy, dx = float(pixel_spacing[0]), float(pixel_spacing[1])
                    
                    rows = getattr(self.current_dataset, "Rows", 512)
                    cols = getattr(self.current_dataset, "Columns", 512)
                    
                    scale_x = view_w / cols
                    scale_y = view_h / rows
                    
                    # Мгновенная выборка контуров текущего среза O(1)
                    matching_contours = []
                    if sop_uid and sop_uid in self.contour_sop_index:
                        matching_contours = self.contour_sop_index[sop_uid]
                    elif self.contour_z_index:
                        matching_contours = [(color, pts) for z, color, pts in self.contour_z_index if abs(z - ipp_z) < 0.5]
                        
                    for color, points in matching_contours:
                        pen = QPen(color, 2, Qt.PenStyle.SolidLine)
                        painter.setPen(pen)
                        painter.setBrush(Qt.BrushStyle.NoBrush)
                        
                        poly = QPolygonF()
                        for pt in points:
                            dp_x = pt[0] - ipp_x
                            dp_y = pt[1] - ipp_y
                            dp_z = pt[2] - ipp_z
                            
                            px = (dp_x * xr + dp_y * yr + dp_z * zr) / dx
                            py = (dp_x * xc + dp_y * yc + dp_z * zc) / dy
                            
                            wx = offset_x + px * scale_x
                            wy = offset_y + py * scale_y
                            poly.append(QPointF(wx, wy))
                            
                        if not poly.isEmpty():
                            painter.drawPolygon(poly)

            # Отрисовка изодоз RTDOSE поверх изображения
            if self.show_isodoses_globally and self.dose_data:
                ipp = getattr(self.current_dataset, "ImagePositionPatient", None)
                iop = getattr(self.current_dataset, "ImageOrientationPatient", None)
                pixel_spacing = getattr(self.current_dataset, "PixelSpacing", None)
                
                if ipp is not None and iop is not None and pixel_spacing is not None and len(ipp) >= 3 and len(iop) >= 6 and len(pixel_spacing) >= 2:
                    ipp_x, ipp_y, ipp_z = float(ipp[0]), float(ipp[1]), float(ipp[2])
                    xr, yr, zr = float(iop[0]), float(iop[1]), float(iop[2])
                    xc, yc, zc = float(iop[3]), float(iop[4]), float(iop[5])
                    dy, dx = float(pixel_spacing[0]), float(pixel_spacing[1])
                    
                    rows = getattr(self.current_dataset, "Rows", 512)
                    cols = getattr(self.current_dataset, "Columns", 512)
                    
                    scale_x = view_w / cols
                    scale_y = view_h / rows

                    slice_grid = get_dose_slice_at_z(self.dose_data, ipp_z)
                    if slice_grid is not None:
                        d_ipp = self.dose_data.get("ipp", [0.0, 0.0, 0.0])
                        d_iop = self.dose_data.get("iop", [1.0, 0.0, 0.0, 0.0, 1.0, 0.0])
                        d_dy, d_dx = self.dose_data.get("pixel_spacing", [1.0, 1.0])
                        
                        xr_d, yr_d, zr_d = float(d_iop[0]), float(d_iop[1]), float(d_iop[2])
                        xc_d, yc_d, zc_d = float(d_iop[3]), float(d_iop[4]), float(d_iop[5])
                        
                        for lvl in self.dose_data.get("levels", []):
                            if lvl["name"] not in self.enabled_isodose_levels:
                                continue
                            val = float(lvl["val"])
                            segments = marching_squares_2d(slice_grid, val)
                            if not segments:
                                continue
                            
                            pen = QPen(lvl["color"], 2, Qt.PenStyle.SolidLine)
                            painter.setPen(pen)
                            
                            for p1, p2 in segments:
                                c1, r1 = p1
                                c2, r2 = p2
                                
                                x1_p = d_ipp[0] + c1 * d_dx * xr_d + r1 * d_dy * xc_d
                                y1_p = d_ipp[1] + c1 * d_dx * yr_d + r1 * d_dy * yc_d
                                z1_p = ipp_z
                                
                                dp1_x = x1_p - ipp_x
                                dp1_y = y1_p - ipp_y
                                dp1_z = z1_p - ipp_z
                                px1 = (dp1_x * xr + dp1_y * yr + dp1_z * zr) / dx
                                py1 = (dp1_x * xc + dp1_y * yc + dp1_z * zc) / dy
                                wx1 = offset_x + px1 * scale_x
                                wy1 = offset_y + py1 * scale_y
                                
                                x2_p = d_ipp[0] + c2 * d_dx * xr_d + r2 * d_dy * xc_d
                                y2_p = d_ipp[1] + c2 * d_dx * yr_d + r2 * d_dy * yc_d
                                z2_p = ipp_z
                                
                                dp2_x = x2_p - ipp_x
                                dp2_y = y2_p - ipp_y
                                dp2_z = z2_p - ipp_z
                                px2 = (dp2_x * xr + dp2_y * yr + dp2_z * zr) / dx
                                py2 = (dp2_x * xc + dp2_y * yc + dp2_z * zc) / dy
                                wx2 = offset_x + px2 * scale_x
                                wy2 = offset_y + py2 * scale_y
                                
                                painter.drawLine(QPointF(wx1, wy1), QPointF(wx2, wy2))

            # Отрисовка изоцентра и геометрии пучков RTPLAN
            if self.show_beams and self.plan_data and self.current_dataset:
                ipp = getattr(self.current_dataset, "ImagePositionPatient", None)
                iop = getattr(self.current_dataset, "ImageOrientationPatient", None)
                pixel_spacing = getattr(self.current_dataset, "PixelSpacing", None)
                
                if ipp is not None and iop is not None and pixel_spacing is not None and len(ipp) >= 3 and len(iop) >= 6 and len(pixel_spacing) >= 2:
                    ipp_x, ipp_y, ipp_z = float(ipp[0]), float(ipp[1]), float(ipp[2])
                    xr, yr, zr = float(iop[0]), float(iop[1]), float(iop[2])
                    xc, yc, zc = float(iop[3]), float(iop[4]), float(iop[5])
                    dy, dx = float(pixel_spacing[0]), float(pixel_spacing[1])

                    thickness = float(getattr(self.current_dataset, "SliceThickness", 5.0) or 5.0)

                    rows = getattr(self.current_dataset, "Rows", 512)
                    cols = getattr(self.current_dataset, "Columns", 512)
                    scale_x = view_w / cols
                    scale_y = view_h / rows

                    beams = self.plan_data.get("beams", [])
                    
                    # 1. Отслеживаем индекс динамических дуг для каскадных радиусов
                    dyn_arc_idx = 0

                    for beam in beams:
                        iso = beam.get("isocenter")
                        if not iso or len(iso) < 3:
                            continue
                        iso_x, iso_y, iso_z = iso[0], iso[1], iso[2]
                        dp_x = iso_x - ipp_x
                        dp_y = iso_y - ipp_y
                        dp_z = iso_z - ipp_z

                        c_angle = beam.get("collimator_angle", 0.0)
                        jaws_x = beam.get("jaws", {}).get("x", [-50.0, 50.0])
                        jaws_y = beam.get("jaws", {}).get("y", [-100.0, 100.0])
                        jx1_mm = float(jaws_x[0]) if len(jaws_x) >= 2 else -50.0
                        jx2_mm = float(jaws_x[1]) if len(jaws_x) >= 2 else 50.0
                        jy1_mm = float(jaws_y[0]) if len(jaws_y) >= 2 else -100.0
                        jy2_mm = float(jaws_y[1]) if len(jaws_y) >= 2 else 100.0

                        # Преобразование координат шторок с учетом поворота коллиматора c_angle
                        c_rad = math.radians(c_angle)
                        cos_c = math.cos(c_rad)
                        sin_c = math.sin(c_rad)

                        corners_xc = [jx1_mm, jx1_mm, jx2_mm, jx2_mm]
                        corners_yc = [jy1_mm, jy2_mm, jy1_mm, jy2_mm]
                        corners_xg = [xc * cos_c - yc * sin_c for xc, yc in zip(corners_xc, corners_yc)]
                        corners_yg = [xc * sin_c + yc * cos_c for xc, yc in zip(corners_xc, corners_yc)]

                        w1_mm = min(corners_xg)
                        w2_mm = max(corners_xg)
                        z1_mm = min(corners_yg)
                        z2_mm = max(corners_yg)

                        max_z_dist = max(35.0, abs(z1_mm), abs(z2_mm))

                        # Проверяем попадание текущего среза в продольный охват пучка
                        if abs(dp_z) <= max_z_dist:
                            px_iso = (dp_x * xr + dp_y * yr + dp_z * zr) / dx
                            py_iso = (dp_x * xc + dp_y * yc + dp_z * zc) / dy
                            wx_iso = offset_x + px_iso * scale_x
                            wy_iso = offset_y + py_iso * scale_y

                            b_num = beam.get("number", 1)
                            b_name = beam.get("name", "") or f"Beam {b_num}"
                            is_dynamic = beam.get("is_dynamic", False)
                            g_start = beam.get("gantry_start", beam.get("gantry_angle", 0.0))
                            g_stop = beam.get("gantry_stop", g_start)
                            rot_dir = beam.get("gantry_rotation_direction", "NONE")

                            wedges = beam.get("wedges", [])
                            wedge_info = ""
                            if wedges:
                                w_id = wedges[0].get("id", "")
                                w_ang = wedges[0].get("angle", "")
                                wedge_info = f" ▲ {w_id} ({w_ang}°)"

                            is_exact_iso_slice = (abs(dp_z) <= thickness / 2.0 + 0.5)

                            # 1. Если это динамическая ротационная дуга (VMAT / Arc) с вращением гантри
                            if is_dynamic and abs(g_start - g_stop) > 0.5:
                                # Каскадный радиус для каждой дуги (предотвращает наложение при нескольких дугах)
                                arc_radius = (135.0 + dyn_arc_idx * 28.0) * self.zoom_factor
                                dyn_arc_idx += 1

                                pts_arc = []
                                if rot_dir in ("CW", "CLOCKWISE"):
                                    span = (g_stop - g_start) if g_stop >= g_start else (g_stop + 360.0 - g_start)
                                    steps = max(2, int(span / 4.0))
                                    angles = [(g_start + (span * s / steps)) % 360.0 for s in range(steps + 1)]
                                elif rot_dir in ("CC", "CCW", "COUNTER_CLOCKWISE"):
                                    span = (g_start - g_stop) if g_start >= g_stop else (g_start + 360.0 - g_stop)
                                    steps = max(2, int(span / 4.0))
                                    angles = [(g_start - (span * s / steps)) % 360.0 for s in range(steps + 1)]
                                else:
                                    span = abs(g_stop - g_start)
                                    steps = max(2, int(span / 4.0))
                                    angles = [g_start + (g_stop - g_start) * (s / steps) for s in range(steps + 1)]

                                for ang in angles:
                                    rad_a = math.radians(ang)
                                    pts_arc.append(QPointF(wx_iso + math.sin(rad_a) * arc_radius, wy_iso - math.cos(rad_a) * arc_radius))

                                is_ccw = rot_dir in ("CC", "CCW", "COUNTER_CLOCKWISE")
                                arc_color = QColor("#06B6D4") if is_ccw else QColor("#F59E0B")
                                arc_fill = QColor(6, 182, 212, 22) if is_ccw else QColor(245, 158, 11, 22)

                                # Полупрозрачная кольцевая полоса дуги
                                if len(pts_arc) >= 2:
                                    pts_inner = []
                                    r_in = max(10.0, arc_radius - 8.0 * self.zoom_factor)
                                    r_out = arc_radius + 8.0 * self.zoom_factor
                                    for ang in angles:
                                        rad_a = math.radians(ang)
                                        pts_inner.append(QPointF(wx_iso + math.sin(rad_a) * r_in, wy_iso - math.cos(rad_a) * r_in))
                                    pts_outer = []
                                    for ang in reversed(angles):
                                        rad_a = math.radians(ang)
                                        pts_outer.append(QPointF(wx_iso + math.sin(rad_a) * r_out, wy_iso - math.cos(rad_a) * r_out))
                                    
                                    painter.setPen(Qt.PenStyle.NoPen)
                                    painter.setBrush(QBrush(arc_fill))
                                    painter.drawPolygon(QPolygonF(pts_inner + pts_outer))

                                # Граничные направляющие
                                pen_ray = QPen(arc_color, 1.4, Qt.PenStyle.DashLine)
                                painter.setPen(pen_ray)
                                if pts_arc:
                                    painter.drawLine(QPointF(wx_iso, wy_iso), pts_arc[0])
                                    painter.drawLine(QPointF(wx_iso, wy_iso), pts_arc[-1])

                                # Дуговая линия
                                pen_arc = QPen(arc_color, 2.0, Qt.PenStyle.SolidLine)
                                painter.setPen(pen_arc)
                                for i in range(len(pts_arc) - 1):
                                    painter.drawLine(pts_arc[i], pts_arc[i+1])

                                # Стрелка направления в середине дуги
                                mid_idx = len(pts_arc) // 2
                                if len(pts_arc) > 2 and mid_idx > 0:
                                    p_prev = pts_arc[mid_idx - 1]
                                    p_mid = pts_arc[mid_idx]
                                    d_vec = p_mid - p_prev
                                    len_d = math.hypot(d_vec.x(), d_vec.y())
                                    if len_d > 0.001:
                                        ux = d_vec.x() / len_d
                                        uy = d_vec.y() / len_d
                                        perp_x = -uy
                                        perp_y = ux
                                        a_head = p_mid
                                        a1 = p_mid - QPointF(ux * 10 - perp_x * 5, uy * 10 - perp_y * 5)
                                        a2 = p_mid - QPointF(ux * 10 + perp_x * 5, uy * 10 + perp_y * 5)
                                        painter.setBrush(QBrush(arc_color))
                                        painter.setPen(Qt.PenStyle.NoPen)
                                        painter.drawPolygon(QPolygonF([a_head, a1, a2]))

                                # Плашка арки (размещается на своем каскадном радиусе)
                                mid_pt = pts_arc[mid_idx] if pts_arc else QPointF(wx_iso, wy_iso - arc_radius)
                                dir_txt = "CCW" if is_ccw else "CW"
                                badge_text = f"[{b_num}] {g_start:.0f}°->{g_stop:.0f}° {dir_txt}".strip()
                                
                                painter.setFont(QFont("Consolas", 9, QFont.Weight.Bold))
                                m_b = painter.fontMetrics()
                                rect_b_txt = m_b.boundingRect(badge_text)
                                badge_rect = QRectF(
                                    mid_pt.x() - rect_b_txt.width() / 2 - 6,
                                    mid_pt.y() - rect_b_txt.height() / 2 - 3,
                                    rect_b_txt.width() + 12,
                                    rect_b_txt.height() + 6
                                )
                                painter.setPen(QPen(arc_color, 1.2))
                                painter.setBrush(QBrush(QColor(15, 23, 42, 230)))
                                painter.drawRoundedRect(badge_rect, 4, 4)
                                painter.setPen(QColor("#FFFFFF"))
                                painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, badge_text)

                            else:
                                # 2. Статический пучок (3D-CRT / IMRT) - только центральная ось пучка
                                ray_len = 160.0 * self.zoom_factor
                                g_angle = beam.get("gantry_angle", 0.0)
                                rad = math.radians(g_angle)
                                sx = math.sin(rad)
                                sy = -math.cos(rad)

                                wx_src = wx_iso + sx * ray_len
                                wy_src = wy_iso + sy * ray_len

                                # 2.1 Центральная ось пучка
                                pen_ray = QPen(QColor("#F59E0B"), 1.8, Qt.PenStyle.SolidLine)
                                painter.setPen(pen_ray)
                                painter.drawLine(QPointF(wx_src, wy_src), QPointF(wx_iso, wy_iso))

                                # 2.2 Стрелка направления входа на изоцентре
                                arrow_len = 12.0
                                arr_dx = -sx
                                arr_dy = -sy
                                arr_px = -arr_dy
                                arr_py = arr_dx
                                p_head = QPointF(wx_iso, wy_iso)
                                p_a1 = QPointF(wx_iso - arr_dx * arrow_len + arr_px * 6, wy_iso - arr_dy * arrow_len + arr_py * 6)
                                p_a2 = QPointF(wx_iso - arr_dx * arrow_len - arr_px * 6, wy_iso - arr_dy * arrow_len - arr_py * 6)
                                painter.setPen(QPen(QColor("#F59E0B"), 1.8))
                                painter.setBrush(QBrush(QColor("#F59E0B")))
                                painter.drawPolygon(QPolygonF([p_head, p_a1, p_a2]))

                                # 2.3 Бейдж с номером, углом и клином
                                badge_beam_text = f"[{b_num}] {g_angle:.1f}°{wedge_info}"
                                painter.setFont(QFont("Consolas", 9, QFont.Weight.Bold))
                                m_b = painter.fontMetrics()
                                rect_b_txt = m_b.boundingRect(badge_beam_text)
                                badge_rect = QRectF(
                                    wx_src - rect_b_txt.width() / 2 - 6,
                                    wy_src - rect_b_txt.height() / 2 - 3,
                                    rect_b_txt.width() + 12,
                                    rect_b_txt.height() + 6
                                )
                                painter.setPen(QPen(QColor("#F59E0B"), 1.2))
                                painter.setBrush(QBrush(QColor(15, 23, 42, 230)))
                                painter.drawRoundedRect(badge_rect, 4, 4)
                                painter.setPen(QColor("#FFFFFF"))
                                painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, badge_beam_text)

                            # 3. Маркер изоцентра
                            if is_exact_iso_slice:
                                painter.setPen(QPen(QColor("#EAB308"), 2.0))
                                painter.setBrush(Qt.BrushStyle.NoBrush)
                                painter.drawEllipse(QPointF(wx_iso, wy_iso), 7, 7)
                                painter.drawLine(QPointF(wx_iso - 12, wy_iso), QPointF(wx_iso + 12, wy_iso))
                                painter.drawLine(QPointF(wx_iso, wy_iso - 12), QPointF(wx_iso, wy_iso + 12))

                                painter.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
                                painter.setPen(QColor("#EAB308"))
                                painter.drawText(int(wx_iso + 10), int(wy_iso - 8), "ISO")
                            else:
                                painter.setPen(QPen(QColor(234, 179, 8, 120), 1.2, Qt.PenStyle.DotLine))
                                painter.setBrush(Qt.BrushStyle.NoBrush)
                                painter.drawLine(QPointF(wx_iso - 6, wy_iso), QPointF(wx_iso + 6, wy_iso))
                                painter.drawLine(QPointF(wx_iso, wy_iso - 6), QPointF(wx_iso, wy_iso + 6))

            # Отрисовка измерительной линейки
            if self.ruler_active and self.start_pos and self.current_pos:
                pen = QPen(QColor("#10B981"), 2, Qt.PenStyle.SolidLine)
                painter.setPen(pen)
                painter.drawLine(self.start_pos.toPoint(), self.current_pos.toPoint())

                self.draw_tick(painter, self.start_pos, self.current_pos)
                self.draw_tick(painter, self.current_pos, self.start_pos)

                x1, y1 = self.to_image_coords(self.start_pos)
                x2, y2 = self.to_image_coords(self.current_pos)

                row_spacing = 1.0
                col_spacing = 1.0
                if self.current_dataset:
                    pixel_spacing = getattr(self.current_dataset, "PixelSpacing", None)
                    if pixel_spacing and len(pixel_spacing) == 2:
                        row_spacing = float(pixel_spacing[0])
                        col_spacing = float(pixel_spacing[1])
                    else:
                        imager_spacing = getattr(self.current_dataset, "ImagerPixelSpacing", None)
                        if imager_spacing and len(imager_spacing) == 2:
                            row_spacing = float(imager_spacing[0])
                            col_spacing = float(imager_spacing[1])

                dx = (x2 - x1) * col_spacing
                dy = (y2 - y1) * row_spacing
                dist_mm = math.sqrt(dx * dx + dy * dy)
                
                text_dist = f"{dist_mm:.1f} " + tr_ui("hud_mm")
                mid_x = (self.start_pos.x() + self.current_pos.x()) / 2
                mid_y = (self.start_pos.y() + self.current_pos.y()) / 2

                font = QFont("Consolas", 10, QFont.Weight.Bold)
                painter.setFont(font)
                metrics = painter.fontMetrics()
                rect_dist = metrics.boundingRect(text_dist)

                padding_x = 4
                padding_y = 2
                cross_width = 12
                space = 6

                total_w = rect_dist.width() + cross_width + space
                total_h = max(rect_dist.height(), cross_width)

                rect_plate = QRect(
                    int(mid_x - total_w / 2 - padding_x),
                    int(mid_y - 15 - total_h / 2 - padding_y),
                    int(total_w + padding_x * 2),
                    int(total_h + padding_y * 2)
                )

                painter.fillRect(rect_plate, QColor(0, 0, 0, 180))

                painter.setPen(QColor("#FFFFFF"))
                text_rect = QRect(
                    rect_plate.x() + padding_x,
                    rect_plate.y() + padding_y,
                    rect_dist.width(),
                    total_h
                )
                painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text_dist)

                cross_rect = QRect(
                    text_rect.x() + text_rect.width() + space,
                    rect_plate.y() + (rect_plate.height() - cross_width) // 2,
                    cross_width,
                    cross_width
                )
                self.ruler_close_rect = cross_rect

                pen_cross = QPen(QColor("#EF4444"), 2, Qt.PenStyle.SolidLine)
                painter.setPen(pen_cross)
                margin = 2
                painter.drawLine(
                    cross_rect.x() + margin, cross_rect.y() + margin,
                    cross_rect.x() + cross_rect.width() - margin, cross_rect.y() + cross_rect.height() - margin
                )
                painter.drawLine(
                    cross_rect.x() + cross_rect.width() - margin, cross_rect.y() + margin,
                    cross_rect.x() + margin, cross_rect.y() + cross_rect.height() - margin
                )
            else:
                self.ruler_close_rect = None

            # Отрисовка инструмента "Доза в точке" (Point Dose Probe)
            if self.dose_point_active and self.dose_data:
                if self.pinned_dose_pos:
                    self._draw_dose_probe(painter, self.pinned_dose_pos, is_pinned=True)
                if self.hover_pos and self.image_rect and self.image_rect.contains(self.hover_pos.toPoint()):
                    self._draw_dose_probe(painter, self.hover_pos, is_pinned=False)
            else:
                self.dose_point_close_rect = None

            # OSD-оверлей
            if self.osd_visible:
                if self.current_dataset:
                    painter.setFont(QFont("Consolas", 10, QFont.Weight.Bold))
                    
                    pat_name = str(getattr(self.current_dataset, "PatientName", "Unknown"))
                    pat_id = str(getattr(self.current_dataset, "PatientID", "Unknown"))
                    
                    dob_raw = getattr(self.current_dataset, "PatientBirthDate", "")
                    dob = ""
                    if dob_raw and len(dob_raw) == 8:
                        dob = f"{dob_raw[6:8]}-{dob_raw[4:6]}-{dob_raw[0:4]}"
                    else:
                        dob = dob_raw
                    sex = getattr(self.current_dataset, "PatientSex", "")
                    pat_info = f"{dob} {sex}".strip()
                    
                    study_desc = getattr(self.current_dataset, "StudyDescription", "")
                    series_desc = getattr(self.current_dataset, "SeriesDescription", "")
                    
                    top_lines = [pat_name, pat_id, pat_info, study_desc, series_desc]
                    top_lines = [line for line in top_lines if line]
                    
                    y_offset = 15
                    for line in top_lines:
                        metrics = painter.fontMetrics()
                        rect_line = metrics.boundingRect(line)
                        rect_line.setWidth(rect_line.width() + 15)
                        rect_line.moveTopLeft(QPoint(15, y_offset))
                        painter.fillRect(rect_line.adjusted(-4, -2, 4, 2), QColor(0, 0, 0, 150))
                        painter.setPen(QColor("#E5E7EB"))
                        painter.drawText(rect_line, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, line)
                        y_offset += rect_line.height() + 5

                    # Правый верхний HUD (TPS, разовая доза, суммарная доза)
                    if (self.show_beams or self.show_isodoses_globally) and self.plan_data:
                        tps = self.plan_data.get("tps_name", "")
                        dose_fx = self.plan_data.get("dose_per_fraction", 0.0)
                        total_rx = self.plan_data.get("rx_dose", 0.0)
                        units = self.dose_data.get("dose_units", "Gy") if self.dose_data else "Gy"

                        lines_hud = []
                        if tps:
                            lines_hud.append(tps)
                        if dose_fx > 0:
                            lines_hud.append(f"{dose_fx:.2f} {units} / fx")
                        if total_rx > 0:
                            lines_hud.append(f"{total_rx:.2f} {units}")

                        if lines_hud:
                            y_hud = 15
                            for line in lines_hud:
                                rect_l = metrics.boundingRect(line)
                                w_l = rect_l.width() + 16
                                h_l = rect_l.height()
                                rect_draw = QRect(self.width() - w_l - 15, y_hud, w_l, h_l)
                                painter.fillRect(rect_draw.adjusted(-4, -2, 4, 2), QColor(0, 0, 0, 150))
                                painter.setPen(QColor("#E5E7EB"))
                                painter.drawText(rect_draw, Qt.AlignmentFlag.AlignCenter, line)
                                y_hud += h_l + 5

                # Параметры окна HU, Zoom
                painter.setFont(QFont("Consolas", 10, QFont.Weight.Bold))
                
                lines_bottom = []
                lines_bottom.append(f"WL: {int(self.window_center)} WW: {int(self.window_width)} | Zoom: {int(self.zoom_factor * 100)}%")
                
                if self.current_dataset:
                    modality = getattr(self.current_dataset, "Modality", "")
                    if modality:
                        lines_bottom.append(f"Modality: {modality}")
                    
                    try:
                        ts_uid = getattr(self.current_dataset, "original_transfer_syntax", None)
                        if not ts_uid:
                            ts_uid = self.current_dataset.file_meta.TransferSyntaxUID
                        ts_name = ts_uid.name
                        if " (" in ts_name:
                            ts_name = ts_name.split(" (")[0]
                        lines_bottom.append(f"TS: {ts_name}")
                    except Exception:
                        pass

                metrics_b = painter.fontMetrics()
                y_offset_b = self.height() - 15
                for line in reversed(lines_bottom):
                    rect_info = metrics_b.boundingRect(line)
                    rect_info.setWidth(rect_info.width() + 15)
                    rect_info.moveBottomLeft(QPoint(15, y_offset_b))
                    painter.fillRect(rect_info.adjusted(-4, -2, 4, 2), QColor(0, 0, 0, 150))
                    painter.setPen(QColor("#E5E7EB"))
                    painter.drawText(rect_info, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, line)
                    y_offset_b -= rect_info.height() + 5

                # Срезы
                if self.total_slices > 0:
                    painter.setFont(QFont("Consolas", 10, QFont.Weight.Bold))
                    slice_info = tr_ui("hud_slice", self.current_slice, self.total_slices)
                    
                    spacing_text = ""
                    if self.current_dataset:
                        spacing = getattr(self.current_dataset, "SliceThickness", None)
                        if spacing is None:
                            spacing = getattr(self.current_dataset, "SpacingBetweenSlices", None)
                        if spacing is not None:
                            try:
                                spacing_val = float(spacing)
                                spacing_text = tr_ui("hud_spacing", spacing_val)
                            except (ValueError, TypeError):
                                pass

                    metrics_r = painter.fontMetrics()
                    
                    rect_slice = metrics_r.boundingRect(slice_info)
                    rect_slice.setWidth(rect_slice.width() + 15)
                    rect_slice.moveBottomRight(QPoint(self.width() - 15, self.height() - 15))
                    painter.fillRect(rect_slice.adjusted(-4, -2, 4, 2), QColor(0, 0, 0, 150))
                    painter.setPen(QColor("#E5E7EB"))
                    painter.drawText(rect_slice, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, slice_info)
                    
                    if spacing_text:
                        rect_spacing = metrics_r.boundingRect(spacing_text)
                        rect_spacing.setWidth(rect_spacing.width() + 15)
                        rect_spacing.moveBottomRight(QPoint(self.width() - 15, rect_slice.top() - 8))
                        painter.fillRect(rect_spacing.adjusted(-4, -2, 4, 2), QColor(0, 0, 0, 150))
                        painter.setPen(QColor("#E5E7EB"))
                        painter.drawText(rect_spacing, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, spacing_text)

    def draw_tick(self, painter: QPainter, pt1: QPointF, pt2: QPointF) -> None:
        dx = pt2.x() - pt1.x()
        dy = pt2.y() - pt1.y()
        length = math.sqrt(dx*dx + dy*dy)
        if length < 1.0:
            return
        px = -dy / length
        py = dx / length
        
        tick_len = 8
        p1 = QPoint(int(pt1.x() + px * tick_len), int(pt1.y() + py * tick_len))
        p2 = QPoint(int(pt1.x() - px * tick_len), int(pt1.y() - py * tick_len))
        painter.drawLine(p1, p2)
