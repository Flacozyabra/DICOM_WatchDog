from __future__ import annotations

import os
import math
import numpy as np
from concurrent.futures import ThreadPoolExecutor
from PyQt6.QtCore import pyqtSignal, QThread
from PyQt6.QtGui import QImage

from core.locale_utils import tr_ui
from .parsers import safe_dcmread, load_rtstruct, load_rtdose, load_rtplan


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
