"""
core.postprocessing — DICOM CT post-processing algorithms.
"""
from .runner import (
    PostprocessingRunner,
    ALGORITHM_ALIGNED,
    ALGORITHM_MAR_V9,
    ALGORITHM_ST_HARD,
    ALGORITHM_ST_NORMAL,
    ALGORITHM_ST_CLAMPED_Z,
    ALGORITHM_ST_MUSICA_NORMAL,
    ALGORITHM_ST_MUSICA_HARD,
    ALGORITHM_ST_PATCH_NLM,
    ALGORITHM_COMBO_NORMAL,
    ALGORITHM_COMBO_HARD,
    ALGORITHM_COMBO_CLAMPED_Z,
    ALGORITHM_COMBO_MUSICA_NORMAL,
    ALGORITHM_COMBO_MUSICA_HARD,
    ALGORITHM_COMBO_PATCH_NLM,
    resolve_soft_tissue_algorithm,
)
from .dicom_io import (
    read_dicom_dataset,
    read_dicom_hu,
    save_derived_dicom_hu,
    is_derived_dataset,
    is_derived_file,
    clean_series_desc,
)
from .aligned import (
    compute_cubic_spline_coeffs,
    compute_rotation_matrix_deg,
    compute_world_cursor_mm,
    detect_applicator_orientation,
    export_aligned_volume_slices,
    load_ct_volume_data,
    sample_oblique_slice,
    sort_slice_paths_by_z,
    validate_aligned_export_geometry,
)
def __getattr__(name: str):
    if name == "process_slice_mar":
        from .mar_algorithm import process_slice_mar
        return process_slice_mar
    if name == "enhance_soft_tissue_slice_v2":
        from .soft_tissue_algorithm import enhance_soft_tissue_slice_v2
        return enhance_soft_tissue_slice_v2
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "PostprocessingRunner",
    "ALGORITHM_ALIGNED",
    "ALGORITHM_MAR_V9",
    "ALGORITHM_ST_HARD",
    "ALGORITHM_ST_NORMAL",
    "ALGORITHM_ST_CLAMPED_Z",
    "ALGORITHM_ST_MUSICA_NORMAL",
    "ALGORITHM_ST_MUSICA_HARD",
    "ALGORITHM_ST_PATCH_NLM",
    "ALGORITHM_COMBO_NORMAL",
    "ALGORITHM_COMBO_HARD",
    "ALGORITHM_COMBO_CLAMPED_Z",
    "ALGORITHM_COMBO_MUSICA_NORMAL",
    "ALGORITHM_COMBO_MUSICA_HARD",
    "ALGORITHM_COMBO_PATCH_NLM",
    "resolve_soft_tissue_algorithm",
    "read_dicom_dataset",
    "read_dicom_hu",
    "save_derived_dicom_hu",
    "is_derived_dataset",
    "is_derived_file",
    "clean_series_desc",
    "compute_cubic_spline_coeffs",
    "compute_rotation_matrix_deg",
    "compute_world_cursor_mm",
    "detect_applicator_orientation",
    "export_aligned_volume_slices",
    "load_ct_volume_data",
    "sample_oblique_slice",
    "sort_slice_paths_by_z",
    "validate_aligned_export_geometry",
    "process_slice_mar",
    "enhance_soft_tissue_slice_v2",
]
