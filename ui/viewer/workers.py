from __future__ import annotations

import os
import math
from collections import defaultdict
import numpy as np
from concurrent.futures import ThreadPoolExecutor
from PyQt6.QtCore import pyqtSignal, QThread, QPointF
from PyQt6.QtGui import QImage, QPainterPath, QPolygonF

from core.locale_utils import tr_ui
from core.dicom_utils import classify_dicom_file
from .parsers import safe_dcmread, load_rtstruct, load_rtdose, load_rtplan, _convex_hull_2d


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
                        t = classify_dicom_file(f, f_path)
                        if t == "RTSTRUCT":
                            struct_files.append(f_path)
                        elif t == "RTDOSE":
                            dose_files.append(f_path)
                        elif t == "RTPLAN":
                            plan_files.append(f_path)

            # 2. Обработка КТ файлов с передачей прогресса (0% -> 60%)
            slices = []
            for idx, f in enumerate(self.files):
                if self._is_cancelled:
                    return
                filename = os.path.basename(f)
                if idx % 3 == 0 or idx == total_files - 1:
                    pct = int(((idx + 1) / total_files) * 60)
                    status = tr_ui("loading_dicom_files", idx + 1, total_files)
                    self.progress_signal.emit(pct, 100, status)

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

            # 3. Выбор и предпарсинг наиболее свежего файла RTSTRUCT (60% -> 85%)
            selected_struct_idx = -1
            parsed_structures = {}
            if struct_files:
                if self._is_cancelled:
                    return
                struct_files.sort(key=lambda x: os.path.basename(x))
                latest_file = max(struct_files, key=lambda x: os.path.getmtime(x))
                selected_struct_idx = struct_files.index(latest_file) + 1
                
                def on_struct_progress(cur_roi, total_roi):
                    if self._is_cancelled:
                        return
                    p = int(60 + (cur_roi / max(1, total_roi)) * 25)
                    status = f"{tr_ui('loading_rtstruct_data')} ({cur_roi}/{total_roi})"
                    self.progress_signal.emit(p, 100, status)

                self.progress_signal.emit(60, 100, tr_ui("loading_rtstruct_data"))
                parsed_structures = load_rtstruct(latest_file, progress_callback=on_struct_progress)
            else:
                self.progress_signal.emit(85, 100, "Завершение обработки КТ...")

            # 4. Выбор и предпарсинг наиболее свежего файла RTDOSE (85% -> 95%)
            selected_dose_idx = -1
            parsed_dose = {}
            if dose_files:
                if self._is_cancelled:
                    return
                dose_files.sort(key=lambda x: os.path.basename(x))
                latest_dose_file = max(dose_files, key=lambda x: os.path.getmtime(x))
                selected_dose_idx = dose_files.index(latest_dose_file) + 1

                self.progress_signal.emit(87, 100, tr_ui("loading_rtdose_data"))
                parsed_dose = load_rtdose(latest_dose_file, plan_files)
                self.progress_signal.emit(95, 100, tr_ui("loading_rtdose_data"))
            else:
                self.progress_signal.emit(95, 100, "Подготовка данных...")

            # 5. Выбор и предпарсинг файла RTPLAN (95% -> 100%)
            parsed_plan = {}
            if plan_files:
                if self._is_cancelled:
                    return
                plan_files.sort(key=lambda x: os.path.basename(x))
                latest_plan_file = max(plan_files, key=lambda x: os.path.getmtime(x))
                self.progress_signal.emit(96, 100, "Загрузка параметров плана RTPLAN...")
                parsed_plan = load_rtplan(latest_plan_file)

            self.progress_signal.emit(99, 100, "Инициализация отображения...")

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
                "parsed_plan": parsed_plan,
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


class DRRPrecomputeWorker(QThread):
    progress_signal = pyqtSignal(int, int, float)  # current, total, gantry_angle
    item_computed_signal = pyqtSignal(tuple, object)  # cache_key, QImage
    finished_signal = pyqtSignal(bool)  # is_cancelled
    error_signal = pyqtSignal(str)

    def __init__(self, sorted_files: list, ct_volume: np.ndarray | None, ct_ipp0: list | None, ct_spacing: tuple | None, beams: list, existing_cache_keys: set) -> None:
        super().__init__()
        self.sorted_files = sorted_files
        self.ct_volume = ct_volume
        self.ct_ipp0 = ct_ipp0
        self.ct_spacing = ct_spacing
        self.beams = beams or []
        self.existing_cache_keys = set(existing_cache_keys)
        self._is_cancelled = False

    def cancel(self) -> None:
        self._is_cancelled = True

    def run(self) -> None:
        try:
            # 1. Сборка 3D-объема КТ если еще не собран
            vol = self.ct_volume
            ipp0 = self.ct_ipp0
            spacing = self.ct_spacing

            if vol is None:
                if not self.sorted_files:
                    self.finished_signal.emit(False)
                    return
                slices_ds = []
                for item in self.sorted_files:
                    if self._is_cancelled:
                        self.finished_signal.emit(True)
                        return
                    f_path = item[0] if isinstance(item, tuple) else item
                    ds = safe_dcmread(f_path)
                    if hasattr(ds, "ImagePositionPatient") and hasattr(ds, "pixel_array") and getattr(ds, "Modality", "CT") == "CT" and ds.pixel_array.ndim == 2:
                        slices_ds.append(ds)
                if not slices_ds or self._is_cancelled:
                    self.finished_signal.emit(self._is_cancelled)
                    return
                slices_ds.sort(key=lambda s: float(s.ImagePositionPatient[2]))
                n_z = len(slices_ds)
                rows = int(slices_ds[0].Rows)
                cols = int(slices_ds[0].Columns)
                vol = np.zeros((n_z, rows, cols), dtype=np.float32)
                for i, s in enumerate(slices_ds):
                    if self._is_cancelled:
                        self.finished_signal.emit(True)
                        return
                    slope = float(getattr(s, "RescaleSlope", 1.0) or 1.0)
                    intercept = float(getattr(s, "RescaleIntercept", 0.0) or 0.0)
                    vol[i] = s.pixel_array.astype(np.float32) * slope + intercept

                ipp0 = [float(x) for x in slices_ds[0].ImagePositionPatient]
                ipp_last = [float(x) for x in slices_ds[-1].ImagePositionPatient]
                sp = [float(x) for x in slices_ds[0].PixelSpacing]
                dy, dx = float(sp[0]), float(sp[1])
                dz = (ipp_last[2] - ipp0[2]) / (n_z - 1) if n_z > 1 else 5.0
                spacing = (dy, dx, dz)

            dy, dx, dz = spacing
            n_z, rows, cols = vol.shape

            # Предварительно срезаем воздух (< -500 HU)
            vol_pre = np.maximum(0.0, vol + 500.0)

            # 2. Формируем список уникальных проекций для расчета
            items = []
            seen_keys = set(self.existing_cache_keys)
            for b in self.beams:
                sad = float(b.get("sad", 1000.0) or 1000.0)
                cps = b.get("control_points", [])
                if not cps:
                    g_angle = float(b.get("gantry_angle", 0.0))
                    iso = b.get("isocenter")
                    if iso and len(iso) >= 3:
                        ck = (round(g_angle, 1), round(iso[0], 2), round(iso[1], 2), round(iso[2], 2), round(sad, 1))
                        if ck not in seen_keys:
                            seen_keys.add(ck)
                            items.append((g_angle, iso, sad, ck))
                else:
                    # Для динамических полей с множеством точек шагаем с интервалом ~3.0°
                    step_deg = 3.0 if len(cps) > 30 else 0.5
                    last_g = None
                    for cp in cps:
                        g_angle = float(cp.get("gantry_angle", b.get("gantry_angle", 0.0)))
                        iso = cp.get("isocenter", b.get("isocenter"))
                        if not iso or len(iso) < 3:
                            continue
                        if last_g is not None and abs(g_angle - last_g) < (step_deg - 0.1):
                            continue
                        ck = (round(g_angle, 1), round(iso[0], 2), round(iso[1], 2), round(iso[2], 2), round(sad, 1))
                        if ck not in seen_keys:
                            seen_keys.add(ck)
                            items.append((g_angle, iso, sad, ck))
                            last_g = g_angle

            total = len(items)
            if total == 0:
                self.finished_signal.emit(False)
                return

            # 3. Предвыделенная сетка DRR (160x160, FOV 400 мм)
            drr_fov = 400.0
            drr_w, drr_h = 160, 160
            u = np.linspace(-drr_fov / 2.0, drr_fov / 2.0, drr_w, dtype=np.float32)
            v = np.linspace(drr_fov / 2.0, -drr_fov / 2.0, drr_h, dtype=np.float32)
            U, V = np.meshgrid(u, v)

            def compute_one(entry):
                if self._is_cancelled:
                    return None
                g_angle, iso, sad, ck = entry
                g_rad = math.radians(g_angle)
                sin_g = math.sin(g_rad)
                cos_g = math.cos(g_rad)

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

                b_raw = drr_rgba.tobytes()
                q_img = QImage(b_raw, drr_w, drr_h, drr_w * 4, QImage.Format.Format_RGBA8888).copy()
                return ck, q_img, g_angle

            # 4. Многопоточный расчет на всех ядрах процессора
            num_workers = min(16, os.cpu_count() or 4)
            with ThreadPoolExecutor(max_workers=num_workers) as executor:
                for idx_item, res in enumerate(executor.map(compute_one, items)):
                    if self._is_cancelled:
                        self.finished_signal.emit(True)
                        return
                    if res is not None:
                        ck, q_img, g_angle = res
                        self.progress_signal.emit(idx_item + 1, total, g_angle)
                        self.item_computed_signal.emit(ck, q_img)

            self.finished_signal.emit(False)

        except Exception as e:
            if not self._is_cancelled:
                self.error_signal.emit(str(e))
            self.finished_signal.emit(self._is_cancelled)


class BEVStructurePrecomputeWorker(QThread):
    """
    Фоновый предрасчет 3D-проекций контуров RTSTRUCT для всех контрол-поинтов всех пучков плана в режиме BEV.
    """
    progress_signal = pyqtSignal(int, int, str)  # (current, total, beam_name)
    item_computed_signal = pyqtSignal(tuple, object, list)  # (cache_key, struct_path_mm, pois_mm)
    finished_signal = pyqtSignal()

    def __init__(self, structures: dict, enabled_structures: set, beams: list[dict] | dict, default_sad: float = 1000.0, active_beam_idx: int = 0) -> None:
        super().__init__()
        self.structures = structures
        self.enabled_structures = set(enabled_structures) if enabled_structures else set()
        if isinstance(beams, list):
            self.beams = list(beams)
        elif isinstance(beams, dict):
            self.beams = [beams]
        else:
            self.beams = []
        self.default_sad = float(default_sad or 1000.0)
        self.active_beam_idx = max(0, min(len(self.beams) - 1, active_beam_idx)) if self.beams else 0
        self._is_cancelled = False

    def cancel(self) -> None:
        self._is_cancelled = True

    def run(self) -> None:
        if not self.beams or not self.structures:
            self.finished_signal.emit()
            return

        import time

        # Фильтруем структуры: только Body, PTV и ориентиры
        def is_bev_struct(name: str) -> bool:
            n = name.lower()
            for k in ("body", "тело", "боди", "external", "skin", "ptv", "птв", "icru", "ориентир", "marker", "poi"):
                if k in n:
                    return True
            return False

        target_structures = {
            roi_num: s for roi_num, s in self.structures.items()
            if is_bev_struct(s.get("name", "")) and (not self.enabled_structures or s.get("name", "") in self.enabled_structures)
        }
        if not target_structures:
            target_structures = self.structures

        # Сортируем поля так, чтобы активное поле рассчитывалось первым
        ordered_beams = []
        if self.active_beam_idx < len(self.beams):
            ordered_beams.append(self.beams[self.active_beam_idx])
        for b_idx, beam in enumerate(self.beams):
            if b_idx != self.active_beam_idx:
                ordered_beams.append(beam)

        # Формируем плоский список уникальных ракурсов (с шагом 2° для дуг)
        items_to_calc = []
        seen_angles = set()
        for b_idx, beam in enumerate(ordered_beams):
            b_name = beam.get("display_name", f"Поле {b_idx + 1}")
            sad = float(beam.get("sad", self.default_sad) or self.default_sad)
            iso_default = beam.get("isocenter", [0.0, 0.0, 0.0])
            cps = beam.get("control_points", [])

            if not cps:
                g_ang = round(float(beam.get("gantry_angle", 0.0)) / 2.0) * 2.0
                iso = iso_default
                key_sig = (g_ang, round(float(iso[0]), 1), round(float(iso[1]), 1), round(float(iso[2]), 1))
                if key_sig not in seen_angles:
                    seen_angles.add(key_sig)
                    items_to_calc.append((b_name, g_ang, iso, sad))
            else:
                for cp in cps:
                    g_ang = round(float(cp.get("gantry_angle", beam.get("gantry_angle", 0.0))) / 2.0) * 2.0
                    iso = cp.get("isocenter", iso_default)
                    if not iso or len(iso) < 3:
                        continue
                    key_sig = (g_ang, round(float(iso[0]), 1), round(float(iso[1]), 1), round(float(iso[2]), 1))
                    if key_sig not in seen_angles:
                        seen_angles.add(key_sig)
                        items_to_calc.append((b_name, g_ang, iso, sad))

        total_items = len(items_to_calc)
        calculated_keys = set()

        for idx_item, (b_name, g_angle, iso, sad) in enumerate(items_to_calc):
            if self._is_cancelled:
                break
            if not iso or len(iso) < 3:
                continue

            # Уступаем время процессора основному GUI потоку
            time.sleep(0.001)

            g_rad = math.radians(g_angle)
            sin_g = math.sin(g_rad)
            cos_g = math.cos(g_rad)
            Sx = iso[0] + sad * sin_g
            Sy = iso[1] - sad * cos_g
            Sz = iso[2]

            def project_pt_mm(x: float, y: float, z: float):
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

            for roi_num, s in target_structures.items():
                if self._is_cancelled:
                    break
                name = s.get("name", "")
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

                if cache_key in calculated_keys:
                    continue

                struct_path_mm = QPainterPath()
                pois_mm = []

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

                calculated_keys.add(cache_key)
                self.item_computed_signal.emit(cache_key, struct_path_mm, pois_mm)

            self.progress_signal.emit(idx_item + 1, total_items, b_name)

        self.finished_signal.emit()
