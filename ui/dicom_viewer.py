from __future__ import annotations

import math
import io
import os
import numpy as np
import pydicom

from PyQt6.QtCore import Qt, pyqtSignal, QSize, QPoint, QRect, QRectF, QPointF, QThread
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel,
    QPushButton, QComboBox, QSlider, QApplication, QSplitter, QSplitterHandle,
    QListWidget, QListWidgetItem, QCheckBox, QTabWidget, QButtonGroup, QStackedWidget, QSizePolicy
)
from PyQt6.QtGui import (
    QIcon, QFont, QPixmap, QBrush, QColor, QPainter,
    QPen, QImage, QLinearGradient, QPolygon, QPolygonF
)
from ui.toggle_switch import ToggleSwitch
from core.config_utils import get_resource_path
from core.locale_utils import tr_ui, tr_log


def safe_dcmread(filepath, *args, **kwargs):
    """
    Безопасно считывает DICOM-файл с помощью pydicom.dcmread.
    При возникновении ошибки ValueError с текстом 'already uncompressed'
    пытается исправить TransferSyntaxUID и перечитать файл.
    """
    try:
        return pydicom.dcmread(filepath, *args, **kwargs)
    except ValueError as e:
        if "already uncompressed" in str(e).lower():
            try:
                ds_meta = pydicom.dcmread(filepath, stop_before_pixels=True)
                ds_meta.file_meta.TransferSyntaxUID = pydicom.uid.ImplicitVRLittleEndian
                if isinstance(filepath, (str, os.PathLike)):
                    ds = pydicom.dcmread(filepath, *args, **kwargs)
                    ds.file_meta.TransferSyntaxUID = pydicom.uid.ImplicitVRLittleEndian
                    return ds
                else:
                    if hasattr(filepath, 'seek'):
                        filepath.seek(0)
                    ds = pydicom.dcmread(filepath, *args, **kwargs)
                    ds.file_meta.TransferSyntaxUID = pydicom.uid.ImplicitVRLittleEndian
                    return ds
            except Exception:
                raise e
        raise e


def load_rtstruct(filepath):
    """
    Парсит файл RTSTRUCT и возвращает словарь со структурами и их контурами.
    """
    structures = {}
    try:
        ds = safe_dcmread(filepath)
        if getattr(ds, "Modality", "") != "RTSTRUCT":
            return structures
            
        roi_names = {}
        if hasattr(ds, "StructureSetROISequence"):
            for roi in ds.StructureSetROISequence:
                num = int(roi.ROINumber)
                name = str(roi.ROIName)
                roi_names[num] = name
                
        if hasattr(ds, "ROIContourSequence"):
            for roi_contour in ds.ROIContourSequence:
                num = int(roi_contour.ReferencedROINumber)
                name = roi_names.get(num, f"ROI {num}")
                
                color = QColor(0, 255, 0)
                if hasattr(roi_contour, "ROIDisplayColor"):
                    rgb = roi_contour.ROIDisplayColor
                    if len(rgb) == 3:
                        color = QColor(int(rgb[0]), int(rgb[1]), int(rgb[2]))
                        
                contours = []
                if hasattr(roi_contour, "ContourSequence"):
                    for contour in roi_contour.ContourSequence:
                        sop_uid = None
                        if hasattr(contour, "ContourImageSequence") and len(contour.ContourImageSequence) > 0:
                            sop_uid = str(contour.ContourImageSequence[0].ReferencedSOPInstanceUID)
                            
                        points = []
                        if hasattr(contour, "ContourData"):
                            cdata = contour.ContourData
                            for i in range(0, len(cdata), 3):
                                if i + 2 < len(cdata):
                                    points.append((float(cdata[i]), float(cdata[i+1]), float(cdata[i+2])))
                                    
                        if points:
                            z_coord = points[0][2]
                            contours.append({
                                "sop_uid": sop_uid,
                                "z": z_coord,
                                "points": points
                            })
                            
                structures[num] = {
                    "name": name,
                    "color": color,
                    "contours": contours
                }
    except Exception as e:
        print(f"Error parsing RTSTRUCT {filepath}: {e}")
        
    return structures


def load_rtdose(filepath: str, plan_files: list[str] = None) -> dict:
    """
    Парсит файл RTDOSE и связывает его с RTPLAN (если передан) для получения предписанной дозы.
    """
    dose_data = {}
    try:
        ds = safe_dcmread(filepath)
        if getattr(ds, "Modality", "") != "RTDOSE":
            return dose_data

        dose_scaling = float(getattr(ds, "DoseGridScaling", 1.0))
        dose_units = str(getattr(ds, "DoseUnits", "Gy"))
        if dose_units.upper() == "GY":
            dose_units = "Gy"

        pixel_arr = ds.pixel_array
        dose_grid = pixel_arr.astype(np.float32) * dose_scaling
        if dose_grid.ndim == 2:
            dose_grid = dose_grid[np.newaxis, ...]

        ipp = [float(x) for x in getattr(ds, "ImagePositionPatient", [0.0, 0.0, 0.0])]
        iop = [float(x) for x in getattr(ds, "ImageOrientationPatient", [1.0, 0.0, 0.0, 0.0, 1.0, 0.0])]
        pixel_spacing = [float(x) for x in getattr(ds, "PixelSpacing", [1.0, 1.0])]

        grid_frame_offset = getattr(ds, "GridFrameOffsetVector", None)
        if grid_frame_offset is not None and len(grid_frame_offset) > 0:
            z_positions = [ipp[2] + float(off) for off in grid_frame_offset]
        else:
            slice_thickness = float(getattr(ds, "SliceThickness", 1.0))
            z_positions = [ipp[2] + i * slice_thickness for i in range(dose_grid.shape[0])]

        max_dose = float(np.max(dose_grid)) if dose_grid.size > 0 else 0.0
        rx_dose = 0.0
        plan_label = ""

        ref_plan_uid = None
        if hasattr(ds, "ReferencedRTPlanSequence") and len(ds.ReferencedRTPlanSequence) > 0:
            ref_plan_uid = str(getattr(ds.ReferencedRTPlanSequence[0], "ReferencedSOPInstanceUID", ""))

        if plan_files:
            for pf in plan_files:
                try:
                    ds_plan = safe_dcmread(pf, stop_before_pixels=True)
                    plan_sop = str(getattr(ds_plan, "SOPInstanceUID", ""))
                    if (ref_plan_uid and plan_sop == ref_plan_uid) or not ref_plan_uid:
                        plan_label = str(getattr(ds_plan, "RTPlanLabel", getattr(ds_plan, "RTPlanName", "")))
                        if hasattr(ds_plan, "DoseReferenceSequence"):
                            for dref in ds_plan.DoseReferenceSequence:
                                if hasattr(dref, "TargetPrescriptionDose"):
                                    rx_dose = float(dref.TargetPrescriptionDose)
                                    break
                                elif hasattr(dref, "DeliveryMaximumDose"):
                                    rx_dose = float(dref.DeliveryMaximumDose)
                                    break
                        if rx_dose > 0:
                            break
                except Exception:
                    pass

        if rx_dose <= 0:
            rx_dose = max_dose if max_dose > 0 else 1.0

        default_levels = [
            {"pct": 107, "color": QColor("#DC2626"), "name": "107%"},
            {"pct": 100, "color": QColor("#EF4444"), "name": "100%"},
            {"pct": 95,  "color": QColor("#F97316"), "name": "95%"},
            {"pct": 90,  "color": QColor("#FBBF24"), "name": "90%"},
            {"pct": 80,  "color": QColor("#84CC16"), "name": "80%"},
            {"pct": 70,  "color": QColor("#06B6D4"), "name": "70%"},
            {"pct": 50,  "color": QColor("#3B82F6"), "name": "50%"},
            {"pct": 30,  "color": QColor("#A855F7"), "name": "30%"},
        ]

        levels = []
        for item in default_levels:
            pct = item["pct"]
            val = round(rx_dose * (pct / 100.0), 2)
            levels.append({
                "name": item["name"],
                "pct": pct,
                "val": val,
                "color": item["color"],
                "enabled": True
            })

        dose_data = {
            "filepath": filepath,
            "dose_grid": dose_grid,
            "z_positions": np.array(z_positions, dtype=np.float32),
            "ipp": ipp,
            "iop": iop,
            "pixel_spacing": pixel_spacing,
            "max_dose": max_dose,
            "rx_dose": rx_dose,
            "dose_units": dose_units,
            "plan_label": plan_label,
            "levels": levels
        }
    except Exception as e:
        print(f"Error parsing RTDOSE {filepath}: {e}")

    return dose_data


def marching_squares_2d(grid: np.ndarray, threshold: float) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """
    Быстрый 2D Marching Squares для поиска сегментов изолиний (изодоз) на скалярной сетке.
    Возвращает список пар координат ((c1, r1), (c2, r2)) в пространстве индексов ячеек сетки.
    """
    if grid.shape[0] < 2 or grid.shape[1] < 2:
        return []

    v0 = grid[:-1, :-1]  # TL
    v1 = grid[:-1, 1:]   # TR
    v2 = grid[1:, 1:]    # BR
    v3 = grid[1:, :-1]   # BL

    b0 = v0 >= threshold
    b1 = v1 >= threshold
    b2 = v2 >= threshold
    b3 = v3 >= threshold

    case_index = (b0.astype(np.uint8) * 1 +
                  b1.astype(np.uint8) * 2 +
                  b2.astype(np.uint8) * 4 +
                  b3.astype(np.uint8) * 8)

    active = (case_index > 0) & (case_index < 15)
    if not np.any(active):
        return []

    rows, cols = np.where(active)
    cases = case_index[rows, cols]

    r_f = rows.astype(np.float32)
    c_f = cols.astype(np.float32)

    v0_a = v0[rows, cols]
    v1_a = v1[rows, cols]
    v2_a = v2[rows, cols]
    v3_a = v3[rows, cols]

    eps = 1e-7
    c_top = c_f + (threshold - v0_a) / (v1_a - v0_a + eps)
    r_top = r_f

    c_right = c_f + 1.0
    r_right = r_f + (threshold - v1_a) / (v2_a - v1_a + eps)

    c_bot = c_f + (threshold - v3_a) / (v2_a - v3_a + eps)
    r_bot = r_f + 1.0

    c_left = c_f
    r_left = r_f + (threshold - v0_a) / (v3_a - v0_a + eps)

    segments = []
    for idx, case in enumerate(cases):
        p_top = (float(c_top[idx]), float(r_top[idx]))
        p_right = (float(c_right[idx]), float(r_right[idx]))
        p_bot = (float(c_bot[idx]), float(r_bot[idx]))
        p_left = (float(c_left[idx]), float(r_left[idx]))

        if case == 1 or case == 14:
            segments.append((p_left, p_top))
        elif case == 2 or case == 13:
            segments.append((p_top, p_right))
        elif case == 3 or case == 12:
            segments.append((p_left, p_right))
        elif case == 4 or case == 11:
            segments.append((p_right, p_bot))
        elif case == 5:
            segments.append((p_left, p_top))
            segments.append((p_right, p_bot))
        elif case == 6 or case == 9:
            segments.append((p_top, p_bot))
        elif case == 7 or case == 8:
            segments.append((p_left, p_bot))
        elif case == 10:
            segments.append((p_left, p_bot))
            segments.append((p_top, p_right))

    return segments


def get_dose_slice_at_z(dose_data: dict, target_z: float) -> np.ndarray | None:
    """
    Возвращает 2D срез сетки дозы для заданной Z-координаты КТ-среза (линейная интерполяция по Z).
    """
    if not dose_data or "dose_grid" not in dose_data:
        return None
    z_pos = dose_data.get("z_positions")
    grid = dose_data.get("dose_grid")
    if z_pos is None or grid is None or len(z_pos) == 0:
        return None
    if len(z_pos) == 1:
        if abs(target_z - z_pos[0]) < 5.0:
            return grid[0]
        return None

    z_min, z_max = min(z_pos[0], z_pos[-1]), max(z_pos[0], z_pos[-1])
    if target_z < z_min - 3.0 or target_z > z_max + 3.0:
        return None

    if z_pos[1] >= z_pos[0]:
        idx = int(np.searchsorted(z_pos, target_z)) - 1
    else:
        idx = int(np.searchsorted(-z_pos, -target_z)) - 1

    idx = max(0, min(len(z_pos) - 2, idx))
    z0 = z_pos[idx]
    z1 = z_pos[idx + 1]
    dz = z1 - z0
    if abs(dz) < 1e-5:
        return grid[idx]

    t = (target_z - z0) / dz
    t = max(0.0, min(1.0, float(t)))
    return (1.0 - t) * grid[idx] + t * grid[idx + 1]


def create_dose_colormap_lut(alpha_base: int = 110) -> np.ndarray:
    """Создает 256x4 RGBA lookup table для гладкого медицинского цветового градиента дозы (Colorwash)."""
    # Опорные точки лучевой терапии: 0 -> 30% Rx, 64 -> 50% Rx, 128 -> 70% Rx, 160 -> 80% Rx, 195 -> 90% Rx, 215 -> 95% Rx, 235 -> 100% Rx, 255 -> 107%+ Rx
    ctrl_pts = np.array([
        [0,   0,   80,  255, alpha_base],
        [64,  0,   220, 255, alpha_base + 15],
        [128, 0,   230, 0,   alpha_base + 25],
        [160, 180, 255, 0,   alpha_base + 35],
        [195, 255, 220, 0,   alpha_base + 45],
        [215, 255, 120, 0,   alpha_base + 55],
        [235, 240, 0,   0,   alpha_base + 65],
        [255, 255, 0,   220, alpha_base + 75]
    ], dtype=np.float32)

    indices = np.arange(256, dtype=np.float32)
    lut = np.zeros((256, 4), dtype=np.uint8)
    for ch in range(4):
        lut[:, ch] = np.clip(np.interp(indices, ctrl_pts[:, 0], ctrl_pts[:, ch + 1]), 0, 255).astype(np.uint8)
    return lut


_DOSE_LUT = create_dose_colormap_lut(110)


def dose_slice_to_rgba(slice_grid: np.ndarray, rx_dose: float, max_dose: float) -> np.ndarray | None:
    """
    Преобразует 2D срез дозы в полупрозрачное RGBA-изображение (H, W, 4).
    Значения ниже 30% от предписанной дозы полностью прозрачны (Alpha = 0).
    """
    if slice_grid is None or slice_grid.size == 0:
        return None

    rx = rx_dose if rx_dose > 0 else (max_dose * 0.9 if max_dose > 0 else 1.0)
    d_min = rx * 0.30
    d_max = max(rx * 1.07, max_dose if max_dose > 0 else rx)

    if d_max <= d_min:
        d_max = d_min + 1.0

    h, w = slice_grid.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)

    mask = slice_grid >= d_min
    if not np.any(mask):
        return rgba

    t = (slice_grid[mask] - d_min) / (d_max - d_min)
    lut_indices = np.clip(t * 255.0, 0, 255).astype(np.int32)
    rgba[mask] = _DOSE_LUT[lut_indices]

    return rgba


class PatientSeriesLoaderWorker(QThread):
    progress_signal = pyqtSignal(int, int, str)
    finished_signal = pyqtSignal(dict)
    error_signal = pyqtSignal(str)

    def __init__(self, files: list[str]) -> None:
        super().__init__()
        self.files = files
        self._is_cancelled = False

    def cancel(self) -> None:
        self._is_cancelled = True

    def run(self) -> None:
        try:
            if not self.files:
                self.error_signal.emit("No files provided")
                return

            total_files = len(self.files)
            series_dir = os.path.dirname(self.files[0])
            
            # 1. Поиск файлов RTSTRUCT, RTDOSE и RTPLAN
            struct_files = []
            dose_files = []
            plan_files = []
            if os.path.exists(series_dir):
                dir_contents = os.listdir(series_dir)
                for f in dir_contents:
                    if self._is_cancelled:
                        return
                    f_path = os.path.join(series_dir, f)
                    if os.path.isfile(f_path):
                        f_up = f.upper()
                        if f_up.startswith("STR"):
                            struct_files.append(f_path)
                        elif f_up.startswith("RD") or f_up.startswith("DOSE"):
                            dose_files.append(f_path)
                        elif f_up.startswith("RP") or f_up.startswith("PLAN"):
                            plan_files.append(f_path)
                        elif f.lower().endswith(".dcm"):
                            try:
                                ds_meta = safe_dcmread(f_path, stop_before_pixels=True)
                                mod = getattr(ds_meta, "Modality", "")
                                if mod == "RTSTRUCT":
                                    if f_path not in struct_files:
                                        struct_files.append(f_path)
                                elif mod == "RTDOSE":
                                    if f_path not in dose_files:
                                        dose_files.append(f_path)
                                elif mod == "RTPLAN":
                                    if f_path not in plan_files:
                                        plan_files.append(f_path)
                            except Exception:
                                pass

            # 2. Обработка КТ файлов с передачей прогресса
            slices = []
            for idx, f in enumerate(self.files):
                if self._is_cancelled:
                    return
                filename = os.path.basename(f)
                if idx % 5 == 0 or idx == total_files - 1:
                    status = tr_ui("loading_dicom_files", idx + 1, total_files)
                    self.progress_signal.emit(idx + 1, total_files, status)

                if filename.startswith("STR") or filename.startswith("RD") or filename.startswith("RP"):
                    continue

                try:
                    ds = safe_dcmread(f, stop_before_pixels=True)
                    if getattr(ds, "Modality", "CT") in ("RTSTRUCT", "RTPLAN", "RTDOSE"):
                        continue
                    if "Rows" not in ds or "Columns" not in ds:
                        continue

                    num_frames = int(ds.get("NumberOfFrames", 1))
                    ipp = getattr(ds, "ImagePositionPatient", None)
                    z_coord = float(ipp[2]) if ipp and len(ipp) >= 3 else 0.0

                    thickness = float(ds.get("SliceThickness", 0.0))
                    if thickness == 0.0:
                        thickness = float(ds.get("SpacingBetweenSlices", 1.0))

                    instance_number = int(getattr(ds, "InstanceNumber", 0))

                    if num_frames > 1:
                        for frame_idx in range(num_frames):
                            frame_z = z_coord + frame_idx * thickness
                            slices.append((f, frame_z, instance_number, frame_idx))
                    else:
                        slices.append((f, z_coord, instance_number, 0))
                except Exception:
                    pass

            if self._is_cancelled:
                return

            if not slices:
                self.error_signal.emit("Серия не содержит корректных DICOM файлов.")
                return

            slices.sort(key=lambda x: (x[1], x[2], x[3]))
            sorted_files = [(x[0], x[3]) for x in slices]

            # 3. Выбор и предпарсинг наиболее свежего файла RTSTRUCT
            selected_struct_idx = -1
            parsed_structures = {}
            if struct_files:
                if self._is_cancelled:
                    return
                struct_files.sort(key=lambda x: os.path.basename(x))
                latest_file = max(struct_files, key=lambda x: os.path.getmtime(x))
                selected_struct_idx = struct_files.index(latest_file) + 1
                
                self.progress_signal.emit(total_files, total_files, tr_ui("loading_rtstruct_data"))
                parsed_structures = load_rtstruct(latest_file)

            # 4. Выбор и предпарсинг наиболее свежего файла RTDOSE
            selected_dose_idx = -1
            parsed_dose = {}
            if dose_files:
                if self._is_cancelled:
                    return
                dose_files.sort(key=lambda x: os.path.basename(x))
                latest_dose_file = max(dose_files, key=lambda x: os.path.getmtime(x))
                selected_dose_idx = dose_files.index(latest_dose_file) + 1

                self.progress_signal.emit(total_files, total_files, tr_ui("loading_rtdose_data"))
                parsed_dose = load_rtdose(latest_dose_file, plan_files)

            if self._is_cancelled:
                return

            result = {
                "struct_files": struct_files,
                "selected_struct_idx": selected_struct_idx,
                "parsed_structures": parsed_structures,
                "dose_files": dose_files,
                "selected_dose_idx": selected_dose_idx,
                "parsed_dose": parsed_dose,
                "plan_files": plan_files,
                "sorted_files": sorted_files
            }
            self.finished_signal.emit(result)

        except Exception as e:
            if not self._is_cancelled:
                self.error_signal.emit(str(e))


class StructureLoaderWorker(QThread):
    finished_signal = pyqtSignal(str, dict)
    error_signal = pyqtSignal(str)

    def __init__(self, sf_path: str) -> None:
        super().__init__()
        self.sf_path = sf_path

    def run(self) -> None:
        try:
            if not self.sf_path or not os.path.exists(self.sf_path):
                self.finished_signal.emit(self.sf_path, {})
                return
            parsed = load_rtstruct(self.sf_path)
            self.finished_signal.emit(self.sf_path, parsed)
        except Exception as e:
            self.error_signal.emit(str(e))


class DoseLoaderWorker(QThread):
    finished_signal = pyqtSignal(str, dict)
    error_signal = pyqtSignal(str)

    def __init__(self, dose_path: str, plan_files: list[str] = None) -> None:
        super().__init__()
        self.dose_path = dose_path
        self.plan_files = plan_files or []

    def run(self) -> None:
        try:
            if not self.dose_path or not os.path.exists(self.dose_path):
                self.finished_signal.emit(self.dose_path, {})
                return
            parsed = load_rtdose(self.dose_path, self.plan_files)
            self.finished_signal.emit(self.dose_path, parsed)
        except Exception as e:
            self.error_signal.emit(str(e))


class HUVerticalSlider(QWidget):
    """Кастомный вертикальный слайдер с двумя ползунками для Window/Level (HU) в стиле Varian Eclipse."""
    values_changed = pyqtSignal(float, float)  # lower_val, upper_val

    def __init__(self, parent: QWidget = None) -> None:
        super().__init__(parent)
        self.min_hu = -1000.0
        self.max_hu = 3000.0
        self.lower_val = -160.0
        self.upper_val = 240.0
        
        self.pad = 12
        self.bar_width = 10
        self.slider_size = 14
        
        self.dragging = None  # None, 'lower', 'upper', 'both'
        self.drag_start_y = 0
        self.drag_start_lower = 0.0
        self.drag_start_upper = 0.0

        self.setMinimumWidth(60)
        self.setMouseTracking(True)

    def set_values(self, lower: float, upper: float) -> None:
        lower = max(self.min_hu, min(self.max_hu, lower))
        upper = max(self.min_hu, min(self.max_hu, upper))
        if lower > upper:
            lower, upper = upper, lower
        if upper - lower < 1.0:
            upper = lower + 1.0
        self.lower_val = lower
        self.upper_val = upper
        self.update()

    def _hu_to_y(self, hu: float) -> int:
        h = self.height()
        active_h = h - 2 * self.pad
        if active_h <= 0:
            return self.pad
        val_pct = (hu - self.min_hu) / (self.max_hu - self.min_hu)
        return int(self.pad + active_h * (1.0 - val_pct))

    def _y_to_hu(self, y: int) -> float:
        h = self.height()
        active_h = h - 2 * self.pad
        if active_h <= 0:
            return self.min_hu
        val_pct = 1.0 - (y - self.pad) / active_h
        val_pct = max(0.0, min(1.0, val_pct))
        return self.min_hu + val_pct * (self.max_hu - self.min_hu)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        h = self.height()
        w = self.width()
        cx = w // 2 - 12

        # 1. Рисуем фон шкалы
        bar_rect = QRect(cx - self.bar_width // 2, self.pad, self.bar_width, h - 2 * self.pad)
        painter.setPen(QPen(QColor("#374151"), 1))
        painter.setBrush(QBrush(QColor("#111827")))
        painter.drawRect(bar_rect)

        # 2. Рисуем градиент внутри шкалы (от lower_val до upper_val)
        y_lower = self._hu_to_y(self.lower_val)
        y_upper = self._hu_to_y(self.upper_val)
        
        # Заливка ниже нижнего порога (черный цвет)
        if y_lower < h - self.pad:
            rect_below = QRect(bar_rect.x(), y_lower, bar_rect.width(), h - self.pad - y_lower)
            painter.fillRect(rect_below, QColor("#000000"))

        # Заливка выше верхнего порога (белый цвет)
        if y_upper > self.pad:
            rect_above = QRect(bar_rect.x(), self.pad, bar_rect.width(), y_upper - self.pad)
            painter.fillRect(rect_above, QColor("#FFFFFF"))

        # Градиент между порогами (от черного снизу до белого сверху)
        if y_upper < y_lower:
            grad = QLinearGradient(cx, y_lower, cx, y_upper)
            grad.setColorAt(0.0, QColor("#000000"))
            grad.setColorAt(1.0, QColor("#FFFFFF"))
            rect_grad = QRect(bar_rect.x(), y_upper, bar_rect.width(), y_lower - y_upper)
            painter.fillRect(rect_grad, grad)

        # 3. Рисуем деления (риски)
        painter.setPen(QPen(QColor("#4B5563"), 1))
        font = painter.font()
        font.setPointSize(8)
        painter.setFont(font)
        
        for hu in range(int(self.min_hu), int(self.max_hu) + 1, 500):
            y_tick = self._hu_to_y(hu)
            painter.drawLine(cx - self.bar_width // 2 - 2, y_tick, cx - self.bar_width // 2, y_tick)
            
            # Подписи (каждые 1000 HU)
            if hu % 1000 == 0:
                painter.setPen(QPen(QColor("#9CA3AF"), 1))
                painter.drawText(cx + self.bar_width // 2 + 5, y_tick + 3, str(hu))
                painter.setPen(QPen(QColor("#4B5563"), 1))

        # 4. Рисуем ползунки
        y_u = self._hu_to_y(self.upper_val)
        y_l = self._hu_to_y(self.lower_val)

        # Рисуем ползунок Upper
        up_poly = [
            QPoint(cx - self.bar_width // 2 - 12, y_u - 5),
            QPoint(cx - self.bar_width // 2 - 2, y_u),
            QPoint(cx - self.bar_width // 2 - 12, y_u + 5)
        ]
        painter.setPen(QPen(QColor("#60A5FA") if self.dragging == "upper" else QColor("#D1D5DB"), 1.5))
        painter.setBrush(QBrush(QColor("#3B82F6") if self.dragging == "upper" else QColor("#4B5563")))
        painter.drawPolygon(QPolygon(up_poly))

        # Рисуем ползунок Lower
        low_poly = [
            QPoint(cx - self.bar_width // 2 - 12, y_l - 5),
            QPoint(cx - self.bar_width // 2 - 2, y_l),
            QPoint(cx - self.bar_width // 2 - 12, y_l + 5)
        ]
        painter.setPen(QPen(QColor("#60A5FA") if self.dragging == "lower" else QColor("#D1D5DB"), 1.5))
        painter.setBrush(QBrush(QColor("#3B82F6") if self.dragging == "lower" else QColor("#4B5563")))
        painter.drawPolygon(QPolygon(low_poly))

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            y = event.position().y()
            w = self.width()
            cx = w // 2 - 12
            
            y_u = self._hu_to_y(self.upper_val)
            y_l = self._hu_to_y(self.lower_val)
            
            click_x = event.position().x()
            in_slider_x = (cx - self.bar_width // 2 - 15 <= click_x <= cx - self.bar_width // 2)

            if in_slider_x and abs(y - y_u) < 8:
                self.dragging = 'upper'
            elif in_slider_x and abs(y - y_l) < 8:
                self.dragging = 'lower'
            elif click_x >= cx - self.bar_width // 2 - 4 and click_x <= cx + self.bar_width // 2 + 4 and y_u <= y <= y_l:
                self.dragging = 'both'
                self.drag_start_y = y
                self.drag_start_lower = self.lower_val
                self.drag_start_upper = self.upper_val
            else:
                new_hu = self._y_to_hu(y)
                if abs(new_hu - self.upper_val) < abs(new_hu - self.lower_val):
                    self.dragging = 'upper'
                    self.upper_val = max(self.lower_val + 10.0, new_hu)
                else:
                    self.dragging = 'lower'
                    self.lower_val = min(self.upper_val - 10.0, new_hu)
                self.values_changed.emit(self.lower_val, self.upper_val)
            self.update()

    def mouseMoveEvent(self, event) -> None:
        y = event.position().y()
        if self.dragging == 'upper':
            new_hu = self._y_to_hu(y)
            self.upper_val = max(self.lower_val + 10.0, min(self.max_hu, new_hu))
            self.values_changed.emit(self.lower_val, self.upper_val)
            self.update()
        elif self.dragging == 'lower':
            new_hu = self._y_to_hu(y)
            self.lower_val = min(self.upper_val - 10.0, max(self.min_hu, new_hu))
            self.values_changed.emit(self.lower_val, self.upper_val)
            self.update()
        elif self.dragging == 'both':
            hu_start = self._y_to_hu(self.drag_start_y)
            hu_current = self._y_to_hu(y)
            delta_hu = hu_current - hu_start
            
            new_lower = self.drag_start_lower + delta_hu
            new_upper = self.drag_start_upper + delta_hu
            
            if new_lower < self.min_hu:
                diff = self.min_hu - new_lower
                new_lower += diff
                new_upper += diff
            elif new_upper > self.max_hu:
                diff = new_upper - self.max_hu
                new_lower -= diff
                new_upper -= diff
                
            self.lower_val = max(self.min_hu, min(self.max_hu, new_lower))
            self.upper_val = max(self.min_hu, min(self.max_hu, new_upper))
            self.values_changed.emit(self.lower_val, self.upper_val)
            self.update()
        else:
            w = self.width()
            cx = w // 2 - 12
            y_u = self._hu_to_y(self.upper_val)
            y_l = self._hu_to_y(self.lower_val)
            click_x = event.position().x()
            in_slider_x = (cx - self.bar_width // 2 - 15 <= click_x <= cx - self.bar_width // 2)
            
            if in_slider_x and (abs(y - y_u) < 8 or abs(y - y_l) < 8):
                self.setCursor(Qt.CursorShape.SplitVCursor)
            elif click_x >= cx - self.bar_width // 2 and click_x <= cx + self.bar_width // 2 and y_u <= y <= y_l:
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, event) -> None:
        self.dragging = None
        self.update()


class DicomViewerWidget(QWidget):
    """Виджет для отрисовки DICOM-изображения, линейки и контуров структур RTSTRUCT."""
    slice_scrolled = pyqtSignal(int)
    window_changed = pyqtSignal(float, float)

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
        self.show_isodoses_globally = True
        self.show_dose_gradient = False

        self.setMouseTracking(True)
        self.setStyleSheet("background-color: #000000;")

    def set_dose_data(self, dose_data: dict) -> None:
        self.dose_data = dose_data or {}
        self.enabled_isodose_levels = {lvl["name"] for lvl in self.dose_data.get("levels", []) if lvl.get("enabled", True)}
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
        self.show_isodoses_globally = True
        self.update()

    def mousePressEvent(self, event) -> None:
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

    def wheelEvent(self, event) -> None:
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

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#000000"))

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
                        rgba = dose_slice_to_rgba(slice_grid, rx, mx)
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


class DicomViewerPanel(QWidget):
    """Панель управления просмотром DICOM серий с поддержкой RTSTRUCT и RTDOSE/RTPLAN."""
    close_requested = pyqtSignal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.parent_app = parent
        self.sorted_files = []
        self.struct_files = []
        self.dose_files = []
        self.plan_files = []
        self.current_index = -1
        self.is_loading = False
        self.loader_worker = None
        self.struct_worker = None
        self.dose_worker = None
        self.progress_dialog = None
        self.pixmap_cache = {}

        self.window_width = 400.0
        self.window_center = 40.0
        self.default_wc = 40.0
        self.default_ww = 400.0

        self.setup_ui()

    def setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)

        # 1. Верхняя панель управления
        top_layout = QHBoxLayout()
        top_layout.setSpacing(10)
        
        self.lbl_info = QLabel(self)
        self.lbl_info.setStyleSheet("font-size: 13px; font-weight: bold; color: #FFFFFF;")
        self.lbl_info.hide()
        top_layout.addWidget(self.lbl_info)

        top_layout.addStretch()

        # Выпадающий список для выбора файла дозы RTDOSE
        self.cb_dose = QComboBox(self)
        self.cb_dose.setFixedWidth(220)
        self.cb_dose.setStyleSheet("""
            QComboBox {
                background-color: #2A2A2A;
                border: 1px solid #374151;
                border-radius: 4px;
                color: #FFFFFF;
                padding: 0px 8px;
                font-size: 12px;
                min-height: 28px;
                max-height: 28px;
            }
            QComboBox QAbstractItemView {
                background-color: #1A1A1A;
                border: 1px solid #374151;
                color: #FFFFFF;
                selection-background-color: #3B82F6;
            }
        """)
        self.cb_dose.hide()
        top_layout.addWidget(self.cb_dose)

        # Выпадающий список для выбора набора структур RTSTRUCT
        self.cb_structures = QComboBox(self)
        self.cb_structures.setFixedWidth(220)
        self.cb_structures.setStyleSheet("""
            QComboBox {
                background-color: #2A2A2A;
                border: 1px solid #374151;
                border-radius: 4px;
                color: #FFFFFF;
                padding: 0px 8px;
                font-size: 12px;
                min-height: 28px;
                max-height: 28px;
            }
            QComboBox QAbstractItemView {
                background-color: #1A1A1A;
                border: 1px solid #374151;
                color: #FFFFFF;
                selection-background-color: #3B82F6;
            }
        """)
        self.cb_structures.hide()
        top_layout.addWidget(self.cb_structures)

        # Выпадающий список пресетов HU
        self.cb_presets = QComboBox(self)
        self.cb_presets.setFixedWidth(200)
        self.cb_presets.setStyleSheet("""
            QComboBox {
                background-color: #2A2A2A;
                border: 1px solid #374151;
                border-radius: 4px;
                color: #FFFFFF;
                padding: 0px 8px;
                font-size: 12px;
                min-height: 28px;
                max-height: 28px;
            }
            QComboBox QAbstractItemView {
                background-color: #1A1A1A;
                border: 1px solid #374151;
                color: #FFFFFF;
                selection-background-color: #3B82F6;
            }
        """)
        top_layout.addWidget(self.cb_presets)

        # Загружаем иконки
        self.img_ruler = QIcon(get_resource_path("themes/ruler.png"))
        self.img_hu = QIcon(get_resource_path("themes/hu.png"))
        self.img_osd = QIcon(get_resource_path("themes/eye.png"))
        self.img_close = QIcon(get_resource_path("themes/close.png"))

        # Кнопка линейки
        self.btn_ruler = QPushButton(self)
        self.btn_ruler.setIcon(self.img_ruler)
        self.btn_ruler.setIconSize(QSize(20, 20))
        self.btn_ruler.setFixedSize(28, 28)
        self.btn_ruler.setToolTip("Линейка")
        self.btn_ruler.clicked.connect(self.toggle_ruler)
        top_layout.addWidget(self.btn_ruler)

        # Кнопка настройки HU
        self.btn_hu = QPushButton(self)
        self.btn_hu.setIcon(self.img_hu)
        self.btn_hu.setIconSize(QSize(20, 20))
        self.btn_hu.setFixedSize(28, 28)
        self.btn_hu.setToolTip("Настройка окна HU")
        self.btn_hu.clicked.connect(self.toggle_hu)
        top_layout.addWidget(self.btn_hu)

        # Кнопка скрытия надписей OSD
        self.btn_osd = QPushButton(self)
        self.btn_osd.setIcon(self.img_osd)
        self.btn_osd.setIconSize(QSize(20, 20))
        self.btn_osd.setFixedSize(28, 28)
        self.btn_osd.setToolTip("Показать/скрыть надписи")
        self.btn_osd.clicked.connect(self.toggle_osd)
        top_layout.addWidget(self.btn_osd)

        # Кнопка закрытия
        self.btn_close = QPushButton(self)
        self.btn_close.setIcon(self.img_close)
        self.btn_close.setIconSize(QSize(20, 20))
        self.btn_close.setFixedSize(28, 28)
        self.btn_close.setToolTip("Закрыть просмотр")
        self.btn_close.clicked.connect(self.close_requested.emit)
        top_layout.addWidget(self.btn_close)
        
        layout.addLayout(top_layout)

        # 2. Центральная область (Изображение, структуры/изодозы слева, шкала HU справа)
        main_layout = QHBoxLayout()
        main_layout.setSpacing(15)

        # Панель структур и изодоз слева (с переключаемыми вкладками)
        self.setup_left_panel()
        main_layout.addWidget(self.structures_panel)

        self.viewer = DicomViewerWidget(self)
        self.viewer.slice_scrolled.connect(self.on_slice_scrolled)
        self.viewer.window_changed.connect(self.on_window_changed)
        main_layout.addWidget(self.viewer, stretch=1)

        # Создаем и добавляем шкалу HU справа
        self.setup_hu_panel()
        main_layout.addWidget(self.hu_panel)

        layout.addLayout(main_layout)

        # 2.5 Горизонтальный слайдер срезов снизу
        self.slider = QSlider(Qt.Orientation.Horizontal, self)
        self.slider.valueChanged.connect(self.on_slider_changed)
        self.slider.setStyleSheet("""
            QSlider::groove:horizontal {
                background: #1F2937;
                height: 6px;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #3B82F6;
                width: 30px;
                margin-top: -5px;
                margin-bottom: -5px;
                border-radius: 6px;
            }
            QSlider::handle:horizontal:hover {
                background: #60A5FA;
            }
        """)
        layout.addWidget(self.slider)

        self.retranslate_ui()
        self.cb_dose.currentIndexChanged.connect(self.on_dose_file_changed)
        self.cb_structures.currentIndexChanged.connect(self.on_structure_file_changed)
        self.cb_presets.currentIndexChanged.connect(self.apply_preset)
        self.update_buttons_style()

    def setup_left_panel(self) -> None:
        self.structures_panel = QFrame(self)
        self.structures_panel.setFixedWidth(225)
        
        eye_path = get_resource_path("themes/eye.png").replace(os.sep, "/")
        self.structures_panel.setStyleSheet("""
            QFrame {
                background-color: #141414;
                border: 1px solid #282828;
                border-radius: 6px;
            }
        """)
        panel_layout = QVBoxLayout(self.structures_panel)
        panel_layout.setContentsMargins(6, 6, 6, 6)
        panel_layout.setSpacing(8)

        # 1. Сегментированный переключатель вкладок
        self.segmented_frame = QFrame(self.structures_panel)
        self.segmented_frame.setStyleSheet("""
            QFrame {
                background-color: #1A1A1A;
                border: 1px solid #282828;
                border-radius: 6px;
            }
        """)
        seg_layout = QHBoxLayout(self.segmented_frame)
        seg_layout.setContentsMargins(2, 2, 2, 2)
        seg_layout.setSpacing(2)

        self.btn_tab_structs = QPushButton(tr_ui("viewer_tab_structures"), self.segmented_frame)
        self.btn_tab_structs.setCheckable(True)
        self.btn_tab_structs.setChecked(True)
        self.btn_tab_structs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_tab_structs.setFixedHeight(26)
        seg_layout.addWidget(self.btn_tab_structs)

        self.btn_tab_isodoses = QPushButton(tr_ui("viewer_tab_isodoses"), self.segmented_frame)
        self.btn_tab_isodoses.setCheckable(True)
        self.btn_tab_isodoses.setChecked(False)
        self.btn_tab_isodoses.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_tab_isodoses.setFixedHeight(26)
        seg_layout.addWidget(self.btn_tab_isodoses)

        self.tab_btn_group = QButtonGroup(self.structures_panel)
        self.tab_btn_group.setExclusive(True)
        self.tab_btn_group.addButton(self.btn_tab_structs, 0)
        self.tab_btn_group.addButton(self.btn_tab_isodoses, 1)
        self.tab_btn_group.idClicked.connect(self.on_tab_button_clicked)

        panel_layout.addWidget(self.segmented_frame)

        # 2. Стек содержимого вкладок
        self.stack_panel = QStackedWidget(self.structures_panel)
        self.stack_panel.setStyleSheet("QStackedWidget { background: transparent; border: none; }")

        list_style = f"""
            QListWidget {{
                background-color: #0f0f0f;
                border: 1px solid #282828;
                border-radius: 6px;
                color: #FFFFFF;
                outline: 0;
                font-family: "Segoe UI", -apple-system, Roboto, sans-serif;
                font-size: 12px;
            }}
            QListWidget::item {{
                padding: 5px 8px;
                border-radius: 4px;
                margin: 2px 2px;
            }}
            QListWidget::item:hover {{
                background-color: #222222;
            }}
            QListWidget::item:selected {{
                background-color: #1f538d;
                color: #FFFFFF;
            }}
            QListWidget::indicator {{
                width: 14px;
                height: 14px;
                border: 1px solid #3d3d3d;
                border-radius: 3px;
                background-color: #0f0f0f;
            }}
            QListWidget::indicator:hover {{
                border-color: #1f538d;
                background-color: #151515;
            }}
            QListWidget::indicator:checked {{
                image: url({eye_path});
                border: 1px solid #1f538d;
                border-radius: 3px;
                background-color: #1f538d;
            }}
        """

        # 2.1 Страница "Структуры"
        page_structs = QWidget()
        page_structs.setStyleSheet("background: transparent; border: none;")
        struct_layout = QVBoxLayout(page_structs)
        struct_layout.setContentsMargins(0, 2, 0, 0)
        struct_layout.setSpacing(8)

        self.cb_show_structures = ToggleSwitch(tr_ui("viewer_show_structures"), page_structs)
        self.cb_show_structures.setChecked(True)
        self.cb_show_structures.stateChanged.connect(self.on_global_structures_changed)
        struct_layout.addWidget(self.cb_show_structures)

        self.list_structures = QListWidget(page_structs)
        self.list_structures.setStyleSheet(list_style)
        self.list_structures.itemChanged.connect(self.on_structure_item_changed)
        struct_layout.addWidget(self.list_structures)

        self.stack_panel.addWidget(page_structs)

        # 2.2 Страница "Изодозы"
        page_isodoses = QWidget()
        page_isodoses.setStyleSheet("background: transparent; border: none;")
        dose_layout = QVBoxLayout(page_isodoses)
        dose_layout.setContentsMargins(0, 2, 0, 0)
        dose_layout.setSpacing(8)

        self.cb_show_isodoses = ToggleSwitch(tr_ui("viewer_show_isodoses"), page_isodoses)
        self.cb_show_isodoses.setChecked(True)
        self.cb_show_isodoses.stateChanged.connect(self.on_global_isodoses_changed)
        dose_layout.addWidget(self.cb_show_isodoses)

        self.cb_dose_gradient = ToggleSwitch(tr_ui("viewer_show_dose_gradient"), page_isodoses)
        self.cb_dose_gradient.setChecked(False)
        self.cb_dose_gradient.stateChanged.connect(self.on_dose_gradient_changed)
        dose_layout.addWidget(self.cb_dose_gradient)

        self.lbl_dose_info = QLabel(page_isodoses)
        self.lbl_dose_info.setStyleSheet("color: #9CA3AF; font-size: 10px; font-weight: bold; padding: 0px 2px; border: none;")
        self.lbl_dose_info.setWordWrap(True)
        dose_layout.addWidget(self.lbl_dose_info)

        self.list_isodoses = QListWidget(page_isodoses)
        self.list_isodoses.setStyleSheet(list_style)
        self.list_isodoses.itemChanged.connect(self.on_isodose_item_changed)
        dose_layout.addWidget(self.list_isodoses)

        self.stack_panel.addWidget(page_isodoses)

        panel_layout.addWidget(self.stack_panel)
        self.update_tab_buttons_style()

    def on_tab_button_clicked(self, tab_id: int) -> None:
        self.stack_panel.setCurrentIndex(tab_id)
        self.update_tab_buttons_style()

    def on_dose_gradient_changed(self, state: int) -> None:
        self.viewer.show_dose_gradient = (state == 2)
        self.viewer.update()

    def update_tab_buttons_style(self) -> None:
        palette = self.parent_app.THEMES[self.parent_app.current_theme] if hasattr(self.parent_app, "current_theme") and hasattr(self.parent_app, "THEMES") else {
            "ACCENT_COLOR": "#3B82F6",
            "ACCENT_COLOR_DARK": "#2563EB",
            "TEXT_COLOR": "#FFFFFF",
            "TEXT_MUTED": "#9CA3AF",
            "BUTTON_BG": "#374151"
        }
        accent = palette.get("ACCENT_COLOR", "#3B82F6")
        accent_dark = palette.get("ACCENT_COLOR_DARK", "#2563EB")
        
        style_active = f"""
            QPushButton {{
                background-color: {accent_dark};
                color: #FFFFFF;
                border: none;
                border-radius: 4px;
                font-size: 11px;
                font-weight: bold;
                padding: 4px 0px;
            }}
        """
        style_inactive = f"""
            QPushButton {{
                background-color: transparent;
                color: {palette.get('TEXT_MUTED', '#9CA3AF')};
                border: none;
                border-radius: 4px;
                font-size: 11px;
                font-weight: bold;
                padding: 4px 0px;
            }}
            QPushButton:hover {{
                background-color: {palette.get('BUTTON_BG', '#374151')};
                color: #FFFFFF;
            }}
        """
        self.btn_tab_structs.setStyleSheet(style_active if self.btn_tab_structs.isChecked() else style_inactive)
        self.btn_tab_isodoses.setStyleSheet(style_active if self.btn_tab_isodoses.isChecked() else style_inactive)

    def on_global_structures_changed(self, state: int) -> None:
        self.viewer.show_structures_globally = (state == 2)
        self.viewer.rebuild_contour_index()
        self.viewer.update()

    def on_structure_item_changed(self, item: QListWidgetItem) -> None:
        name = item.text()
        checked = (item.checkState() == Qt.CheckState.Checked)
        if checked:
            self.viewer.enabled_structures.add(name)
        else:
            self.viewer.enabled_structures.discard(name)
        self.viewer.rebuild_contour_index()
        self.viewer.update()

    def apply_structures(self, parsed: dict) -> None:
        self.viewer.structures.clear()
        self.viewer.enabled_structures.clear()
        
        self.list_structures.blockSignals(True)
        self.list_structures.clear()
        
        for num, data in parsed.items():
            self.viewer.structures[num] = data
            self.viewer.enabled_structures.add(data["name"])
            
        for num, data in self.viewer.structures.items():
            item = QListWidgetItem(data["name"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            
            color = data["color"]
            item.setForeground(QBrush(color))
            self.list_structures.addItem(item)
            
        self.list_structures.blockSignals(False)
        self.viewer.rebuild_contour_index()
        self.viewer.update()

    def on_structure_file_changed(self, index: int) -> None:
        self.viewer.structures.clear()
        self.viewer.enabled_structures.clear()
        
        self.list_structures.blockSignals(True)
        self.list_structures.clear()
        self.list_structures.blockSignals(False)
        self.viewer.update()
        
        if index >= 0:
            sf_path = self.cb_structures.itemData(index)
            if sf_path and os.path.exists(sf_path):
                if self.struct_worker is not None and self.struct_worker.isRunning():
                    self.struct_worker.quit()
                    self.struct_worker.wait()
                
                self.struct_worker = StructureLoaderWorker(sf_path)
                self.struct_worker.finished_signal.connect(self._on_structure_loaded)
                self.struct_worker.start()

    def _on_structure_loaded(self, sf_path: str, parsed: dict) -> None:
        current_sf = self.cb_structures.currentData()
        if current_sf == sf_path:
            self.apply_structures(parsed)

    def on_global_isodoses_changed(self, state: int) -> None:
        self.viewer.show_isodoses_globally = (state == 2)
        self.viewer.update()

    def on_isodose_item_changed(self, item: QListWidgetItem) -> None:
        name = item.data(Qt.ItemDataRole.UserRole)
        checked = (item.checkState() == Qt.CheckState.Checked)
        if checked:
            self.viewer.enabled_isodose_levels.add(name)
        else:
            self.viewer.enabled_isodose_levels.discard(name)
        self.viewer.update()

    def apply_dose_data(self, dose_data: dict) -> None:
        self.viewer.set_dose_data(dose_data)
        
        self.list_isodoses.blockSignals(True)
        self.list_isodoses.clear()
        
        if not dose_data or "levels" not in dose_data:
            self.lbl_dose_info.setText("")
            self.list_isodoses.blockSignals(False)
            return

        rx = dose_data.get("rx_dose", 0.0)
        mx = dose_data.get("max_dose", 0.0)
        units = dose_data.get("dose_units", "Gy")
        self.lbl_dose_info.setText(f"{tr_ui('viewer_rx_dose')}: {rx:.2f} {units} | {tr_ui('viewer_max_dose')}: {mx:.2f} {units}")

        for lvl in dose_data.get("levels", []):
            name = lvl["name"]
            val = lvl["val"]
            col = lvl["color"]

            item_text = f"{name} — {val:.2f} {units}"
            item = QListWidgetItem(item_text)
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if lvl.get("enabled", True) else Qt.CheckState.Unchecked)
            item.setForeground(QBrush(col))
            self.list_isodoses.addItem(item)

        self.list_isodoses.blockSignals(False)

    def on_dose_file_changed(self, index: int) -> None:
        self.viewer.set_dose_data({})
        self.list_isodoses.blockSignals(True)
        self.list_isodoses.clear()
        self.list_isodoses.blockSignals(False)
        self.lbl_dose_info.setText("")
        self.viewer.update()

        if index >= 0:
            dose_path = self.cb_dose.itemData(index)
            if dose_path and os.path.exists(dose_path):
                if self.dose_worker is not None and self.dose_worker.isRunning():
                    self.dose_worker.quit()
                    self.dose_worker.wait()

                self.dose_worker = DoseLoaderWorker(dose_path, self.plan_files)
                self.dose_worker.finished_signal.connect(self._on_dose_loaded)
                self.dose_worker.start()

    def _on_dose_loaded(self, dose_path: str, parsed: dict) -> None:
        current_path = self.cb_dose.currentData()
        if current_path == dose_path:
            self.apply_dose_data(parsed)

    def setup_hu_panel(self) -> None:
        self.hu_panel = QFrame(self)
        self.hu_panel.setFixedWidth(70)
        self.hu_panel.setStyleSheet("""
            QFrame {
                background-color: #1F2937;
                border: 1px solid #374151;
                border-radius: 6px;
            }
            QLabel {
                border: none;
                background: transparent;
                color: #EF4444;
                font-size: 11px;
                font-weight: bold;
            }
        """)
        panel_layout = QVBoxLayout(self.hu_panel)
        panel_layout.setContentsMargins(4, 10, 4, 10)
        panel_layout.setSpacing(6)

        self.lbl_upper_hu = QLabel("240 HU", self.hu_panel)
        self.lbl_upper_hu.setAlignment(Qt.AlignmentFlag.AlignCenter)
        panel_layout.addWidget(self.lbl_upper_hu)

        self.hu_slider = HUVerticalSlider(self.hu_panel)
        self.hu_slider.values_changed.connect(self.on_vertical_slider_changed)
        panel_layout.addWidget(self.hu_slider, stretch=1)

        self.lbl_lower_hu = QLabel("-160 HU", self.hu_panel)
        self.lbl_lower_hu.setAlignment(Qt.AlignmentFlag.AlignCenter)
        panel_layout.addWidget(self.lbl_lower_hu)

        self.hu_panel.hide()

    def on_vertical_slider_changed(self, lower: float, upper: float) -> None:
        self.window_width = upper - lower
        self.window_center = (upper + lower) / 2.0
        
        self.cb_presets.blockSignals(True)
        self.cb_presets.setCurrentIndex(-1)
        self.cb_presets.blockSignals(False)
        
        self.lbl_upper_hu.setText(f"{int(upper)} HU")
        self.lbl_lower_hu.setText(f"{int(lower)} HU")
        self.update_current_slice_pixels()

    def retranslate_ui(self) -> None:
        self.cb_presets.blockSignals(True)
        self.cb_presets.clear()
        self.cb_presets.addItem("По умолчанию (DICOM)", "dicom")
        self.cb_presets.addItem("Мягкие ткани", "soft")
        self.cb_presets.addItem("Кости", "bone")
        self.cb_presets.addItem("Легкие", "lung")
        self.cb_presets.addItem("Мозг", "brain")
        self.cb_presets.blockSignals(False)

        self.cb_dose.blockSignals(True)
        if not getattr(self, "dose_files", []):
            self.cb_dose.clear()
            self.cb_dose.addItem(tr_ui("viewer_no_dose_available"), None)
        else:
            self.cb_dose.setItemText(0, tr_ui("viewer_no_dose"))
        self.cb_dose.blockSignals(False)

        self.cb_structures.blockSignals(True)
        if not getattr(self, "struct_files", []):
            self.cb_structures.clear()
            self.cb_structures.addItem(tr_ui("viewer_no_structures_available"), None)
        else:
            self.cb_structures.setItemText(0, tr_ui("viewer_no_structures"))
        self.cb_structures.blockSignals(False)

        if hasattr(self, "btn_tab_structs"):
            self.btn_tab_structs.setText(tr_ui("viewer_tab_structures"))
        if hasattr(self, "btn_tab_isodoses"):
            self.btn_tab_isodoses.setText(tr_ui("viewer_tab_isodoses"))

        if hasattr(self, "cb_show_structures"):
            self.cb_show_structures.setText(tr_ui("viewer_show_structures"))
        if hasattr(self, "cb_show_isodoses"):
            self.cb_show_isodoses.setText(tr_ui("viewer_show_isodoses"))
        if hasattr(self, "cb_dose_gradient"):
            self.cb_dose_gradient.setText(tr_ui("viewer_show_dose_gradient"))

        self.btn_ruler.setToolTip("Линейка")
        self.btn_hu.setToolTip("Настройка окна HU")
        self.btn_osd.setToolTip("Показать/скрыть надписи")
        self.btn_close.setToolTip("Закрыть просмотр")

    def apply_theme(self) -> None:
        if not hasattr(self.parent_app, "current_theme") or not hasattr(self.parent_app, "THEMES"):
            return
        palette = self.parent_app.THEMES[self.parent_app.current_theme]
        
        self.lbl_info.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {palette['TEXT_LIGHT']};")
        
        style_combo = f"""
            QComboBox {{
                background-color: {palette['PANEL_BG']};
                border: 1px solid {palette['BORDER_COLOR_ALT']};
                color: {palette['TEXT_COLOR']};
                selection-background-color: {palette['ACCENT_COLOR']};
                selection-color: #FFFFFF;
            }}
            QComboBox QAbstractItemView {{
                background-color: {palette['PANEL_BG']};
                border: 1px solid {palette['BORDER_COLOR']};
                selection-background-color: {palette['ACCENT_COLOR']};
                selection-color: #FFFFFF;
                outline: none;
            }}
        """
        self.cb_presets.setStyleSheet(style_combo)
        self.cb_dose.setStyleSheet(style_combo)
        self.cb_structures.setStyleSheet(style_combo)
        
        self.hu_panel.setStyleSheet(f"""
            QFrame {{
                background-color: {palette['PANEL_BG']};
                border: 1px solid {palette['BORDER_COLOR']};
                border-radius: 6px;
            }}
            QLabel {{
                border: none;
                background: transparent;
                color: {palette['TEXT_COLOR']};
            }}
        """)

        self.structures_panel.setStyleSheet(f"""
            QFrame {{
                background-color: {palette['PANEL_BG']};
                border: 1px solid {palette['BORDER_COLOR']};
                border-radius: 6px;
            }}
            QLabel {{
                border: none;
                background: transparent;
                color: {palette['TEXT_COLOR']};
            }}
        """)

        if hasattr(self, "segmented_frame"):
            self.segmented_frame.setStyleSheet(f"""
                QFrame {{
                    background-color: {palette.get('WINDOW_BG', '#111827')};
                    border: 1px solid {palette['BORDER_COLOR']};
                    border-radius: 6px;
                }}
            """)

        eye_path = get_resource_path("themes/eye.png").replace(os.sep, "/")
        list_style = f"""
            QListWidget {{
                background-color: {palette.get('WINDOW_BG', '#111827')};
                border: 1px solid {palette['BORDER_COLOR']};
                border-radius: 6px;
                color: {palette['TEXT_COLOR']};
                outline: 0;
                font-family: "Segoe UI", -apple-system, Roboto, sans-serif;
                font-size: 12px;
            }}
            QListWidget::item {{
                padding: 5px 8px;
                border-radius: 4px;
                margin: 2px 2px;
            }}
            QListWidget::item:hover {{
                background-color: {palette.get('HOVER_BG', '#222222')};
            }}
            QListWidget::item:selected {{
                background-color: {palette['ACCENT_COLOR']};
                color: #FFFFFF;
            }}
            QListWidget::indicator {{
                width: 14px;
                height: 14px;
                border: 1px solid {palette.get('BORDER_COLOR_ALT', '#3d3d3d')};
                border-radius: 3px;
                background-color: {palette.get('WINDOW_BG', '#0f0f0f')};
            }}
            QListWidget::indicator:hover {{
                border-color: {palette['ACCENT_COLOR']};
                background-color: {palette.get('PANEL_BG', '#151515')};
            }}
            QListWidget::indicator:checked {{
                image: url({eye_path});
                border: 1px solid {palette['ACCENT_COLOR']};
                border-radius: 3px;
                background-color: {palette['ACCENT_COLOR']};
            }}
        """
        if hasattr(self, "list_structures"):
            self.list_structures.setStyleSheet(list_style)
        if hasattr(self, "list_isodoses"):
            self.list_isodoses.setStyleSheet(list_style)
        self.update_tab_buttons_style()

        self.slider.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                background: {palette['BORDER_COLOR_ALT']};
                height: 6px;
                border-radius: 3px;
            }}
            QSlider::handle:horizontal {{
                background: {palette['ACCENT_COLOR']};
                width: 30px;
                margin-top: -5px;
                margin-bottom: -5px;
                border-radius: 6px;
            }}
            QSlider::handle:horizontal:hover {{
                background: {palette['ACCENT_COLOR_DARK']};
            }}
        """)
        
        self.update_buttons_style()

    def update_buttons_style(self) -> None:
        palette = self.parent_app.THEMES[self.parent_app.current_theme] if hasattr(self.parent_app, "current_theme") and hasattr(self.parent_app, "THEMES") else {
            "ACCENT_COLOR": "#3B82F6",
            "ACCENT_COLOR_DARK": "#2563EB",
            "BUTTON_BG": "#374151",
            "BORDER_COLOR_ALT": "#4B5563",
            "BUTTON_HOVER_BG": "#4B5563"
        }
        
        accent_color = palette.get("ACCENT_COLOR", "#3B82F6")
        accent_dark = palette.get("ACCENT_COLOR_DARK", "#2563EB")
        
        btn_bg = palette.get("BUTTON_BG", "#374151")
        btn_border = palette.get("BORDER_COLOR_ALT", "#4B5563")
        btn_hover = palette.get("BUTTON_HOVER_BG", "#4B5563")

        style_ruler_active = f"""
            QPushButton {{
                background-color: {accent_color};
                border: 1px solid {accent_dark};
                border-radius: 4px;
                padding: 0px;
                min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px;
            }}
        """
        style_ruler_inactive = f"""
            QPushButton {{
                background-color: {btn_bg};
                border: 1px solid {btn_border};
                border-radius: 4px;
                padding: 0px;
                min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px;
            }}
            QPushButton:hover {{ background-color: {btn_hover}; }}
        """
        style_hu_active = f"""
            QPushButton {{
                background-color: {accent_color};
                border: 1px solid {accent_dark};
                border-radius: 4px;
                padding: 0px;
                min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px;
            }}
        """
        style_hu_inactive = f"""
            QPushButton {{
                background-color: {btn_bg};
                border: 1px solid {btn_border};
                border-radius: 4px;
                padding: 0px;
                min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px;
            }}
            QPushButton:hover {{ background-color: {btn_hover}; }}
        """
        style_osd_active = f"""
            QPushButton {{
                background-color: {accent_color};
                border: 1px solid {accent_dark};
                border-radius: 4px;
                padding: 0px;
                min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px;
            }}
        """
        style_osd_inactive = f"""
            QPushButton {{
                background-color: {btn_bg};
                border: 1px solid {btn_border};
                border-radius: 4px;
                padding: 0px;
                min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px;
            }}
            QPushButton:hover {{ background-color: {btn_hover}; }}
        """
        style_close = """
            QPushButton {
                background-color: #BE123C;
                border: 1px solid #E11D48;
                border-radius: 4px;
                padding: 0px;
                min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px;
            }
            QPushButton:hover {
                background-color: #E11D48;
                border-color: #F43F5E;
            }
            QPushButton:pressed {
                background-color: #9F1239;
            }
        """

        self.btn_ruler.setStyleSheet(style_ruler_active if self.viewer.ruler_active else style_ruler_inactive)
        self.btn_hu.setStyleSheet(style_hu_active if self.viewer.hu_active else style_hu_inactive)
        self.btn_osd.setStyleSheet(style_osd_active if self.viewer.osd_visible else style_osd_inactive)
        self.btn_close.setStyleSheet(style_close)

    def toggle_osd(self) -> None:
        self.viewer.set_osd_visible(not self.viewer.osd_visible)
        self.update_buttons_style()

    def toggle_ruler(self) -> None:
        active = not self.viewer.ruler_active
        self.viewer.ruler_active = active
        if active:
            self.viewer.hu_active = False
            self.hu_panel.hide()
        self.update_buttons_style()

    def toggle_hu(self) -> None:
        active = not self.viewer.hu_active
        self.viewer.hu_active = active
        if active:
            self.viewer.ruler_active = False
            self.hu_panel.show()
        else:
            self.hu_panel.hide()
        self.update_buttons_style()

    def clear_panel(self) -> None:
        import gc
        if self.loader_worker is not None and self.loader_worker.isRunning():
            self.loader_worker.quit()
            self.loader_worker.wait()
        if self.struct_worker is not None and self.struct_worker.isRunning():
            self.struct_worker.quit()
            self.struct_worker.wait()
        if self.dose_worker is not None and self.dose_worker.isRunning():
            self.dose_worker.quit()
            self.dose_worker.wait()

        self.viewer.clear_viewer()
        self.pixmap_cache.clear()
        self.sorted_files.clear()
        self.struct_files.clear()
        self.dose_files.clear()
        self.plan_files.clear()

        self.cb_dose.blockSignals(True)
        self.cb_dose.clear()
        self.cb_dose.blockSignals(False)

        self.cb_structures.blockSignals(True)
        self.cb_structures.clear()
        self.cb_structures.blockSignals(False)

        self.list_structures.blockSignals(True)
        self.list_structures.clear()
        self.list_structures.blockSignals(False)

        self.list_isodoses.blockSignals(True)
        self.list_isodoses.clear()
        self.list_isodoses.blockSignals(False)

        self.lbl_dose_info.setText("")
        self.lbl_info.setText("")
        self.current_index = -1
        self.is_loading = False
        gc.collect()

    def load_series(self, files: list[str]) -> None:
        self.is_loading = True
        self.sorted_files = []
        self.current_index = -1
        self.pixmap_cache.clear()
        self.viewer.clear_viewer()
        self.hu_panel.hide()
        self.update_buttons_style()

        self.cb_presets.blockSignals(True)
        self.cb_presets.setCurrentIndex(0)
        self.cb_presets.blockSignals(False)

        self.cb_dose.blockSignals(True)
        self.cb_dose.clear()
        self.dose_files = []

        self.cb_structures.blockSignals(True)
        self.cb_structures.clear()
        self.struct_files = []
        self.plan_files = []

        if self.loader_worker is not None and self.loader_worker.isRunning():
            self.loader_worker.quit()
            self.loader_worker.wait()

        from ui.loading_dialog import LoadingProgressDialog
        self.progress_dialog = LoadingProgressDialog(
            self,
            title=tr_ui("loading_viewer_title"),
            show_cancel=True,
            on_cancel=self._on_cancel_load_series
        )
        total_count = len(files) if files else 0
        self.progress_dialog.set_custom_progress(0, total_count, tr_ui("loading_dicom_files", 0, total_count))

        self.loader_worker = PatientSeriesLoaderWorker(files)
        self.loader_worker.progress_signal.connect(self._on_load_progress)
        self.loader_worker.finished_signal.connect(self._on_series_loaded)
        self.loader_worker.error_signal.connect(self._on_series_load_error)
        self.loader_worker.start()

        self.progress_dialog.exec()

    def _on_cancel_load_series(self) -> None:
        if self.loader_worker is not None:
            self.loader_worker.cancel()
        self.is_loading = False
        self.lbl_info.setText("Загрузка отменена.")

    def _on_load_progress(self, current: int, total: int, text: str) -> None:
        if self.progress_dialog:
            self.progress_dialog.set_custom_progress(current, total, text)

    def _on_series_loaded(self, result: dict) -> None:
        if self.loader_worker and getattr(self.loader_worker, '_is_cancelled', False):
            return

        if self.progress_dialog:
            self.progress_dialog.accept()
            self.progress_dialog = None

        self.struct_files = result.get("struct_files", [])
        self.dose_files = result.get("dose_files", [])
        self.plan_files = result.get("plan_files", [])
        
        selected_struct_idx = result.get("selected_struct_idx", -1)
        parsed_structures = result.get("parsed_structures", {})
        
        selected_dose_idx = result.get("selected_dose_idx", -1)
        parsed_dose = result.get("parsed_dose", {})
        
        self.sorted_files = result.get("sorted_files", [])

        # Настройка выпадающего списка RTDOSE
        self.cb_dose.blockSignals(True)
        if self.dose_files:
            self.cb_dose.addItem(tr_ui("viewer_no_dose"), None)
            for df in self.dose_files:
                display_name = os.path.basename(df)
                if df == parsed_dose.get("filepath") and parsed_dose.get("plan_label"):
                    display_name = f"Dose: {parsed_dose['plan_label']}"
                self.cb_dose.addItem(display_name, df)

            if selected_dose_idx > 0:
                self.cb_dose.setCurrentIndex(selected_dose_idx)
            else:
                self.cb_dose.setCurrentIndex(0)

            self.cb_dose.setEnabled(True)
            self.cb_dose.show()
        else:
            self.cb_dose.addItem(tr_ui("viewer_no_dose_available"), None)
            self.cb_dose.setCurrentIndex(0)
            self.cb_dose.setEnabled(False)
            self.cb_dose.hide()
        self.cb_dose.blockSignals(False)

        # Настройка выпадающего списка RTSTRUCT
        self.cb_structures.blockSignals(True)
        if self.struct_files:
            self.cb_structures.addItem(tr_ui("viewer_no_structures"), None)
            for sf in self.struct_files:
                self.cb_structures.addItem(os.path.basename(sf), sf)

            if selected_struct_idx > 0:
                self.cb_structures.setCurrentIndex(selected_struct_idx)
            else:
                self.cb_structures.setCurrentIndex(0)

            self.cb_structures.setEnabled(True)
            self.cb_structures.show()
        else:
            self.cb_structures.addItem(tr_ui("viewer_no_structures_available"), None)
            self.cb_structures.setCurrentIndex(0)
            self.cb_structures.setEnabled(False)
            self.cb_structures.hide()
        self.cb_structures.blockSignals(False)

        self.apply_structures(parsed_structures)
        self.viewer.show_structures_globally = self.cb_show_structures.isChecked()

        self.apply_dose_data(parsed_dose)
        self.viewer.show_isodoses_globally = self.cb_show_isodoses.isChecked()
        self.viewer.show_dose_gradient = self.cb_dose_gradient.isChecked()

        if not self.sorted_files:
            self.lbl_info.setText("Серия не содержит корректных DICOM файлов.")
            self.viewer.set_slice_info(0, 0)
            self.is_loading = False
            return

        self.slider.setRange(0, len(self.sorted_files) - 1)
        self.is_loading = False
        self.set_current_slice(0)

    def _on_series_load_error(self, error_msg: str) -> None:
        if self.loader_worker and getattr(self.loader_worker, '_is_cancelled', False):
            return

        if self.progress_dialog:
            self.progress_dialog.reject()
            self.progress_dialog = None
        self.lbl_info.setText(error_msg)
        self.viewer.set_slice_info(0, 0)
        self.is_loading = False

    def read_truncated_dicom(self, filepath: str):
        ds_meta = safe_dcmread(filepath, stop_before_pixels=True)
        transfer_syntax = ds_meta.file_meta.TransferSyntaxUID
        
        with open(filepath, "rb") as f:
            file_bytes = bytearray(f.read())
            
        is_compressed = transfer_syntax.startswith("1.2.840.10008.1.2.4.") or "rle" in getattr(ds_meta.file_meta, "TransferSyntaxUID_name", "").lower()
        
        if is_compressed:
            has_eoi = file_bytes.endswith(b"\xff\xd9") or b"\xff\xd9" in file_bytes[-20:]
            if not has_eoi:
                file_bytes.extend(b"\xff\xd9")
                
            has_delim = b"\xfe\xff\xdd\xe0" in file_bytes[-20:]
            if not has_delim:
                file_bytes.extend(b"\xfe\xff\xdd\xe0\x00\x00\x00\x00")
        else:
            rows = getattr(ds_meta, "Rows", 512)
            cols = getattr(ds_meta, "Columns", 512)
            bits = getattr(ds_meta, "BitsAllocated", 16)
            expected_pixels = rows * cols * (bits // 8)
            if len(file_bytes) < expected_pixels:
                file_bytes.extend(b"\x00" * (expected_pixels * 2))
                
        bio = io.BytesIO(file_bytes)
        ds = safe_dcmread(bio)
        
        try:
            _ = ds.pixel_array
        except Exception:
            rows = getattr(ds, "Rows", 512)
            cols = getattr(ds, "Columns", 512)
            bits = getattr(ds, "BitsAllocated", 16)
            pixel_repr = getattr(ds, "PixelRepresentation", 0)
            if bits == 16:
                dtype = np.int16 if pixel_repr == 1 else np.uint16
            else:
                dtype = np.int8 if pixel_repr == 1 else np.uint8
            arr = np.zeros((rows, cols), dtype=dtype)
            
            class TruncatedDataset(type(ds)):
                @property
                def pixel_array(self):
                    return getattr(self, "_pixel_array", None)
            
            ds._pixel_array = arr
            ds.__class__ = TruncatedDataset
            
        return ds

    def set_current_slice(self, index: int) -> None:
        if index < 0 or index >= len(self.sorted_files):
            return

        self.current_index = index
        self.slider.setValue(index)

        filepath, frame_idx = self.sorted_files[index]
        cache_key = (filepath, frame_idx, round(self.window_width, 1), round(self.window_center, 1))

        if cache_key in self.pixmap_cache:
            pixmap, ds = self.pixmap_cache[cache_key]
            pat_name = getattr(ds, "PatientName", "Unknown")
            pat_id = getattr(ds, "PatientID", "Unknown")
            study_desc = getattr(ds, "StudyDescription", "")
            series_desc = getattr(ds, "SeriesDescription", "")
            
            info_text = f"{pat_name} [{pat_id}] | {study_desc} | {series_desc}"
            self.lbl_info.setText(info_text)
            self.viewer.set_slice_info(index + 1, len(self.sorted_files))
            self.viewer.set_dicom_image(pixmap, ds)
            self.viewer.set_window_params(self.window_width, self.window_center)
            return

        try:
            try:
                ds = safe_dcmread(filepath)
                if not hasattr(ds, "pixel_array") or len(ds) == 0:
                    raise ValueError("Empty dataset or missing pixel array")
            except Exception:
                ds = self.read_truncated_dicom(filepath)

            ds.current_frame_idx = frame_idx

            if self.current_index == 0:
                self.default_wc = 40.0
                self.default_ww = 400.0
                wc = getattr(ds, "WindowCenter", None)
                ww = getattr(ds, "WindowWidth", None)
                if wc is not None and ww is not None:
                    try:
                        c_val = wc[0] if hasattr(wc, "__iter__") else wc
                        w_val = ww[0] if hasattr(ww, "__iter__") else ww
                        self.default_wc = float(c_val)
                        self.default_ww = float(w_val)
                    except Exception:
                        pass
                
                preset_data = self.cb_presets.currentData()
                if preset_data == "dicom":
                    self.window_center = self.default_wc
                    self.window_width = self.default_ww
                    cache_key = (filepath, frame_idx, round(self.window_width, 1), round(self.window_center, 1))

            pat_name = getattr(ds, "PatientName", "Unknown")
            pat_id = getattr(ds, "PatientID", "Unknown")
            study_desc = getattr(ds, "StudyDescription", "")
            series_desc = getattr(ds, "SeriesDescription", "")
            
            info_text = f"{pat_name} [{pat_id}] | {study_desc} | {series_desc}"
            self.lbl_info.setText(info_text)
            self.viewer.set_slice_info(index + 1, len(self.sorted_files))

            pixmap = self.dicom_to_pixmap(ds, self.window_width, self.window_center)
            if pixmap:
                if len(self.pixmap_cache) > 80:
                    oldest_key = next(iter(self.pixmap_cache))
                    del self.pixmap_cache[oldest_key]
                self.pixmap_cache[cache_key] = (pixmap, ds)
                self.viewer.set_dicom_image(pixmap, ds)
                self.viewer.set_window_params(self.window_width, self.window_center)
            else:
                raise ValueError("Failed to decode pixel array to pixmap")
                
        except Exception as e:
            print(f"[Viewer] Skipping corrupted file {filepath} (frame {frame_idx}): {str(e)}")
            self.sorted_files.pop(index)
            if not self.sorted_files:
                self.lbl_info.setText("Нет доступных изображений в серии.")
                self.viewer.set_slice_info(0, 0)
                self.viewer.clear_viewer()
                return

            self.slider.setRange(0, len(self.sorted_files) - 1)
            new_index = min(index, len(self.sorted_files) - 1)
            self.set_current_slice(new_index)

    def on_slider_changed(self, value: int) -> None:
        if not self.is_loading and value != self.current_index:
            self.set_current_slice(value)

    def on_slice_scrolled(self, step: int) -> None:
        new_index = self.current_index + step
        if 0 <= new_index < len(self.sorted_files):
            self.set_current_slice(new_index)

    def on_window_changed(self, width: float, center: float) -> None:
        self.window_width = width
        self.window_center = center
        
        if self.sender() == self.viewer:
            self.cb_presets.blockSignals(True)
            self.cb_presets.setCurrentIndex(-1)
            self.cb_presets.blockSignals(False)
            
        if hasattr(self, "hu_panel") and hasattr(self, "hu_slider"):
            lower = center - width / 2.0
            upper = center + width / 2.0
            
            self.hu_slider.blockSignals(True)
            self.hu_slider.set_values(lower, upper)
            self.hu_slider.blockSignals(False)
            
            self.lbl_upper_hu.setText(f"{int(upper)} HU")
            self.lbl_lower_hu.setText(f"{int(lower)} HU")
            
        self.update_current_slice_pixels()

    def update_current_slice_pixels(self) -> None:
        ds = self.viewer.current_dataset
        if ds is not None:
            pixmap = self.dicom_to_pixmap(ds, self.window_width, self.window_center)
            if pixmap:
                self.viewer.set_dicom_image(pixmap, ds)
                self.viewer.set_window_params(self.window_width, self.window_center)
            return

        if self.current_index < 0 or self.current_index >= len(self.sorted_files):
            return
        filepath, frame_idx = self.sorted_files[self.current_index]
        try:
            ds = safe_dcmread(filepath)
            ds.current_frame_idx = frame_idx
            pixmap = self.dicom_to_pixmap(ds, self.window_width, self.window_center)
            if pixmap:
                self.viewer.set_dicom_image(pixmap, ds)
                self.viewer.set_window_params(self.window_width, self.window_center)
        except Exception:
            pass

    def apply_preset(self, index: int) -> None:
        preset_type = self.cb_presets.itemData(index)
        
        if preset_type == "dicom":
            self.window_width = self.default_ww
            self.window_center = self.default_wc
        elif preset_type == "soft":
            self.window_width = 400.0
            self.window_center = 40.0
        elif preset_type == "bone":
            self.window_width = 1500.0
            self.window_center = 300.0
        elif preset_type == "lung":
            self.window_width = 1500.0
            self.window_center = -600.0
        elif preset_type == "brain":
            self.window_width = 80.0
            self.window_center = 40.0

        self.on_window_changed(self.window_width, self.window_center)

    def dicom_to_pixmap(self, ds, window_width: float, window_center: float) -> QPixmap | None:
        try:
            if not hasattr(ds, "pixel_array"):
                return None

            original_ts = getattr(ds.file_meta, "TransferSyntaxUID", None)
            if original_ts and not hasattr(ds, "original_transfer_syntax"):
                ds.original_transfer_syntax = original_ts

            try:
                ds.decompress()
            except Exception as e:
                if "already uncompressed" in str(e).lower():
                    ds.file_meta.TransferSyntaxUID = pydicom.uid.ImplicitVRLittleEndian

            pixel_array = ds.pixel_array
            frame_idx = getattr(ds, "current_frame_idx", 0)

            # Определяем размерность массива и извлекаем нужный кадр
            if pixel_array.ndim == 3:
                # Если третья ось имеет размер 3 или 4, это RGB/RGBA изображение (один кадр)
                if pixel_array.shape[2] in (3, 4):
                    if pixel_array.shape[2] == 3:
                        arr = (0.299 * pixel_array[..., 0] + 
                               0.587 * pixel_array[..., 1] + 
                               0.114 * pixel_array[..., 2]).astype(float)
                    else:
                        arr = (0.299 * pixel_array[..., 0] + 
                               0.587 * pixel_array[..., 1] + 
                               0.114 * pixel_array[..., 2]).astype(float)
                else:
                    # Иначе это многокадровое монохромное изображение (Frames, Rows, Columns)
                    f_idx = min(max(0, frame_idx), pixel_array.shape[0] - 1)
                    arr = pixel_array[f_idx].astype(float)
            elif pixel_array.ndim == 4:
                # Многокадровое цветное изображение (Frames, Rows, Columns, Channels)
                f_idx = min(max(0, frame_idx), pixel_array.shape[0] - 1)
                frame_data = pixel_array[f_idx]
                if frame_data.shape[2] in (3, 4):
                    arr = (0.299 * frame_data[..., 0] + 
                           0.587 * frame_data[..., 1] + 
                           0.114 * frame_data[..., 2]).astype(float)
                else:
                    arr = frame_data.astype(float)
            else:
                arr = pixel_array.astype(float)

            slope = float(getattr(ds, "RescaleSlope", 1.0))
            intercept = float(getattr(ds, "RescaleIntercept", 0.0))
            arr = arr * slope + intercept

            min_val = window_center - window_width / 2.0
            max_val = window_center + window_width / 2.0

            arr = np.clip(arr, min_val, max_val)
            arr = ((arr - min_val) / (max_val - min_val) * 255.0).astype(np.uint8)

            height, width = arr.shape
            bytes_per_line = width

            self._temp_arr = np.ascontiguousarray(arr)
            qimg = QImage(self._temp_arr.data, width, height, bytes_per_line, QImage.Format.Format_Grayscale8)
            return QPixmap.fromImage(qimg)
        except Exception:
            return None
