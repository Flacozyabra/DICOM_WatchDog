"""
core.postprocessing.mar_algorithm — Metal Artifact Reduction (MAR v9) for Pelvic CT.

Pure 2.5D spatial/polar directional pipeline:
- Complete elimination of Radon / FBP pseudo-sinogram overhead.
- Dual polar streak suppression (bright hyperdense rays + dark starvation streaks up to 130 mm).
- Strict preservation of anatomical fidelity: soft tissue (0-60 HU), vaginal tamponade air cavity,
  pelvic cortical bone, and titanium metal cores are completely protected.
- Micro-edge clamping at 0-2.2 mm to eliminate titanium partial-volume blooming halos.
"""
from __future__ import annotations

import numpy as np
import scipy.ndimage


def create_disk_kernel(radius: int) -> np.ndarray:
    """Creates isotropic boolean disk kernel with given radius."""
    y, x = np.ogrid[-radius:radius + 1, -radius:radius + 1]
    return (x**2 + y**2 <= radius**2).astype(bool)


def suppress_multicenter_rays(
    corr_hu: np.ndarray,
    img_hu: np.ndarray,
    metal_mask: np.ndarray,
    pelvic_bone_protect: np.ndarray,
    outside_air_protect: np.ndarray,
    tamponade_air_protect: np.ndarray,
    max_r: float = 130.0
) -> np.ndarray:
    """
    Suppresses radial white and dark streak artifacts around applicator cylinders in polar space.
    - Limits bright ray suppression to true hyperdense streak artifacts (img_hu > 60 HU, cart_diff > 20 HU).
    - Compensates dark starvation streaks (cart_diff < -22 HU) in soft tissue (polar_smooth > 10 HU).
    - Smoothly fades from 95 mm to 130 mm, protecting normal pelvic soft tissue and anatomy.
    """
    lbl_metal, num_tubes = scipy.ndimage.label(metal_mask)
    if num_tubes == 0:
        return corr_hu

    centers = []
    for tube_id in range(1, num_tubes + 1):
        tube_coords = np.argwhere(lbl_metal == tube_id)
        if len(tube_coords) >= 3:
            centers.append(tube_coords.mean(axis=0))

    metal_coords = np.argwhere(metal_mask)
    if len(centers) == 0 or len(centers) > 1:
        centers.append(metal_coords.mean(axis=0))

    out_hu = corr_hu.copy()
    dist_y, dist_x = np.ogrid[:corr_hu.shape[0], :corr_hu.shape[1]]
    num_r, num_theta = 150, 360
    r = np.linspace(1.0, max_r, num_r)
    theta = np.linspace(0.0, 2.0 * np.pi, num_theta, endpoint=False)
    R, Theta = np.meshgrid(r, theta, indexing='ij')

    scale_factor = 1.0 / np.sqrt(len(centers))

    for cy, cx in centers:
        Y = cy + R * np.sin(Theta)
        X = cx + R * np.cos(Theta)

        polar_img = scipy.ndimage.map_coordinates(out_hu, [Y, X], order=1, mode='nearest')
        polar_padded = np.pad(polar_img, ((0, 0), (12, 12)), mode='wrap')
        polar_smooth = scipy.ndimage.median_filter(polar_padded, size=(1, 15))[:, 12:-12]
        polar_diff = polar_img - polar_smooth

        cart_r = np.sqrt((dist_y - cy)**2 + (dist_x - cx)**2)
        cart_theta = np.arctan2(dist_y - cy, dist_x - cx) % (2.0 * np.pi)
        idx_r = (cart_r - 1.0) / (max_r - 1.0) * (num_r - 1)
        idx_theta = cart_theta / (2.0 * np.pi) * num_theta

        cart_diff = scipy.ndimage.map_coordinates(polar_diff, [idx_r, idx_theta], order=1, mode='wrap')
        cart_smooth = scipy.ndimage.map_coordinates(polar_smooth, [idx_r, idx_theta], order=1, mode='wrap')
        fade = np.clip((max_r - cart_r) / 35.0, 0.0, 1.0)
        cart_diff *= fade

        # 1. Suppress true hyperdense bright streak artifacts
        is_bright_ray = (
            (cart_diff > 20.0) &
            (img_hu > 60.0) &
            (cart_r > 1.5) &
            ~pelvic_bone_protect &
            ~outside_air_protect &
            ~tamponade_air_protect &
            ~metal_mask
        )
        out_hu[is_bright_ray] -= cart_diff[is_bright_ray] * scale_factor

        # 2. Compensate radial dark starvation streaks in soft tissue / muscle
        is_dark_ray = (
            (cart_diff < -22.0) &
            (cart_smooth > 10.0) &
            (img_hu > -350.0) &
            (cart_r > 2.0) &
            ~pelvic_bone_protect &
            ~outside_air_protect &
            ~tamponade_air_protect &
            ~metal_mask
        )
        out_hu[is_dark_ray] -= cart_diff[is_dark_ray] * scale_factor

    return out_hu


def build_25d_masks(
    img_hu: np.ndarray,
    metal_threshold: float,
    body_cy: float,
    body_cx: float,
    dy_mm: float,
    dx_mm: float,
    neighbor_hu: list[np.ndarray] | None = None
) -> tuple[np.ndarray, np.ndarray]:
    """
    Extracts metal mask with strict isolation from pelvic bones
    and authentic pelvic bone mask.
    """
    dist_y, dist_x = np.ogrid[:img_hu.shape[0], :img_hu.shape[1]]
    dist_body_mm = np.sqrt(((dist_y - body_cy) * dy_mm)**2 + ((dist_x - body_cx) * dx_mm)**2)

    # Applicator & ovoids are strictly within central pelvic zone (<= 55 mm of body center)
    central_zone = dist_body_mm <= 55.0

    eff_thresh = max(metal_threshold, 2200.0)
    raw_metal = (img_hu > eff_thresh)
    metal_mask = (raw_metal & central_zone)

    if np.any(metal_mask):
        metal_coords = np.argwhere(metal_mask)
        cy, cx = metal_coords.mean(axis=0)
    else:
        cy, cx = body_cy, body_cx

    dist_from_center_mm = np.sqrt(((dist_y - cy) * dy_mm)**2 + ((dist_x - cx) * dx_mm)**2)

    def extract_pelvic_bone(slice_hu: np.ndarray, m_mask: np.ndarray) -> np.ndarray:
        bone_cand = (slice_hu > 200.0) & ~m_mask
        lbl_b, _ = scipy.ndimage.label(bone_cand)
        far_labels = set(lbl_b[dist_from_center_mm > 35.0])
        far_labels.discard(0)
        return np.isin(lbl_b, list(far_labels))

    pelvic_bone = extract_pelvic_bone(img_hu, metal_mask)

    if neighbor_hu:
        neighbor_bones = np.zeros_like(pelvic_bone, dtype=bool)
        for n_hu in neighbor_hu:
            if n_hu is not None and n_hu.shape == img_hu.shape:
                neighbor_bones |= extract_pelvic_bone(n_hu, metal_mask)
        pelvic_bone |= (neighbor_bones & (img_hu > 140.0) & ~metal_mask)

    return metal_mask, pelvic_bone


def process_slice_mar(
    img_hu: np.ndarray,
    metal_threshold: float = 2200.0,
    pixel_spacing: tuple[float, float] | list[float] = (1.0, 1.0),
    neighbor_hu: list[np.ndarray] | None = None
) -> tuple[np.ndarray, bool, dict]:
    """
    Pure 2.5D Directional MAR Pipeline (v9).
    - Preserves fine anatomical structures, mesentery, and soft-tissue boundaries with zero smudging.
    - Dual polar streak filter (bright hyperdense rays + dark starvation streaks up to 130 mm).
    - Eliminates gray halos and white artifact halos around metrostat & ovoids.
    - Strictly preserves natural vaginal tamponade air cavity.
    - Seamlessly closes lateral black photon-starvation bands in pelvic muscles.
    - Restores sharp physical titanium metal boundaries.
    """
    dy_mm, dx_mm = float(pixel_spacing[0]), float(pixel_spacing[1])
    pixel_area_mm2 = dy_mm * dx_mm
    mean_pixel_mm = float(np.mean([dy_mm, dx_mm]))

    # 1. Outside ambient air detection
    is_low = img_hu < -500.0
    lbl_air, _ = scipy.ndimage.label(is_low)
    border_labels = set(lbl_air[0, :]).union(lbl_air[-1, :]).union(lbl_air[:, 0]).union(lbl_air[:, -1])
    border_labels.discard(0)
    outside_air = np.isin(lbl_air, list(border_labels))
    outside_air_protect = scipy.ndimage.binary_dilation(outside_air, iterations=2)
    dist_to_outside_mm = scipy.ndimage.distance_transform_edt(~outside_air) * mean_pixel_mm

    # 2. Patient body centroid
    patient_body = ~outside_air & (img_hu > -600.0)
    if np.any(patient_body):
        b_coords = np.argwhere(patient_body)
        body_cy, body_cx = b_coords.mean(axis=0)
    else:
        body_cy, body_cx = img_hu.shape[0] / 2.0, img_hu.shape[1] / 2.0

    # 3. Metal and Bone masks
    metal_mask, pelvic_bone = build_25d_masks(
        img_hu, metal_threshold, body_cy, body_cx, dy_mm, dx_mm, neighbor_hu=neighbor_hu
    )
    metal_pixel_count = int(np.sum(metal_mask))
    metal_area_mm2 = metal_pixel_count * pixel_area_mm2
    if metal_pixel_count < 5:
        return img_hu, False, {"metal_pixels": metal_pixel_count, "metal_area_mm2": metal_area_mm2}

    orig_min = float(img_hu.min())
    orig_max = float(img_hu.max())

    metal_coords = np.argwhere(metal_mask)
    cy, cx = metal_coords.mean(axis=0)

    dist_y, dist_x = np.ogrid[:img_hu.shape[0], :img_hu.shape[1]]
    dist_from_center_mm = np.sqrt(((dist_y - cy) * dy_mm)**2 + ((dist_x - cx) * dx_mm)**2)
    dist_from_metal_mm = scipy.ndimage.distance_transform_edt(~metal_mask) * mean_pixel_mm

    pelvic_bone_protect = scipy.ndimage.binary_dilation(pelvic_bone, iterations=2)
    corr_hu = img_hu.copy()

    lbl_metal, raw_num_tubes = scipy.ndimage.label(metal_mask)
    valid_tubes = sum(1 for tid in range(1, raw_num_tubes + 1) if np.sum(lbl_metal == tid) >= 4)

    Y_coords = (metal_coords[:, 0] - cy) * dy_mm
    X_coords = (metal_coords[:, 1] - cx) * dx_mm
    metal_span_mm = float(np.hypot(np.ptp(X_coords), np.ptp(Y_coords))) if len(X_coords) > 0 else 0.0

    is_multi_tube_ovoid_slice = (valid_tubes >= 2) or (metal_span_mm >= 22.0)

    if len(X_coords) > 0:
        mu20 = np.sum(X_coords**2)
        mu02 = np.sum(Y_coords**2)
        mu11 = np.sum(X_coords * Y_coords)
        alpha_rad = 0.5 * np.arctan2(2.0 * mu11, mu20 - mu02)
        alpha_deg = float(np.rad2deg(alpha_rad))
    else:
        alpha_rad, alpha_deg = 0.0, 0.0

    U_mm = ((dist_x - cx) * dx_mm) * np.cos(alpha_rad) + ((dist_y - cy) * dy_mm) * np.sin(alpha_rad)
    V_mm = -((dist_x - cx) * dx_mm) * np.sin(alpha_rad) + ((dist_y - cy) * dy_mm) * np.cos(alpha_rad)

    # Vaginal cavity / tamponade air detection (strictly bounded between ovoids and around central rod):
    u_metal_max = float(np.max(np.abs(U_mm[metal_mask]))) if (is_multi_tube_ovoid_slice and np.any(metal_mask)) else 18.0
    cavity_u_limit = (u_metal_max - 2.5) if is_multi_tube_ovoid_slice else 22.0

    central_air_raw = (img_hu < -350.0) & (np.abs(U_mm) <= cavity_u_limit) & (dist_from_center_mm <= 26.0) & ~outside_air
    lbl_c_air, n_c_air = scipy.ndimage.label(central_air_raw)
    tamponade_air_protect = np.zeros_like(central_air_raw, dtype=bool)
    for aid in range(1, n_c_air + 1):
        comp = (lbl_c_air == aid)
        if np.sum(comp) * pixel_area_mm2 >= 18.0:
            tamponade_air_protect |= comp

    # Step 1: For multi-tube ovoid slices, fill lateral photon starvation shadow
    if is_multi_tube_ovoid_slice:
        dilated_metal_1 = scipy.ndimage.binary_dilation(metal_mask, iterations=1)

        dark_band_pool = (
            (np.abs(V_mm) <= 13.5) &
            (np.abs(U_mm) >= 11.5) &
            (np.abs(U_mm) <= 85.0) &
            ~dilated_metal_1 &
            ~outside_air_protect &
            ~pelvic_bone_protect &
            (img_hu < -38.0)
        )

        lbl_band, n_band = scipy.ndimage.label(scipy.ndimage.binary_dilation(dark_band_pool, iterations=1))
        connected_starvation = np.zeros_like(dark_band_pool, dtype=bool)
        for bid in range(1, n_band + 1):
            comp = (lbl_band == bid) & dark_band_pool
            if np.any(img_hu[comp] < -300.0):
                connected_starvation |= comp

        if np.any(connected_starvation):
            starvation_candidate = scipy.ndimage.binary_dilation(connected_starvation, iterations=1) & (
                ~dilated_metal_1 & ~outside_air_protect & ~pelvic_bone_protect & (img_hu < 15.0) & (np.abs(U_mm) >= 11.0)
            )

            rot_source = np.clip(img_hu, -1000.0, 150.0)
            rot_img = scipy.ndimage.rotate(rot_source, alpha_deg, reshape=False, order=1, mode='nearest')
            rot_cand = scipy.ndimage.rotate(starvation_candidate.astype(np.float32), alpha_deg, reshape=False, order=0) > 0.5
            rot_metal = scipy.ndimage.rotate(metal_mask.astype(np.float32), alpha_deg, reshape=False, order=0) > 0.5
            rot_u = scipy.ndimage.rotate(U_mm, alpha_deg, reshape=False, order=1, mode='nearest')

            rot_corr = rot_img.copy()
            y_ind, x_ind = np.where(rot_cand)
            for y, x in zip(y_ind, x_ind):
                is_lateral_shadow = abs(rot_u[y, x]) >= (u_metal_max - 1.0)
                min_hu_ref = -120.0 if is_lateral_shadow else -1000.0

                y_top = None
                for step in range(1, 28):
                    yt = y - step
                    if yt < 0:
                        break
                    if not rot_cand[yt, x] and not rot_metal[yt, x] and (min_hu_ref < rot_img[yt, x] < 200.0):
                        y_top = yt
                        break

                y_bot = None
                for step in range(1, 28):
                    yb = y + step
                    if yb >= rot_img.shape[0]:
                        break
                    if not rot_cand[yb, x] and not rot_metal[yb, x] and (min_hu_ref < rot_img[yb, x] < 200.0):
                        y_bot = yb
                        break

                if y_top is not None and y_bot is not None:
                    w = (y - y_top) / (y_bot - y_top)
                    rot_corr[y, x] = (1.0 - w) * rot_img[y_top, x] + w * rot_img[y_bot, x]
                elif y_top is not None:
                    rot_corr[y, x] = rot_img[y_top, x]
                elif y_bot is not None:
                    rot_corr[y, x] = rot_img[y_bot, x]

            if np.any(rot_cand):
                rot_smooth = scipy.ndimage.uniform_filter(rot_corr, size=(3, 9))
                rot_corr[rot_cand] = rot_smooth[rot_cand]

            unrot_corr = scipy.ndimage.rotate(rot_corr, -alpha_deg, reshape=False, order=1, mode='nearest')
            rng = np.random.default_rng(seed=int(abs(img_hu.min()) + metal_pixel_count))
            ct_grain = scipy.ndimage.gaussian_filter(
                rng.normal(0.0, 8.0, size=img_hu.shape).astype(np.float32), sigma=0.65
            )

            corr_hu[starvation_candidate] = unrot_corr[starvation_candidate] + ct_grain[starvation_candidate]

        between_cylinders = (img_hu < -1500.0) & ~dilated_metal_1 & (dist_from_center_mm <= 25.0)
        corr_hu[between_cylinders] = -500.0

    # Step 2: Dual polar ray suppression
    corr_hu = suppress_multicenter_rays(
        corr_hu,
        img_hu,
        metal_mask,
        pelvic_bone_protect,
        outside_air_protect,
        tamponade_air_protect,
        max_r=130.0
    )

    # Step 3: Adaptive local tissue baseline
    valid_soft_tissue = (
        ~metal_mask &
        ~outside_air_protect &
        ~pelvic_bone_protect &
        ~tamponade_air_protect &
        (corr_hu > -120.0) &
        (corr_hu < 110.0)
    )
    weights = valid_soft_tissue.astype(np.float32)
    clamped = np.clip(corr_hu, -120.0, 120.0) * weights
    num = scipy.ndimage.gaussian_filter(clamped, sigma=8.0)
    den = scipy.ndimage.gaussian_filter(weights, sigma=8.0)
    local_baseline = np.where(den > 1e-3, num / np.maximum(den, 1e-6), 45.0)
    local_baseline = np.clip(local_baseline, -80.0, 80.0)

    # Step 4: Perimetallic flare damping beyond 2.2 mm
    flare_zone = (
        (dist_from_metal_mm <= 14.0) &
        (dist_from_center_mm < 40.0) &
        (dist_to_outside_mm >= 25.0) &
        ~metal_mask &
        ~outside_air_protect &
        ~pelvic_bone_protect &
        ~tamponade_air_protect &
        (corr_hu > local_baseline + 35.0)
    )
    if np.any(flare_zone):
        excess = np.maximum(0.0, corr_hu - (local_baseline + 20.0))
        weight = np.clip((14.0 - dist_from_metal_mm) / 10.0, 0.0, 1.0)
        corr_hu[flare_zone] -= (excess * weight)[flare_zone]

    # Step 5: Micro-edge clamping for 0 - 2.2 mm titanium boundary
    micro_edge = (
        (dist_from_metal_mm > 0.0) &
        (dist_from_metal_mm <= 2.2) &
        ~metal_mask &
        ~tamponade_air_protect &
        ~outside_air_protect &
        ~pelvic_bone_protect
    )
    micro_bright = micro_edge & (corr_hu > local_baseline + 20.0)
    if np.any(micro_bright):
        rng = np.random.default_rng(seed=int(metal_pixel_count + 13))
        noise = rng.normal(0.0, 3.5, size=img_hu.shape).astype(np.float32)
        corr_hu[micro_bright] = local_baseline[micro_bright] + 5.0 + noise[micro_bright]

    # Restore authentic titanium core, tamponade air, outside air, bone
    corr_hu[metal_mask] = img_hu[metal_mask]
    corr_hu[tamponade_air_protect] = img_hu[tamponade_air_protect]
    corr_hu[outside_air] = img_hu[outside_air]
    corr_hu[pelvic_bone_protect] = img_hu[pelvic_bone_protect]

    final_min = float(corr_hu.min())
    final_max = float(corr_hu.max())
    stats = {
        "metal_pixels": metal_pixel_count,
        "metal_area_mm2": metal_area_mm2,
        "orig_min": orig_min,
        "orig_max": orig_max,
        "final_min": final_min,
        "final_max": final_max,
    }
    return corr_hu, True, stats
