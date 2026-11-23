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
ALGORITHM_ST_CLAMPED_Z = "soft_tissue_clamped_z"
ALGORITHM_ST_MUSICA_NORMAL = "soft_tissue_musica_normal"
ALGORITHM_ST_MUSICA_HARD = "soft_tissue_musica_hard"
ALGORITHM_ST_PATCH_NLM = "soft_tissue_patch_nlm"
ALGORITHM_COMBO_NORMAL = "combo_mar_st_normal"
ALGORITHM_COMBO_HARD = "combo_mar_st_hard"
ALGORITHM_COMBO_CLAMPED_Z = "combo_mar_st_clamped_z"
ALGORITHM_COMBO_MUSICA_NORMAL = "combo_mar_st_musica_normal"
ALGORITHM_COMBO_MUSICA_HARD = "combo_mar_st_musica_hard"
ALGORITHM_COMBO_PATCH_NLM = "combo_mar_st_patch_nlm"

# (st_mode, solo_algo_id, combo_algo_id, file_prefix, human_desc, win_c, win_w)
SOFT_TISSUE_MODE_SPECS: tuple[tuple[str, str, str, str, str, int, int], ...] = (
    ("normal", ALGORITHM_ST_NORMAL, ALGORITHM_COMBO_NORMAL, "STN", "Guided + S-Curve (Normal)", 40, 220),
    ("hard", ALGORITHM_ST_HARD, ALGORITHM_COMBO_HARD, "STH", "Guided + S-Curve (Hard)", 40, 180),
    ("clamped_v2", ALGORITHM_ST_CLAMPED_Z, ALGORITHM_COMBO_CLAMPED_Z, "STC", "Clamped Guided + 2.5D Z", 40, 200),
    ("musica_normal", ALGORITHM_ST_MUSICA_NORMAL, ALGORITHM_COMBO_MUSICA_NORMAL, "STMN", "MUSICA Pyramid (Normal)", 40, 210),
    ("musica_hard", ALGORITHM_ST_MUSICA_HARD, ALGORITHM_COMBO_MUSICA_HARD, "STMH", "MUSICA Pyramid (Hard)", 40, 190),
    ("patch_nlm", ALGORITHM_ST_PATCH_NLM, ALGORITHM_COMBO_PATCH_NLM, "STNLM", "Patch-NLM 2.5D", 40, 190),
)

ST_WINDOW_PRESETS: dict[str, tuple[int, int]] = {
    st_mode: (wc, ww) for st_mode, _, _, _, _, wc, ww in SOFT_TISSUE_MODE_SPECS
}

# (file_prefix, human_description, use_mar, st_mode)
ALGO_SPECS: dict[str, tuple[str, str, bool, str | None]] = {
    ALGORITHM_ALIGNED: ("ALIGNED", "Aligned CT", False, None),
    ALGORITHM_MAR_V9: ("MAR", "MAR v9", True, None),
}
for _st_mode, _solo_id, _combo_id, _pref, _desc, _, _ in SOFT_TISSUE_MODE_SPECS:
    ALGO_SPECS[_solo_id] = (_pref, _desc, False, _st_mode)
    ALGO_SPECS[_combo_id] = (f"MAR_{_pref}", f"MAR v9 + {_desc}", True, _st_mode)


def resolve_soft_tissue_algorithm(st_mode: str, use_mar: bool) -> str:
    """Resolve PostprocessingRunner algo_type identifier for a soft-tissue mode and MAR flag."""
    for mode_key, solo_id, combo_id, _, _, _, _ in SOFT_TISSUE_MODE_SPECS:
        if mode_key == st_mode:
            return combo_id if use_mar else solo_id
    return ALGORITHM_COMBO_HARD if use_mar else ALGORITHM_ST_HARD


def _shutdown_executor(executor: ThreadPoolExecutor) -> None:
    """Cancel pending futures cleanly with Python 3.8 fallback."""
    try:
        executor.shutdown(wait=False, cancel_futures=True)
    except TypeError:
        executor.shutdown(wait=False)


def _apply_mar_slice(
    img_hu: np.ndarray,
    pixel_spacing: list[float] | tuple[float, float],
    neighbor_hu: list[np.ndarray],
    params: dict,
) -> tuple[np.ndarray, bool, dict]:
    from .mar_algorithm import process_slice_mar
    metal_thresh = float(params.get("metal_threshold", 2200.0))
    return process_slice_mar(
        img_hu,
        metal_threshold=metal_thresh,
        pixel_spacing=pixel_spacing,
        neighbor_hu=neighbor_hu,
        enable_polar_filter=bool(params.get("mar_enable_polar", True)),
        polar_radius_limit_pelvis=bool(params.get("mar_polar_limit_radius", True)),
        protect_muscle_fat=bool(params.get("mar_protect_fat", True)),
        enable_starvation_fill=bool(params.get("mar_enable_starvation_fill", True)),
        enable_micro_edge=bool(params.get("mar_enable_micro_edge", True)),
    )


def _apply_soft_tissue_slice(
    img_hu: np.ndarray,
    pixel_spacing: list[float] | tuple[float, float],
    neighbor_hu: list[np.ndarray],
    st_mode: str,
    params: dict,
) -> tuple[np.ndarray, int, int]:
    from .soft_tissue_algorithm import enhance_soft_tissue_slice_v2
    use_z = bool(params.get("use_z_filter", False))
    win_c, win_w = ST_WINDOW_PRESETS.get(st_mode, (40, 180))
    corr_hu = enhance_soft_tissue_slice_v2(
        img_hu=img_hu,
        neighbor_hu=neighbor_hu,
        mode=st_mode,
        use_z_filter=use_z,
        pixel_spacing=pixel_spacing,
    )
    return corr_hu, win_c, win_w


def _collect_neighbors(slices: list[np.ndarray | None] | np.ndarray, idx: int) -> list[np.ndarray]:
    nbrs: list[np.ndarray] = []
    n_total = len(slices)
    if idx > 0 and slices[idx - 1] is not None:
        nbrs.append(slices[idx - 1])
    if idx < n_total - 1 and slices[idx + 1] is not None:
        nbrs.append(slices[idx + 1])
    return nbrs


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

    neighbor_hu: list[np.ndarray] = []
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
        corr_hu, has_metal, stats = _apply_mar_slice(corr_hu, pixel_spacing, neighbor_hu, params)
        stats_info["has_metal"] = has_metal
        stats_info["stats"] = stats

    win_c = None
    win_w = None
    if st_mode is not None:
        corr_hu, win_c, win_w = _apply_soft_tissue_slice(corr_hu, pixel_spacing, neighbor_hu, st_mode, params)
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
        dy, dx, dz = float(spacing[0]), float(spacing[1]), float(spacing[2])
        spacing_xy = (dy, dx)

        if has_slice_filter:
            cpu_count = os.cpu_count() or 4
            workers = max(1, min(cpu_count - 1, 8))
            stages = int(use_mar) + int(st_mode is not None)
            stage_idx = 0
            cur_vol = orig_vol

            if use_mar:
                mar_vol = np.empty_like(orig_vol, dtype=np.float32)
                done_stage = 0
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    futures = {
                        executor.submit(
                            _apply_mar_slice,
                            cur_vol[i],
                            spacing_xy,
                            _collect_neighbors(cur_vol, i),
                            self.params,
                        ): i
                        for i in range(total_slices)
                    }
                    for fut in as_completed(futures):
                        if self._is_cancelled:
                            _shutdown_executor(executor)
                            self.error_signal.emit("Processing cancelled by user.")
                            return
                        idx = futures[fut]
                        try:
                            corr_hu, _, _ = fut.result()
                        except Exception as exc:
                            self.error_signal.emit(f"Failed to process slices: {exc}")
                            return
                        mar_vol[idx] = corr_hu
                        done_stage += 1
                        frac = (stage_idx + done_stage / max(1, total_slices)) / stages
                        self.progress_signal.emit(
                            load_weight + int(frac * filter_weight),
                            100,
                            f"{done_stage} / {total_slices}",
                        )
                cur_vol = mar_vol
                stage_idx += 1

            if st_mode is not None:
                proc_vol = np.empty_like(cur_vol, dtype=np.float32)
                done_stage = 0
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    futures = {
                        executor.submit(
                            _apply_soft_tissue_slice,
                            cur_vol[i],
                            spacing_xy,
                            _collect_neighbors(cur_vol, i),
                            st_mode,
                            self.params,
                        ): i
                        for i in range(total_slices)
                    }
                    for fut in as_completed(futures):
                        if self._is_cancelled:
                            _shutdown_executor(executor)
                            self.error_signal.emit("Processing cancelled by user.")
                            return
                        idx = futures[fut]
                        try:
                            corr_hu, wc, ww = fut.result()
                        except Exception as exc:
                            self.error_signal.emit(f"Failed to process slices: {exc}")
                            return
                        proc_vol[idx] = corr_hu
                        win_c, win_w = wc, ww
                        done_stage += 1
                        frac = (stage_idx + done_stage / max(1, total_slices)) / stages
                        self.progress_signal.emit(
                            load_weight + int(frac * filter_weight),
                            100,
                            f"{done_stage} / {total_slices}",
                        )
                cur_vol = None
            else:
                proc_vol = cur_vol
        else:
            proc_vol = orig_vol

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
            cpu_count = os.cpu_count() or 4
            workers = max(1, min(cpu_count - 1, 8))

            # Stage weights inside [0..100]: single disk read -> optional MAR pass -> final filter & save
            two_filter_passes = bool(use_mar and st_mode is not None)
            load_weight = 20
            mar_weight = 35 if two_filter_passes else 0
            final_weight = 100 - load_weight - mar_weight

            errors: list[tuple[str, str]] = []
            out_files: list[str | None] = [None] * total_slices
            hu_slices: list[np.ndarray | None] = [None] * total_slices
            slice_meta: list[tuple[object, float, float, list[float]] | None] = [None] * total_slices

            self.progress_signal.emit(0, 100, f"0 / {total_slices}")

            def _load_one(path: str):
                ds, img_hu, slope, intercept, pixel_spacing = read_dicom_hu(path)
                # Strip raw pixel buffer from metadata header to keep memory footprint low
                ds.PixelData = b""
                return ds, img_hu, slope, intercept, pixel_spacing

            done_load = 0
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(_load_one, p): idx for idx, p in enumerate(self.slice_paths)}
                for fut in as_completed(futures):
                    if self._is_cancelled:
                        _shutdown_executor(executor)
                        self.error_signal.emit("Processing cancelled by user.")
                        return
                    idx = futures[fut]
                    in_p = self.slice_paths[idx]
                    try:
                        ds, img_hu, slope, intercept, pixel_spacing = fut.result()
                        hu_slices[idx] = img_hu
                        slice_meta[idx] = (ds, slope, intercept, pixel_spacing)
                    except Exception as exc:
                        errors.append((in_p, str(exc)))
                    done_load += 1
                    self.progress_signal.emit(
                        int((done_load / max(1, total_slices)) * load_weight),
                        100,
                        f"{done_load} / {total_slices}",
                    )

            valid_indices = [i for i in range(total_slices) if hu_slices[i] is not None and slice_meta[i] is not None]
            if not valid_indices:
                first_err = errors[0][1] if errors else "Failed to read CT slices."
                self.error_signal.emit(f"Failed to process slices: {first_err}")
                return

            stage_slices = hu_slices
            if two_filter_passes:
                # Run MAR first across the volume so 2.5D Soft Tissue reads MAR-cleaned neighbors
                mar_slices: list[np.ndarray | None] = [None] * total_slices
                done_mar = 0
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    futures = {
                        executor.submit(
                            _apply_mar_slice,
                            hu_slices[i],
                            slice_meta[i][3],
                            _collect_neighbors(hu_slices, i),
                            self.params,
                        ): i
                        for i in valid_indices
                    }
                    for fut in as_completed(futures):
                        if self._is_cancelled:
                            _shutdown_executor(executor)
                            self.error_signal.emit("Processing cancelled by user.")
                            return
                        idx = futures[fut]
                        in_p = self.slice_paths[idx]
                        try:
                            corr_hu, _, _ = fut.result()
                            mar_slices[idx] = corr_hu
                        except Exception as exc:
                            errors.append((in_p, str(exc)))
                        done_mar += 1
                        self.progress_signal.emit(
                            load_weight + int((done_mar / max(1, len(valid_indices))) * mar_weight),
                            100,
                            f"{done_mar} / {total_slices}",
                        )
                hu_slices = None
                stage_slices = mar_slices
                valid_indices = [i for i in valid_indices if stage_slices[i] is not None]

            def _finish_and_save(idx: int) -> str:
                in_p = self.slice_paths[idx]
                fname = os.path.basename(in_p)
                out_path = os.path.join(self.output_dir, f"DERIVED_{prefix}_{fname}")
                ds, slope, intercept, pixel_spacing = slice_meta[idx]
                nbrs = _collect_neighbors(stage_slices, idx)
                win_c: int | None = None
                win_w: int | None = None
                if st_mode is not None:
                    corr_hu, win_c, win_w = _apply_soft_tissue_slice(
                        stage_slices[idx], pixel_spacing, nbrs, st_mode, self.params
                    )
                elif use_mar:
                    corr_hu, _, _ = _apply_mar_slice(
                        stage_slices[idx], pixel_spacing, nbrs, self.params
                    )
                else:
                    corr_hu = stage_slices[idx]
                save_derived_dicom_hu(
                    ds=ds,
                    corr_hu=corr_hu,
                    slope=slope,
                    intercept=intercept,
                    output_path=out_path,
                    series_desc=final_desc,
                    series_uid=series_uid,
                    window_center=win_c,
                    window_width=win_w,
                )
                slice_meta[idx] = None
                return out_path

            success_count = 0
            done_final = 0
            base_pct = load_weight + mar_weight
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(_finish_and_save, i): i for i in valid_indices}
                for fut in as_completed(futures):
                    if self._is_cancelled:
                        _shutdown_executor(executor)
                        self.error_signal.emit("Processing cancelled by user.")
                        return
                    idx = futures[fut]
                    in_p = self.slice_paths[idx]
                    try:
                        out_files[idx] = fut.result()
                        success_count += 1
                    except Exception as exc:
                        errors.append((in_p, str(exc)))
                    done_final += 1
                    self.progress_signal.emit(
                        base_pct + int((done_final / max(1, len(valid_indices))) * final_weight),
                        100,
                        f"{done_final} / {total_slices}",
                    )

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
