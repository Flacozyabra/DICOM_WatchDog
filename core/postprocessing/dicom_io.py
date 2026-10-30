"""
core.postprocessing.dicom_io — DICOM I/O utilities for CT postprocessing.
"""
from __future__ import annotations

import os
import numpy as np
import pydicom
from pydicom.uid import generate_uid


def is_derived_dataset(ds: pydicom.Dataset) -> bool:
    """Checks whether the given DICOM dataset represents a derived series."""
    img_type = getattr(ds, "ImageType", None)
    if not img_type:
        return False
    if isinstance(img_type, (list, tuple)):
        return any("DERIVED" in str(x).upper() for x in img_type)
    return "DERIVED" in str(img_type).upper()


def is_derived_file(file_path: str) -> bool:
    """Fast check whether the given DICOM file is a derived series slice."""
    try:
        ds = pydicom.dcmread(
            file_path,
            stop_before_pixels=True,
            force=True,
            specific_tags=["ImageType"]
        )
        return is_derived_dataset(ds)
    except Exception:
        return False


def read_dicom_hu(file_path: str) -> tuple[pydicom.Dataset, np.ndarray, float, float, list[float]]:
    """
    Reads a DICOM file and converts stored pixel values into Hounsfield Units (HU).
    Returns (ds, img_hu, slope, intercept, pixel_spacing).
    """
    ds = pydicom.dcmread(file_path)
    slope = float(getattr(ds, "RescaleSlope", 1.0))
    intercept = float(getattr(ds, "RescaleIntercept", 0.0))
    pixel_array = ds.pixel_array.astype(np.float32)
    img_hu = pixel_array * slope + intercept
    pixel_spacing = [float(x) for x in getattr(ds, "PixelSpacing", [1.0, 1.0])]
    return ds, img_hu, slope, intercept, pixel_spacing


def save_derived_dicom_hu(
    ds: pydicom.Dataset,
    corr_hu: np.ndarray,
    slope: float,
    intercept: float,
    output_path: str,
    series_desc: str,
    series_uid: str,
    window_center: float | None = None,
    window_width: float | None = None
) -> None:
    """
    Converts HU back to stored pixel values and saves as a compliant DERIVED DICOM file.
    Original dataset is preserved; tags are updated to create a distinct derived series.
    """
    raw_pixels = np.round((corr_hu - intercept) / slope)

    bit_depth = int(getattr(ds, "BitsAllocated", 16))
    pixel_rep = int(getattr(ds, "PixelRepresentation", 0))

    if pixel_rep == 0:
        min_val = 0
        max_val = (1 << bit_depth) - 1
        raw_pixels = np.clip(raw_pixels, min_val, max_val).astype(np.uint16)
    else:
        min_val = -(1 << (bit_depth - 1))
        max_val = (1 << (bit_depth - 1)) - 1
        raw_pixels = np.clip(raw_pixels, min_val, max_val).astype(np.int16)

    ds.PixelData = raw_pixels.tobytes()

    # Mark as Derived Secondary according to DICOM Part 3 PS 3.3
    ds.ImageType = ["DERIVED", "SECONDARY"]

    # Assign new UIDs for series and individual slice
    new_sop_uid = generate_uid()
    ds.SeriesInstanceUID = series_uid
    ds.SOPInstanceUID = new_sop_uid

    if hasattr(ds, "file_meta") and ds.file_meta is not None:
        ds.file_meta.MediaStorageSOPInstanceUID = new_sop_uid

    # Offset SeriesNumber to avoid collisions with original series
    try:
        orig_snum = int(getattr(ds, "SeriesNumber", 1))
        ds.SeriesNumber = orig_snum + 100
    except Exception:
        ds.SeriesNumber = 101

    ds.SeriesDescription = series_desc

    if window_center is not None and window_width is not None:
        ds.WindowCenter = int(round(window_center))
        ds.WindowWidth = int(round(window_width))

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    ds.save_as(output_path)
