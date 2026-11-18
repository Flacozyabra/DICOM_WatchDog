from __future__ import annotations

import os
import math
import numpy as np
import scipy.ndimage
import pydicom
from pydicom.uid import generate_uid

from PyQt6.QtCore import Qt, pyqtSignal, QPoint, QPointF, QRectF, QThread, QEvent
from PyQt6.QtWidgets import (
    QWidget, QGridLayout, QFrame, QSizePolicy, QMenu, QMessageBox,
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
)
from PyQt6.QtGui import (
    QPainter, QPen, QColor, QFont, QPixmap, QImage,
    QBrush
)

from core.locale_utils import tr_ui
from ui.settings_tabs.settings_utils import apply_dark_title_bar
from .parsers import safe_dcmread


# Safe Qt constants for both PyQt6 and PyQt5
ALIGN_CENTER = Qt.AlignmentFlag.AlignCenter if hasattr(Qt, "AlignmentFlag") else Qt.AlignCenter
ALIGN_LEFT = Qt.AlignmentFlag.AlignLeft if hasattr(Qt, "AlignmentFlag") else Qt.AlignLeft
ALIGN_RIGHT = Qt.AlignmentFlag.AlignRight if hasattr(Qt, "AlignmentFlag") else Qt.AlignRight
CURSOR_CROSS = Qt.CursorShape.CrossCursor if hasattr(Qt, "CursorShape") else Qt.CrossCursor
CURSOR_HAND = Qt.CursorShape.PointingHandCursor if hasattr(Qt, "CursorShape") else Qt.PointingHandCursor
CURSOR_SIZEALL = Qt.CursorShape.SizeAllCursor if hasattr(Qt, "CursorShape") else Qt.SizeAllCursor
CURSOR_SIZEVER = Qt.CursorShape.SizeVerCursor if hasattr(Qt, "CursorShape") else Qt.SizeVerCursor
CURSOR_SIZEHOR = Qt.CursorShape.SizeHorCursor if hasattr(Qt, "CursorShape") else Qt.SizeHorCursor
BTN_LEFT = Qt.MouseButton.LeftButton if hasattr(Qt, "MouseButton") else Qt.LeftButton
BTN_MIDDLE = Qt.MouseButton.MiddleButton if hasattr(Qt, "MouseButton") else Qt.MiddleButton
BTN_RIGHT = Qt.MouseButton.RightButton if hasattr(Qt, "MouseButton") else Qt.RightButton
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


def load_ct_volume_data(
    sorted_files: list,
    on_progress=None,
    is_cancelled=None
) -> tuple[np.ndarray, list[float], tuple[float, float, float]] | None:
    """
    Extracts 3D CT HU volume and coordinate metadata from DICOM slice files.
    Returns (vol, ipp0, (dy, dx, dz)) or None.
    """
    if not sorted_files:
        return None
    try:
        total_files = len(sorted_files)
        slices_ds = []

        for idx, item in enumerate(sorted_files):
            if is_cancelled and is_cancelled():
                return None
            fp = item[0] if isinstance(item, tuple) else item
            try:
                ds = safe_dcmread(fp)
                if (hasattr(ds, "ImagePositionPatient") and hasattr(ds, "pixel_array") and 
                        getattr(ds, "Modality", "CT") == "CT" and ds.pixel_array.ndim == 2):
                    slices_ds.append(ds)
            except Exception:
                pass

            if on_progress and (idx % 2 == 0 or idx == total_files - 1):
                on_progress(idx + 1, total_files)

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


def compute_cubic_spline_coeffs(vol: np.ndarray) -> np.ndarray:
    """Precompute 3D float32 cubic B-spline coefficients for sharp order=3 resampling."""
    return scipy.ndimage.spline_filter(vol, order=3, output=np.float32, mode="constant")


def sample_oblique_slice(
    vol: np.ndarray,
    coords: np.ndarray,
    spline_coeffs: np.ndarray | None = None
) -> np.ndarray:
    """
    Resample a 2D slice from 3D volume using cached cubic B-spline interpolation (order=3)
    with 8-neighbor local bounds clamping to prevent Gibbs ringing near high-contrast metal.
    """
    if spline_coeffs is None or spline_coeffs.shape != vol.shape:
        spline_coeffs = compute_cubic_spline_coeffs(vol)
    slice_hu = scipy.ndimage.map_coordinates(
        spline_coeffs,
        coords,
        order=3,
        mode="constant",
        cval=-1000.0,
        prefilter=False
    )
    if vol.ndim != 3:
        return slice_hu
    nz, nr, nc = vol.shape
    if nz < 2 or nr < 2 or nc < 2:
        return slice_hu

    fz, fy, fx = coords[0], coords[1], coords[2]
    inside = (
        (fz >= 0.0) & (fz <= float(nz - 1)) &
        (fy >= 0.0) & (fy <= float(nr - 1)) &
        (fx >= 0.0) & (fx <= float(nc - 1))
    )
    if not np.any(inside):
        return slice_hu

    z0 = np.clip(np.floor(fz[inside]).astype(np.intp), 0, nz - 2)
    y0 = np.clip(np.floor(fy[inside]).astype(np.intp), 0, nr - 2)
    x0 = np.clip(np.floor(fx[inside]).astype(np.intp), 0, nc - 2)

    if vol.flags["C_CONTIGUOUS"]:
        flat = vol.reshape(-1)
        s_row = nc
        s_sl = nr * nc
        idx0 = (z0 * nr + y0) * nc + x0
        corners = (
            flat[idx0], flat[idx0 + 1],
            flat[idx0 + s_row], flat[idx0 + s_row + 1],
            flat[idx0 + s_sl], flat[idx0 + s_sl + 1],
            flat[idx0 + s_sl + s_row], flat[idx0 + s_sl + s_row + 1],
        )
    else:
        z1, y1, x1 = z0 + 1, y0 + 1, x0 + 1
        corners = (
            vol[z0, y0, x0], vol[z0, y0, x1],
            vol[z0, y1, x0], vol[z0, y1, x1],
            vol[z1, y0, x0], vol[z1, y0, x1],
            vol[z1, y1, x0], vol[z1, y1, x1],
        )

    v_min = corners[0].copy()
    v_max = corners[0].copy()
    for c in corners[1:]:
        np.minimum(v_min, c, out=v_min)
        np.maximum(v_max, c, out=v_max)
    slice_hu[inside] = np.clip(slice_hu[inside], v_min, v_max)
    return slice_hu


class MprVolumeLoaderWorker(QThread):
    """
    Background worker that builds 3D CT volume(s) from DICOM slice files
    with progress reporting.
    Emits progress_signal(int, int, str), finished_signal(object, object), error_signal(str).
    """
    progress_signal = pyqtSignal(int, int, str)
    finished_signal = pyqtSignal(object, object)
    error_signal = pyqtSignal(str)

    def __init__(
        self,
        files_orig: list,
        files_derived: list = None,
        need_orig: bool = True,
        need_derived: bool = False,
        parent=None
    ) -> None:
        super().__init__(parent)
        self.files_orig = list(files_orig) if files_orig else []
        self.files_derived = list(files_derived) if files_derived else []
        self.need_orig = need_orig
        self.need_derived = need_derived
        self.spline_orig = None
        self.spline_derived = None
        self._is_cancelled = False

    def cancel(self) -> None:
        self._is_cancelled = True

    def run(self) -> None:
        try:
            res_orig = None
            res_derived = None

            total_stages = (1 if self.need_orig else 0) + (1 if self.need_derived else 0)
            if total_stages == 0:
                self.finished_signal.emit(None, None)
                return

            if self.need_orig:
                def on_p_orig(cur: int, tot: int) -> None:
                    if total_stages == 2:
                        pct = int((cur / tot) * 42)
                    else:
                        pct = int((cur / tot) * 85)
                    msg = tr_ui("loading_dicom_files", cur, tot)
                    self.progress_signal.emit(pct, 100, msg)

                res_orig = load_ct_volume_data(
                    self.files_orig,
                    on_progress=on_p_orig,
                    is_cancelled=lambda: self._is_cancelled
                )
                if self._is_cancelled:
                    return
                if res_orig is not None:
                    self.progress_signal.emit(45 if total_stages == 2 else 90, 100, tr_ui("main_progress_launch") or "Preparing MPR...")
                    self.spline_orig = compute_cubic_spline_coeffs(res_orig[0])
                    if self._is_cancelled:
                        return

            if self.need_derived:
                def on_p_der(cur: int, tot: int) -> None:
                    if total_stages == 2:
                        pct = 50 + int((cur / tot) * 42)
                    else:
                        pct = int((cur / tot) * 85)
                    msg = tr_ui("loading_dicom_files", cur, tot)
                    self.progress_signal.emit(pct, 100, msg)

                res_derived = load_ct_volume_data(
                    self.files_derived,
                    on_progress=on_p_der,
                    is_cancelled=lambda: self._is_cancelled
                )
                if self._is_cancelled:
                    return
                if res_derived is not None:
                    self.progress_signal.emit(95, 100, tr_ui("main_progress_launch") or "Preparing MPR...")
                    self.spline_derived = compute_cubic_spline_coeffs(res_derived[0])
                    if self._is_cancelled:
                        return

            self.progress_signal.emit(100, 100, tr_ui("main_progress_launch") if tr_ui("main_progress_launch") else "Ready")
            self.finished_signal.emit(res_orig, res_derived)
        except Exception as e:
            self.error_signal.emit(str(e))


def show_dark_message_box(
    parent: QWidget | None,
    title: str,
    text: str,
    icon: QMessageBox.Icon = QMessageBox.Icon.Information,
    buttons: QMessageBox.StandardButton = QMessageBox.StandardButton.Ok
) -> QMessageBox.StandardButton:
    """
    Shows a dark-themed modal message box with Windows DWM dark title bar.
    """
    dlg = QMessageBox(parent)
    dlg.setIcon(icon)
    dlg.setWindowTitle(title)
    dlg.setText(text)
    dlg.setStandardButtons(buttons)
    apply_dark_title_bar(dlg)
    dlg.setStyleSheet("""
        QMessageBox {
            background-color: #202020;
            font-family: 'Segoe UI', -apple-system, Roboto, sans-serif;
        }
        QLabel {
            background-color: transparent;
            border: none;
            color: #ffffff;
            font-size: 13px;
        }
        QPushButton {
            background-color: #1f538d;
            color: #ffffff;
            border: none;
            border-radius: 6px;
            padding: 6px 18px;
            font-size: 13px;
            min-width: 72px;
        }
        QPushButton:hover {
            background-color: #2a69a5;
        }
        QPushButton:pressed {
            background-color: #1a4675;
        }
    """)
    return dlg.exec()


_DARK_MPR_DIALOG_QSS = """
    QDialog {
        background-color: #202020;
        color: #ffffff;
        font-family: 'Segoe UI', -apple-system, Roboto, sans-serif;
    }
    QLabel {
        background-color: transparent;
        border: none;
        color: #ffffff;
    }
    QFrame#mprSaveInfoCard {
        background-color: #161616;
        border: 1px solid #2d2d2d;
        border-radius: 8px;
    }
"""

_BTN_PRIMARY_QSS = """
    QPushButton {
        background-color: #1f538d;
        color: #ffffff;
        border: none;
        border-radius: 6px;
        padding: 10px 14px;
        font-size: 13px;
        font-weight: 600;
    }
    QPushButton:hover {
        background-color: #2a69a5;
    }
    QPushButton:pressed {
        background-color: #1a4675;
    }
"""

_BTN_SECONDARY_QSS = """
    QPushButton {
        background-color: #2d2d2d;
        color: #ffffff;
        border: 1px solid #454545;
        border-radius: 6px;
        padding: 10px 14px;
        font-size: 13px;
        font-weight: 600;
    }
    QPushButton:hover {
        background-color: #3a3a3a;
    }
    QPushButton:pressed {
        background-color: #242424;
    }
"""


class MprExportChoiceDialog(QDialog):
    """
    Dark-themed modal dialog prompting user whether to replace original CT
    or save aligned series as a new DICOM series.
    """
    def __init__(
        self,
        parent: QWidget | None,
        rot_x: float,
        rot_y: float,
        rot_z: float,
        n_slices: int
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr_ui("viewer_mpr_save_title"))
        self.setMinimumWidth(440)
        self.selected_action = None  # "replace", "new", or None

        apply_dark_title_bar(self)
        self.setStyleSheet(_DARK_MPR_DIALOG_QSS)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        title_lbl = QLabel(tr_ui("viewer_mpr_save_header"), self)
        title_lbl.setStyleSheet("font-size: 15px; font-weight: bold; color: #ffffff;")
        layout.addWidget(title_lbl)

        info_card = QFrame(self)
        info_card.setObjectName("mprSaveInfoCard")
        info_layout = QVBoxLayout(info_card)
        info_layout.setContentsMargins(14, 12, 14, 12)
        info_lbl = QLabel(
            tr_ui("viewer_mpr_save_info", rot_z, rot_y, rot_x, n_slices),
            info_card
        )
        info_lbl.setStyleSheet("font-size: 13px; color: #d4d4d8;")
        info_lbl.setWordWrap(True)
        info_layout.addWidget(info_lbl)
        layout.addWidget(info_card)

        btn_new = QPushButton(tr_ui("viewer_mpr_save_new"), self)
        btn_new.setCursor(CURSOR_HAND)
        btn_new.setStyleSheet("""
            QPushButton {
                background-color: #1f538d;
                color: #ffffff;
                border: none;
                border-radius: 6px;
                padding: 10px 14px;
                font-size: 13px;
                font-weight: 600;
                text-align: left;
            }
            QPushButton:hover {
                background-color: #2a69a5;
            }
            QPushButton:pressed {
                background-color: #1a4675;
            }
        """)
        btn_new.setToolTip(tr_ui("viewer_mpr_save_new_tip"))
        btn_new.clicked.connect(self._on_new)
        layout.addWidget(btn_new)

        btn_replace = QPushButton(tr_ui("viewer_mpr_save_replace"), self)
        btn_replace.setCursor(CURSOR_HAND)
        btn_replace.setStyleSheet("""
            QPushButton {
                background-color: #2a1818;
                color: #fca5a5;
                border: 1px solid #7f2929;
                border-radius: 6px;
                padding: 10px 14px;
                font-size: 13px;
                font-weight: 600;
                text-align: left;
            }
            QPushButton:hover {
                background-color: #3a1f1f;
                border-color: #a63434;
                color: #ffffff;
            }
            QPushButton:pressed {
                background-color: #221212;
            }
        """)
        btn_replace.setToolTip(tr_ui("viewer_mpr_save_replace_tip"))
        btn_replace.clicked.connect(self._on_replace)
        layout.addWidget(btn_replace)

        bottom_row = QHBoxLayout()
        bottom_row.addStretch()
        btn_cancel = QPushButton(tr_ui("btn_cancel"), self)
        btn_cancel.setCursor(CURSOR_HAND)
        btn_cancel.setFixedHeight(32)
        btn_cancel.setMinimumWidth(96)
        btn_cancel.setStyleSheet("""
            QPushButton {
                background-color: #3a3a3a;
                color: #ffffff;
                border: 1px solid #555555;
                border-radius: 6px;
                padding: 4px 16px;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #4a4a4a;
            }
            QPushButton:pressed {
                background-color: #2a2a2a;
            }
        """)
        btn_cancel.clicked.connect(self.reject)
        bottom_row.addWidget(btn_cancel)
        layout.addLayout(bottom_row)

    def _on_replace(self) -> None:
        self.selected_action = "replace"
        self.accept()

    def _on_new(self) -> None:
        self.selected_action = "new"
        self.accept()


class MprAfterSaveDialog(QDialog):
    """
    Dark-themed modal dialog allowing the user to pick how to display the study
    after saving a new aligned CT series: Original, Split, or Aligned.
    """
    def __init__(
        self,
        parent: QWidget | None,
        n_slices: int,
        out_dir: str
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr_ui("viewer_mpr_save_done_title"))
        self.setMinimumWidth(460)
        self.selected_mode = None

        apply_dark_title_bar(self)
        self.setStyleSheet(_DARK_MPR_DIALOG_QSS)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        title_lbl = QLabel(tr_ui("viewer_mpr_save_done_header"), self)
        title_lbl.setStyleSheet("font-size: 15px; font-weight: bold; color: #ffffff;")
        layout.addWidget(title_lbl)

        info_card = QFrame(self)
        info_card.setObjectName("mprSaveInfoCard")
        info_layout = QVBoxLayout(info_card)
        info_layout.setContentsMargins(14, 12, 14, 12)
        info_lbl = QLabel(tr_ui("viewer_mpr_save_done_new", n_slices, out_dir), info_card)
        info_lbl.setStyleSheet("font-size: 13px; color: #d4d4d8;")
        info_lbl.setWordWrap(True)
        info_layout.addWidget(info_lbl)
        layout.addWidget(info_card)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        choices = (
            (tr_ui("viewer_mode_orig") or "Original", "original", _BTN_SECONDARY_QSS),
            (tr_ui("viewer_mode_side") or "Split", "side_by_side", _BTN_PRIMARY_QSS),
            (tr_ui("viewer_mpr_mode_aligned") or "Aligned", "processed", _BTN_PRIMARY_QSS),
        )
        for label, mode_val, qss in choices:
            btn = QPushButton(label, self)
            btn.setCursor(CURSOR_HAND)
            btn.setMinimumHeight(36)
            btn.setStyleSheet(qss)
            btn.clicked.connect(lambda _checked=False, m=mode_val: self._choose(m))
            btn_row.addWidget(btn, 1)
        layout.addLayout(btn_row)

    def _choose(self, mode_val: str) -> None:
        self.selected_mode = mode_val
        self.accept()


class MprExportAlignedWorker(QThread):
    """
    Background worker that resamples 3D CT volume along aligned MPR axes
    and writes standard axial DICOM slices for Varian Eclipse TPS.
    """
    progress_signal = pyqtSignal(int, int, str)
    finished_signal = pyqtSignal(dict)
    error_signal = pyqtSignal(str)

    def __init__(
        self,
        slice_paths: list[str],
        vol: np.ndarray,
        spacing: tuple[float, float, float],
        rot_x: float,
        rot_y: float,
        rot_z: float,
        cur_x: int,
        cur_y: int,
        cur_z: int,
        mode: str,
        parent=None,
        origin_mm: np.ndarray | list[float] | None = None,
        float_cur: list[float] | tuple[float, float, float] | None = None,
        spline_coeffs: np.ndarray | None = None
    ) -> None:
        super().__init__(parent)
        self.slice_paths = list(slice_paths)
        self.vol = vol
        self.spacing = tuple(spacing)
        self.rot_x = float(rot_x)
        self.rot_y = float(rot_y)
        self.rot_z = float(rot_z)
        self.cur_x = int(cur_x)
        self.cur_y = int(cur_y)
        self.cur_z = int(cur_z)
        self.mode = mode
        self.origin_mm = np.array(origin_mm, dtype=np.float64) if origin_mm is not None else np.zeros(3, dtype=np.float64)
        self.float_cur = list(float_cur) if float_cur is not None else [float(self.cur_x), float(self.cur_y), float(self.cur_z)]
        self.spline_coeffs = spline_coeffs
        self._is_cancelled = False

    def cancel(self) -> None:
        self._is_cancelled = True

    def run(self) -> None:
        try:
            n_slices = len(self.slice_paths)
            if n_slices == 0 or self.vol is None:
                self.error_signal.emit(tr_ui("viewer_mpr_no_export_data"))
                return

            study_dir = os.path.dirname(self.slice_paths[0])
            output_dir = study_dir
            new_series_uid = generate_uid()

            dy, dx, dz = self.spacing
            n_z, rows, cols = self.vol.shape

            rx, ry, rz = np.radians([self.rot_x, self.rot_y, self.rot_z])
            Rx = np.array([[1, 0, 0], [0, np.cos(rx), -np.sin(rx)], [0, np.sin(rx), np.cos(rx)]], dtype=np.float64)
            Ry = np.array([[np.cos(ry), 0, np.sin(ry)], [0, 1, 0], [-np.sin(ry), 0, np.cos(ry)]], dtype=np.float64)
            Rz = np.array([[np.cos(rz), np.sin(rz), 0], [-np.sin(rz), np.cos(rz), 0], [0, 0, 1]], dtype=np.float64)
            R = Ry @ Rx @ Rz

            c_world = _compute_world_cursor_mm(R, self.origin_mm, self.float_cur, self.spacing)
            cx_rot, cy_rot, cz_rot = float(c_world[0]), float(c_world[1]), float(c_world[2])

            u_ax = (np.arange(cols, dtype=np.float32) - float(self.float_cur[0])) * dx
            v_ax = (np.arange(rows, dtype=np.float32) - float(self.float_cur[1])) * dy
            uu, vv = np.meshgrid(u_ax, v_ax)

            spline_coeffs = self.spline_coeffs
            if spline_coeffs is None or spline_coeffs.shape != self.vol.shape:
                self.progress_signal.emit(0, n_slices, tr_ui("main_progress_launch") or "Preparing...")
                spline_coeffs = compute_cubic_spline_coeffs(self.vol)

            out_files = []
            series_desc = "CT (Aligned)"

            for k in range(n_slices):
                if self._is_cancelled:
                    return

                in_fp = self.slice_paths[k]
                self.progress_signal.emit(k + 1, n_slices, f"{k + 1} / {n_slices}")

                w = (k - float(self.float_cur[2])) * dz
                px = cx_rot + uu * R[0, 0] + vv * R[0, 1] + w * R[0, 2]
                py = cy_rot + uu * R[1, 0] + vv * R[1, 1] + w * R[1, 2]
                pz = cz_rot + uu * R[2, 0] + vv * R[2, 1] + w * R[2, 2]

                coords = np.array([pz / dz, py / dy, px / dx], dtype=np.float32)
                slice_hu = sample_oblique_slice(self.vol, coords, spline_coeffs=spline_coeffs)

                ds = safe_dcmread(in_fp)
                slope = float(getattr(ds, "RescaleSlope", 1.0))
                if slope == 0.0:
                    slope = 1.0
                intercept = float(getattr(ds, "RescaleIntercept", 0.0))

                raw_pixels = np.round((slice_hu - intercept) / slope)
                bit_depth = int(getattr(ds, "BitsAllocated", 16))
                pixel_rep = int(getattr(ds, "PixelRepresentation", 0))

                if pixel_rep == 0:
                    raw_pixels = np.clip(raw_pixels, 0, (1 << bit_depth) - 1).astype(np.uint16)
                else:
                    min_val = -(1 << (bit_depth - 1))
                    max_val = (1 << (bit_depth - 1)) - 1
                    raw_pixels = np.clip(raw_pixels, min_val, max_val).astype(np.int16)

                ds.PixelData = raw_pixels.tobytes()
                ds.Rows = rows
                ds.Columns = cols
                ds.ImageOrientationPatient = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0]
                ds.InstanceNumber = k + 1

                orig_desc = str(getattr(ds, "SeriesDescription", "CT"))
                if "(Aligned)" not in orig_desc:
                    ds.SeriesDescription = f"{orig_desc} (Aligned)"
                series_desc = str(ds.SeriesDescription)

                ds.SeriesInstanceUID = new_series_uid
                new_sop_uid = generate_uid()
                ds.SOPInstanceUID = new_sop_uid
                if hasattr(ds, "file_meta") and ds.file_meta is not None:
                    ds.file_meta.MediaStorageSOPInstanceUID = new_sop_uid

                if self.mode == "new":
                    try:
                        snum = int(getattr(ds, "SeriesNumber", 1))
                        ds.SeriesNumber = snum + 100
                    except Exception:
                        ds.SeriesNumber = 101
                    ds.ImageType = ["DERIVED", "SECONDARY", "AXIAL"]
                    orig_fname = os.path.basename(in_fp)
                    if orig_fname.upper().startswith("DERIVED_"):
                        out_fname = f"DERIVED_ALIGNED_{orig_fname.split('_', 2)[-1]}"
                    else:
                        out_fname = f"DERIVED_ALIGNED_{orig_fname}"
                    out_fp = os.path.join(output_dir, out_fname)
                else:
                    out_fp = in_fp

                ds.save_as(out_fp)
                out_files.append(out_fp)

            self.finished_signal.emit({
                "mode": self.mode,
                "output_dir": output_dir,
                "files": out_files,
                "count": len(out_files),
                "series_uid": new_series_uid,
                "series_desc": series_desc
            })

        except Exception as e:
            self.error_signal.emit(str(e))


def build_ct_volume(sorted_files: list) -> tuple[np.ndarray, list[float], tuple[float, float, float]] | None:
    """
    Synchronous fallback for building 3D HU volume from CT DICOM files.
    Returns (vol, ipp0, (dy, dx, dz)) or None.
    """
    return load_ct_volume_data(sorted_files)


def detect_applicator_orientation(
    vol: np.ndarray,
    spacing: tuple[float, float, float]
) -> dict | None:
    """
    Detects high-density metallic brachytherapy applicator (tandem/metrocolpostat) in 3D volume.
    Calculates primary shaft tilt (rot_x, rot_y), bend plane angle (rot_z), and pivot voxel.
    Returns:
        dict: {
            "cur_x": int,
            "cur_y": int,
            "cur_z": int,
            "rot_x": float,
            "rot_y": float,
            "rot_z": float,
        }
        or None if no applicator detected.
    """
    if vol is None or vol.ndim != 3:
        return None

    dy, dx, dz = float(spacing[0]), float(spacing[1]), float(spacing[2])
    n_z, rows, cols = vol.shape

    # Metallic density threshold (> 2000 HU, fallback to 1500 HU)
    thresh = 2000.0
    metal_mask = vol > thresh
    if np.sum(metal_mask) < 50:
        thresh = 1500.0
        metal_mask = vol > thresh
        if np.sum(metal_mask) < 50:
            return None

    z_metal = np.argwhere(np.any(metal_mask, axis=(1, 2))).flatten()
    if len(z_metal) < 10:
        return None

    z_top = int(z_metal.max())
    top_coords = np.argwhere(metal_mask[z_top])
    if len(top_coords) == 0:
        return None
    prev_c = np.mean(top_coords, axis=0)  # [y, x]

    track = []
    for z in range(z_top, -1, -1):
        sl = metal_mask[z]
        if not np.any(sl):
            continue
        labeled, num = scipy.ndimage.label(sl)
        if num == 0:
            continue
        best_dist = 1e9
        best_c = None
        for i in range(1, num + 1):
            comp_pts = np.argwhere(labeled == i)
            c = np.mean(comp_pts, axis=0)
            d = math.hypot(float(c[0] - prev_c[0]), float(c[1] - prev_c[1]))
            if d < best_dist:
                best_dist = d
                best_c = c
        if best_dist < 15.0:  # max jump threshold between adjacent slices
            track.append((z, float(best_c[0]), float(best_c[1])))
            prev_c = best_c

    if len(track) < 10:
        return None

    track_arr = np.array(track)  # [z, y, x]
    pts_mm = np.column_stack([track_arr[:, 2] * dx, track_arr[:, 1] * dy, track_arr[:, 0] * dz])

    n_pts = len(track_arr)
    # The upper ~35-40% is the curved tip, lower ~60-65% is the straight shaft
    split_idx = max(5, int(n_pts * 0.4))
    tip_pts = pts_mm[:split_idx]
    shaft_pts = pts_mm[split_idx:]

    if len(shaft_pts) < 5:
        shaft_pts = pts_mm

    # PCA on shaft to get longitudinal direction V_shaft
    mean_shaft = np.mean(shaft_pts, axis=0)
    cov = np.cov(shaft_pts - mean_shaft, rowvar=False)
    eigvals, eigvecs = np.linalg.eigh(cov)
    v_shaft = eigvecs[:, -1]
    if v_shaft[2] < 0:
        v_shaft = -v_shaft

    # Calculate rot_x and rot_y
    rot_x = float(np.degrees(-np.arcsin(np.clip(v_shaft[1], -1.0, 1.0))))
    cos_rx = math.cos(math.radians(rot_x))
    if abs(cos_rx) > 1e-4:
        rot_y = float(np.degrees(np.arcsin(np.clip(v_shaft[0] / cos_rx, -1.0, 1.0))))
    else:
        rot_y = 0.0

    # Pivot point at the transition/cervical junction projected onto the fitted shaft axis
    pivot_z_idx = min(len(track_arr) - 1, split_idx)
    raw_pivot_mm = pts_mm[pivot_z_idx]
    proj_t = float(np.dot(raw_pivot_mm - mean_shaft, v_shaft))
    pivot_pt_mm = mean_shaft + proj_t * v_shaft
    pivot_fx = float(np.clip(pivot_pt_mm[0] / dx, 0.0, float(cols - 1)))
    pivot_fy = float(np.clip(pivot_pt_mm[1] / dy, 0.0, float(rows - 1)))
    pivot_fz = float(np.clip(pivot_pt_mm[2] / dz, 0.0, float(n_z - 1)))
    cur_x = int(round(pivot_fx))
    cur_y = int(round(pivot_fy))
    cur_z = int(round(pivot_fz))

    # Calculate rot_z: align lateral colpostats/ovoids along horizontal R-L axis when present,
    # otherwise fall back to sagittal/bend plane alignment from the upper tip.
    rx = math.radians(rot_x)
    ry = math.radians(rot_y)
    Rx = np.array([[1, 0, 0], [0, math.cos(rx), -math.sin(rx)], [0, math.sin(rx), math.cos(rx)]])
    Ry = np.array([[math.cos(ry), 0, math.sin(ry)], [0, 1, 0], [-math.sin(ry), 0, math.cos(ry)]])
    A = Ry @ Rx

    ovoid_angles = []
    max_ovoid_z = min(n_z - 1, cur_z + 2)
    for z_idx in range(0, max_ovoid_z + 1):
        sl_mask = metal_mask[z_idx]
        if not np.any(sl_mask):
            continue
        lbl, num_comp = scipy.ndimage.label(sl_mask)
        if num_comp < 2:
            continue
        local_uv = []
        for comp_id in range(1, num_comp + 1):
            comp_pts = np.argwhere(lbl == comp_id)
            if len(comp_pts) < 3:
                continue
            cy_c, cx_c = comp_pts.mean(axis=0)
            p_mm = np.array([float(cx_c) * dx, float(cy_c) * dy, float(z_idx) * dz], dtype=np.float64)
            q_pt = (p_mm - pivot_pt_mm) @ A
            u_c, v_c = float(q_pt[0]), float(q_pt[1])
            if math.hypot(u_c, v_c) <= 50.0:
                local_uv.append((u_c, v_c))
        if len(local_uv) < 2:
            continue

        best_span = 0.0
        best_pair = None
        for i in range(len(local_uv)):
            for j in range(i + 1, len(local_uv)):
                ui, vi = local_uv[i]
                uj, vj = local_uv[j]
                if math.hypot(ui, vi) < 4.0 or math.hypot(uj, vj) < 4.0:
                    continue
                if ui * uj + vi * vj >= 0.0:
                    continue
                span = math.hypot(uj - ui, vj - vi)
                if span > best_span:
                    best_span = span
                    best_pair = (local_uv[i], local_uv[j])

        if best_pair is not None and best_span >= 10.0:
            (u0, v0), (u1, v1) = best_pair
            du, dv = u1 - u0, v1 - v0
            if du < 0.0 or (abs(du) < 1e-4 and dv > 0.0):
                du, dv = -du, -dv
            ovoid_angles.append(math.atan2(-dv, du))

    if len(ovoid_angles) >= 3:
        rads = np.array(ovoid_angles, dtype=np.float64)
        s2 = float(np.median(np.sin(2.0 * rads)))
        c2 = float(np.median(np.cos(2.0 * rads)))
        rot_z = float(np.degrees(0.5 * math.atan2(s2, c2)))
    else:
        Q_tip = (tip_pts - pivot_pt_mm) @ A
        v_bend = np.mean(Q_tip[:, 0:2], axis=0)
        bend_mag = math.hypot(float(v_bend[0]), float(v_bend[1]))

        if bend_mag > 0.5:
            rot_z = float(np.degrees(math.atan2(float(v_bend[0]), float(v_bend[1]))))
            while rot_z > 90.0:
                rot_z -= 180.0
            while rot_z < -90.0:
                rot_z += 180.0
        else:
            rot_z = 0.0

    return {
        "cur_x": cur_x,
        "cur_y": cur_y,
        "cur_z": cur_z,
        "float_cur": [pivot_fx, pivot_fy, pivot_fz],
        "rot_x": round(rot_x, 1),
        "rot_y": round(rot_y, 1),
        "rot_z": round(rot_z, 1)
    }


def _compute_world_cursor_mm(
    rot_matrix: np.ndarray,
    origin_mm: np.ndarray,
    float_cur: list[float] | tuple[float, float, float],
    spacing: tuple[float, float, float]
) -> np.ndarray:
    """Map rotated-frame voxel cursor (sx, sy, sz) to 3D volume coordinates in mm."""
    dy, dx, dz = float(spacing[0]), float(spacing[1]), float(spacing[2])
    s_mm = np.array([
        float(float_cur[0]) * dx,
        float(float_cur[1]) * dy,
        float(float_cur[2]) * dz,
    ], dtype=np.float64)
    return origin_mm + rot_matrix @ s_mm


class MprSliceWidget(QWidget):
    """
    Individual viewport for rendering one orthogonal plane (Axial, Coronal, or Sagittal).
    Maintains physical pixel aspect ratio, crosshair overlay, synchronous zoom/pan,
    and interactive cyclic navigation.
    """
    cursor_moved = pyqtSignal(int, int, int) # (cur_x, cur_y, cur_z)
    slice_scrolled = pyqtSignal(int, int, int)
    zoom_changed = pyqtSignal(float)
    pan_changed = pyqtSignal(QPointF)
    pan_delta_requested = pyqtSignal(str, float, float) # (plane_type, dx, dy)
    rotation_changed = pyqtSignal(str, float) # (plane_type, angle)
    double_clicked = pyqtSignal(str) # plane_type
    cycle_plane_requested = pyqtSignal()

    def __init__(self, plane_type: str, parent: QWidget = None) -> None:
        super().__init__(parent)
        self.plane_type = plane_type
        self.vol = None
        self.spline_coeffs = None
        self.ipp0 = [0.0, 0.0, 0.0]
        self.spacing = (1.0, 1.0, 1.0) # (dy, dx, dz)

        self.cur_x = 0
        self.cur_y = 0
        self.cur_z = 0

        self.window_width = 400.0
        self.window_center = 40.0

        self.zoom_factor = 1.0
        self.pan_offset = QPointF(0, 0)
        self.rotation_angle = 0.0
        self.rot_matrix = np.eye(3, dtype=np.float64)
        self.frame_origin_mm = np.zeros(3, dtype=np.float64)
        self._float_cur = [0.0, 0.0, 0.0]

        self.current_pixmap = None
        self._target_rect = QRectF()
        self._is_panning = False
        self._pan_start = None
        self._is_rotating = False
        self._rot_start_angle = 0.0
        self._mouse_start_rad = 0.0
        self._dragging_axis = None  # None, "horizontal", "vertical", "both"
        self._handle_h_left = QRectF()
        self._handle_h_right = QRectF()
        self._handle_v_top = QRectF()
        self._handle_v_bottom = QRectF()
        self._last_pivot = QPointF(0, 0)
        self._rot_pill_rect = QRectF()
        self._context_menu_shown = False

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus if hasattr(Qt, "FocusPolicy") else Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setCursor(CURSOR_CROSS)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet("MprSliceWidget { background-color: #000000; border: none; }")

    def set_rotation_matrix(
        self,
        rot_matrix: np.ndarray,
        angle: float,
        frame_origin_mm: np.ndarray | None = None
    ) -> None:
        self.rot_matrix = rot_matrix
        if frame_origin_mm is not None:
            self.frame_origin_mm = np.array(frame_origin_mm, dtype=np.float64)
        norm_angle = angle % 360.0
        if norm_angle > 180.0:
            norm_angle -= 360.0
        elif norm_angle < -180.0:
            norm_angle += 360.0
        if abs(norm_angle) < 0.05:
            norm_angle = 0.0
        else:
            norm_angle = round(norm_angle, 1)
        self.rotation_angle = norm_angle
        self.refresh_slice()

    def set_rotation(self, angle: float) -> None:
        norm_angle = angle % 360.0
        if norm_angle > 180.0:
            norm_angle -= 360.0
        elif norm_angle < -180.0:
            norm_angle += 360.0
        if abs(norm_angle) < 0.05:
            norm_angle = 0.0
        else:
            norm_angle = round(norm_angle, 1)
        if abs(self.rotation_angle - norm_angle) > 1e-4:
            self.rotation_angle = norm_angle
            self.rotation_changed.emit(self.plane_type, self.rotation_angle)

    def event(self, event: QEvent) -> bool:
        event_type = event.type()
        key_press_type = getattr(QEvent.Type, "KeyPress", getattr(QEvent, "KeyPress", None))
        if event_type == key_press_type:
            modifiers = event.modifiers()
            is_ctrl = bool(
                (modifiers & Qt.KeyboardModifier.ControlModifier) if hasattr(Qt, "KeyboardModifier")
                else (modifiers & Qt.ControlModifier)
            )
            key_tab = getattr(Qt.Key, "Key_Tab", getattr(Qt, "Key_Tab", 0x01000001))
            key_backtab = getattr(Qt.Key, "Key_Backtab", getattr(Qt, "Key_Backtab", 0x01000002))
            if is_ctrl and event.key() in (key_tab, key_backtab):
                self.cycle_plane_requested.emit()
                return True
        return super().event(event)

    def _map_screen_to_unrotated(self, pt: QPoint | QPointF) -> QPointF:
        if abs(self.rotation_angle) < 1e-4 or self._last_pivot is None:
            return QPointF(pt)
        rad = math.radians(-self.rotation_angle)
        dx = pt.x() - self._last_pivot.x()
        dy = pt.y() - self._last_pivot.y()
        rx = self._last_pivot.x() + dx * math.cos(rad) - dy * math.sin(rad)
        ry = self._last_pivot.y() + dx * math.sin(rad) + dy * math.cos(rad)
        return QPointF(rx, ry)

    def _is_rotated(self) -> bool:
        return not (
            not hasattr(self, "rot_matrix")
            or self.rot_matrix is None
            or np.allclose(self.rot_matrix, np.eye(3), atol=1e-3)
        )

    def _get_float_cursor(self) -> list[float]:
        if getattr(self, "_float_cur", None) is not None:
            return [float(self._float_cur[0]), float(self._float_cur[1]), float(self._float_cur[2])]
        return [float(self.cur_x), float(self.cur_y), float(self.cur_z)]

    def _get_plane_axes_spec(self) -> tuple[int, int, bool, int, int, bool, int, int]:
        """Return (h_idx, h_dim, h_flip, v_idx, v_dim, v_flip, n_idx, n_dim) for the active plane."""
        n_z, rows, cols = self.vol.shape
        if self.plane_type == PLANE_AXIAL:
            return 0, cols, False, 1, rows, False, 2, n_z
        if self.plane_type == PLANE_CORONAL:
            return 0, cols, False, 2, n_z, True, 1, rows
        return 1, rows, True, 2, n_z, True, 0, cols

    def _update_axis_pos(self, horizontal_y: float = None, vertical_x: float = None) -> None:
        if self.vol is None or self._target_rect.isEmpty():
            return

        draw_x = self._target_rect.left()
        draw_y = self._target_rect.top()
        draw_w = self._target_rect.width()
        draw_h = self._target_rect.height()

        new_float = self._get_float_cursor()
        h_idx, h_dim, h_flip, v_idx, v_dim, v_flip, _, _ = self._get_plane_axes_spec()

        if vertical_x is not None:
            fx = max(0.0, min(1.0, (vertical_x - draw_x) / max(1.0, draw_w)))
            new_float[h_idx] = ((1.0 - fx) if h_flip else fx) * float(h_dim - 1)
        if horizontal_y is not None:
            fy = max(0.0, min(1.0, (horizontal_y - draw_y) / max(1.0, draw_h)))
            new_float[v_idx] = ((1.0 - fy) if v_flip else fy) * float(v_dim - 1)

        new_x = int(round(new_float[0]))
        new_y = int(round(new_float[1]))
        new_z = int(round(new_float[2]))
        self.set_cursor(new_x, new_y, new_z, float_cur=new_float)
        self.cursor_moved.emit(new_x, new_y, new_z)

    def set_volume_data(
        self,
        vol: np.ndarray,
        ipp0: list[float],
        spacing: tuple[float, float, float],
        cur_x: int,
        cur_y: int,
        cur_z: int,
        window_width: float,
        window_center: float,
        spline_coeffs: np.ndarray | None = None
    ) -> None:
        self.vol = vol
        self.spline_coeffs = spline_coeffs
        self.ipp0 = list(ipp0)
        self.spacing = tuple(spacing)
        self.cur_x = cur_x
        self.cur_y = cur_y
        self.cur_z = cur_z
        if not getattr(self, "_is_panning", False):
            self._float_cur = [float(cur_x), float(cur_y), float(cur_z)]
        self.rot_matrix = np.eye(3, dtype=np.float64)
        self.frame_origin_mm = np.zeros(3, dtype=np.float64)
        self.window_width = window_width
        self.window_center = window_center
        self.refresh_slice()

    def set_cursor(
        self,
        cur_x: int,
        cur_y: int,
        cur_z: int,
        float_cur: list[float] | tuple[float, float, float] | None = None
    ) -> None:
        old_float = self._get_float_cursor()
        if float_cur is not None:
            new_float = [float(float_cur[0]), float(float_cur[1]), float(float_cur[2])]
        elif getattr(self, "_is_panning", False) and getattr(self, "_float_cur", None) is not None:
            new_float = list(self._float_cur)
        else:
            new_float = [float(cur_x), float(cur_y), float(cur_z)]

        need_slice_refresh = False
        if self.vol is not None:
            _, _, _, _, _, _, n_idx, _ = self._get_plane_axes_spec()
            if self._is_rotated():
                # Only the slice orthogonal to n_idx changes when s[n_idx] changes; other two planes stay fixed
                need_slice_refresh = abs(new_float[n_idx] - old_float[n_idx]) > 1e-5
            else:
                old_int = (self.cur_x, self.cur_y, self.cur_z)
                new_int = (cur_x, cur_y, cur_z)
                need_slice_refresh = new_int[n_idx] != old_int[n_idx]

        self.cur_x = cur_x
        self.cur_y = cur_y
        self.cur_z = cur_z
        self._float_cur = new_float

        if need_slice_refresh:
            self.refresh_slice()
        else:
            self.update()

    def set_window_params(self, width: float, center: float) -> None:
        if self.window_width != width or self.window_center != center:
            self.window_width = width
            self.window_center = center
            self.refresh_slice()

    def set_zoom(self, zoom: float) -> None:
        self.zoom_factor = max(0.05, min(30.0, float(zoom)))
        self.update()

    def set_pan(self, pan: QPointF) -> None:
        self.pan_offset = QPointF(pan)
        self.update()

    def _compute_scale(self) -> float:
        parent_panel = self.parent()
        if hasattr(parent_panel, "get_unified_scale"):
            s = parent_panel.get_unified_scale()
            if s > 0:
                return s
        w_width = float(self.width())
        w_height = float(self.height())
        max_w, max_h = self._get_volume_max_dims()
        return min(w_width / max_w, w_height / max_h) * self.zoom_factor

    def _extract_oblique_slice(self) -> np.ndarray:
        n_z, rows, cols = self.vol.shape
        dy, dx, dz = self.spacing

        cur_x_val, cur_y_val, cur_z_val = self._get_float_cursor()

        R = getattr(self, "rot_matrix", np.eye(3, dtype=np.float64))
        origin_mm = getattr(self, "frame_origin_mm", np.zeros(3, dtype=np.float64))
        c_world = _compute_world_cursor_mm(R, origin_mm, (cur_x_val, cur_y_val, cur_z_val), self.spacing)
        cx_rot, cy_rot, cz_rot = float(c_world[0]), float(c_world[1]), float(c_world[2])

        if self.plane_type == PLANE_AXIAL:
            u_ax = (np.arange(cols, dtype=np.float32) - cur_x_val) * float(dx)
            v_ax = (np.arange(rows, dtype=np.float32) - cur_y_val) * float(dy)
            uu, vv = np.meshgrid(u_ax, v_ax)
            px = cx_rot + uu * R[0, 0] + vv * R[0, 1]
            py = cy_rot + uu * R[1, 0] + vv * R[1, 1]
            pz = cz_rot + uu * R[2, 0] + vv * R[2, 1]

        elif self.plane_type == PLANE_CORONAL:
            u_cor = (np.arange(cols, dtype=np.float32) - cur_x_val) * float(dx)
            cur_r_cor = (n_z - 1.0) - cur_z_val
            v_cor = (cur_r_cor - np.arange(n_z, dtype=np.float32)) * float(dz)
            uu, vv = np.meshgrid(u_cor, v_cor)
            px = cx_rot + uu * R[0, 0] + vv * R[0, 2]
            py = cy_rot + uu * R[1, 0] + vv * R[1, 2]
            pz = cz_rot + uu * R[2, 0] + vv * R[2, 2]

        else:  # PLANE_SAGITTAL (Eclipse convention: Posterior left, Anterior right)
            cur_c_sag = (rows - 1.0) - cur_y_val
            u_sag = (cur_c_sag - np.arange(rows, dtype=np.float32)) * float(dy)
            cur_r_sag = (n_z - 1.0) - cur_z_val
            v_sag = (cur_r_sag - np.arange(n_z, dtype=np.float32)) * float(dz)
            uu, vv = np.meshgrid(u_sag, v_sag)
            px = cx_rot + uu * R[0, 1] + vv * R[0, 2]
            py = cy_rot + uu * R[1, 1] + vv * R[1, 2]
            pz = cz_rot + uu * R[2, 1] + vv * R[2, 2]

        coords = np.array([pz / dz, py / dy, px / dx], dtype=np.float32)
        coeffs = self.spline_coeffs
        if coeffs is None or coeffs.shape != self.vol.shape:
            parent_panel = self.parent()
            if hasattr(parent_panel, "ensure_spline_coeffs"):
                coeffs = parent_panel.ensure_spline_coeffs()
            else:
                coeffs = compute_cubic_spline_coeffs(self.vol)
                self.spline_coeffs = coeffs
        return sample_oblique_slice(self.vol, coords, spline_coeffs=coeffs)

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
            if not self._is_rotated():
                if self.plane_type == PLANE_AXIAL:
                    slice_hu = self.vol[self.cur_z, :, :]
                elif self.plane_type == PLANE_CORONAL:
                    slice_hu = self.vol[::-1, self.cur_y, :]
                elif self.plane_type == PLANE_SAGITTAL:
                    slice_hu = self.vol[::-1, ::-1, self.cur_x]
                else:
                    return
            else:
                slice_hu = self._extract_oblique_slice()

            ww = max(1.0, float(self.window_width))
            wc = float(self.window_center)
            v_min = wc - ww / 2.0

            scaled = np.clip((slice_hu - v_min) / ww * 255.0, 0, 255).astype(np.uint8)
            h, w = scaled.shape

            fmt = getattr(QImage.Format, "Format_Grayscale8", getattr(QImage, "Format_Grayscale8", 24))
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

    def _get_volume_max_dims(self) -> tuple[float, float]:
        """Returns (max_phys_w_mm, max_phys_h_mm) across volume to unify MPR scale and positioning."""
        if self.vol is None:
            return 1.0, 1.0
        n_z, rows, cols = self.vol.shape
        dy, dx, dz = self.spacing
        max_w = max(float(cols * dx), float(rows * dy))
        max_h = max(float(rows * dy), float(n_z * dz))
        return max_w, max_h

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#000000"))

        if self.vol is None or self.current_pixmap is None:
            painter.setPen(QColor("#6B7280"))
            painter.setFont(QFont("Segoe UI", 11))
            painter.drawText(self.rect(), ALIGN_CENTER, "No Volume Data")
            return

        w_width = float(self.width())
        w_height = float(self.height())
        phys_w, phys_h, img_w, img_h = self._get_physical_dims()

        # Compute aspect-preserving fit rectangle with unified volume scale and pan
        scale = self._compute_scale()
        draw_w = phys_w * scale
        draw_h = phys_h * scale
        draw_x = (w_width - draw_w) / 2.0 + self.pan_offset.x()
        draw_y = (w_height - draw_h) / 2.0 + self.pan_offset.y()
        self._target_rect = QRectF(draw_x, draw_y, draw_w, draw_h)

        # Crosshair calculations
        n_z, rows, cols = self.vol.shape
        pen_horiz = QPen()
        pen_horiz.setWidth(1)
        pen_horiz.setStyle(PEN_DASH)

        pen_vert = QPen()
        pen_vert.setWidth(1)
        pen_vert.setStyle(PEN_DASH)

        if hasattr(self, "_float_cur") and self._float_cur is not None:
            fx_val = float(self._float_cur[0])
            fy_val = float(self._float_cur[1])
            fz_val = float(self._float_cur[2])
        else:
            fx_val = float(self.cur_x)
            fy_val = float(self.cur_y)
            fz_val = float(self.cur_z)

        if self.plane_type == PLANE_AXIAL:
            pen_horiz.setColor(COLOR_CORONAL)
            pen_vert.setColor(COLOR_SAGITTAL)
            cx_img = fx_val / max(1.0, float(cols - 1))
            cy_img = fy_val / max(1.0, float(rows - 1))
            cx_px = draw_x + cx_img * draw_w
            cy_px = draw_y + cy_img * draw_h

        elif self.plane_type == PLANE_CORONAL:
            pen_horiz.setColor(COLOR_AXIAL)
            pen_vert.setColor(COLOR_SAGITTAL)
            cx_img = fx_val / max(1.0, float(cols - 1))
            cy_img = ((n_z - 1.0) - fz_val) / max(1.0, float(n_z - 1))
            cx_px = draw_x + cx_img * draw_w
            cy_px = draw_y + cy_img * draw_h

        else: # PLANE_SAGITTAL
            pen_horiz.setColor(COLOR_AXIAL)
            pen_vert.setColor(COLOR_CORONAL)
            cx_img = ((rows - 1.0) - fy_val) / max(1.0, float(rows - 1))
            cy_img = ((n_z - 1.0) - fz_val) / max(1.0, float(n_z - 1))
            cx_px = draw_x + cx_img * draw_w
            cy_px = draw_y + cy_img * draw_h

        self._last_pivot = QPointF(cx_px, cy_px)

        # Render CT slice (already reconstructed in 3D along rotated plane)
        painter.drawPixmap(self._target_rect.toRect(), self.current_pixmap)

        # Draw cross lines across the FULL viewport continuously (no gap)
        painter.setPen(pen_horiz)
        painter.drawLine(QPointF(0, cy_px), QPointF(w_width, cy_px))

        painter.setPen(pen_vert)
        painter.drawLine(QPointF(cx_px, 0), QPointF(cx_px, w_height))

        # Handle dimensions: 14x8 for horiz, 8x14 for vert
        self._handle_h_left = QRectF(2, cy_px - 4, 14, 8)
        self._handle_h_right = QRectF(w_width - 16, cy_px - 4, 14, 8)
        self._handle_v_top = QRectF(cx_px - 4, 2, 8, 14)
        self._handle_v_bottom = QRectF(cx_px - 4, w_height - 16, 8, 14)

        # Draw handles with white border and axis color fill
        h_color = pen_horiz.color()
        v_color = pen_vert.color()

        painter.setPen(QPen(QColor("#FFFFFF"), 1))
        painter.setBrush(QBrush(h_color))
        painter.drawRoundedRect(self._handle_h_left, 2, 2)
        painter.drawRoundedRect(self._handle_h_right, 2, 2)

        painter.setBrush(QBrush(v_color))
        painter.drawRoundedRect(self._handle_v_top, 2, 2)
        painter.drawRoundedRect(self._handle_v_bottom, 2, 2)

        # OSD Header Tag (Plane Name + Index + Rotation Angle)
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
            slice_text = ""
            mm_val = self.ipp0[1] + self.cur_y * self.spacing[0]
            coord_text = f"Y: {mm_val:.1f} mm"
        else:
            title = tr_ui("viewer_mpr_sagittal") or "Sagittal"
            badge_color = COLOR_SAGITTAL
            slice_text = ""
            mm_val = self.ipp0[0] + self.cur_x * self.spacing[1]
            coord_text = f"X: {mm_val:.1f} mm"

        # Plane title at top-left (transparent background)
        painter.setPen(badge_color)
        painter.drawText(QRectF(10, 8, 140, 22), ALIGN_LEFT | Qt.AlignmentFlag.AlignVCenter, title)

        # Rotation angle indicator in top-right (transparent background, keep click hit-box)
        rot_val = self.rotation_angle
        parent_panel = self.parent()
        has_any_rot = False
        if hasattr(parent_panel, "rot_x") and hasattr(parent_panel, "rot_y") and hasattr(parent_panel, "rot_z"):
            has_any_rot = (abs(parent_panel.rot_x) > 0.05 or abs(parent_panel.rot_y) > 0.05 or abs(parent_panel.rot_z) > 0.05)
        is_rotated = abs(rot_val) > 0.05
        pill_color = QColor("#F59E0B") if (is_rotated or has_any_rot) else QColor("#9CA3AF")

        self._rot_pill_rect = QRectF(self.width() - 85, 8, 75, 22)
        painter.setPen(pill_color)
        rot_str = f"∠ {rot_val:+.1f}°" if is_rotated else "∠ 0.0°"
        painter.drawText(self._rot_pill_rect, ALIGN_RIGHT | Qt.AlignmentFlag.AlignVCenter, rot_str)

        # Slice info at bottom-left: only show slice count for Axial
        if slice_text:
            painter.setPen(QColor("#D1D5DB"))
            painter.drawText(QRectF(10, self.height() - 40, 160, 16), ALIGN_LEFT, slice_text)
        painter.setPen(QColor("#9CA3AF"))
        painter.drawText(QRectF(10, self.height() - 22, 160, 16), ALIGN_LEFT, coord_text)

    def mousePressEvent(self, event) -> None:
        self.setFocus()
        self._context_menu_shown = False
        pos_f = QPointF(event.pos())
        if hasattr(self, "_rot_pill_rect") and self._rot_pill_rect.contains(pos_f):
            parent_panel = self.parent()
            if hasattr(parent_panel, "reset_rotation"):
                parent_panel.reset_rotation()
            else:
                self.set_rotation(0.0)
                self.rotation_changed.emit(self.plane_type, 0.0)
            return

        if event.button() == BTN_LEFT:
            cx = self._last_pivot.x()
            cy = self._last_pivot.y()

            # Check handles or lines
            is_h = (
                (hasattr(self, "_handle_h_left") and (self._handle_h_left.contains(pos_f) or self._handle_h_right.contains(pos_f)))
                or abs(pos_f.y() - cy) <= 6
            )
            is_v = (
                (hasattr(self, "_handle_v_top") and (self._handle_v_top.contains(pos_f) or self._handle_v_bottom.contains(pos_f)))
                or abs(pos_f.x() - cx) <= 6
            )

            dist_center = math.hypot(pos_f.x() - cx, pos_f.y() - cy)
            if dist_center <= 10:
                self._dragging_axis = "both"
            elif is_h:
                self._dragging_axis = "horizontal"
                self._update_axis_pos(horizontal_y=pos_f.y())
            elif is_v:
                self._dragging_axis = "vertical"
                self._update_axis_pos(vertical_x=pos_f.x())
            else:
                self._dragging_axis = None
                # Clicking free space does NOT move crosshairs

        elif event.button() == BTN_MIDDLE:
            is_shift = bool(
                (event.modifiers() & Qt.KeyboardModifier.ShiftModifier) if hasattr(Qt, "KeyboardModifier")
                else (event.modifiers() & Qt.ShiftModifier)
            )
            if is_shift:
                # Shift + MMB: Interactive rotation around crosshair pivot (never panning)
                self._is_rotating = True
                self._is_panning = False
                self._rot_start_angle = self.rotation_angle
                cx = self._last_pivot.x()
                cy = self._last_pivot.y()
                dx = pos_f.x() - cx
                dy = pos_f.y() - cy
                self._mouse_start_rad = math.atan2(dy, dx)
                self.setCursor(CURSOR_SIZEALL)
            else:
                # MMB: Panning
                self._is_panning = True
                self._is_rotating = False
                self._pan_start = event.pos()

    def mouseMoveEvent(self, event) -> None:
        pos_f = QPointF(event.pos())

        # Handle Shift + MMB rotation around crosshair pivot (follows hand movement)
        if getattr(self, "_is_rotating", False):
            cx = self._last_pivot.x()
            cy = self._last_pivot.y()
            dx = pos_f.x() - cx
            dy = pos_f.y() - cy
            dist = math.hypot(dx, dy)
            if dist < 8.0:
                # Deadzone right at pivot to avoid wild angle spin
                return
            curr_rad = math.atan2(dy, dx)
            delta_deg = math.degrees(curr_rad - self._mouse_start_rad)
            # Normalize delta to [-180, 180] to prevent sudden branch-cut jumps
            delta_deg = (delta_deg + 180.0) % 360.0 - 180.0
            if self.plane_type in (PLANE_CORONAL, PLANE_SAGITTAL):
                delta_deg = -delta_deg
            new_angle = round(self._rot_start_angle + delta_deg, 1)
            self.set_rotation(new_angle)
            self.rotation_changed.emit(self.plane_type, self.rotation_angle)
            return

        # Handle axis dragging
        if getattr(self, "_dragging_axis", None) is not None:
            if self._dragging_axis == "horizontal":
                self._update_axis_pos(horizontal_y=pos_f.y())
            elif self._dragging_axis == "vertical":
                self._update_axis_pos(vertical_x=pos_f.x())
            elif self._dragging_axis == "both":
                self._update_axis_pos(horizontal_y=pos_f.y(), vertical_x=pos_f.x())
            return

        # Handle panning (Middle button only)
        if getattr(self, "_is_panning", False) and self._pan_start is not None:
            modifiers = event.modifiers()
            is_shift = bool(
                (modifiers & Qt.KeyboardModifier.ShiftModifier) if hasattr(Qt, "KeyboardModifier")
                else (modifiers & Qt.ShiftModifier)
            )
            if is_shift:
                self._is_panning = False
                self._is_rotating = True
                self._rot_start_angle = self.rotation_angle
                cx = self._last_pivot.x()
                cy = self._last_pivot.y()
                dx = pos_f.x() - cx
                dy = pos_f.y() - cy
                self._mouse_start_rad = math.atan2(dy, dx)
                self.setCursor(CURSOR_SIZEALL)
                return
            delta = event.pos() - self._pan_start
            self._pan_start = event.pos()

            dx_pan = float(delta.x())
            dy_pan = float(delta.y())

            # Update pan offset
            self.pan_offset = self.pan_offset + QPointF(dx_pan, dy_pan)

            # Recompute voxel under the stationary crosshairs (cx_px, cy_px)
            if self.vol is not None and not self._target_rect.isEmpty():
                n_z, rows, cols = self.vol.shape
                draw_w = self._target_rect.width()
                draw_h = self._target_rect.height()

                if not hasattr(self, "_float_cur") or self._float_cur is None:
                    self._float_cur = [float(self.cur_x), float(self.cur_y), float(self.cur_z)]

                if self.plane_type == PLANE_AXIAL:
                    self._float_cur[0] -= dx_pan * (cols - 1) / max(1.0, draw_w)
                    self._float_cur[1] -= dy_pan * (rows - 1) / max(1.0, draw_h)
                elif self.plane_type == PLANE_CORONAL:
                    self._float_cur[0] -= dx_pan * (cols - 1) / max(1.0, draw_w)
                    self._float_cur[2] += dy_pan * (n_z - 1) / max(1.0, draw_h)
                else:  # PLANE_SAGITTAL
                    self._float_cur[1] += dx_pan * (rows - 1) / max(1.0, draw_w)
                    self._float_cur[2] += dy_pan * (n_z - 1) / max(1.0, draw_h)

                self._float_cur[0] = max(0.0, min(float(cols - 1), self._float_cur[0]))
                self._float_cur[1] = max(0.0, min(float(rows - 1), self._float_cur[1]))
                self._float_cur[2] = max(0.0, min(float(n_z - 1), self._float_cur[2]))

                new_x = int(round(self._float_cur[0]))
                new_y = int(round(self._float_cur[1]))
                new_z = int(round(self._float_cur[2]))

                self.set_cursor(new_x, new_y, new_z, float_cur=self._float_cur)
                self.cursor_moved.emit(new_x, new_y, new_z)

            self.pan_changed.emit(self.pan_offset)
            self.pan_delta_requested.emit(self.plane_type, dx_pan, dy_pan)
            self.update()
            return

        # Hover cursor update
        if hasattr(self, "_last_pivot"):
            cx = self._last_pivot.x()
            cy = self._last_pivot.y()
            dist_c = math.hypot(pos_f.x() - cx, pos_f.y() - cy)
            if dist_c <= 10:
                self.setCursor(CURSOR_SIZEALL)
            elif (hasattr(self, "_handle_h_left") and (self._handle_h_left.contains(pos_f) or self._handle_h_right.contains(pos_f))) or abs(pos_f.y() - cy) <= 6:
                self.setCursor(CURSOR_SIZEVER)
            elif (hasattr(self, "_handle_v_top") and (self._handle_v_top.contains(pos_f) or self._handle_v_bottom.contains(pos_f))) or abs(pos_f.x() - cx) <= 6:
                self.setCursor(CURSOR_SIZEHOR)
            else:
                self.setCursor(CURSOR_CROSS)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == BTN_LEFT:
            self._dragging_axis = None
        elif event.button() == BTN_MIDDLE:
            if getattr(self, "_is_rotating", False):
                self._is_rotating = False
                self.setCursor(CURSOR_CROSS)
            elif getattr(self, "_is_panning", False):
                self._is_panning = False
                self._pan_start = None
                parent_panel = self.parent()
                if hasattr(parent_panel, "on_subview_pan_ended"):
                    parent_panel.on_subview_pan_ended()

    def contextMenuEvent(self, event) -> None:
        g_pos = event.globalPos()
        self._show_context_menu(g_pos)

    def _show_context_menu(self, global_pos: QPoint) -> None:
        parent_panel = self.parent()
        if parent_panel is None:
            return

        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #0f0f0f;
                color: #ffffff;
                border: 1px solid #3d3d3d;
                border-radius: 6px;
                padding: 4px;
                font-family: 'Segoe UI';
                font-size: 12px;
            }
            QMenu::item {
                padding: 6px 20px 6px 12px;
                background-color: transparent;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #1f538d;
                color: #ffffff;
            }
            QMenu::item:disabled {
                color: #666666;
            }
            QMenu::separator {
                height: 1px;
                background: #2d2d2d;
                margin: 4px 8px;
            }
        """)

        action_align = menu.addAction(tr_ui("viewer_mpr_ctx_align"))
        menu.addSeparator()
        action_export = menu.addAction(tr_ui("viewer_mpr_ctx_save"))

        if hasattr(parent_panel, "auto_align_applicator"):
            action_align.triggered.connect(parent_panel.auto_align_applicator)
        else:
            action_align.setEnabled(False)

        parent_viewer = parent_panel.parent() if parent_panel is not None else None
        postproc_enabled = getattr(parent_viewer, "postprocessing_enabled", True) if parent_viewer is not None else True
        if hasattr(parent_panel, "export_aligned_dicom_series") and postproc_enabled:
            action_export.triggered.connect(parent_panel.export_aligned_dicom_series)
        else:
            action_export.setEnabled(False)

        menu.exec(global_pos)

    def mouseDoubleClickEvent(self, event) -> None:
        is_shift = bool(
            (event.modifiers() & Qt.KeyboardModifier.ShiftModifier) if hasattr(Qt, "KeyboardModifier")
            else (event.modifiers() & Qt.ShiftModifier)
        )
        if event.button() == BTN_LEFT:
            if is_shift:
                parent_panel = self.parent()
                if hasattr(parent_panel, "reset_rotation"):
                    parent_panel.reset_rotation()
                else:
                    self.set_rotation(0.0)
                    self.rotation_changed.emit(self.plane_type, 0.0)
                return
            self.double_clicked.emit(self.plane_type)

    def wheelEvent(self, event) -> None:
        modifiers = event.modifiers()
        is_ctrl = bool(
            (modifiers & Qt.KeyboardModifier.ControlModifier) if hasattr(Qt, "KeyboardModifier")
            else (modifiers & Qt.ControlModifier)
        )
        is_shift = bool(
            (modifiers & Qt.KeyboardModifier.ShiftModifier) if hasattr(Qt, "KeyboardModifier")
            else (modifiers & Qt.ShiftModifier)
        )
        delta = event.angleDelta().y()
        if delta == 0:
            return

        # Shift + Wheel: Rotate active plane by +/- 0.1 degree (with Alt: +/- 1.0 degree)
        if is_shift:
            is_alt = bool(
                (modifiers & Qt.KeyboardModifier.AltModifier) if hasattr(Qt, "KeyboardModifier")
                else (modifiers & Qt.AltModifier)
            )
            step = (1.0 if is_alt else 0.1) if delta > 0 else (-1.0 if is_alt else -0.1)
            if self.plane_type in (PLANE_CORONAL, PLANE_SAGITTAL):
                step = -step
            new_angle = round(self.rotation_angle + step, 1)
            self.set_rotation(new_angle)
            self.rotation_changed.emit(self.plane_type, self.rotation_angle)
            return

        # Ctrl + Wheel: Zoom all planes synchronously (inverted to match 2D viewer)
        if is_ctrl:
            factor = 0.9 if delta > 0 else 1.1
            new_zoom = max(0.05, min(30.0, self.zoom_factor * factor))
            self.zoom_changed.emit(new_zoom)
            return

        # Standard Wheel: Cyclic Slice Navigation along active plane normal
        step = 1 if delta > 0 else -1

        if self.vol is None:
            return

        _, _, _, _, _, _, n_idx, n_dim = self._get_plane_axes_spec()
        new_float = self._get_float_cursor()
        curr_val = new_float[n_idx]
        if step > 0:
            new_val = 0.0 if curr_val >= float(n_dim - 1) - 1e-4 else min(float(n_dim - 1), curr_val + float(step))
        else:
            new_val = float(n_dim - 1) if curr_val <= 1e-4 else max(0.0, curr_val + float(step))
        new_float[n_idx] = new_val
        new_x = int(round(new_float[0]))
        new_y = int(round(new_float[1]))
        new_z = int(round(new_float[2]))

        self.set_cursor(new_x, new_y, new_z, float_cur=new_float)
        self.cursor_moved.emit(new_x, new_y, new_z)
        self.slice_scrolled.emit(new_x, new_y, new_z)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        key_r = Qt.Key.Key_R if hasattr(Qt.Key, "Key_R") else Qt.Key_R

        # Key R: Reset rotation across all planes (Eclipse TPS standard)
        if key == key_r:
            parent_panel = self.parent()
            if hasattr(parent_panel, "reset_rotation"):
                parent_panel.reset_rotation()
            else:
                self.set_rotation(0.0)
                self.rotation_changed.emit(self.plane_type, 0.0)
            return

        super().keyPressEvent(event)

    def _update_cursor_from_pos(self, pt) -> None:
        if self.vol is None or self._target_rect.isEmpty():
            return
        m_pt = self._map_screen_to_unrotated(pt)
        n_z, rows, cols = self.vol.shape

        fx = (m_pt.x() - self._target_rect.left()) / max(1.0, self._target_rect.width())
        fy = (m_pt.y() - self._target_rect.top()) / max(1.0, self._target_rect.height())

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
            new_y = int(round((1.0 - fx) * (rows - 1)))
            new_z = int(round((1.0 - fy) * (n_z - 1)))

        self.set_cursor(new_x, new_y, new_z)
        self.cursor_moved.emit(new_x, new_y, new_z)


class MprPanelWidget(QWidget):
    """
    2x2 MPR Container widget:
    - (0, 0): Axial View (XY)
    - (0, 1): Empty Frame (clean placeholder for future 3D view)
    - (1, 0): Coronal View (XZ)
    - (1, 1): Sagittal View (YZ)
    Coordinates, slices, zoom, pan, and W/L parameters are coordinated across views.
    """
    cursor_changed = pyqtSignal(int, int, int) # (x, y, z)
    zoom_changed = pyqtSignal(float)
    pan_changed = pyqtSignal(QPointF)
    plane_pan_changed = pyqtSignal(str, QPointF) # (plane_type, pan)
    rotation_changed = pyqtSignal(str, float) # (plane_type, angle)
    plane_maximized = pyqtSignal(str) # plane_type or "" if restored

    def __init__(self, parent: QWidget = None) -> None:
        super().__init__(parent)
        self.vol = None
        self.spline_coeffs = None
        self.ipp0 = [0.0, 0.0, 0.0]
        self.spacing = (1.0, 1.0, 1.0)
        self.cur_x = 0
        self.cur_y = 0
        self.cur_z = 0
        self._float_cur = [0.0, 0.0, 0.0]
        self.window_width = 400.0
        self.window_center = 40.0
        self.zoom_factor = 1.0
        self.pan_offset = QPointF(0, 0)
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.pan_z = 0.0
        self.rot_x = 0.0
        self.rot_y = 0.0
        self.rot_z = 0.0
        self._rot_matrix = np.eye(3, dtype=np.float64)
        self._frame_origin_mm = np.zeros(3, dtype=np.float64)
        self._maximized_plane = None

        self._setup_ui()

    def _compute_rotation_matrix(self) -> np.ndarray:
        rx, ry, rz = np.radians([self.rot_x, self.rot_y, self.rot_z])
        Rx = np.array([[1, 0, 0], [0, np.cos(rx), -np.sin(rx)], [0, np.sin(rx), np.cos(rx)]], dtype=np.float64)
        Ry = np.array([[np.cos(ry), 0, np.sin(ry)], [0, 1, 0], [-np.sin(ry), 0, np.cos(ry)]], dtype=np.float64)
        Rz = np.array([[np.cos(rz), np.sin(rz), 0], [-np.sin(rz), np.cos(rz), 0], [0, 0, 1]], dtype=np.float64)
        # Intrinsic rotation: axial rotation (Rz) acts directly around the tilted long axis (Ry @ Rx)
        return Ry @ Rx @ Rz

    def _setup_ui(self) -> None:
        self.setStyleSheet("MprPanelWidget { background-color: #000000; }")
        self.grid_layout = QGridLayout(self)
        self.grid_layout.setContentsMargins(0, 0, 0, 0)
        self.grid_layout.setSpacing(1)
        self.grid_layout.setRowStretch(0, 1)
        self.grid_layout.setRowStretch(1, 1)
        self.grid_layout.setColumnStretch(0, 1)
        self.grid_layout.setColumnStretch(1, 1)

        # 3 Orthogonal viewports
        self.view_axial = MprSliceWidget(PLANE_AXIAL, self)
        self.view_coronal = MprSliceWidget(PLANE_CORONAL, self)
        self.view_sagittal = MprSliceWidget(PLANE_SAGITTAL, self)

        for v in (self.view_axial, self.view_coronal, self.view_sagittal):
            v.cursor_moved.connect(self._on_subview_cursor_moved)
            v.zoom_changed.connect(self._on_subview_zoom_changed)
            v.pan_changed.connect(self._on_subview_pan_changed)
            v.pan_delta_requested.connect(self._on_subview_pan_delta)
            v.rotation_changed.connect(self._on_subview_rotation_changed)
            v.double_clicked.connect(self._toggle_maximize_plane)
            v.cycle_plane_requested.connect(self._cycle_maximized_plane)

        # Empty Frame in Top-Right (0, 1) as requested (pure black)
        self.view_empty = QFrame(self)
        self.view_empty.setStyleSheet("background-color: #000000; border: none;")
        self.view_empty.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # Exact layout requested by user:
        # Верх-лево (0, 0): Аксиальная
        # Верх-право (0, 1): Пустое окно
        # Низ-лево (1, 0): Фронтальная / Коронарная
        # Низ-право (1, 1): Сагиттальная
        self.grid_layout.addWidget(self.view_axial, 0, 0)
        self.grid_layout.addWidget(self.view_empty, 0, 1)
        self.grid_layout.addWidget(self.view_coronal, 1, 0)
        self.grid_layout.addWidget(self.view_sagittal, 1, 1)

    def ensure_spline_coeffs(self) -> np.ndarray | None:
        """Return cached float32 cubic B-spline coefficients for the loaded volume."""
        if self.vol is None:
            return None
        if self.spline_coeffs is None or self.spline_coeffs.shape != self.vol.shape:
            self.spline_coeffs = compute_cubic_spline_coeffs(self.vol)
            for v in (self.view_axial, self.view_coronal, self.view_sagittal):
                v.spline_coeffs = self.spline_coeffs
        return self.spline_coeffs

    def set_volume_data(
        self,
        vol: np.ndarray,
        ipp0: list[float],
        spacing: tuple[float, float, float],
        window_width: float,
        window_center: float,
        initial_z: int = None,
        slice_files: list = None,
        initial_zoom: float = 1.0,
        spline_coeffs: np.ndarray | None = None,
        initial_cursor: list[float] | tuple[float, float, float] | None = None
    ) -> None:
        self.vol = vol
        self.spline_coeffs = spline_coeffs if (spline_coeffs is not None and spline_coeffs.shape == vol.shape) else None
        self.ipp0 = list(ipp0)
        self.spacing = tuple(spacing)
        self.window_width = window_width
        self.window_center = window_center
        self.slice_files = [item[0] if isinstance(item, tuple) else item for item in (slice_files or [])]
        self.zoom_factor = max(0.05, min(30.0, float(initial_zoom)))
        self.pan_offset = QPointF(0, 0)
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.pan_z = 0.0
        self.rot_x = 0.0
        self.rot_y = 0.0
        self.rot_z = 0.0
        R = np.eye(3, dtype=np.float64)
        self._rot_matrix = R
        self._frame_origin_mm = np.zeros(3, dtype=np.float64)

        n_z, rows, cols = vol.shape
        if initial_cursor is not None and len(initial_cursor) >= 3:
            fx = max(0.0, min(float(cols - 1), float(initial_cursor[0])))
            fy = max(0.0, min(float(rows - 1), float(initial_cursor[1])))
            fz = max(0.0, min(float(n_z - 1), float(initial_cursor[2])))
            self.cur_x = int(round(fx))
            self.cur_y = int(round(fy))
            self.cur_z = int(round(fz))
            self._float_cur = [fx, fy, fz]
        else:
            self.cur_x = cols // 2
            self.cur_y = rows // 2
            self.cur_z = (n_z // 2) if initial_z is None else max(0, min(n_z - 1, initial_z))
            self._float_cur = [float(self.cur_x), float(self.cur_y), float(self.cur_z)]

        for v in (self.view_axial, self.view_coronal, self.view_sagittal):
            v.set_zoom(self.zoom_factor)
            v.set_pan(QPointF(0, 0))
            v.set_rotation_matrix(R, 0.0, frame_origin_mm=self._frame_origin_mm)
            v.set_volume_data(
                vol=self.vol,
                ipp0=self.ipp0,
                spacing=self.spacing,
                cur_x=self.cur_x,
                cur_y=self.cur_y,
                cur_z=self.cur_z,
                window_width=self.window_width,
                window_center=self.window_center,
                spline_coeffs=self.spline_coeffs
            )
            v.set_cursor(self.cur_x, self.cur_y, self.cur_z, float_cur=self._float_cur)

    def set_cursor(
        self,
        cur_x: int,
        cur_y: int,
        cur_z: int,
        float_cur: list[float] | tuple[float, float, float] | None = None
    ) -> None:
        self.cur_x = cur_x
        self.cur_y = cur_y
        self.cur_z = cur_z
        self._float_cur = (
            [float(float_cur[0]), float(float_cur[1]), float(float_cur[2])]
            if float_cur is not None
            else [float(cur_x), float(cur_y), float(cur_z)]
        )

        for v in (self.view_axial, self.view_coronal, self.view_sagittal):
            v.set_cursor(cur_x, cur_y, cur_z, float_cur=self._float_cur)

    def scroll_axial_to(self, target_z: int) -> None:
        """Scroll axial plane along rotated axis R[:, 2] while keeping Coronal and Sagittal fixed."""
        if self.vol is None:
            return
        n_z = self.vol.shape[0]
        target_z = max(0, min(n_z - 1, int(target_z)))
        curr = list(getattr(self, "_float_cur", [float(self.cur_x), float(self.cur_y), float(self.cur_z)]))
        new_float = [float(curr[0]), float(curr[1]), float(target_z)]
        self.set_cursor(int(round(new_float[0])), int(round(new_float[1])), target_z, float_cur=new_float)

    def set_window_params(self, width: float, center: float) -> None:
        self.window_width = width
        self.window_center = center
        for v in (self.view_axial, self.view_coronal, self.view_sagittal):
            v.set_window_params(width, center)

    def set_zoom(self, zoom: float) -> None:
        self.zoom_factor = max(0.05, min(30.0, float(zoom)))
        for v in (self.view_axial, self.view_coronal, self.view_sagittal):
            v.set_zoom(self.zoom_factor)

    def get_unified_scale(self) -> float:
        if getattr(self, "_maximized_plane", None) is not None:
            return 0.0
        if self.vol is None:
            return 0.0
        spacing = float(self.grid_layout.spacing())
        cell_w = max(1.0, (float(self.width()) - spacing) / 2.0)
        cell_h = max(1.0, (float(self.height()) - spacing) / 2.0)
        n_z, rows, cols = self.vol.shape
        dy, dx, dz = self.spacing
        max_w = max(float(cols * dx), float(rows * dy))
        max_h = max(float(rows * dy), float(n_z * dz))
        return min(cell_w / max_w, cell_h / max_h) * self.zoom_factor

    def set_pan(self, pan: QPointF) -> None:
        self.pan_offset = QPointF(pan)
        self.pan_x = pan.x()
        self.pan_y = pan.y()
        self.view_axial.set_pan(QPointF(self.pan_x, self.pan_y))
        self.view_coronal.set_pan(QPointF(self.pan_x, self.pan_z))
        self.view_sagittal.set_pan(QPointF(self.pan_y, self.pan_z))

    def set_plane_pan(self, plane_type: str, pan: QPointF) -> None:
        if plane_type == PLANE_AXIAL:
            self.pan_x = pan.x()
            self.pan_y = pan.y()
        elif plane_type == PLANE_CORONAL:
            self.pan_x = pan.x()
            self.pan_z = pan.y()
        elif plane_type == PLANE_SAGITTAL:
            self.pan_y = pan.x()
            self.pan_z = pan.y()
        self.view_axial.set_pan(QPointF(self.pan_x, self.pan_y))
        self.view_coronal.set_pan(QPointF(self.pan_x, self.pan_z))
        self.view_sagittal.set_pan(QPointF(self.pan_y, self.pan_z))

    def _apply_rotation_around_cursor(self, R_new: np.ndarray, is_reset: bool = False) -> None:
        """Update rotation matrix while keeping the 3D world point under the crosshair stationary."""
        R_old = self._rot_matrix
        O_old = self._frame_origin_mm
        curr = list(getattr(self, "_float_cur", [float(self.cur_x), float(self.cur_y), float(self.cur_z)]))
        is_identity = is_reset or np.allclose(R_new, np.eye(3), atol=1e-3)
        cursor_changed = False

        if self.vol is not None:
            n_z, rows, cols = self.vol.shape
            dy, dx, dz = float(self.spacing[0]), float(self.spacing[1]), float(self.spacing[2])
            c_world = _compute_world_cursor_mm(R_old, O_old, curr, self.spacing)
            if is_identity:
                O_new = np.zeros(3, dtype=np.float64)
                new_float = [
                    float(np.clip(c_world[0] / dx, 0.0, float(cols - 1))),
                    float(np.clip(c_world[1] / dy, 0.0, float(rows - 1))),
                    float(np.clip(c_world[2] / dz, 0.0, float(n_z - 1))),
                ]
                new_x, new_y, new_z = int(round(new_float[0])), int(round(new_float[1])), int(round(new_float[2]))
                cursor_changed = (new_x, new_y, new_z) != (self.cur_x, self.cur_y, self.cur_z)
                self._float_cur = new_float
                self.cur_x, self.cur_y, self.cur_z = new_x, new_y, new_z
                for v in (self.view_axial, self.view_coronal, self.view_sagittal):
                    v.cur_x, v.cur_y, v.cur_z = new_x, new_y, new_z
                    v._float_cur = list(new_float)
            else:
                s_mm = np.array([curr[0] * dx, curr[1] * dy, curr[2] * dz], dtype=np.float64)
                O_new = c_world - R_new @ s_mm
        else:
            O_new = np.zeros(3, dtype=np.float64)

        self._rot_matrix = np.eye(3, dtype=np.float64) if is_identity else R_new
        self._frame_origin_mm = O_new

        for v in (self.view_axial, self.view_coronal, self.view_sagittal):
            a = (
                0.0 if is_reset else
                (self.rot_z if v.plane_type == PLANE_AXIAL else (self.rot_y if v.plane_type == PLANE_CORONAL else self.rot_x))
            )
            v.set_rotation_matrix(self._rot_matrix, a, frame_origin_mm=self._frame_origin_mm)

        if cursor_changed:
            self.cursor_changed.emit(self.cur_x, self.cur_y, self.cur_z)

    def set_plane_rotation(self, plane_type: str, angle: float) -> None:
        if plane_type == "all":
            self.reset_rotation()
            return
        if plane_type == PLANE_AXIAL:
            self.rot_z = angle
        elif plane_type == PLANE_CORONAL:
            self.rot_y = angle
        elif plane_type == PLANE_SAGITTAL:
            self.rot_x = angle

        self._apply_rotation_around_cursor(self._compute_rotation_matrix())

    def reset_rotation(self) -> None:
        self.rot_x = 0.0
        self.rot_y = 0.0
        self.rot_z = 0.0
        self._apply_rotation_around_cursor(np.eye(3, dtype=np.float64), is_reset=True)
        self.rotation_changed.emit("all", 0.0)

    def reset_to_center(self) -> None:
        if self.vol is None:
            return
        self.reset_rotation()
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.pan_z = 0.0
        self.pan_offset = QPointF(0, 0)
        self.view_axial.set_pan(QPointF(0, 0))
        self.view_coronal.set_pan(QPointF(0, 0))
        self.view_sagittal.set_pan(QPointF(0, 0))
        n_z, rows, cols = self.vol.shape
        cx = cols // 2
        cy = rows // 2
        cz = n_z // 2
        self.set_cursor(cx, cy, cz, float_cur=[float(cx), float(cy), float(cz)])
        self.cursor_changed.emit(cx, cy, cz)

    def auto_align_applicator(self) -> bool:
        if self.vol is None:
            return False

        res = detect_applicator_orientation(self.vol, self.spacing)
        if res is None:
            show_dark_message_box(
                self,
                tr_ui("viewer_mpr_auto_align_title"),
                tr_ui("viewer_mpr_not_detected")
            )
            return False

        self.rot_x = res["rot_x"]
        self.rot_y = res["rot_y"]
        self.rot_z = res["rot_z"]
        self.cur_x = res["cur_x"]
        self.cur_y = res["cur_y"]
        self.cur_z = res["cur_z"]
        float_cur = list(res.get("float_cur", [float(self.cur_x), float(self.cur_y), float(self.cur_z)]))
        self._float_cur = float_cur

        dy, dx, dz = float(self.spacing[0]), float(self.spacing[1]), float(self.spacing[2])
        pivot_mm = np.array([float_cur[0] * dx, float_cur[1] * dy, float_cur[2] * dz], dtype=np.float64)
        R = self._compute_rotation_matrix()
        self._rot_matrix = R
        self._frame_origin_mm = pivot_mm - R @ pivot_mm

        for v in (self.view_axial, self.view_coronal, self.view_sagittal):
            a = self.rot_z if v.plane_type == PLANE_AXIAL else (self.rot_y if v.plane_type == PLANE_CORONAL else self.rot_x)
            v.cur_x = self.cur_x
            v.cur_y = self.cur_y
            v.cur_z = self.cur_z
            v._float_cur = list(float_cur)
            v.set_rotation_matrix(R, a, frame_origin_mm=self._frame_origin_mm)

        self.rotation_changed.emit("all", 0.0)
        self.cursor_changed.emit(self.cur_x, self.cur_y, self.cur_z)
        return True

    def export_aligned_dicom_series(self) -> None:
        parent_viewer = self.parent()
        if parent_viewer is not None and not getattr(parent_viewer, "postprocessing_enabled", True):
            return

        if self.vol is None:
            show_dark_message_box(
                self,
                tr_ui("viewer_mpr_save_title"),
                tr_ui("viewer_mpr_no_volume")
            )
            return

        slice_paths = getattr(self, "slice_files", None)
        if not slice_paths:
            parent_viewer = self.parent()
            if hasattr(parent_viewer, "sorted_files") and parent_viewer.sorted_files:
                slice_paths = [item[0] if isinstance(item, tuple) else item for item in parent_viewer.sorted_files]
            elif hasattr(parent_viewer, "derived_files") and parent_viewer.derived_files:
                slice_paths = [item[0] if isinstance(item, tuple) else item for item in parent_viewer.derived_files]

        if not slice_paths:
            show_dark_message_box(
                self,
                tr_ui("viewer_mpr_save_title"),
                tr_ui("viewer_mpr_no_source_files")
            )
            return

        choice_dlg = MprExportChoiceDialog(
            parent=self,
            rot_x=self.rot_x,
            rot_y=self.rot_y,
            rot_z=self.rot_z,
            n_slices=len(slice_paths)
        )
        if choice_dlg.exec() != QDialog.DialogCode.Accepted or not choice_dlg.selected_action:
            return

        action = choice_dlg.selected_action

        from ui.loading_dialog import LoadingProgressDialog
        progress_dialog = LoadingProgressDialog(
            self,
            title=tr_ui("viewer_mpr_save_title"),
            show_cancel=True
        )
        progress_dialog.set_custom_progress(0, len(slice_paths), f"0 / {len(slice_paths)}")

        export_float_cur = list(getattr(self, "_float_cur", [float(self.cur_x), float(self.cur_y), float(self.cur_z)]))

        worker = MprExportAlignedWorker(
            slice_paths=slice_paths,
            vol=self.vol,
            spacing=self.spacing,
            rot_x=self.rot_x,
            rot_y=self.rot_y,
            rot_z=self.rot_z,
            cur_x=self.cur_x,
            cur_y=self.cur_y,
            cur_z=self.cur_z,
            mode=action,
            parent=self,
            origin_mm=self._frame_origin_mm,
            float_cur=export_float_cur,
            spline_coeffs=self.spline_coeffs
        )
        progress_dialog.on_cancel_cb = worker.cancel

        def on_prog(cur: int, tot: int, text: str) -> None:
            progress_dialog.set_custom_progress(cur, tot, tr_ui("viewer_mpr_saving_progress", text))

        def on_fin(res: dict) -> None:
            progress_dialog.accept()
            out_files = res.get("files", [])
            out_dir = res.get("output_dir", "")
            mode = res.get("mode", "new")
            cnt = res.get("count", len(out_files))
            series_uid = res.get("series_uid", "")
            series_desc = res.get("series_desc", "CT (Aligned)")

            parent_viewer = self.parent()
            if mode == "replace":
                show_dark_message_box(
                    self,
                    tr_ui("viewer_mpr_save_done_title"),
                    tr_ui("viewer_mpr_save_done_replace", cnt)
                )
                if hasattr(parent_viewer, "load_series"):
                    parent_viewer.load_series(out_files)
            else:
                after_dlg = MprAfterSaveDialog(self, n_slices=cnt, out_dir=out_dir)
                accepted = (after_dlg.exec() == QDialog.DialogCode.Accepted)
                chosen_mode = after_dlg.selected_mode if (accepted and after_dlg.selected_mode) else None

                if hasattr(parent_viewer, "register_derived_series"):
                    target = chosen_mode or getattr(parent_viewer, "view_mode", "original")
                    parent_viewer.register_derived_series(
                        files=out_files,
                        series_uid=series_uid,
                        desc=series_desc,
                        target_mode=target,
                        initial_cursor=export_float_cur,
                        reset_orig_rotation=(chosen_mode in ("original", "side_by_side"))
                    )
                elif chosen_mode == "processed" and hasattr(parent_viewer, "load_series"):
                    parent_viewer.load_series(out_files)

        def on_err(err_msg: str) -> None:
            progress_dialog.reject()
            show_dark_message_box(
                self,
                tr_ui("viewer_mpr_save_error_title"),
                tr_ui("viewer_mpr_save_error_msg", err_msg),
                icon=QMessageBox.Icon.Critical
            )

        worker.progress_signal.connect(on_prog)
        worker.finished_signal.connect(on_fin)
        worker.error_signal.connect(on_err)
        worker.start()

        progress_dialog.exec()
        worker.wait()

    def _on_subview_cursor_moved(self, x: int, y: int, z: int) -> None:
        self.cur_x = x
        self.cur_y = y
        self.cur_z = z
        sender_view = self.sender()
        float_cur = getattr(sender_view, "_float_cur", None)
        self._float_cur = list(float_cur) if float_cur is not None else [float(x), float(y), float(z)]
        is_panning = getattr(sender_view, "_is_panning", False)
        for v in (self.view_axial, self.view_coronal, self.view_sagittal):
            if v != sender_view:
                v._is_panning = is_panning
                v.set_cursor(x, y, z, float_cur=self._float_cur)
        self.cursor_changed.emit(x, y, z)

    def on_subview_pan_ended(self) -> None:
        for v in (self.view_axial, self.view_coronal, self.view_sagittal):
            v._is_panning = False
            v.update()

    def _on_subview_zoom_changed(self, zoom: float) -> None:
        self.zoom_factor = max(0.05, min(30.0, float(zoom)))
        for v in (self.view_axial, self.view_coronal, self.view_sagittal):
            v.set_zoom(self.zoom_factor)
        self.zoom_changed.emit(self.zoom_factor)

    def _on_subview_pan_delta(self, plane_type: str, dx: float, dy: float) -> None:
        if plane_type == PLANE_AXIAL:
            self.pan_x += dx
            self.pan_y += dy
        elif plane_type == PLANE_CORONAL:
            self.pan_x += dx
            self.pan_z += dy
        else:  # PLANE_SAGITTAL
            self.pan_y += dx
            self.pan_z += dy

        self.view_axial.set_pan(QPointF(self.pan_x, self.pan_y))
        self.view_coronal.set_pan(QPointF(self.pan_x, self.pan_z))
        self.view_sagittal.set_pan(QPointF(self.pan_y, self.pan_z))
        if plane_type:
            self.plane_pan_changed.emit(plane_type, QPointF(self.pan_x, self.pan_y))
        self.pan_changed.emit(QPointF(self.pan_x, self.pan_y))

    def _on_subview_pan_changed(self, pan: QPointF) -> None:
        sender_view = self.sender()
        plane_type = getattr(sender_view, "plane_type", None)
        if plane_type:
            self.plane_pan_changed.emit(plane_type, pan)
        self.pan_changed.emit(pan)

    def _on_subview_rotation_changed(self, plane_type: str, angle: float) -> None:
        if plane_type == PLANE_AXIAL:
            self.rot_z = angle
        elif plane_type == PLANE_CORONAL:
            self.rot_y = angle
        elif plane_type == PLANE_SAGITTAL:
            self.rot_x = angle

        self._apply_rotation_around_cursor(self._compute_rotation_matrix())
        self.rotation_changed.emit(plane_type, angle)

    def _cycle_maximized_plane(self) -> None:
        if self._maximized_plane is None:
            return
        order = [PLANE_AXIAL, PLANE_CORONAL, PLANE_SAGITTAL]
        try:
            curr_idx = order.index(self._maximized_plane)
            next_plane = order[(curr_idx + 1) % len(order)]
        except ValueError:
            next_plane = PLANE_AXIAL
        self._set_maximized_plane(next_plane)

    def _set_maximized_plane(self, plane_type: str | None) -> None:
        self._maximized_plane = plane_type
        if plane_type is None:
            # Restore 2x2 layout
            self.grid_layout.setRowStretch(0, 1)
            self.grid_layout.setRowStretch(1, 1)
            self.grid_layout.setColumnStretch(0, 1)
            self.grid_layout.setColumnStretch(1, 1)
            self.view_axial.show()
            self.view_empty.show()
            self.view_coronal.show()
            self.view_sagittal.show()
        else:
            # Maximize clicked plane: assign 100% stretch to its row and column
            active_row = 0 if plane_type == PLANE_AXIAL else 1
            active_col = 1 if plane_type == PLANE_SAGITTAL else 0
            self.grid_layout.setRowStretch(0, 1 if active_row == 0 else 0)
            self.grid_layout.setRowStretch(1, 1 if active_row == 1 else 0)
            self.grid_layout.setColumnStretch(0, 1 if active_col == 0 else 0)
            self.grid_layout.setColumnStretch(1, 1 if active_col == 1 else 0)
            for p, v in [
                (PLANE_AXIAL, self.view_axial),
                (PLANE_CORONAL, self.view_coronal),
                (PLANE_SAGITTAL, self.view_sagittal),
            ]:
                if p == plane_type:
                    v.show()
                    v.setFocus()
                else:
                    v.hide()
            self.view_empty.hide()
        self.plane_maximized.emit(self._maximized_plane or "")

    def _toggle_maximize_plane(self, plane_type: str) -> None:
        if self._maximized_plane == plane_type:
            self._set_maximized_plane(None)
        else:
            self._set_maximized_plane(plane_type)
