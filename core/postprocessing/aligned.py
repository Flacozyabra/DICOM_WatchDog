"""
core.postprocessing.aligned — 3D CT volume loading, spline oblique sampling,
applicator orientation detection, geometry validation, and aligned DICOM export.
"""
from __future__ import annotations

import math
import os
import numpy as np
import scipy.ndimage
from pydicom.uid import generate_uid

from core.locale_utils import tr_ui
from .dicom_io import read_dicom_dataset


def sort_slice_paths_by_z(slice_paths: list[str]) -> list[str]:
    """Return CT slice file paths sorted ascending by ImagePositionPatient[2]."""
    def _z_key(fp: str) -> float:
        try:
            ds = read_dicom_dataset(fp, stop_before_pixels=True, specific_tags=["ImagePositionPatient"])
            ipp = getattr(ds, "ImagePositionPatient", None)
            if ipp is not None and len(ipp) >= 3:
                return float(ipp[2])
        except Exception:
            pass
        return 0.0

    return sorted(slice_paths, key=_z_key)


def load_ct_volume_data(
    sorted_files: list,
    on_progress=None,
    is_cancelled=None
) -> tuple[np.ndarray, list[float], tuple[float, float, float]] | None:
    """
    Extracts 3D CT HU volume and coordinate metadata from DICOM slice files.
    Returns (vol, ipp0, (dy, dx, dz)) or None.
    """
    if not sorted_files:
        return None
    try:
        total_files = len(sorted_files)
        slices_ds = []

        for idx, item in enumerate(sorted_files):
            if is_cancelled and is_cancelled():
                return None
            fp = item[0] if isinstance(item, tuple) else item
            try:
                ds = read_dicom_dataset(fp)
                if (
                    hasattr(ds, "ImagePositionPatient")
                    and hasattr(ds, "pixel_array")
                    and getattr(ds, "Modality", "CT") == "CT"
                    and ds.pixel_array.ndim == 2
                ):
                    slices_ds.append(ds)
            except Exception:
                pass

            if on_progress and (idx % 2 == 0 or idx == total_files - 1):
                on_progress(idx + 1, total_files)

        if not slices_ds:
            return None

        slices_ds.sort(key=lambda s: float(s.ImagePositionPatient[2]))
        n_z = len(slices_ds)
        rows = int(slices_ds[0].Rows)
        cols = int(slices_ds[0].Columns)
        ipp0 = [float(x) for x in slices_ds[0].ImagePositionPatient]
        ipp_last = [float(x) for x in slices_ds[-1].ImagePositionPatient]
        spacing = [float(x) for x in slices_ds[0].PixelSpacing]
        dy, dx = float(spacing[0]), float(spacing[1])
        dz = abs(float(ipp_last[2] - ipp0[2])) / (n_z - 1) if n_z > 1 else 3.0
        if dz <= 0:
            dz = 3.0

        vol = np.zeros((n_z, rows, cols), dtype=np.float32)
        for i in range(n_z):
            s = slices_ds[i]
            slices_ds[i] = None  # Free raw DICOM dataset immediately after copying pixel array
            slope = float(getattr(s, "RescaleSlope", 1.0) or 1.0)
            intercept = float(getattr(s, "RescaleIntercept", 0.0) or 0.0)
            vol[i] = s.pixel_array.astype(np.float32) * slope + intercept
        slices_ds.clear()

        return vol, ipp0, (dy, dx, dz)
    except Exception:
        return None


def compute_cubic_spline_coeffs(vol: np.ndarray) -> np.ndarray:
    """Precompute 3D float32 cubic B-spline coefficients for sharp order=3 resampling."""
    return scipy.ndimage.spline_filter(vol, order=3, output=np.float32, mode="constant")


def sample_oblique_slice(
    vol: np.ndarray,
    coords: np.ndarray,
    spline_coeffs: np.ndarray | None = None,
    order: int = 3,
    clamp_neighbors: bool = True
) -> np.ndarray:
    """
    Resample a 2D slice from 3D volume using cached cubic B-spline (order=3 with 8-neighbor clamping)
    or fast trilinear interpolation (order=1) for interactive MPR viewport dragging.
    """
    if order != 3:
        return scipy.ndimage.map_coordinates(
            vol,
            coords,
            order=int(order),
            mode="constant",
            cval=-1000.0,
            prefilter=False
        )

    if spline_coeffs is None or spline_coeffs.shape != vol.shape:
        spline_coeffs = compute_cubic_spline_coeffs(vol)
    slice_hu = scipy.ndimage.map_coordinates(
        spline_coeffs,
        coords,
        order=3,
        mode="constant",
        cval=-1000.0,
        prefilter=False
    )
    if not clamp_neighbors or vol.ndim != 3:
        return slice_hu
    nz, nr, nc = vol.shape
    if nz < 2 or nr < 2 or nc < 2:
        return slice_hu

    fz, fy, fx = coords[0], coords[1], coords[2]
    inside = (
        (fz >= 0.0) & (fz <= float(nz - 1)) &
        (fy >= 0.0) & (fy <= float(nr - 1)) &
        (fx >= 0.0) & (fx <= float(nc - 1))
    )
    if not np.any(inside):
        return slice_hu

    z0 = np.clip(np.floor(fz[inside]).astype(np.intp), 0, nz - 2)
    y0 = np.clip(np.floor(fy[inside]).astype(np.intp), 0, nr - 2)
    x0 = np.clip(np.floor(fx[inside]).astype(np.intp), 0, nc - 2)

    if vol.flags["C_CONTIGUOUS"]:
        flat = vol.reshape(-1)
        s_row = nc
        s_sl = nr * nc
        idx0 = (z0 * nr + y0) * nc + x0
        corners = (
            flat[idx0], flat[idx0 + 1],
            flat[idx0 + s_row], flat[idx0 + s_row + 1],
            flat[idx0 + s_sl], flat[idx0 + s_sl + 1],
            flat[idx0 + s_sl + s_row], flat[idx0 + s_sl + s_row + 1],
        )
    else:
        z1, y1, x1 = z0 + 1, y0 + 1, x0 + 1
        corners = (
            vol[z0, y0, x0], vol[z0, y0, x1],
            vol[z0, y1, x0], vol[z0, y1, x1],
            vol[z1, y0, x0], vol[z1, y0, x1],
            vol[z1, y1, x0], vol[z1, y1, x1],
        )

    v_min = corners[0].copy()
    v_max = corners[0].copy()
    for c in corners[1:]:
        np.minimum(v_min, c, out=v_min)
        np.maximum(v_max, c, out=v_max)
    slice_hu[inside] = np.clip(slice_hu[inside], v_min, v_max)
    return slice_hu


_Z_BSPLINE_CACHE: dict[tuple[int, int], tuple[np.ndarray, ...]] = {}


def resample_slice_z_isotropic(slice_2d: np.ndarray, target_h: int) -> np.ndarray:
    """
    Upsample a 2D orthogonal Coronal/Sagittal slice (n_z, width) along Z using
    clamped 1D cubic B-spline interpolation without ringing artifacts.
    """
    n_z = int(slice_2d.shape[0])
    target_h = int(target_h)
    if n_z < 2 or target_h <= n_z:
        return slice_2d

    key = (n_z, target_h)
    cached = _Z_BSPLINE_CACHE.get(key)
    if cached is None:
        z_coords = np.linspace(0.0, float(n_z - 1), target_h, dtype=np.float32)
        k = np.clip(np.floor(z_coords).astype(np.intp), 0, n_z - 2)
        f = (z_coords - k)[:, None]
        omf = np.float32(1.0) - f
        w_m1 = (omf * omf * omf) * np.float32(1.0 / 6.0)
        w_0 = (np.float32(3.0) * f * f * f - np.float32(6.0) * f * f + np.float32(4.0)) * np.float32(1.0 / 6.0)
        w_1 = (np.float32(-3.0) * f * f * f + np.float32(3.0) * f * f + np.float32(3.0) * f + np.float32(1.0)) * np.float32(1.0 / 6.0)
        w_2 = (f * f * f) * np.float32(1.0 / 6.0)
        im1 = np.clip(k - 1, 0, n_z - 1)
        i0 = k
        i1 = k + 1
        i2 = np.clip(k + 2, 0, n_z - 1)
        if len(_Z_BSPLINE_CACHE) >= 16:
            _Z_BSPLINE_CACHE.clear()
        cached = (im1, i0, i1, i2, w_m1, w_0, w_1, w_2)
        _Z_BSPLINE_CACHE[key] = cached

    im1, i0, i1, i2, w_m1, w_0, w_1, w_2 = cached
    src = np.ascontiguousarray(slice_2d, dtype=np.float32)
    c1d = scipy.ndimage.spline_filter1d(src, order=3, axis=0, mode="mirror")
    out = w_m1 * c1d[im1] + w_0 * c1d[i0] + w_1 * c1d[i1] + w_2 * c1d[i2]
    v_min = np.minimum(src[i0], src[i1])
    v_max = np.maximum(src[i0], src[i1])
    return np.clip(out, v_min, v_max)


def compute_rotation_matrix_deg(rot_x: float, rot_y: float, rot_z: float) -> np.ndarray:
    """Compute intrinsic MPR alignment rotation matrix Ry @ Rx @ Rz from degree angles."""
    rx, ry, rz = np.radians([rot_x, rot_y, rot_z])
    Rx = np.array([[1, 0, 0], [0, np.cos(rx), -np.sin(rx)], [0, np.sin(rx), np.cos(rx)]], dtype=np.float64)
    Ry = np.array([[np.cos(ry), 0, np.sin(ry)], [0, 1, 0], [-np.sin(ry), 0, np.cos(ry)]], dtype=np.float64)
    Rz = np.array([[np.cos(rz), np.sin(rz), 0], [-np.sin(rz), np.cos(rz), 0], [0, 0, 1]], dtype=np.float64)
    return Ry @ Rx @ Rz


def compute_world_cursor_mm(
    rot_matrix: np.ndarray,
    origin_mm: np.ndarray,
    float_cur: list[float] | tuple[float, float, float],
    spacing: tuple[float, float, float]
) -> np.ndarray:
    """Map rotated-frame voxel cursor (sx, sy, sz) to 3D volume coordinates in mm."""
    dy, dx, dz = float(spacing[0]), float(spacing[1]), float(spacing[2])
    s_mm = np.array([
        float(float_cur[0]) * dx,
        float(float_cur[1]) * dy,
        float(float_cur[2]) * dz,
    ], dtype=np.float64)
    return origin_mm + rot_matrix @ s_mm


def validate_aligned_export_geometry(
    slice_paths: list[str],
    vol: np.ndarray | None = None
) -> tuple[bool, str]:
    """
    Validate that source CT slices follow standard axial HFS geometry without gantry tilt
    and have uniform Z spacing so aligned DICOM export preserves clinical 3D geometry.
    """
    if not slice_paths or len(slice_paths) < 2:
        return False, tr_ui(
            "viewer_mpr_err_unsupported_geometry",
            tr_ui("viewer_mpr_geom_slice_count", len(slice_paths) if slice_paths else 0)
        )

    if vol is not None and len(slice_paths) != vol.shape[0]:
        return False, tr_ui(
            "viewer_mpr_err_unsupported_geometry",
            tr_ui("viewer_mpr_geom_slice_count", f"{len(slice_paths)} != {vol.shape[0]}")
        )

    expected_iop = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)
    ref_shape = None
    ref_ps = None
    z_positions = []

    for fp in slice_paths:
        try:
            ds = read_dicom_dataset(fp, stop_before_pixels=True)
        except Exception as e:
            return False, tr_ui("viewer_mpr_err_unsupported_geometry", str(e))

        iop = getattr(ds, "ImageOrientationPatient", None)
        if iop is None or len(iop) != 6:
            return False, tr_ui("viewer_mpr_err_unsupported_geometry", tr_ui("viewer_mpr_geom_orientation"))
        try:
            if max(abs(float(v) - e) for v, e in zip(iop, expected_iop)) > 1e-3:
                return False, tr_ui("viewer_mpr_err_unsupported_geometry", tr_ui("viewer_mpr_geom_orientation"))
        except (TypeError, ValueError):
            return False, tr_ui("viewer_mpr_err_unsupported_geometry", tr_ui("viewer_mpr_geom_orientation"))

        tilt = getattr(ds, "GantryDetectorTilt", None)
        if tilt is not None and str(tilt).strip() != "":
            try:
                tilt_val = float(tilt)
            except (TypeError, ValueError):
                tilt_val = 0.0
            if abs(tilt_val) > 0.5:
                return False, tr_ui("viewer_mpr_err_unsupported_geometry", tr_ui("viewer_mpr_geom_tilt", tilt_val))

        rows = getattr(ds, "Rows", None)
        cols = getattr(ds, "Columns", None)
        ps = getattr(ds, "PixelSpacing", None)
        if rows is None or cols is None or ps is None or len(ps) < 2:
            return False, tr_ui("viewer_mpr_err_unsupported_geometry", tr_ui("viewer_mpr_geom_spacing"))
        try:
            cur_shape = (int(rows), int(cols))
            cur_ps = (float(ps[0]), float(ps[1]))
        except (TypeError, ValueError):
            return False, tr_ui("viewer_mpr_err_unsupported_geometry", tr_ui("viewer_mpr_geom_spacing"))
        if cur_ps[0] <= 0.0 or cur_ps[1] <= 0.0:
            return False, tr_ui("viewer_mpr_err_unsupported_geometry", tr_ui("viewer_mpr_geom_spacing"))
        if vol is not None and cur_shape != vol.shape[1:]:
            return False, tr_ui("viewer_mpr_err_unsupported_geometry", tr_ui("viewer_mpr_geom_spacing"))
        if ref_shape is None:
            ref_shape = cur_shape
            ref_ps = cur_ps
        elif cur_shape != ref_shape or abs(cur_ps[0] - ref_ps[0]) > 1e-3 or abs(cur_ps[1] - ref_ps[1]) > 1e-3:
            return False, tr_ui("viewer_mpr_err_unsupported_geometry", tr_ui("viewer_mpr_geom_spacing"))

        ipp = getattr(ds, "ImagePositionPatient", None)
        if ipp is None or len(ipp) < 3:
            return False, tr_ui("viewer_mpr_err_unsupported_geometry", tr_ui("viewer_mpr_geom_z_step"))
        try:
            z_positions.append(float(ipp[2]))
        except (TypeError, ValueError):
            return False, tr_ui("viewer_mpr_err_unsupported_geometry", tr_ui("viewer_mpr_geom_z_step"))

    diffs = np.diff(np.array(z_positions, dtype=np.float64))
    if np.any(diffs <= 1e-4):
        return False, tr_ui("viewer_mpr_err_unsupported_geometry", tr_ui("viewer_mpr_geom_z_step"))
    mean_dz = float(np.mean(diffs))
    max_dev = float(np.max(np.abs(diffs - mean_dz)))
    if max_dev > max(0.1, 0.05 * mean_dz):
        return False, tr_ui("viewer_mpr_err_unsupported_geometry", tr_ui("viewer_mpr_geom_z_step"))

    return True, ""


def _find_primary_applicator_track(
    metal_mask: np.ndarray,
    min_track_slices: int = 10,
    max_step_px: float = 15.0,
) -> list[tuple[int, float, float]] | None:
    """
    Trace high-density 2D components top-to-bottom across Z slices as independent
    candidate tracks and return the dominant shaft track (longest Z-span, then voxels).
    """
    z_slices = np.flatnonzero(np.any(metal_mask, axis=(1, 2)))
    if len(z_slices) < min_track_slices:
        return None

    tracks: list[dict] = []
    for z in range(int(z_slices.max()), int(z_slices.min()) - 1, -1):
        sl = metal_mask[z]
        if not np.any(sl):
            continue
        lbl, num = scipy.ndimage.label(sl)
        if num == 0:
            continue

        comps = []
        for comp_id in range(1, num + 1):
            comp_pts = np.argwhere(lbl == comp_id)
            cy, cx = comp_pts.mean(axis=0)
            comps.append((float(cy), float(cx), int(len(comp_pts))))

        used: set[int] = set()
        for tr in sorted(tracks, key=lambda t: len(t["pts"]), reverse=True):
            py, px = tr["prev"]
            best_idx = -1
            best_dist = max_step_px
            for ci, (cy, cx, _area) in enumerate(comps):
                if ci in used:
                    continue
                d = math.hypot(cy - py, cx - px)
                if d < best_dist:
                    best_dist = d
                    best_idx = ci
            if best_idx >= 0:
                cy, cx, area = comps[best_idx]
                tr["pts"].append((z, cy, cx))
                tr["prev"] = (cy, cx)
                tr["voxels"] += area
                used.add(best_idx)

        for ci, (cy, cx, area) in enumerate(comps):
            if ci not in used:
                tracks.append({"pts": [(z, cy, cx)], "prev": (cy, cx), "voxels": area})

    valid = [t for t in tracks if len(t["pts"]) >= min_track_slices]
    if not valid:
        return None
    return max(valid, key=lambda t: (len(t["pts"]), t["voxels"]))["pts"]


def detect_applicator_orientation(
    vol: np.ndarray,
    spacing: tuple[float, float, float]
) -> dict | None:
    """
    Detects high-density metallic brachytherapy applicator (tandem/metrocolpostat) in 3D volume.
    Calculates primary shaft tilt (rot_x, rot_y), ovoid/bend plane angle (rot_z), and pivot voxel.
    """
    if vol is None or vol.ndim != 3:
        return None

    dy, dx, dz = float(spacing[0]), float(spacing[1]), float(spacing[2])
    n_z, rows, cols = vol.shape

    metal_mask = None
    track = None
    for thresh in (2000.0, 1500.0):
        cand_mask = vol > thresh
        if int(np.sum(cand_mask)) < 50:
            continue
        cand_track = _find_primary_applicator_track(cand_mask)
        if cand_track is not None:
            metal_mask = cand_mask
            track = cand_track
            break

    if track is None or metal_mask is None:
        return None

    track_arr = np.array(track)
    pts_mm = np.column_stack([track_arr[:, 2] * dx, track_arr[:, 1] * dy, track_arr[:, 0] * dz])

    n_pts = len(track_arr)
    split_idx = max(5, int(n_pts * 0.4))
    tip_pts = pts_mm[:split_idx]
    shaft_pts = pts_mm[split_idx:]
    if len(shaft_pts) < 5:
        shaft_pts = pts_mm

    mean_shaft = np.mean(shaft_pts, axis=0)
    cov = np.cov(shaft_pts - mean_shaft, rowvar=False)
    _, eigvecs = np.linalg.eigh(cov)
    v_shaft = eigvecs[:, -1]
    if v_shaft[2] < 0:
        v_shaft = -v_shaft

    rot_x = float(np.degrees(-np.arcsin(np.clip(v_shaft[1], -1.0, 1.0))))
    cos_rx = math.cos(math.radians(rot_x))
    if abs(cos_rx) > 1e-4:
        rot_y = float(np.degrees(np.arcsin(np.clip(v_shaft[0] / cos_rx, -1.0, 1.0))))
    else:
        rot_y = 0.0

    pivot_z_idx = min(len(track_arr) - 1, split_idx)
    raw_pivot_mm = pts_mm[pivot_z_idx]
    proj_t = float(np.dot(raw_pivot_mm - mean_shaft, v_shaft))
    pivot_pt_mm = mean_shaft + proj_t * v_shaft
    pivot_fx = float(np.clip(pivot_pt_mm[0] / dx, 0.0, float(cols - 1)))
    pivot_fy = float(np.clip(pivot_pt_mm[1] / dy, 0.0, float(rows - 1)))
    pivot_fz = float(np.clip(pivot_pt_mm[2] / dz, 0.0, float(n_z - 1)))
    cur_x = int(round(pivot_fx))
    cur_y = int(round(pivot_fy))
    cur_z = int(round(pivot_fz))

    rx = math.radians(rot_x)
    ry = math.radians(rot_y)
    Rx = np.array([[1, 0, 0], [0, math.cos(rx), -math.sin(rx)], [0, math.sin(rx), math.cos(rx)]])
    Ry = np.array([[math.cos(ry), 0, math.sin(ry)], [0, 1, 0], [-math.sin(ry), 0, math.cos(ry)]])
    A = Ry @ Rx

    ovoid_angles = []
    max_ovoid_z = min(n_z - 1, cur_z + 2)
    for z_idx in range(0, max_ovoid_z + 1):
        sl_mask = metal_mask[z_idx]
        if not np.any(sl_mask):
            continue
        lbl, num_comp = scipy.ndimage.label(sl_mask)
        if num_comp < 2:
            continue
        local_uv = []
        for comp_id in range(1, num_comp + 1):
            comp_pts = np.argwhere(lbl == comp_id)
            if len(comp_pts) < 3:
                continue
            cy_c, cx_c = comp_pts.mean(axis=0)
            p_mm = np.array([float(cx_c) * dx, float(cy_c) * dy, float(z_idx) * dz], dtype=np.float64)
            q_pt = (p_mm - pivot_pt_mm) @ A
            u_c, v_c = float(q_pt[0]), float(q_pt[1])
            if math.hypot(u_c, v_c) <= 50.0:
                local_uv.append((u_c, v_c))
        if len(local_uv) < 2:
            continue

        best_span = 0.0
        best_pair = None
        for i in range(len(local_uv)):
            for j in range(i + 1, len(local_uv)):
                ui, vi = local_uv[i]
                uj, vj = local_uv[j]
                if math.hypot(ui, vi) < 4.0 or math.hypot(uj, vj) < 4.0:
                    continue
                if ui * uj + vi * vj >= 0.0:
                    continue
                span = math.hypot(uj - ui, vj - vi)
                if span > best_span:
                    best_span = span
                    best_pair = (local_uv[i], local_uv[j])

        if best_pair is not None and best_span >= 10.0:
            (u0, v0), (u1, v1) = best_pair
            du, dv = u1 - u0, v1 - v0
            if du < 0.0 or (abs(du) < 1e-4 and dv > 0.0):
                du, dv = -du, -dv
            ovoid_angles.append(math.atan2(-dv, du))

    if len(ovoid_angles) >= 3:
        rads = np.array(ovoid_angles, dtype=np.float64)
        s2 = float(np.median(np.sin(2.0 * rads)))
        c2 = float(np.median(np.cos(2.0 * rads)))
        rot_z = float(np.degrees(0.5 * math.atan2(s2, c2)))
    else:
        Q_tip = (tip_pts - pivot_pt_mm) @ A
        v_bend = np.mean(Q_tip[:, 0:2], axis=0)
        bend_mag = math.hypot(float(v_bend[0]), float(v_bend[1]))

        if bend_mag > 0.5:
            rot_z = float(np.degrees(math.atan2(float(v_bend[0]), float(v_bend[1]))))
            while rot_z > 90.0:
                rot_z -= 180.0
            while rot_z < -90.0:
                rot_z += 180.0
        else:
            rot_z = 0.0

    return {
        "cur_x": cur_x,
        "cur_y": cur_y,
        "cur_z": cur_z,
        "float_cur": [pivot_fx, pivot_fy, pivot_fz],
        "rot_x": round(rot_x, 1),
        "rot_y": round(rot_y, 1),
        "rot_z": round(rot_z, 1),
    }


def export_aligned_volume_slices(
    slice_paths: list[str],
    vol: np.ndarray,
    spacing: tuple[float, float, float],
    rot_x: float,
    rot_y: float,
    rot_z: float,
    float_cur: list[float] | tuple[float, float, float],
    origin_mm: np.ndarray | list[float],
    output_dir: str,
    mode: str = "new",
    series_uid: str | None = None,
    frame_of_reference_uid: str | None = None,
    series_desc: str | None = None,
    file_prefix: str = "DERIVED_ALIGNED_",
    window_center: float | None = None,
    window_width: float | None = None,
    spline_coeffs: np.ndarray | None = None,
    on_progress=None,
    is_cancelled=None,
) -> dict | None:
    """
    Resample a 3D HU volume along rotated axes and write compliant DICOM CT slices
    with a fresh FrameOfReferenceUID and standard axial HFS orientation.
    """
    n_slices = len(slice_paths)
    if n_slices == 0 or vol is None:
        return None

    dy, dx, dz = float(spacing[0]), float(spacing[1]), float(spacing[2])
    _, rows, cols = vol.shape
    origin_arr = np.array(origin_mm, dtype=np.float64)
    fc = [float(float_cur[0]), float(float_cur[1]), float(float_cur[2])]

    R = compute_rotation_matrix_deg(rot_x, rot_y, rot_z)
    c_world = compute_world_cursor_mm(R, origin_arr, fc, (dy, dx, dz))
    cx_rot, cy_rot, cz_rot = float(c_world[0]), float(c_world[1]), float(c_world[2])

    u_ax = (np.arange(cols, dtype=np.float32) - np.float32(fc[0])) * np.float32(dx)
    v_ax = (np.arange(rows, dtype=np.float32) - np.float32(fc[1])) * np.float32(dy)
    uu, vv = np.meshgrid(u_ax, v_ax)

    if spline_coeffs is None or spline_coeffs.shape != vol.shape:
        spline_coeffs = compute_cubic_spline_coeffs(vol)

    new_series_uid = series_uid or generate_uid()
    new_for_uid = frame_of_reference_uid or generate_uid()
    if mode == "new":
        os.makedirs(output_dir, exist_ok=True)

    out_files = []
    final_series_desc = series_desc or "CT (Aligned)"
    inv_dz = np.float32(1.0 / dz)
    inv_dy = np.float32(1.0 / dy)
    inv_dx = np.float32(1.0 / dx)
    coords = np.empty((3, rows, cols), dtype=np.float32)

    for k in range(n_slices):
        if is_cancelled and is_cancelled():
            return None

        in_fp = slice_paths[k]
        if on_progress:
            on_progress(k + 1, n_slices)

        w = (k - fc[2]) * dz
        px = np.float32(cx_rot + w * R[0, 2]) + uu * np.float32(R[0, 0]) + vv * np.float32(R[0, 1])
        py = np.float32(cy_rot + w * R[1, 2]) + uu * np.float32(R[1, 0]) + vv * np.float32(R[1, 1])
        pz = np.float32(cz_rot + w * R[2, 2]) + uu * np.float32(R[2, 0]) + vv * np.float32(R[2, 1])

        coords[0] = pz * inv_dz
        coords[1] = py * inv_dy
        coords[2] = px * inv_dx
        slice_hu = sample_oblique_slice(vol, coords, spline_coeffs=spline_coeffs)

        ds = read_dicom_dataset(in_fp)
        slope = float(getattr(ds, "RescaleSlope", 1.0) or 1.0)
        intercept = float(getattr(ds, "RescaleIntercept", 0.0) or 0.0)

        raw_pixels = np.round((slice_hu - intercept) / slope)
        bit_depth = int(getattr(ds, "BitsAllocated", 16))
        pixel_rep = int(getattr(ds, "PixelRepresentation", 0))

        if pixel_rep == 0:
            raw_pixels = np.clip(raw_pixels, 0, (1 << bit_depth) - 1).astype(np.uint16)
        else:
            min_val = -(1 << (bit_depth - 1))
            max_val = (1 << (bit_depth - 1)) - 1
            raw_pixels = np.clip(raw_pixels, min_val, max_val).astype(np.int16)

        ds.PixelData = raw_pixels.tobytes()
        ds.Rows = rows
        ds.Columns = cols
        ds.ImageOrientationPatient = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0]
        ds.InstanceNumber = k + 1

        if series_desc:
            ds.SeriesDescription = series_desc
        else:
            orig_desc = str(getattr(ds, "SeriesDescription", "CT"))
            if "(Aligned)" not in orig_desc:
                ds.SeriesDescription = f"{orig_desc} (Aligned)"
        final_series_desc = str(ds.SeriesDescription)

        ds.SeriesInstanceUID = new_series_uid
        ds.FrameOfReferenceUID = new_for_uid
        new_sop_uid = generate_uid()
        ds.SOPInstanceUID = new_sop_uid
        if hasattr(ds, "file_meta") and ds.file_meta is not None:
            ds.file_meta.MediaStorageSOPInstanceUID = new_sop_uid

        if window_center is not None and window_width is not None:
            ds.WindowCenter = int(round(window_center))
            ds.WindowWidth = int(round(window_width))

        if mode == "new":
            try:
                snum = int(getattr(ds, "SeriesNumber", 1))
                ds.SeriesNumber = snum + 100
            except Exception:
                ds.SeriesNumber = 101
            ds.ImageType = ["DERIVED", "SECONDARY", "AXIAL"]
            orig_fname = os.path.basename(in_fp)
            if orig_fname.upper().startswith("DERIVED_"):
                out_fname = f"{file_prefix}{orig_fname.split('_', 2)[-1]}"
            else:
                out_fname = f"{file_prefix}{orig_fname}"
            out_fp = os.path.join(output_dir, out_fname)
        else:
            out_fp = in_fp

        ds.save_as(out_fp)
        out_files.append(out_fp)

    return {
        "mode": mode,
        "output_dir": output_dir,
        "files": out_files,
        "count": len(out_files),
        "series_uid": new_series_uid,
        "frame_of_reference_uid": new_for_uid,
        "series_desc": final_series_desc,
    }

