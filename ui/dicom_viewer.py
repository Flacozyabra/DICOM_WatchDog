from __future__ import annotations

"""
Backward compatibility proxy module for the modularized ui.viewer package.
All viewer components are now organized inside the ui/viewer/ package:
- ui/viewer/parsers.py: DICOM data parsing & math (RTSTRUCT, RTDOSE, RTPLAN, colormaps)
- ui/viewer/workers.py: Background QThread workers
- ui/viewer/controls.py: Auxiliary UI widgets (HUVerticalSlider, DRRProgressDialog)
- ui/viewer/canvas.py: DicomViewerWidget (viewport rendering, BEV, contours, isodoses)
- ui/viewer/panel.py: DicomViewerPanel (main viewer panel)
"""

from ui.viewer import (
    safe_dcmread,
    load_rtstruct,
    load_rtdose,
    load_rtplan,
    clean_tps_name,
    get_dose_slice_at_z,
    dose_slice_to_rgba,
    create_dose_colormap_lut,
    marching_squares_2d,
    _convex_hull_2d,
    PatientSeriesLoaderWorker,
    StructureLoaderWorker,
    DoseLoaderWorker,
    DRRPrecomputeWorker,
    BEVStructurePrecomputeWorker,
    HUVerticalSlider,
    DRRProgressDialog,
    DicomViewerWidget,
    DicomViewerPanel,
)

__all__ = [
    "safe_dcmread",
    "load_rtstruct",
    "load_rtdose",
    "load_rtplan",
    "clean_tps_name",
    "get_dose_slice_at_z",
    "dose_slice_to_rgba",
    "create_dose_colormap_lut",
    "marching_squares_2d",
    "_convex_hull_2d",
    "PatientSeriesLoaderWorker",
    "StructureLoaderWorker",
    "DoseLoaderWorker",
    "DRRPrecomputeWorker",
    "BEVStructurePrecomputeWorker",
    "HUVerticalSlider",
    "DRRProgressDialog",
    "DicomViewerWidget",
    "DicomViewerPanel",
]
