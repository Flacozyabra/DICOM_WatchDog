# -*- coding: utf-8 -*-
"""
Plan Kinematics & Machine Deliverability Simulation Engine.

Evaluates Monaco VMAT / IMRT RTPLAN deliverability on Elekta linear accelerators,
detecting dose rate drop-outs, extreme MU/degree modulation, and gantry/MLC kinematic
conflicts that cause 'DOSE RATE MON' and related interlocks.
"""

import os
from typing import Optional, Dict, Any, List
import pydicom


class PlanKinematicsAnalyzer:
    """Parses and simulates machine kinematics for an RTPLAN file."""

    def __init__(
        self,
        plan_path: str,
        thresholds: Optional[Dict[str, float]] = None,
        is_ru: bool = True
    ):
        self.plan_path = plan_path
        self.is_ru = is_ru
        self.ds = pydicom.dcmread(plan_path, force=True)

        if thresholds is None:
            thresholds = {}
            try:
                from core.config_utils import get_config_path
                import json
                cp = get_config_path()
                if os.path.exists(cp):
                    with open(cp, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                        thresholds['min_dose_rate'] = float(cfg.get('plan_min_dose_rate', 60.0))
                        thresholds['min_mu_per_deg'] = float(cfg.get('plan_min_mu_per_deg', 0.165))
                        thresholds['max_modulation_factor'] = float(cfg.get('plan_max_modulation_factor', 10.0))
                        thresholds['warn_dose_rate'] = float(cfg.get('plan_warn_dose_rate', 75.0))
                        thresholds['warn_mu_per_deg'] = float(cfg.get('plan_warn_mu_per_deg', 0.200))
                        thresholds['warn_modulation_factor'] = float(cfg.get('plan_warn_modulation_factor', 8.0))
            except Exception:
                pass

        self.min_dose_rate = float(thresholds.get('min_dose_rate', 60.0))
        self.min_mu_per_deg = float(thresholds.get('min_mu_per_deg', 0.165))
        self.max_modulation_factor = float(thresholds.get('max_modulation_factor', 10.0))
        self.warn_dose_rate = float(thresholds.get('warn_dose_rate', 75.0))
        self.warn_mu_per_deg = float(thresholds.get('warn_mu_per_deg', 0.200))
        self.warn_modulation_factor = float(thresholds.get('warn_modulation_factor', 8.0))

        self.plan_label = str(getattr(self.ds, 'RTPlanLabel', '') or getattr(self.ds, 'RTPlanName', 'Unnamed Plan'))
        self.plan_date = str(getattr(self.ds, 'RTPlanDate', ''))
        self.patient_name = str(getattr(self.ds, 'PatientName', 'Unknown'))
        self.patient_id = str(getattr(self.ds, 'PatientID', 'Unknown'))

        # Software and Manufacturer detection
        manufacturer = str(getattr(self.ds, 'Manufacturer', ''))
        software = str(getattr(self.ds, 'SoftwareVersions', ''))
        model = str(getattr(self.ds, 'ManufacturerModelName', ''))
        desc = str(getattr(self.ds, 'RTPlanDescription', ''))

        self.manufacturer = manufacturer
        self.software_version = software
        combined_text = f"{manufacturer} {software} {model} {desc}".upper()

        self.is_monaco = ('CMS' in combined_text) or ('MONACO' in combined_text)
        if self.is_monaco:
            ver_str = software if software else "5.x"
            self.tps_name = f"Elekta Monaco ({ver_str})"
        else:
            name_parts = [p for p in (manufacturer, software) if p]
            self.tps_name = " ".join(name_parts) if name_parts else "Unknown TPS"

        # Prescription & Fractions
        self.num_fractions = 1
        self.beam_mu_map: Dict[int, float] = {}
        frac_group = self.ds.FractionGroupSequence[0] if 'FractionGroupSequence' in self.ds and self.ds.FractionGroupSequence else None
        if frac_group:
            self.num_fractions = int(getattr(frac_group, 'NumberOfFractionsPlanned', 1) or 1)
            for ref_beam in getattr(frac_group, 'ReferencedBeamSequence', []):
                b_num = getattr(ref_beam, 'ReferencedBeamNumber', None)
                mu = getattr(ref_beam, 'BeamMeterset', None)
                if b_num is not None and mu is not None:
                    try:
                        self.beam_mu_map[int(b_num)] = float(mu)
                    except ValueError:
                        pass

        # Parse beams
        self.beams: List[Dict[str, Any]] = []
        for b in getattr(self.ds, 'BeamSequence', []):
            analyzed_beam = self._analyze_beam(b)
            if analyzed_beam:
                self.beams.append(analyzed_beam)

    def _analyze_beam(self, b) -> Optional[Dict[str, Any]]:
        b_num = int(getattr(b, 'BeamNumber', 0))
        b_name = str(getattr(b, 'BeamName', '') or f"Beam {b_num}")
        b_desc = str(getattr(b, 'BeamDescription', ''))
        b_type = str(getattr(b, 'BeamType', 'STATIC')).upper()
        rad_type = str(getattr(b, 'RadiationType', 'PHOTON'))
        machine_name = str(getattr(b, 'TreatmentMachineName', 'ELEKTA'))

        cps = getattr(b, 'ControlPointSequence', [])
        if not cps:
            return None

        cp0 = cps[0]
        energy = getattr(cp0, 'NominalBeamEnergy', '6.0')
        final_weight = float(getattr(b, 'FinalCumulativeMetersetWeight', 1.0) or 1.0)
        total_mu = self.beam_mu_map.get(b_num, final_weight)

        # Jaw Positions from CP0
        jaws_x = None
        jaws_y = None
        for bld in getattr(cp0, 'BeamLimitingDevicePositionSequence', []):
            dev_type = str(getattr(bld, 'RTBeamLimitingDeviceType', '')).upper()
            pos = [float(p) for p in getattr(bld, 'LeafJawPositions', [])]
            if 'X' in dev_type and 'MLC' not in dev_type:
                jaws_x = pos
            elif 'Y' in dev_type:
                jaws_y = pos

        # Calculate rotation span to check if it's VMAT
        total_gantry_travel = 0.0
        for i in range(len(cps) - 1):
            g1 = float(getattr(cps[i], 'GantryAngle', 0.0))
            g2 = float(getattr(cps[i+1], 'GantryAngle', 0.0))
            dg = abs(g2 - g1)
            if dg > 180:
                dg = 360 - dg
            total_gantry_travel += dg

        # A beam is an arc (VMAT) only if the gantry actually rotates
        g_rot_dir = str(getattr(cp0, 'GantryRotationDirection', 'NONE')).upper()
        is_vmat = (total_gantry_travel > 5.0) and (g_rot_dir in ('CW', 'CCW', 'CC') or total_gantry_travel >= 10.0)
        fixed_gantry_angle = float(getattr(cp0, 'GantryAngle', 0.0))
        beam_mode = 'VMAT' if is_vmat else ('Static IMRT' if len(cps) > 2 else 'Static 3D-CRT')

        # Control points evaluation
        intervals: List[Dict[str, Any]] = []
        critical_count = 0
        warning_count = 0

        # Elekta physical parameters
        max_gantry_speed = 6.0   # deg/s (1.0 RPM)
        safe_leaf_speed = 35.0   # mm/s continuous leaf speed for Agility
        max_dose_rate = 600.0    # MU/min nominal for flattened 6 MV
        min_stable_dose_rate = self.min_dose_rate
        min_density_limit = self.min_mu_per_deg
        max_mod_jump = self.max_modulation_factor
        warn_dose_rate = self.warn_dose_rate
        warn_density_limit = self.warn_mu_per_deg
        warn_mod_jump = self.warn_modulation_factor

        mu_per_deg_list = []
        est_dose_rates = []

        prev_gantry_speed = 0.0
        pass_idx = 0
        prev_dir = None

        for i in range(len(cps) - 1):
            cp_curr = cps[i]
            cp_next = cps[i+1]

            w_curr = float(cp_curr.CumulativeMetersetWeight) / final_weight * total_mu
            w_next = float(cp_next.CumulativeMetersetWeight) / final_weight * total_mu
            d_mu = max(0.0, w_next - w_curr)

            g_curr = float(cp_curr.GantryAngle)
            g_next = float(cp_next.GantryAngle)
            diff_g = abs(g_next - g_curr)
            if diff_g > 180:
                diff_g = 360 - diff_g

            # Gantry direction and multi-pass (reversal) detection
            rot_dir = getattr(cp_curr, 'GantryRotationDirection', '')
            if not rot_dir or rot_dir == 'NONE':
                d_raw = (g_next - g_curr) % 360.0
                if 0.001 < d_raw <= 180.0:
                    rot_dir = 'CW'
                elif d_raw > 180.0 and (360.0 - d_raw) > 0.001:
                    rot_dir = 'CC'
                else:
                    rot_dir = prev_dir or 'CW'

            if rot_dir in ('CCW', 'CC'):
                rot_dir = 'CC'

            if prev_dir is not None and rot_dir != prev_dir and diff_g > 0.1:
                pass_idx += 1
            prev_dir = rot_dir

            # Modulation density (MU per degree)
            if is_vmat and diff_g > 0.01:
                mu_per_deg = d_mu / diff_g
                if d_mu > 0.001:
                    mu_per_deg_list.append(mu_per_deg)
            else:
                mu_per_deg = 0.0

            # Max MLC displacement
            mlc_curr = []
            mlc_next = []
            for bld in getattr(cp_curr, 'BeamLimitingDevicePositionSequence', []):
                if 'MLC' in str(getattr(bld, 'RTBeamLimitingDeviceType', '')).upper():
                    mlc_curr = [float(p) for p in bld.LeafJawPositions]
                    break
            for bld in getattr(cp_next, 'BeamLimitingDevicePositionSequence', []):
                if 'MLC' in str(getattr(bld, 'RTBeamLimitingDeviceType', '')).upper():
                    mlc_next = [float(p) for p in bld.LeafJawPositions]
                    break

            max_leaf_disp = 0.0
            if mlc_curr and mlc_next and len(mlc_curr) == len(mlc_next):
                max_leaf_disp = max([abs(p2 - p1) for p1, p2 in zip(mlc_curr, mlc_next)] or [0.0])

            # Timing and kinematics
            if is_vmat and diff_g > 0.01:
                time_gantry = diff_g / max_gantry_speed
                time_leaf = max_leaf_disp / safe_leaf_speed
                step_time = max(time_gantry, time_leaf, 0.1)

                # Raw required dose rate
                raw_dr = (d_mu / step_time) * 60.0

                # Linac cannot exceed max_dose_rate
                if raw_dr > max_dose_rate:
                    step_time = (d_mu / max_dose_rate) * 60.0
                    est_dr = max_dose_rate
                    gantry_speed = diff_g / step_time if step_time > 0 else 0.0
                else:
                    est_dr = raw_dr
                    gantry_speed = diff_g / step_time
            else:
                # Static segment or beam-off turnaround
                time_leaf = max_leaf_disp / safe_leaf_speed
                step_time = max(d_mu / max_dose_rate * 60.0 if d_mu > 0 else 0.0, time_leaf, 0.1)
                est_dr = max_dose_rate if d_mu > 0 else 0.0
                gantry_speed = 0.0

            if d_mu > 0.01:
                est_dose_rates.append(est_dr)

            # Gantry acceleration demand (informative, smoothed by linac controller)
            gantry_accel = 0.0
            if i > 0 and step_time > 0:
                gantry_accel = abs(gantry_speed - prev_gantry_speed) / step_time
            prev_gantry_speed = gantry_speed

            # Risk classification
            reasons = []
            risk_level = 'OK'

            if is_vmat:
                # When beam is OFF (e.g. dual-arc turnaround or pause), no deliverability error
                if d_mu < 0.01:
                    pass
                else:
                    # 1. Low Dose Rate Drop-out (Causes DOSE RATE MON on Elekta strictly below min_stable_dose_rate)
                    if round(est_dr) < min_stable_dose_rate or mu_per_deg < (min_density_limit - 0.005):
                        if self.is_ru:
                            reasons.append(f"Мощность дозы ({est_dr:.1f} MU/мин, {mu_per_deg:.2f} MU/deg) ниже порога стабильности (< {min_stable_dose_rate:.0f} MU/мин, < {min_density_limit:.3f} MU/deg)")
                        else:
                            reasons.append(f"Dose rate ({est_dr:.1f} MU/min, {mu_per_deg:.2f} MU/deg) below stability limit (< {min_stable_dose_rate:.0f} MU/min, < {min_density_limit:.3f} MU/deg)")
                        risk_level = 'CRITICAL'
                    elif est_dr < warn_dose_rate or mu_per_deg < warn_density_limit:
                        if self.is_ru:
                            reasons.append(f"Пониженная плотность/мощность дозы ({mu_per_deg:.2f} MU/deg, ~{est_dr:.0f} MU/мин)")
                        else:
                            reasons.append(f"Low dose density/rate ({mu_per_deg:.2f} MU/deg, ~{est_dr:.0f} MU/min)")
                        if risk_level != 'CRITICAL':
                            risk_level = 'WARNING'

                    # 2. Extreme Modulation Shock (Severe jumps between adjacent active CPs)
                    if len(mu_per_deg_list) > 1:
                        prev_mpd = mu_per_deg_list[-2]
                        if prev_mpd > 0.01 and mu_per_deg > 0.01:
                            factor = max(mu_per_deg, prev_mpd) / min(mu_per_deg, prev_mpd)
                            if factor >= max_mod_jump:
                                if self.is_ru:
                                    reasons.append(f"Экстремальный перепад плотности дозы в {factor:.1f}× ({prev_mpd:.2f} → {mu_per_deg:.2f} MU/deg)")
                                else:
                                    reasons.append(f"Extreme dose density jump {factor:.1f}× ({prev_mpd:.2f} → {mu_per_deg:.2f} MU/deg)")
                                risk_level = 'CRITICAL'
                            elif factor >= warn_mod_jump:
                                if self.is_ru:
                                    reasons.append(f"Резкий перепад плотности дозы в {factor:.1f}× ({prev_mpd:.2f} → {mu_per_deg:.2f} MU/deg)")
                                else:
                                    reasons.append(f"Sharp dose density jump {factor:.1f}× ({prev_mpd:.2f} → {mu_per_deg:.2f} MU/deg)")
                                if risk_level != 'CRITICAL':
                                    risk_level = 'WARNING'

                    # 3. High leaf movement speed during active beam
                    leaf_speed = max_leaf_disp / step_time if step_time > 0 else 0
                    if leaf_speed > 65.0:
                        if self.is_ru:
                            reasons.append(f"Конфликт MLC: скорость лепестков {leaf_speed:.0f} мм/с превышает лимит Agility (65 мм/с)")
                        else:
                            reasons.append(f"MLC conflict: leaf speed {leaf_speed:.0f} mm/s exceeds Agility limit (65 mm/s)")
                        risk_level = 'CRITICAL'
                    elif leaf_speed > 48.0:
                        if self.is_ru:
                            reasons.append(f"Высокая скорость движения лепестков ({leaf_speed:.0f} мм/с)")
                        else:
                            reasons.append(f"High leaf motion speed ({leaf_speed:.0f} mm/s)")
                        if risk_level != 'CRITICAL':
                            risk_level = 'WARNING'

                    # 4. Heavy MU/deg modulation (gantry deceleration)
                    if mu_per_deg >= 15.0:
                        if self.is_ru:
                            reasons.append(f"Высокая плотность дозы ({mu_per_deg:.1f} MU/deg, замедление гентри до {gantry_speed:.1f}°/с)")
                        else:
                            reasons.append(f"High dose density ({mu_per_deg:.1f} MU/deg, gantry deceleration to {gantry_speed:.1f}°/s)")
                        if risk_level != 'CRITICAL':
                            risk_level = 'WARNING'

            if risk_level == 'CRITICAL':
                critical_count += 1
            elif risk_level == 'WARNING':
                warning_count += 1

            intervals.append({
                'index': i,
                'gantry_start': g_curr,
                'gantry_end': g_next,
                'delta_gantry': diff_g,
                'mu_start': w_curr,
                'mu_end': w_next,
                'delta_mu': d_mu,
                'mu_per_deg': mu_per_deg,
                'max_leaf_disp_mm': max_leaf_disp,
                'est_step_time_s': step_time,
                'est_dose_rate': est_dr,
                'gantry_speed': gantry_speed,
                'gantry_accel': gantry_accel,
                'risk_level': risk_level,
                'reasons': reasons,
                'pass_idx': pass_idx,
                'direction': rot_dir
            })

        # Overall beam verdict
        if not is_vmat:
            verdict = 'STATIC'
        elif critical_count >= 1:
            verdict = 'CRITICAL'
        elif warning_count >= 4:
            verdict = 'WARNING'
        else:
            verdict = 'OK'

        # Collect summary per pass
        passes_info = []
        if is_vmat and intervals:
            for p_i in range(pass_idx + 1):
                p_items = [it for it in intervals if it.get('pass_idx') == p_i]
                if p_items:
                    passes_info.append({
                        'pass_idx': p_i,
                        'start_angle': p_items[0]['gantry_start'],
                        'end_angle': p_items[-1]['gantry_end'],
                        'direction': p_items[0].get('direction', 'CW')
                    })

        return {
            'beam_number': b_num,
            'beam_name': b_name,
            'beam_description': b_desc,
            'beam_type': b_type,
            'radiation_type': rad_type,
            'machine_name': machine_name,
            'energy_mv': energy,
            'total_mu': total_mu,
            'num_control_points': len(cps),
            'total_gantry_travel': total_gantry_travel,
            'fixed_gantry_angle': fixed_gantry_angle,
            'beam_mode': beam_mode,
            'is_vmat': is_vmat,
            'num_passes': pass_idx + 1 if is_vmat else 1,
            'passes_info': passes_info,
            'jaws_x': jaws_x,
            'jaws_y': jaws_y,
            'intervals': intervals,
            'min_mu_per_deg': min(mu_per_deg_list) if (is_vmat and mu_per_deg_list) else 0.0,
            'max_mu_per_deg': max(mu_per_deg_list) if (is_vmat and mu_per_deg_list) else 0.0,
            'avg_mu_per_deg': (sum(mu_per_deg_list) / len(mu_per_deg_list)) if (is_vmat and mu_per_deg_list) else 0.0,
            'min_dose_rate': min(est_dose_rates) if est_dose_rates else 0.0,
            'max_dose_rate': max(est_dose_rates) if est_dose_rates else 0.0,
            'critical_count': critical_count,
            'warning_count': warning_count,
            'verdict': verdict,
            'thresholds': {
                'min_dose_rate': self.min_dose_rate,
                'min_mu_per_deg': self.min_mu_per_deg,
                'max_modulation_factor': self.max_modulation_factor,
                'warn_dose_rate': self.warn_dose_rate,
                'warn_mu_per_deg': self.warn_mu_per_deg,
                'warn_modulation_factor': self.warn_modulation_factor,
            }
        }
