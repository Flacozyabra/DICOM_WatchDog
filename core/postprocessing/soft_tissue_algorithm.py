"""
core.postprocessing.soft_tissue_algorithm — Soft-Tissue Contrast & Boundary Enhancement (v2).

Features:
- Modes:
  * "hard": High-contrast S-curve stretch (2.4x) + deep fascial cleft carving (W: 180, L: 40).
            Provides maximum anatomical demarcation of uterus, bladder, rectum, and bowel boundaries.
  * "normal": Balanced S-curve stretch (1.8x) + moderate edge enhancement (W: 220, L: 40).
- Optional 2.5D Z-filtering:
  * Applies inter-slice smoothing along the Z-axis when enabled.
- Full anatomical safety:
  * Air cavities (< -150 HU), pelvic bones, and titanium metal cores remain uncorrupted.
"""
from __future__ import annotations

import numpy as np
import scipy.ndimage


def guided_filter_2d(p: np.ndarray, r: int = 3, eps: float = 500.0) -> np.ndarray:
    """
    Edge-preserving Guided Filter using uniform box filters.
    Suppresses CT quantum noise while strictly preserving true organ boundaries.
    """
    size = 2 * r + 1
    mean_p = scipy.ndimage.uniform_filter(p, size=size)
    mean_p2 = scipy.ndimage.uniform_filter(p * p, size=size)
    var_p = mean_p2 - mean_p * mean_p
    a = var_p / (var_p + eps)
    b = mean_p - a * mean_p
    mean_a = scipy.ndimage.uniform_filter(a, size=size)
    mean_b = scipy.ndimage.uniform_filter(b, size=size)
    return mean_a * p + mean_b


def enhance_soft_tissue_slice_v2(
    img_hu: np.ndarray,
    neighbor_hu: list[np.ndarray] | None = None,
    mode: str = "hard",
    use_z_filter: bool = False
) -> np.ndarray:
    """
    Applies soft-tissue contrast expansion, guided denoising,
    and fascial cleft carving in the pelvic soft-tissue range.
    """
    if mode == "normal":
        eps = 350.0
        contrast_gain = 1.8
        contrast_span = 1.4
        edge_weight = 1.2
        blackhat_weight = 1.2
        blackhat_size = (3, 3)
    else:  # hard
        eps = 550.0
        contrast_gain = 2.4
        contrast_span = 1.7
        edge_weight = 2.0
        blackhat_weight = 2.2
        blackhat_size = (5, 5)

    center_hu = 40.0
    scale_hu = 22.0

    # 1. Soft tissue blending weights [-40 HU to 140 HU]
    w_low = np.clip((img_hu - (-40.0)) / 25.0, 0.0, 1.0)
    w_high = np.clip((140.0 - img_hu) / 50.0, 0.0, 1.0)
    st_weight = w_low * w_high

    # Never alter ambient scanner air (< -150) or high density bone/metal (> 400)
    st_weight[img_hu < -150.0] = 0.0
    st_weight[img_hu > 400.0] = 0.0

    if not np.any(st_weight > 0.01):
        return img_hu.copy()

    # 2. Source image: pure 2D by default, or optional 2.5D Z-filtering
    z_source = img_hu.copy()
    if use_z_filter and neighbor_hu and len(neighbor_hu) > 0:
        valid_neighbors = [n for n in neighbor_hu if n is not None and n.shape == img_hu.shape]
        st_z_cand = (img_hu > -60.0) & (img_hu < 120.0)
        if len(valid_neighbors) == 2:
            z_smooth = 0.70 * img_hu + 0.15 * valid_neighbors[0] + 0.15 * valid_neighbors[1]
            z_source[st_z_cand] = z_smooth[st_z_cand]
        elif len(valid_neighbors) == 1:
            z_smooth = 0.80 * img_hu + 0.20 * valid_neighbors[0]
            z_source[st_z_cand] = z_smooth[st_z_cand]

    # Step 3: Guided edge-preserving denoising
    denoised = guided_filter_2d(z_source, r=3, eps=eps)

    # Step 4: Non-linear S-curve contrast expansion around soft tissue baseline (40 HU)
    diff_c = (denoised - center_hu) / scale_hu
    stretched_st = center_hu + np.tanh(diff_c * contrast_gain) * (scale_hu * contrast_span)

    # Step 5: Multi-scale unsharp masking for organ boundaries
    blur_fine = scipy.ndimage.gaussian_filter(denoised, sigma=1.0)
    blur_coarse = scipy.ndimage.gaussian_filter(denoised, sigma=3.5)
    unsharp = blur_fine - blur_coarse

    # Step 6: Morphological black-hat carving for inter-organ fascial clefts
    closed = scipy.ndimage.grey_closing(denoised, size=blackhat_size)
    black_hat = np.clip(closed - denoised, 0.0, 30.0)

    # Combine enhanced soft tissue
    enhanced_st = stretched_st + edge_weight * unsharp - blackhat_weight * black_hat

    # Step 7: Seamless blend back into CT volume
    out_hu = img_hu * (1.0 - st_weight) + enhanced_st * st_weight

    # Strictly preserve physical titanium metal core (> 1500 HU)
    metal_mask = img_hu > 1500.0
    if np.any(metal_mask):
        out_hu[metal_mask] = img_hu[metal_mask]

    return out_hu
