"""
core.postprocessing.runner — Multiprocess execution worker for CT postprocessing.
"""
from __future__ import annotations

import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from PyQt6.QtCore import QThread, pyqtSignal
from pydicom.uid import generate_uid

from .dicom_io import read_dicom_hu, save_derived_dicom_hu


ALGORITHM_MAR_V9 = "mar_v9"
ALGORITHM_ST_HARD = "soft_tissue_hard"
ALGORITHM_ST_NORMAL = "soft_tissue_normal"
ALGORITHM_COMBO_NORMAL = "combo_mar_st_normal"
ALGORITHM_COMBO_HARD = "combo_mar_st_hard"


def process_slice_task(task_args: tuple) -> tuple[str, bool, dict]:
    """
    Top-level task function executed in ProcessPoolExecutor workers.
    Must be picklable across process boundaries.
    """
    (
        in_path,
        out_path,
        algo_type,
        series_uid,
        series_desc,
        neighbor_paths,
        params
    ) = task_args

    try:
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

        enable_polar = bool(params.get("mar_enable_polar", True))
        limit_polar_r = bool(params.get("mar_polar_limit_radius", True))
        protect_fat = bool(params.get("mar_protect_fat", True))
        enable_starv = bool(params.get("mar_enable_starvation_fill", True))
        enable_micro = bool(params.get("mar_enable_micro_edge", True))

        if algo_type == ALGORITHM_MAR_V9:
            from .mar_algorithm import process_slice_mar
            metal_thresh = float(params.get("metal_threshold", 2200.0))
            corr_hu, has_metal, stats = process_slice_mar(
                img_hu,
                metal_threshold=metal_thresh,
                pixel_spacing=pixel_spacing,
                neighbor_hu=neighbor_hu,
                enable_polar_filter=enable_polar,
                polar_radius_limit_pelvis=limit_polar_r,
                protect_muscle_fat=protect_fat,
                enable_starvation_fill=enable_starv,
                enable_micro_edge=enable_micro
            )
            save_derived_dicom_hu(
                ds=ds,
                corr_hu=corr_hu,
                slope=slope,
                intercept=intercept,
                output_path=out_path,
                series_desc=series_desc,
                series_uid=series_uid
            )
            return in_path, True, {"has_metal": has_metal, "stats": stats}

        elif algo_type in (ALGORITHM_ST_HARD, ALGORITHM_ST_NORMAL):
            from .soft_tissue_algorithm import enhance_soft_tissue_slice_v2
            mode = "normal" if algo_type == ALGORITHM_ST_NORMAL else "hard"
            use_z = bool(params.get("use_z_filter", False))
            win_c = 40
            win_w = 220 if mode == "normal" else 180

            enhanced_hu = enhance_soft_tissue_slice_v2(
                img_hu=img_hu,
                neighbor_hu=neighbor_hu,
                mode=mode,
                use_z_filter=use_z
            )
            save_derived_dicom_hu(
                ds=ds,
                corr_hu=enhanced_hu,
                slope=slope,
                intercept=intercept,
                output_path=out_path,
                series_desc=series_desc,
                series_uid=series_uid,
                window_center=win_c,
                window_width=win_w
            )
            return in_path, True, {"mode": mode}

        elif algo_type in (ALGORITHM_COMBO_NORMAL, ALGORITHM_COMBO_HARD):
            from .mar_algorithm import process_slice_mar
            from .soft_tissue_algorithm import enhance_soft_tissue_slice_v2
            metal_thresh = float(params.get("metal_threshold", 2200.0))
            corr_mar, has_metal, stats = process_slice_mar(
                img_hu,
                metal_threshold=metal_thresh,
                pixel_spacing=pixel_spacing,
                neighbor_hu=neighbor_hu,
                enable_polar_filter=enable_polar,
                polar_radius_limit_pelvis=limit_polar_r,
                protect_muscle_fat=protect_fat,
                enable_starvation_fill=enable_starv,
                enable_micro_edge=enable_micro
            )
            mode = "normal" if algo_type == ALGORITHM_COMBO_NORMAL else "hard"
            use_z = bool(params.get("use_z_filter", False))
            win_c = 40
            win_w = 220 if mode == "normal" else 180

            final_hu = enhance_soft_tissue_slice_v2(
                img_hu=corr_mar,
                neighbor_hu=neighbor_hu,
                mode=mode,
                use_z_filter=use_z
            )
            save_derived_dicom_hu(
                ds=ds,
                corr_hu=final_hu,
                slope=slope,
                intercept=intercept,
                output_path=out_path,
                series_desc=series_desc,
                series_uid=series_uid,
                window_center=win_c,
                window_width=win_w
            )
            return in_path, True, {"has_metal": has_metal, "mode": mode, "stats": stats}

        else:
            return in_path, False, {"error": f"Unknown algorithm: {algo_type}"}

    except Exception as e:
        import traceback
        return in_path, False, {"error": str(e), "trace": traceback.format_exc()}


class PostprocessingRunner(QThread):
    """
    QThread worker that orchestrates multiprocessing CT volume postprocessing.
    Provides cancellation and progress reporting to UI.
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

    def run(self) -> None:
        try:
            if not self.slice_paths:
                self.error_signal.emit("No slices provided for postprocessing.")
                return

            total_slices = len(self.slice_paths)
            series_uid = generate_uid()

            # Choose algorithm prefix and default description
            if self.algo_type == ALGORITHM_MAR_V9:
                prefix = "MAR"
                default_desc = "MAR v9"
            elif self.algo_type == ALGORITHM_ST_NORMAL:
                prefix = "STN"
                default_desc = "Soft Tissue Normal"
            elif self.algo_type == ALGORITHM_ST_HARD:
                prefix = "STH"
                default_desc = "Soft Tissue Hard"
            elif self.algo_type == ALGORITHM_COMBO_NORMAL:
                prefix = "MAR_STN"
                default_desc = "MAR v9 + ST Normal"
            elif self.algo_type == ALGORITHM_COMBO_HARD:
                prefix = "MAR_STH"
                default_desc = "MAR v9 + ST Hard"
            else:
                prefix = "DERIVED"
                default_desc = "Postprocessed"

            final_desc = self.series_desc or default_desc

            # Prepare task list with 2.5D neighbor paths
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
                    self.params
                ))

            # Number of CPU workers: leave 1 core free for system responsiveness
            cpu_count = os.cpu_count() or 4
            workers = max(1, min(cpu_count - 1, 8))

            processed_count = 0
            success_count = 0
            errors = []

            self.progress_signal.emit(0, total_slices, f"0 / {total_slices}")

            with ProcessPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(process_slice_task, t): t[0] for t in tasks}

                for future in as_completed(futures):
                    if self._is_cancelled:
                        executor.shutdown(wait=False, cancel_futures=True)
                        self.error_signal.emit("Processing cancelled by user.")
                        return

                    in_p = futures[future]
                    try:
                        _, success, info = future.result()
                        if success:
                            success_count += 1
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
                "series_uid": series_uid,
                "series_desc": final_desc,
                "algo_type": self.algo_type,
                "output_dir": self.output_dir
            }
            self.finished_signal.emit(result)

        except Exception as e:
            import traceback
            self.error_signal.emit(f"{e}\n{traceback.format_exc()}")
