"""
core.postprocessing.runner — Multithreaded execution worker for CT postprocessing.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal
from pydicom.uid import generate_uid

from core.locale_utils import tr_ui
from .dicom_io import read_dicom_hu, save_derived_dicom_hu
from .aligned import (
    compute_cubic_spline_coeffs,
    compute_rotation_matrix_deg,
    detect_applicator_orientation,
    export_aligned_volume_slices,
    load_ct_volume_data,
    sort_slice_paths_by_z,
    validate_aligned_export_geometry,
)


ALGORITHM_ALIGNED = "aligned"
ALGORITHM_MAR_V9 = "mar_v9"
ALGORITHM_ST_HARD = "soft_tissue_hard"
ALGORITHM_ST_NORMAL = "soft_tissue_normal"
ALGORITHM_COMBO_NORMAL = "combo_mar_st_normal"
ALGORITHM_COMBO_HARD = "combo_mar_st_hard"

# (file_prefix, human_description, use_mar, st_mode)
ALGO_SPECS: dict[str, tuple[str, str, bool, str | None]] = {
    ALGORITHM_ALIGNED: ("ALIGNED", "Aligned CT", False, None),
    ALGORITHM_MAR_V9: ("MAR", "MAR v9", True, None),
    ALGORITHM_ST_NORMAL: ("STN", "Soft Tissue Normal", False, "normal"),
    ALGORITHM_ST_HARD: ("STH", "Soft Tissue Hard", False, "hard"),
    ALGORITHM_COMBO_NORMAL: ("MAR_STN", "MAR v9 + ST Normal", True, "normal"),
    ALGORITHM_COMBO_HARD: ("MAR_STH", "MAR v9 + ST Hard", True, "hard"),
}


def compute_slice_hu(
    in_path: str,
    algo_type: str,
    neighbor_paths: list[str] | None,
    params: dict | None
) -> tuple[object, np.ndarray, float, float, int | None, int | None, dict]:
    """
    Read one CT DICOM slice and apply configured MAR / Soft Tissue filters in HU space.
    Returns (ds, corr_hu, slope, intercept, win_c, win_w, stats_info).
    """
    spec = ALGO_SPECS.get(algo_type)
    if spec is None:
        raise ValueError(f"Unknown algorithm: {algo_type}")

    _, _, use_mar, st_mode = spec
    params = params or {}
    ds, img_hu, slope, intercept, pixel_spacing = read_dicom_hu(in_path)

    neighbor_hu = []
    if neighbor_paths:
        for np_path in neighbor_paths:
            if np_path and os.path.exists(np_path):
                try:
                    _, n_hu, _, _, _ = read_dicom_hu(np_path)
                    neighbor_hu.append(n_hu)
                except Exception:
                    pass

    corr_hu = img_hu
    stats_info: dict = {}

    if use_mar:
        from .mar_algorithm import process_slice_mar
        metal_thresh = float(params.get("metal_threshold", 2200.0))
        corr_hu, has_metal, stats = process_slice_mar(
            corr_hu,
            metal_threshold=metal_thresh,
            pixel_spacing=pixel_spacing,
            neighbor_hu=neighbor_hu,
            enable_polar_filter=bool(params.get("mar_enable_polar", True)),
            polar_radius_limit_pelvis=bool(params.get("mar_polar_limit_radius", True)),
            protect_muscle_fat=bool(params.get("mar_protect_fat", True)),
            enable_starvation_fill=bool(params.get("mar_enable_starvation_fill", True)),
            enable_micro_edge=bool(params.get("mar_enable_micro_edge", True)),
        )
        stats_info["has_metal"] = has_metal
        stats_info["stats"] = stats

    win_c = None
    win_w = None
    if st_mode is not None:
        from .soft_tissue_algorithm import enhance_soft_tissue_slice_v2
        use_z = bool(params.get("use_z_filter", False))
        win_c = 40
        win_w = 220 if st_mode == "normal" else 180
        corr_hu = enhance_soft_tissue_slice_v2(
            img_hu=corr_hu,
            neighbor_hu=neighbor_hu,
            mode=st_mode,
            use_z_filter=use_z,
        )
        stats_info["mode"] = st_mode

    return ds, corr_hu, slope, intercept, win_c, win_w, stats_info


def process_slice_task(task_args: tuple) -> tuple[str, bool, dict]:
    """
    Top-level task function executed in worker pool to filter and save one CT DICOM slice.
    """
    (
        in_path,
        out_path,
        algo_type,
        series_uid,
        series_desc,
        neighbor_paths,
        params,
    ) = task_args

    try:
        ds, corr_hu, slope, intercept, win_c, win_w, info = compute_slice_hu(
            in_path=in_path,
            algo_type=algo_type,
            neighbor_paths=neighbor_paths,
            params=params,
        )
        save_derived_dicom_hu(
            ds=ds,
            corr_hu=corr_hu,
            slope=slope,
            intercept=intercept,
            output_path=out_path,
            series_desc=series_desc,
            series_uid=series_uid,
            window_center=win_c,
            window_width=win_w,
        )
        info["out_path"] = out_path
        return in_path, True, info

    except Exception as e:
        import traceback
        return in_path, False, {"error": str(e), "trace": traceback.format_exc()}


def _process_slice_to_hu_task(idx: int, in_path: str, algo_type: str, neighbor_paths: list[str], params: dict):
    try:
        _, corr_hu, _, _, win_c, win_w, _ = compute_slice_hu(
            in_path=in_path,
            algo_type=algo_type,
            neighbor_paths=neighbor_paths,
            params=params,
        )
        return idx, True, corr_hu, win_c, win_w, ""
    except Exception as e:
        return idx, False, None, None, None, str(e)


class PostprocessingRunner(QThread):
    """
    QThread worker that orchestrates multithreaded CT volume postprocessing
    (MAR v9, Soft Tissue Contrast, and/or automatic applicator alignment).
    """
    progress_signal = pyqtSignal(int, int, str)
    finished_signal = pyqtSignal(dict)
    error_signal = pyqtSignal(str)

    def __init__(
        self,
        slice_paths: list[str],
        algo_type: str,
        output_dir: str | None = None,
        series_desc: str | None = None,
        params: dict | None = None,
        parent=None
    ) -> None:
        super().__init__(parent)
        self.slice_paths = list(slice_paths)
        self.algo_type = algo_type
        self.output_dir = output_dir or (os.path.dirname(slice_paths[0]) if slice_paths else "")
        self.series_desc = series_desc
        self.params = params or {}
        self._is_cancelled = False

    def cancel(self) -> None:
        self._is_cancelled = True

    def _build_metadata(self, use_align: bool) -> tuple[str, str, bool, str | None]:
        spec = ALGO_SPECS.get(self.algo_type)
        if spec is None:
            prefix, default_desc, use_mar, st_mode = "DERIVED", "Postprocessed", False, None
        else:
            prefix, default_desc, use_mar, st_mode = spec

        if use_align and (use_mar or st_mode is not None):
            prefix = f"{prefix}_ALIGNED"
            default_desc = f"{default_desc} + Aligned"

        final_desc = self.series_desc or default_desc
        return prefix, final_desc, use_mar, st_mode

    def _run_aligned_pipeline(
        self,
        prefix: str,
        final_desc: str,
        use_mar: bool,
        st_mode: str | None,
        series_uid: str,
    ) -> None:
        sorted_paths = sort_slice_paths_by_z(self.slice_paths)
        total_slices = len(sorted_paths)

        geom_ok, geom_err = validate_aligned_export_geometry(sorted_paths)
        if not geom_ok:
            self.error_signal.emit(geom_err)
            return

        has_slice_filter = use_mar or (st_mode is not None)
        # Weight stages cleanly into [0..100] progress steps
        load_weight = 20
        filter_weight = 40 if has_slice_filter else 0
        export_weight = 100 - load_weight - filter_weight

        self.progress_signal.emit(0, 100, f"0 / {total_slices}")
        vol_res = load_ct_volume_data(
            sorted_paths,
            on_progress=lambda cur, tot: self.progress_signal.emit(
                int((cur / max(1, tot)) * load_weight),
                100,
                f"{cur} / {tot}",
            ),
            is_cancelled=lambda: self._is_cancelled,
        )
        if self._is_cancelled:
            self.error_signal.emit("Processing cancelled by user.")
            return
        if vol_res is None:
            self.error_signal.emit(tr_ui("viewer_mpr_no_volume"))
            return

        orig_vol, _, spacing = vol_res
        det = detect_applicator_orientation(orig_vol, spacing)
        if det is None:
            self.error_signal.emit(tr_ui("viewer_mpr_not_detected"))
            return

        win_c: int | None = None
        win_w: int | None = None
        if has_slice_filter:
            proc_vol = np.empty_like(orig_vol, dtype=np.float32)
            cpu_count = os.cpu_count() or 4
            workers = max(1, min(cpu_count - 1, 8))
            done_filter = 0

            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {}
                for i, in_p in enumerate(sorted_paths):
                    nbrs = []
                    if i > 0:
                        nbrs.append(sorted_paths[i - 1])
                    if i < total_slices - 1:
                        nbrs.append(sorted_paths[i + 1])
                    fut = executor.submit(
                        _process_slice_to_hu_task,
                        i,
                        in_p,
                        self.algo_type,
                        nbrs,
                        self.params,
                    )
                    futures[fut] = i

                for fut in as_completed(futures):
                    if self._is_cancelled:
                        try:
                            executor.shutdown(wait=False, cancel_futures=True)
                        except TypeError:
                            executor.shutdown(wait=False)
                        self.error_signal.emit("Processing cancelled by user.")
                        return
                    idx, ok, corr_hu, wc, ww, err = fut.result()
                    if not ok or corr_hu is None:
                        self.error_signal.emit(f"Failed to process slices: {err}")
                        return
                    proc_vol[idx] = corr_hu
                    if wc is not None and ww is not None:
                        win_c, win_w = wc, ww
                    done_filter += 1
                    pct = load_weight + int((done_filter / total_slices) * filter_weight)
                    self.progress_signal.emit(pct, 100, f"{done_filter} / {total_slices}")
        else:
            proc_vol = orig_vol

        dy, dx, dz = float(spacing[0]), float(spacing[1]), float(spacing[2])
        float_cur = list(det["float_cur"])
        pivot_mm = np.array([float_cur[0] * dx, float_cur[1] * dy, float_cur[2] * dz], dtype=np.float64)
        R = compute_rotation_matrix_deg(det["rot_x"], det["rot_y"], det["rot_z"])
        origin_mm = pivot_mm - R @ pivot_mm

        spline_coeffs = compute_cubic_spline_coeffs(proc_vol)
        if self._is_cancelled:
            self.error_signal.emit("Processing cancelled by user.")
            return

        base_pct = load_weight + filter_weight
        exp_res = export_aligned_volume_slices(
            slice_paths=sorted_paths,
            vol=proc_vol,
            spacing=spacing,
            rot_x=det["rot_x"],
            rot_y=det["rot_y"],
            rot_z=det["rot_z"],
            float_cur=float_cur,
            origin_mm=origin_mm,
            output_dir=self.output_dir,
            mode="new",
            series_uid=series_uid,
            series_desc=final_desc,
            file_prefix=f"DERIVED_{prefix}_",
            window_center=win_c,
            window_width=win_w,
            spline_coeffs=spline_coeffs,
            on_progress=lambda cur, tot: self.progress_signal.emit(
                base_pct + int((cur / max(1, tot)) * export_weight),
                100,
                f"{cur} / {tot}",
            ),
            is_cancelled=lambda: self._is_cancelled,
        )
        if self._is_cancelled:
            self.error_signal.emit("Processing cancelled by user.")
            return
        if exp_res is None:
            self.error_signal.emit("Failed to export aligned CT series.")
            return

        self.finished_signal.emit({
            "total": total_slices,
            "success": exp_res["count"],
            "errors": [],
            "files": exp_res["files"],
            "series_uid": exp_res["series_uid"],
            "frame_of_reference_uid": exp_res["frame_of_reference_uid"],
            "series_desc": exp_res["series_desc"],
            "algo_type": self.algo_type,
            "output_dir": self.output_dir,
            "aligned": True,
            "initial_cursor": float_cur,
            "align_angles": (det["rot_x"], det["rot_y"], det["rot_z"]),
        })

    def run(self) -> None:
        try:
            if not self.slice_paths:
                self.error_signal.emit("No slices provided for postprocessing.")
                return

            use_align = bool(self.params.get("align_to_applicator", False) or self.algo_type == ALGORITHM_ALIGNED)
            prefix, final_desc, use_mar, st_mode = self._build_metadata(use_align)
            series_uid = generate_uid()

            if use_align:
                self._run_aligned_pipeline(prefix, final_desc, use_mar, st_mode, series_uid)
                return

            total_slices = len(self.slice_paths)
            tasks = []
            for i, in_path in enumerate(self.slice_paths):
                fname = os.path.basename(in_path)
                out_name = f"DERIVED_{prefix}_{fname}"
                out_path = os.path.join(self.output_dir, out_name)

                neighbor_paths = []
                if i > 0:
                    neighbor_paths.append(self.slice_paths[i - 1])
                if i < total_slices - 1:
                    neighbor_paths.append(self.slice_paths[i + 1])

                tasks.append((
                    in_path,
                    out_path,
                    self.algo_type,
                    series_uid,
                    final_desc,
                    neighbor_paths,
                    self.params,
                ))

            cpu_count = os.cpu_count() or 4
            workers = max(1, min(cpu_count - 1, 8))

            processed_count = 0
            success_count = 0
            errors = []
            out_files: list[str | None] = [None] * total_slices

            self.progress_signal.emit(0, total_slices, f"0 / {total_slices}")

            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(process_slice_task, t): idx for idx, t in enumerate(tasks)}

                for future in as_completed(futures):
                    if self._is_cancelled:
                        try:
                            executor.shutdown(wait=False, cancel_futures=True)
                        except TypeError:
                            executor.shutdown(wait=False)
                        self.error_signal.emit("Processing cancelled by user.")
                        return

                    idx = futures[future]
                    in_p = tasks[idx][0]
                    try:
                        _, success, info = future.result()
                        if success:
                            success_count += 1
                            out_files[idx] = info.get("out_path")
                        else:
                            errors.append((in_p, info.get("error", "Unknown error")))
                    except Exception as exc:
                        errors.append((in_p, str(exc)))

                    processed_count += 1
                    status_text = f"{processed_count} / {total_slices}"
                    self.progress_signal.emit(processed_count, total_slices, status_text)

            if success_count == 0 and errors:
                first_err = errors[0][1] if len(errors[0]) > 1 else str(errors[0])
                self.error_signal.emit(f"Failed to process slices: {first_err}")
                return

            result = {
                "total": total_slices,
                "success": success_count,
                "errors": errors,
                "files": [fp for fp in out_files if fp],
                "series_uid": series_uid,
                "series_desc": final_desc,
                "algo_type": self.algo_type,
                "output_dir": self.output_dir,
            }
            self.finished_signal.emit(result)

        except Exception as e:
            import traceback
            self.error_signal.emit(f"{e}\n{traceback.format_exc()}")
