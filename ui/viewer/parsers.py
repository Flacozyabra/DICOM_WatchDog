from __future__ import annotations

import os
import re
import numpy as np
import pydicom
from PyQt6.QtGui import QColor


def safe_dcmread(filepath, *args, **kwargs):
    """
    Безопасно считывает DICOM-файл с помощью pydicom.dcmread.
    Поддерживает force=True по умолчанию для корректного чтения файлов без Part 10 преамбулы (Elekta и др.).
    Гарантирует инициализацию file_meta.TransferSyntaxUID для возможности декодирования pixel_array.
    При возникновении ошибки ValueError с текстом 'already uncompressed'
    пытается исправить TransferSyntaxUID и перечитать файл.
    """
    kwargs.setdefault('force', True)
    try:
        ds = pydicom.dcmread(filepath, *args, **kwargs)
        if not hasattr(ds, 'file_meta') or ds.file_meta is None:
            ds.file_meta = pydicom.dataset.FileMetaDataset()
        if not hasattr(ds.file_meta, 'TransferSyntaxUID') or not ds.file_meta.TransferSyntaxUID:
            if getattr(ds, 'is_implicit_VR', True):
                ds.file_meta.TransferSyntaxUID = pydicom.uid.ImplicitVRLittleEndian
            else:
                ds.file_meta.TransferSyntaxUID = pydicom.uid.ExplicitVRLittleEndian
        return ds
    except ValueError as e:
        if "already uncompressed" in str(e).lower():
            try:
                ds_meta = pydicom.dcmread(filepath, stop_before_pixels=True, force=True)
                ds_meta.file_meta.TransferSyntaxUID = pydicom.uid.ImplicitVRLittleEndian
                if isinstance(filepath, (str, os.PathLike)):
                    ds = pydicom.dcmread(filepath, *args, **kwargs)
                    ds.file_meta.TransferSyntaxUID = pydicom.uid.ImplicitVRLittleEndian
                    return ds
                else:
                    if hasattr(filepath, 'seek'):
                        filepath.seek(0)
                    ds = pydicom.dcmread(filepath, *args, **kwargs)
                    ds.file_meta.TransferSyntaxUID = pydicom.uid.ImplicitVRLittleEndian
                    return ds
            except Exception:
                raise e
        raise e


def _convex_hull_2d(points):
    """Monotone chain 2D convex hull algorithm: O(N log N)."""
    pts = sorted(set(points), key=lambda p: (p[0], p[1]))
    if len(pts) <= 2:
        return pts
    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def load_rtstruct(filepath, progress_callback=None):
    """
    Парсит файл RTSTRUCT и возвращает словарь со структурами и их контурами.
    Использует векторизованное чтение сырых бинарных данных ContourData (defer_size и numpy)
    для многократного ускорения парсинга.
    """
    structures = {}
    try:
        ds = safe_dcmread(filepath, defer_size=512)
        if getattr(ds, "Modality", "") != "RTSTRUCT":
            return structures
            
        roi_names = {}
        if hasattr(ds, "StructureSetROISequence"):
            for roi in ds.StructureSetROISequence:
                num = int(roi.ROINumber)
                name = str(roi.ROIName)
                roi_names[num] = name
                
        roi_contours = list(getattr(ds, "ROIContourSequence", []))
        total_rois = len(roi_contours)

        for roi_idx, roi_contour in enumerate(roi_contours):
            if progress_callback is not None:
                progress_callback(roi_idx + 1, total_rois)

            num = int(roi_contour.ReferencedROINumber)
            name = roi_names.get(num, f"ROI {num}")
            
            color = QColor(0, 255, 0)
            if hasattr(roi_contour, "ROIDisplayColor"):
                rgb = roi_contour.ROIDisplayColor
                if len(rgb) == 3:
                    color = QColor(int(rgb[0]), int(rgb[1]), int(rgb[2]))
                    
            contours = []
            if hasattr(roi_contour, "ContourSequence"):
                for contour in roi_contour.ContourSequence:
                    sop_uid = None
                    if hasattr(contour, "ContourImageSequence") and len(contour.ContourImageSequence) > 0:
                        sop_uid = str(contour.ContourImageSequence[0].ReferencedSOPInstanceUID)
                        
                    elem = contour.get_item(0x30060050)
                    if elem is not None and elem.value is not None:
                        raw_val = elem.value
                        if isinstance(raw_val, bytes):
                            s = raw_val.decode("ascii", errors="ignore")
                            arr = np.fromiter((float(x) for x in s.split("\\") if x), dtype=np.float32).reshape(-1, 3)
                        elif isinstance(raw_val, str):
                            arr = np.fromiter((float(x) for x in raw_val.split("\\") if x), dtype=np.float32).reshape(-1, 3)
                        elif isinstance(raw_val, (list, tuple)) or hasattr(raw_val, "__iter__"):
                            arr = np.fromiter(raw_val, dtype=np.float32).reshape(-1, 3)
                        else:
                            arr = np.array(raw_val, dtype=np.float32).reshape(-1, 3)

                        if len(arr) > 0:
                            z_coord = float(arr[0, 2])
                            contours.append({
                                "sop_uid": sop_uid,
                                "z": z_coord,
                                "points": arr.tolist()
                            })
                            
            structures[num] = {
                "name": name,
                "color": color,
                "contours": contours
            }
    except Exception as e:
        print(f"Error parsing RTSTRUCT {filepath}: {e}")
        
    return structures


def load_rtdose(filepath: str, plan_files: list[str] = None) -> dict:
    """
    Парсит файл RTDOSE и связывает его с RTPLAN (если передан) для получения предписанной дозы.
    """
    dose_data = {}
    try:
        ds = safe_dcmread(filepath)
        if getattr(ds, "Modality", "") != "RTDOSE":
            return dose_data

        dose_scaling = float(getattr(ds, "DoseGridScaling", 1.0))
        dose_units = str(getattr(ds, "DoseUnits", "Gy"))
        if dose_units.upper() == "GY":
            dose_units = "Gy"

        pixel_arr = ds.pixel_array
        dose_grid = pixel_arr.astype(np.float32) * dose_scaling
        if dose_grid.ndim == 2:
            dose_grid = dose_grid[np.newaxis, ...]

        ipp = [float(x) for x in getattr(ds, "ImagePositionPatient", [0.0, 0.0, 0.0])]
        iop = [float(x) for x in getattr(ds, "ImageOrientationPatient", [1.0, 0.0, 0.0, 0.0, 1.0, 0.0])]
        pixel_spacing = [float(x) for x in getattr(ds, "PixelSpacing", [1.0, 1.0])]

        grid_frame_offset = getattr(ds, "GridFrameOffsetVector", None)
        if grid_frame_offset is not None and len(grid_frame_offset) > 0:
            z_positions = [ipp[2] + float(off) for off in grid_frame_offset]
        else:
            slice_thickness = float(getattr(ds, "SliceThickness", 1.0))
            z_positions = [ipp[2] + i * slice_thickness for i in range(dose_grid.shape[0])]

        max_dose = float(np.max(dose_grid)) if dose_grid.size > 0 else 0.0
        rx_dose = 0.0
        plan_label = ""

        ref_plan_uid = None
        if hasattr(ds, "ReferencedRTPlanSequence") and len(ds.ReferencedRTPlanSequence) > 0:
            ref_plan_uid = str(getattr(ds.ReferencedRTPlanSequence[0], "ReferencedSOPInstanceUID", ""))

        if plan_files:
            for pf in plan_files:
                try:
                    ds_plan = safe_dcmread(pf, stop_before_pixels=True)
                    plan_sop = str(getattr(ds_plan, "SOPInstanceUID", ""))
                    if (ref_plan_uid and plan_sop == ref_plan_uid) or not ref_plan_uid:
                        plan_label = str(getattr(ds_plan, "RTPlanName", "") or getattr(ds_plan, "RTPlanLabel", "") or getattr(ds_plan, "RTPlanDescription", "")).strip()
                        if hasattr(ds_plan, "DoseReferenceSequence"):
                            for dref in ds_plan.DoseReferenceSequence:
                                if hasattr(dref, "TargetPrescriptionDose"):
                                    rx_dose = float(dref.TargetPrescriptionDose)
                                    break
                                elif hasattr(dref, "DeliveryMaximumDose"):
                                    rx_dose = float(dref.DeliveryMaximumDose)
                                    break
                        if rx_dose > 0:
                            break
                except Exception:
                    pass

        if rx_dose <= 0:
            rx_dose = max_dose if max_dose > 0 else 1.0

        default_levels = [
            {"pct": 107, "color": QColor("#D946EF"), "name": "107%"},
            {"pct": 100, "color": QColor("#EF4444"), "name": "100%"},
            {"pct": 95,  "color": QColor("#F97316"), "name": "95%"},
            {"pct": 90,  "color": QColor("#EAB308"), "name": "90%"},
            {"pct": 80,  "color": QColor("#84CC16"), "name": "80%"},
            {"pct": 70,  "color": QColor("#22C55E"), "name": "70%"},
            {"pct": 50,  "color": QColor("#06B6D4"), "name": "50%"},
            {"pct": 30,  "color": QColor("#3B82F6"), "name": "30%"},
        ]

        levels = []
        for item in default_levels:
            pct = item["pct"]
            val = round(rx_dose * (pct / 100.0), 2)
            levels.append({
                "name": item["name"],
                "pct": pct,
                "val": val,
                "color": item["color"],
                "enabled": True
            })

        dose_data = {
            "filepath": filepath,
            "dose_grid": dose_grid,
            "z_positions": np.array(z_positions, dtype=np.float32),
            "ipp": ipp,
            "iop": iop,
            "pixel_spacing": pixel_spacing,
            "max_dose": max_dose,
            "rx_dose": rx_dose,
            "dose_units": dose_units,
            "plan_label": plan_label,
            "levels": levels
        }
    except Exception as e:
        print(f"Error parsing RTDOSE {filepath}: {e}")

    return dose_data


def clean_tps_name(model_name: str, manufacturer: str) -> str:
    raw = model_name or manufacturer or "Unknown"
    raw = re.sub(r'[,;]?\s*(version|ver\.?|v\.?)\s*[\d\.\w\-_]+', '', raw, flags=re.IGNORECASE)
    raw = re.sub(r'^(TPS|Treatment Planning System)\s+', '', raw, flags=re.IGNORECASE)
    raw = raw.strip()
    return raw or "RT Plan"


def load_rtplan(filepath: str) -> dict:
    plan_data = {}
    if not filepath or not os.path.exists(filepath):
        return plan_data

    try:
        ds = safe_dcmread(filepath, stop_before_pixels=True)
        if getattr(ds, "Modality", "") != "RTPLAN":
            return plan_data

        sop_instance_uid = str(getattr(ds, "SOPInstanceUID", ""))
        plan_label = str(getattr(ds, "RTPlanName", "") or getattr(ds, "RTPlanLabel", "") or getattr(ds, "RTPlanDescription", "")).strip()
        model_name = str(getattr(ds, "ManufacturerModelName", ""))
        manufacturer = str(getattr(ds, "Manufacturer", ""))
        tps_name = clean_tps_name(model_name, manufacturer)
        approval_status = str(getattr(ds, "ApprovalStatus", ""))

        patient_position = "HFS"
        if hasattr(ds, "PatientSetupSequence") and len(ds.PatientSetupSequence) > 0:
            ps0 = ds.PatientSetupSequence[0]
            patient_position = str(getattr(ps0, "PatientPosition", "HFS") or "HFS").upper()

        rx_dose = 0.0
        if hasattr(ds, "DoseReferenceSequence"):
            for dref in ds.DoseReferenceSequence:
                if hasattr(dref, "TargetPrescriptionDose"):
                    rx_dose = float(dref.TargetPrescriptionDose)
                    break
                elif hasattr(dref, "DeliveryMaximumDose"):
                    rx_dose = float(dref.DeliveryMaximumDose)
                    break

        fractions_count = 1
        if hasattr(ds, "FractionGroupSequence") and len(ds.FractionGroupSequence) > 0:
            fg0 = ds.FractionGroupSequence[0]
            fractions_count = int(getattr(fg0, "NumberOfFractionsPlanned", 1) or 1)

        dose_per_fraction = round(rx_dose / fractions_count, 2) if (rx_dose > 0 and fractions_count > 0) else 0.0

        beams = []
        if hasattr(ds, "BeamSequence"):
            for b in ds.BeamSequence:
                b_num = int(getattr(b, "BeamNumber", len(beams) + 1))
                b_name = str(getattr(b, "BeamName", f"Beam {b_num}"))
                b_type = str(getattr(b, "BeamType", "STATIC"))
                rad_type = str(getattr(b, "RadiationType", "PHOTON"))
                mach_name = str(getattr(b, "TreatmentMachineName", ""))
                sad = float(getattr(b, "SourceAxisDistance", 1000.0) or 1000.0)

                # Wedges
                wedges = []
                num_wedges = int(getattr(b, "NumberOfWedges", getattr(b, "NumberofWedges", 0)) or 0)
                if num_wedges > 0 and hasattr(b, "WedgeSequence"):
                    for w in b.WedgeSequence:
                        wedges.append({
                            "id": str(getattr(w, "WedgeID", "")),
                            "angle": int(getattr(w, "WedgeAngle", 0) or 0),
                            "type": str(getattr(w, "WedgeType", "STANDARD")),
                            "orientation": float(getattr(w, "WedgeOrientation", 0.0) or 0.0)
                        })

                # Global leaf boundaries from BeamLimitingDeviceSequence
                global_leaf_bounds = []
                if hasattr(b, "BeamLimitingDeviceSequence"):
                    for bld in b.BeamLimitingDeviceSequence:
                        if "MLC" in getattr(bld, "RTBeamLimitingDeviceType", ""):
                            bounds = getattr(bld, "LeafPositionBoundaries", [])
                            if bounds:
                                global_leaf_bounds = [float(x) for x in bounds]

                # Control points (Gantry, Collimator, Jaws, MLC, Isocenter)
                cps = []
                cur_gantry = 0.0
                cur_coll = 0.0
                cur_couch = 0.0
                cur_iso = None
                cur_jaws = {"x": [-200.0, 200.0], "y": [-200.0, 200.0], "has_x": False, "has_y": False}
                cur_mlc = []

                if hasattr(b, "ControlPointSequence"):
                    for cp in b.ControlPointSequence:
                        cp_idx = int(getattr(cp, "ControlPointIndex", len(cps)))
                        
                        raw_g = getattr(cp, "GantryAngle", None)
                        if raw_g is not None:
                            try:
                                cur_gantry = float(raw_g)
                            except (ValueError, TypeError):
                                pass

                        raw_c = getattr(cp, "BeamLimitingDeviceAngle", None)
                        if raw_c is not None:
                            try:
                                cur_coll = float(raw_c)
                            except (ValueError, TypeError):
                                pass

                        raw_couch = getattr(cp, "PatientSupportAngle", None)
                        if raw_couch is not None:
                            try:
                                cur_couch = float(raw_couch)
                            except (ValueError, TypeError):
                                pass

                        meterset_w = float(getattr(cp, "CumulativeMetersetWeight", 0.0) or 0.0)
                        energy = float(getattr(cp, "NominalBeamEnergy", 0.0) or 0.0)

                        raw_iso = getattr(cp, "IsocenterPosition", None)
                        if raw_iso is not None and len(raw_iso) >= 3:
                            try:
                                cur_iso = [float(x) for x in raw_iso]
                            except (ValueError, TypeError):
                                pass

                        if hasattr(cp, "BeamLimitingDevicePositionSequence"):
                            for dev in cp.BeamLimitingDevicePositionSequence:
                                dev_type = getattr(dev, "RTBeamLimitingDeviceType", "")
                                pos = getattr(dev, "LeafJawPositions", [])
                                if dev_type in ("ASYMX", "X") and len(pos) >= 2:
                                    cur_jaws["x"] = [float(pos[0]), float(pos[1])]
                                    cur_jaws["has_x"] = True
                                elif dev_type in ("ASYMY", "Y") and len(pos) >= 2:
                                    cur_jaws["y"] = [float(pos[0]), float(pos[1])]
                                    cur_jaws["has_y"] = True
                                elif "MLC" in dev_type and len(pos) > 0:
                                    cur_mlc = [float(p) for p in pos]

                        cps.append({
                            "index": cp_idx,
                            "gantry_angle": cur_gantry,
                            "collimator_angle": cur_coll,
                            "couch_angle": cur_couch,
                            "meterset_weight": meterset_w,
                            "energy": energy,
                            "isocenter": cur_iso,
                            "jaws": dict(cur_jaws),
                            "mlc_leaves": list(cur_mlc),
                            "leaf_boundaries": global_leaf_bounds
                        })

                cp0 = cps[0] if cps else {}

                is_dynamic = False
                if b_type in ("DYNAMIC", "ROTATIONAL"):
                    is_dynamic = True
                elif len(cps) > 2:
                    is_dynamic = True
                elif len(cps) == 2:
                    g0, g1 = cps[0]["gantry_angle"], cps[1]["gantry_angle"]
                    mlc0, mlc1 = cps[0]["mlc_leaves"], cps[1]["mlc_leaves"]
                    if abs(g0 - g1) > 0.1 or (mlc0 != mlc1 and mlc0 and mlc1):
                        is_dynamic = True

                wedge_suffix = ""
                if wedges:
                    w_first = wedges[0]
                    wedge_suffix = f" [▲ {w_first['id']} ({w_first['angle']}°)]"

                # Calculate total angular travel and detect arc passes (handles bidirectional/dual-arc and 360-deg arcs)
                total_gantry_travel = 0.0
                arc_passes = []
                if len(cps) > 1:
                    for i in range(len(cps) - 1):
                        g1 = float(cps[i]["gantry_angle"])
                        g2 = float(cps[i+1]["gantry_angle"])
                        dg = abs(g2 - g1)
                        if dg > 180.0:
                            dg = 360.0 - dg
                        total_gantry_travel += dg

                    cur_pass = {"start": None, "stop": None, "dir": None}
                    for i in range(len(cps) - 1):
                        g1 = float(cps[i]["gantry_angle"])
                        g2 = float(cps[i+1]["gantry_angle"])
                        rdir = "NONE"
                        if hasattr(b, "ControlPointSequence") and len(b.ControlPointSequence) > i:
                            rdir = str(getattr(b.ControlPointSequence[i], "GantryRotationDirection", "NONE") or "NONE").upper()
                        if not rdir or rdir == "NONE":
                            d_raw = (g2 - g1) % 360.0
                            if 0.001 < d_raw <= 180.0:
                                rdir = "CW"
                            elif d_raw > 180.0 and (360.0 - d_raw) > 0.001:
                                rdir = "CC"
                            else:
                                rdir = "NONE"

                        norm_dir = "CCW" if rdir in ("CC", "CCW", "COUNTER_CLOCKWISE") else ("CW" if rdir in ("CW", "CLOCKWISE") else None)
                        if norm_dir:
                            if cur_pass["dir"] is None:
                                cur_pass["dir"] = norm_dir
                                cur_pass["start"] = g1
                            elif cur_pass["dir"] != norm_dir:
                                cur_pass["stop"] = g1
                                arc_passes.append(dict(cur_pass))
                                cur_pass = {"start": g1, "stop": None, "dir": norm_dir}
                            cur_pass["stop"] = g2
                    if cur_pass["dir"] is not None and cur_pass["start"] is not None and cur_pass["stop"] is not None:
                        arc_passes.append(dict(cur_pass))

                is_vmat = (total_gantry_travel > 5.0) and (len(arc_passes) > 0)

                g_start = cps[0]["gantry_angle"] if cps else 0.0
                g_stop = cps[-1]["gantry_angle"] if cps else g_start
                rot_dir = arc_passes[0]["dir"] if arc_passes else "NONE"
                if rot_dir == "NONE" and hasattr(b, "ControlPointSequence") and len(b.ControlPointSequence) > 0:
                    rot_dir = str(getattr(b.ControlPointSequence[0], "GantryRotationDirection", "NONE") or "NONE").upper()
                if rot_dir == "NONE" and hasattr(b, "GantryRotationDirection"):
                    rot_dir = str(getattr(b, "GantryRotationDirection", "NONE") or "NONE").upper()

                clean_name = str(b_name).strip() if b_name else ""
                if not clean_name or clean_name == f"Beam {b_num}":
                    clean_name = f"Поле {b_num}"

                if is_vmat and arc_passes:
                    if len(arc_passes) == 1:
                        p = arc_passes[0]
                        display_name = f"{clean_name} ({p['start']:.0f}°->{p['stop']:.0f}° {p['dir']})"
                    else:
                        passes_str = " | ".join(f"{p['start']:.0f}°->{p['stop']:.0f}° {p['dir']}" for p in arc_passes)
                        display_name = f"{clean_name} ({passes_str})"
                elif is_dynamic and abs(g_start - g_stop) > 0.5:
                    dir_txt = " (CW)" if rot_dir in ("CW", "CLOCKWISE") else (" (CCW)" if rot_dir in ("CC", "CCW", "COUNTER_CLOCKWISE") else "")
                    display_name = f"{clean_name} ({g_start:.0f}°->{g_stop:.0f}°{dir_txt})"
                else:
                    display_name = f"{clean_name} ({g_start:.1f}°){wedge_suffix}"

                beams.append({
                    "number": b_num,
                    "name": b_name,
                    "display_name": display_name,
                    "type": b_type,
                    "is_dynamic": is_dynamic,
                    "is_vmat": is_vmat,
                    "total_gantry_travel": total_gantry_travel,
                    "arc_passes": arc_passes,
                    "radiation_type": rad_type,
                    "machine_name": mach_name,
                    "sad": sad,
                    "gantry_angle": cp0.get("gantry_angle", 0.0),
                    "gantry_start": g_start,
                    "gantry_stop": g_stop,
                    "gantry_rotation_direction": rot_dir,
                    "collimator_angle": cp0.get("collimator_angle", 0.0),
                    "couch_angle": cp0.get("couch_angle", 0.0),
                    "isocenter": cp0.get("isocenter", None),
                    "jaws": cp0.get("jaws", {"x": [-200.0, 200.0], "y": [-200.0, 200.0]}),
                    "mlc_leaves": cp0.get("mlc_leaves", []),
                    "leaf_boundaries": global_leaf_bounds,
                    "wedges": wedges,
                    "control_points": cps
                })

        plan_data = {
            "filepath": filepath,
            "sop_instance_uid": sop_instance_uid,
            "plan_label": plan_label,
            "patient_position": patient_position,
            "tps_name": tps_name,
            "approval_status": approval_status,
            "rx_dose": rx_dose,
            "fractions_count": fractions_count,
            "dose_per_fraction": dose_per_fraction,
            "beams": beams
        }
    except Exception as e:
        print(f"Error loading RTPLAN {filepath}: {e}")

    return plan_data


def marching_squares_2d(grid: np.ndarray, threshold: float) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """
    Быстрый 2D Marching Squares для поиска сегментов изолиний (изодоз) на скалярной сетке.
    Возвращает список пар координат ((c1, r1), (c2, r2)) в пространстве индексов ячеек сетки.
    """
    if grid.shape[0] < 2 or grid.shape[1] < 2:
        return []

    v0 = grid[:-1, :-1]  # TL
    v1 = grid[:-1, 1:]   # TR
    v2 = grid[1:, 1:]    # BR
    v3 = grid[1:, :-1]   # BL

    b0 = v0 >= threshold
    b1 = v1 >= threshold
    b2 = v2 >= threshold
    b3 = v3 >= threshold

    case_index = (b0.astype(np.uint8) * 1 +
                  b1.astype(np.uint8) * 2 +
                  b2.astype(np.uint8) * 4 +
                  b3.astype(np.uint8) * 8)

    active = (case_index > 0) & (case_index < 15)
    if not np.any(active):
        return []

    rows, cols = np.where(active)
    cases = case_index[rows, cols]

    r_f = rows.astype(np.float32)
    c_f = cols.astype(np.float32)

    v0_a = v0[rows, cols]
    v1_a = v1[rows, cols]
    v2_a = v2[rows, cols]
    v3_a = v3[rows, cols]

    eps = 1e-7
    c_top = c_f + (threshold - v0_a) / (v1_a - v0_a + eps)
    r_top = r_f

    c_right = c_f + 1.0
    r_right = r_f + (threshold - v1_a) / (v2_a - v1_a + eps)

    c_bot = c_f + (threshold - v3_a) / (v2_a - v3_a + eps)
    r_bot = r_f + 1.0

    c_left = c_f
    r_left = r_f + (threshold - v0_a) / (v3_a - v0_a + eps)

    segments = []
    for idx, case in enumerate(cases):
        p_top = (float(c_top[idx]), float(r_top[idx]))
        p_right = (float(c_right[idx]), float(r_right[idx]))
        p_bot = (float(c_bot[idx]), float(r_bot[idx]))
        p_left = (float(c_left[idx]), float(r_left[idx]))

        if case == 1 or case == 14:
            segments.append((p_left, p_top))
        elif case == 2 or case == 13:
            segments.append((p_top, p_right))
        elif case == 3 or case == 12:
            segments.append((p_left, p_right))
        elif case == 4 or case == 11:
            segments.append((p_right, p_bot))
        elif case == 5:
            segments.append((p_left, p_top))
            segments.append((p_right, p_bot))
        elif case == 6 or case == 9:
            segments.append((p_top, p_bot))
        elif case == 7 or case == 8:
            segments.append((p_left, p_bot))
        elif case == 10:
            segments.append((p_left, p_bot))
            segments.append((p_top, p_right))

    return segments


def get_dose_slice_at_z(dose_data: dict, target_z: float) -> np.ndarray | None:
    """
    Возвращает 2D срез сетки дозы для заданной Z-координаты КТ-среза (линейная интерполяция по Z).
    """
    if not dose_data or "dose_grid" not in dose_data:
        return None
    z_pos = dose_data.get("z_positions")
    grid = dose_data.get("dose_grid")
    if z_pos is None or grid is None or len(z_pos) == 0:
        return None
    if len(z_pos) == 1:
        if abs(target_z - z_pos[0]) < 5.0:
            return grid[0]
        return None

    z_min, z_max = min(z_pos[0], z_pos[-1]), max(z_pos[0], z_pos[-1])
    if target_z < z_min - 3.0 or target_z > z_max + 3.0:
        return None

    if z_pos[1] >= z_pos[0]:
        idx = int(np.searchsorted(z_pos, target_z)) - 1
    else:
        idx = int(np.searchsorted(-z_pos, -target_z)) - 1

    idx = max(0, min(len(z_pos) - 2, idx))
    z0 = z_pos[idx]
    z1 = z_pos[idx + 1]
    dz = z1 - z0
    if abs(dz) < 1e-5:
        return grid[idx]

    t = (target_z - z0) / dz
    t = max(0.0, min(1.0, float(t)))
    return (1.0 - t) * grid[idx] + t * grid[idx + 1]


def create_dose_colormap_lut(alpha_base: int = 110) -> np.ndarray:
    """Создает 256x4 RGBA lookup table для гладкого медицинского цветового градиента дозы (Colorwash)."""
    # Опорные точки лучевой терапии: 0 -> 30% Rx, 64 -> 50% Rx, 128 -> 70% Rx, 160 -> 80% Rx, 195 -> 90% Rx, 215 -> 95% Rx, 235 -> 100% Rx, 255 -> 107%+ Rx
    ctrl_pts = np.array([
        [0,   0,   80,  255, alpha_base],
        [64,  0,   220, 255, alpha_base + 15],
        [128, 0,   230, 0,   alpha_base + 25],
        [160, 180, 255, 0,   alpha_base + 35],
        [195, 255, 220, 0,   alpha_base + 45],
        [215, 255, 120, 0,   alpha_base + 55],
        [235, 240, 0,   0,   alpha_base + 65],
        [255, 255, 0,   220, alpha_base + 75]
    ], dtype=np.float32)

    indices = np.arange(256, dtype=np.float32)
    lut = np.zeros((256, 4), dtype=np.uint8)
    for ch in range(4):
        lut[:, ch] = np.clip(np.interp(indices, ctrl_pts[:, 0], ctrl_pts[:, ch + 1]), 0, 255).astype(np.uint8)
    return lut


_DOSE_LUT = create_dose_colormap_lut(110)


def dose_slice_to_rgba(
    slice_grid: np.ndarray,
    rx_dose: float,
    max_dose: float,
    levels: list[dict] | None = None,
    enabled_levels: set[str] | None = None
) -> np.ndarray | None:
    """
    Преобразует 2D срез дозы в полупрозрачное RGBA-изображение (H, W, 4).
    Значения ниже 30% или отключенные через чекбоксы уровни изодоз полностью прозрачны (Alpha = 0).
    """
    if slice_grid is None or slice_grid.size == 0:
        return None

    rx = rx_dose if rx_dose > 0 else (max_dose * 0.9 if max_dose > 0 else 1.0)
    d_min = rx * 0.30
    d_max = max(rx * 1.07, max_dose if max_dose > 0 else rx)

    if d_max <= d_min:
        d_max = d_min + 1.0

    h, w = slice_grid.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)

    if levels and enabled_levels is not None:
        sorted_levels = sorted(levels, key=lambda x: float(x.get("val", 0.0)))
        mask = np.zeros((h, w), dtype=bool)
        for i, lvl in enumerate(sorted_levels):
            lvl_name = lvl["name"]
            if lvl_name in enabled_levels:
                low_val = float(lvl["val"])
                high_val = float(sorted_levels[i + 1]["val"]) if (i + 1 < len(sorted_levels)) else float("inf")
                mask |= (slice_grid >= low_val) & (slice_grid < high_val)
    else:
        mask = slice_grid >= d_min

    if not np.any(mask):
        return rgba

    norm = np.clip((slice_grid - d_min) / (d_max - d_min) * 255.0, 0, 255).astype(np.uint8)
    rgba[mask] = _DOSE_LUT[norm[mask]]
    return rgba
