"""
core.postprocessing — DICOM CT post-processing algorithms.
"""
from .runner import (
    PostprocessingRunner,
    ALGORITHM_MAR_V9,
    ALGORITHM_ST_HARD,
    ALGORITHM_ST_NORMAL,
    ALGORITHM_COMBO_NORMAL,
    ALGORITHM_COMBO_HARD,
)
from .dicom_io import (
    read_dicom_hu,
    save_derived_dicom_hu,
    is_derived_dataset,
    is_derived_file,
    clean_series_desc,
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
    "ALGORITHM_MAR_V9",
    "ALGORITHM_ST_HARD",
    "ALGORITHM_ST_NORMAL",
    "ALGORITHM_COMBO_NORMAL",
    "ALGORITHM_COMBO_HARD",
    "read_dicom_hu",
    "save_derived_dicom_hu",
    "is_derived_dataset",
    "is_derived_file",
    "clean_series_desc",
    "process_slice_mar",
    "enhance_soft_tissue_slice_v2",
]
