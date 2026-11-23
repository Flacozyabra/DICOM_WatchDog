"""
core.postprocessing.soft_tissue_algorithm — Soft-Tissue Contrast & Boundary Enhancement.

Available algorithms / modes:
- "normal"        : Legacy Guided Filter + tanh S-curve (gain 1.8x) + DoG + Black-Hat (W: 220, L: 40).
- "hard"          : Legacy Guided Filter + tanh S-curve (gain 2.4x) + DoG + Black-Hat (W: 180, L: 40).
- "clamped_v2"    : Halo-free v2 with pre-clamping [-135, +150] HU and range-gated 2.5D Z smoothing.
- "musica_normal" : Voronoi-padded 4-band MUSICA pyramid + 2.5D Z + 3x3 bilateral (balanced contrast).
- "musica_hard"   : Voronoi-padded 4-band MUSICA pyramid + 2.5D Z + 3x3 bilateral (strong pelvic contrast).
- "patch_nlm"     : 2.5D Non-Local Means (3x3 patches, 5x5 search + +/-1 Z) + masked multi-scale detail.

Anatomical safety:
- Ambient/luminal gas (< -150 HU), pelvic bones (> 300-400 HU), and titanium metal cores (> 1500 HU)
  remain preserved without boundary corruption.
"""
from __future__ import annotations

import numpy as np
import scipy.ndimage


SOFT_TISSUE_MODES = (
    "normal",
    "hard",
    "clamped_v2",
    "musica_normal",
    "musica_hard",
    "patch_nlm",
)


def guided_filter_2d(p: np.ndarray, r: int = 3, eps: float = 500.0) -> np.ndarray:
    """
    Edge-preserving Guided Filter using uniform box filters.
    Suppresses CT quantum noise while preserving strong organ boundaries.
    """
    src = p.astype(np.float32, copy=False)
    size = 2 * r + 1
    mean_p = scipy.ndimage.uniform_filter(src, size=size)
    mean_p2 = scipy.ndimage.uniform_filter(src * src, size=size)
    # Clamp variance >= 0 to prevent float32 catastrophic cancellation near high gradients
    var_p = np.maximum(0.0, mean_p2 - mean_p * mean_p)
    a = var_p / (var_p + eps)
    b = mean_p - a * mean_p
    mean_a = scipy.ndimage.uniform_filter(a, size=size)
    mean_b = scipy.ndimage.uniform_filter(b, size=size)
    return (mean_a * src + mean_b).astype(np.float32, copy=False)


def _soft_tissue_blend_weight(
    img_hu: np.ndarray,
    lo_start: float,
    lo_span: float,
    hi_end: float,
    hi_span: float,
    hard_min: float = -150.0,
    hard_max: float = 300.0,
) -> np.ndarray:
    """Compute smooth soft-tissue blending mask excluding air/gas and high-density bone/metal."""
    w_low = np.clip((img_hu - lo_start) / lo_span, 0.0, 1.0)
    w_high = np.clip((hi_end - img_hu) / hi_span, 0.0, 1.0)
    weight = (w_low * w_high).astype(np.float32, copy=False)
    weight[(img_hu < hard_min) | (img_hu > hard_max)] = 0.0
    return weight


def _restore_metal_core(out_hu: np.ndarray, orig_hu: np.ndarray) -> np.ndarray:
    """Restore physical titanium applicator / high-Z metal voxels untouched (> 1500 HU)."""
    metal_mask = orig_hu > 1500.0
    if np.any(metal_mask):
        out_hu[metal_mask] = orig_hu[metal_mask]
    return out_hu.astype(np.float32, copy=False)


def _resolve_pixel_scale(pixel_spacing: list[float] | tuple[float, ...] | None) -> float:
    """Normalize spatial filter radii relative to a nominal 0.95 mm pelvic pixel pitch."""
    try:
        sp = float(pixel_spacing[0]) if pixel_spacing else 0.95
    except (TypeError, ValueError, IndexError):
        sp = 0.95
    if sp <= 0.1 or sp > 5.0:
        sp = 0.95
    return 0.95 / max(0.45, sp)


def _range_gated_z_smooth(
    img_hu: np.ndarray,
    neighbor_hu: list[np.ndarray] | None,
    sigma_z: float = 22.0,
    max_alpha: float = 0.30,
) -> np.ndarray:
    """
    Range-gated inter-slice Z smoothing inside soft tissue.
    Prevents gas pockets or moving loops on adjacent slices from bleeding dark artifacts.
    """
    cur = img_hu.astype(np.float32, copy=True)
    if not neighbor_hu:
        return cur
    valid_nbrs = [
        np.asarray(n, dtype=np.float32)
        for n in neighbor_hu
        if n is not None and getattr(n, "shape", None) == img_hu.shape
    ]
    if not valid_nbrs:
        return cur

    st_mask = (cur > -140.0) & (cur < 160.0)
    acc = cur.copy()
    w_sum = np.ones_like(cur, dtype=np.float32)
    inv_2s2 = np.float32(1.0 / (2.0 * sigma_z * sigma_z))
    alpha_f = np.float32(max_alpha)

    for nbr in valid_nbrs:
        nbr_valid = st_mask & (nbr > -145.0) & (nbr < 165.0)
        diff = nbr - cur
        wt = np.where(nbr_valid, alpha_f * np.exp(-(diff * diff) * inv_2s2), 0.0).astype(np.float32)
        acc += wt * nbr
        w_sum += wt
    return (acc / w_sum).astype(np.float32, copy=False)


def _separable_bilateral_clamped(
    img: np.ndarray,
    lo: float = -135.0,
    hi: float = 150.0,
    radius: int = 1,
    sigma_s: float = 1.0,
    sigma_r: float = 12.5,
) -> np.ndarray:
    """
    Fast separable (Y then X) bilateral filter masked to soft tissue [lo, hi].
    Out-of-range air/bone pixels receive zero spatial weight so no boundary halos form.
    """
    src = np.clip(img, lo, hi).astype(np.float32, copy=False)
    valid = ((img >= lo - 15.0) & (img <= hi + 25.0)).astype(np.float32)
    h, w = src.shape
    inv_2ss = np.float32(1.0 / (2.0 * sigma_s * sigma_s))
    inv_2sr = np.float32(1.0 / (2.0 * sigma_r * sigma_r))

    def _pass_1d(arr: np.ndarray, axis: int) -> np.ndarray:
        if axis == 0:
            pad_a = np.pad(arr, ((radius, radius), (0, 0)), mode="edge")
            pad_v = np.pad(valid, ((radius, radius), (0, 0)), mode="constant", constant_values=0.0)
        else:
            pad_a = np.pad(arr, ((0, 0), (radius, radius)), mode="edge")
            pad_v = np.pad(valid, ((0, 0), (radius, radius)), mode="constant", constant_values=0.0)

        num = arr * valid
        den = valid.copy()
        for d in range(-radius, radius + 1):
            if d == 0:
                continue
            ws = np.float32(np.exp(-(d * d) * inv_2ss))
            off = radius + d
            if axis == 0:
                nbr = pad_a[off:off + h, :]
                vn = pad_v[off:off + h, :]
            else:
                nbr = pad_a[:, off:off + w]
                vn = pad_v[:, off:off + w]
            diff = nbr - arr
            wt = (ws * np.exp(-(diff * diff) * inv_2sr)) * vn
            num += wt * nbr
            den += wt
        return np.where(den > 1e-4, num / np.maximum(den, 1e-6), arr).astype(np.float32, copy=False)

    mid = _pass_1d(src, axis=0)
    return _pass_1d(mid, axis=1)


def _masked_gaussian(arr: np.ndarray, mask: np.ndarray, sigma: float) -> np.ndarray:
    """Normalized convolution excluding masked air/bone pixels from smoothing support."""
    num = scipy.ndimage.gaussian_filter(arr * mask, sigma=sigma)
    den = scipy.ndimage.gaussian_filter(mask, sigma=sigma)
    return np.where(den > 1e-4, num / np.maximum(den, 1e-6), arr).astype(np.float32, copy=False)


def _voronoi_fill_invalid(work: np.ndarray, valid_mask: np.ndarray) -> np.ndarray:
    """
    Extend valid soft-tissue edge values into gas/bone regions before Gaussian pyramids
    so multi-scale bands stay completely free of air/metal overshoot halos.
    """
    if np.all(valid_mask) or not np.any(valid_mask):
        return work
    _, indices = scipy.ndimage.distance_transform_edt(~valid_mask, return_indices=True)
    return work[indices[0], indices[1]]


def _musica_band_boost(
    band: np.ndarray,
    gain: float,
    p: float,
    ref: float,
    coring: float = 0.0,
) -> np.ndarray:
    """Sub-linear MUSICA contrast amplification with soft noise coring."""
    amp = np.abs(band)
    eff = np.maximum(0.0, amp - coring) if coring > 0.0 else amp
    ratio = eff / np.float32(ref)
    nl = np.float32(ref * gain) * np.power(np.maximum(ratio, np.float32(1e-6)), np.float32(p))
    nl = np.minimum(nl, eff * np.float32(gain * 1.35))
    return (np.sign(band) * nl).astype(np.float32, copy=False)


def _pelvic_tone_curve(hu: np.ndarray, gain: float = 1.85, center: float = 38.0) -> np.ndarray:
    """Dual-zone tone curve expanding [0..75 HU] bowel/uterus separation while keeping fat legible."""
    scale = np.float32(26.0)
    s_core = np.float32(center) + np.tanh(((hu - np.float32(center)) / scale) * np.float32(gain)) * np.float32(26.0 * 1.45)
    mapped = np.float32(0.72) * s_core + np.float32(0.28) * hu
    fat = hu < -25.0
    if np.any(fat):
        fat_mapped = np.float32(-25.0) + (hu + np.float32(25.0)) * np.float32(0.50)
        mapped = np.where(fat, np.float32(0.55) * fat_mapped + np.float32(0.45) * mapped, mapped)
    return mapped.astype(np.float32, copy=False)


def _enhance_legacy_v2(
    img_hu: np.ndarray,
    neighbor_hu: list[np.ndarray] | None,
    mode: str,
    use_z_filter: bool,
) -> np.ndarray:
    """Original v2 Guided + S-Curve + DoG + Black-Hat algorithm preserved unchanged."""
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

    st_weight = _soft_tissue_blend_weight(
        img_hu, lo_start=-40.0, lo_span=25.0, hi_end=140.0, hi_span=50.0, hard_min=-150.0, hard_max=400.0
    )
    if not np.any(st_weight > 0.01):
        return img_hu.copy()

    z_source = img_hu.copy()
    if use_z_filter and neighbor_hu:
        valid_neighbors = [
            np.asarray(n, dtype=np.float32)
            for n in neighbor_hu
            if n is not None and getattr(n, "shape", None) == img_hu.shape
        ]
        st_z_cand = (img_hu > -60.0) & (img_hu < 120.0)
        if len(valid_neighbors) == 2:
            z_smooth = 0.70 * img_hu + 0.15 * valid_neighbors[0] + 0.15 * valid_neighbors[1]
            z_source[st_z_cand] = z_smooth[st_z_cand]
        elif len(valid_neighbors) == 1:
            z_smooth = 0.80 * img_hu + 0.20 * valid_neighbors[0]
            z_source[st_z_cand] = z_smooth[st_z_cand]

    denoised = guided_filter_2d(z_source, r=3, eps=eps)
    diff_c = (denoised - center_hu) / scale_hu
    stretched_st = center_hu + np.tanh(diff_c * contrast_gain) * (scale_hu * contrast_span)

    blur_fine = scipy.ndimage.gaussian_filter(denoised, sigma=1.0)
    blur_coarse = scipy.ndimage.gaussian_filter(denoised, sigma=3.5)
    unsharp = blur_fine - blur_coarse

    closed = scipy.ndimage.grey_closing(denoised, size=blackhat_size)
    black_hat = np.clip(closed - denoised, 0.0, 30.0)

    enhanced_st = stretched_st + edge_weight * unsharp - blackhat_weight * black_hat
    out_hu = img_hu * (1.0 - st_weight) + enhanced_st * st_weight
    return _restore_metal_core(out_hu, img_hu)


def _enhance_clamped_v2(img_hu: np.ndarray, neighbor_hu: list[np.ndarray] | None) -> np.ndarray:
    """Halo-free v2: soft-tissue pre-clamping [-135, +150] HU + range-gated 2.5D Z."""
    st_weight = _soft_tissue_blend_weight(img_hu, lo_start=-125.0, lo_span=35.0, hi_end=150.0, hi_span=45.0)
    if not np.any(st_weight > 0.01):
        return img_hu.copy()

    z_src = _range_gated_z_smooth(img_hu, neighbor_hu, sigma_z=26.0, max_alpha=0.25)
    clamped = np.clip(z_src, -135.0, 150.0).astype(np.float32, copy=False)
    denoised = guided_filter_2d(clamped, r=2, eps=190.0)

    center_hu, scale_hu, gain, span = 35.0, 32.0, 1.85, 1.9
    s_curve = center_hu + np.tanh(((denoised - center_hu) / scale_hu) * gain) * (scale_hu * span)
    stretched = 0.72 * s_curve + 0.28 * denoised

    blur_fine = scipy.ndimage.gaussian_filter(denoised, sigma=0.9)
    blur_coarse = scipy.ndimage.gaussian_filter(denoised, sigma=2.8)
    unsharp = np.clip(blur_fine - blur_coarse, -16.0, 16.0)

    closed = scipy.ndimage.grey_closing(denoised, size=(5, 5))
    black_hat = np.clip(closed - denoised - 2.5, 0.0, 18.0)

    enh = stretched + 1.35 * unsharp - 1.25 * black_hat
    out_hu = img_hu * (1.0 - st_weight) + enh * st_weight
    return _restore_metal_core(out_hu, img_hu)


def _enhance_musica(
    img_hu: np.ndarray,
    neighbor_hu: list[np.ndarray] | None,
    pixel_spacing: list[float] | tuple[float, ...] | None,
    is_hard: bool,
) -> np.ndarray:
    """Multi-scale Voronoi-padded MUSICA pyramid with 2.5D Z and fat-safe macro contrast."""
    scale_px = _resolve_pixel_scale(pixel_spacing)
    st_weight = _soft_tissue_blend_weight(img_hu, lo_start=-125.0, lo_span=35.0, hi_end=150.0, hi_span=45.0)
    if not np.any(st_weight > 0.01):
        return img_hu.copy()

    z_src = _range_gated_z_smooth(img_hu, neighbor_hu, sigma_z=20.0, max_alpha=0.34)
    den = _separable_bilateral_clamped(z_src, lo=-135.0, hi=150.0, radius=1, sigma_s=1.0, sigma_r=12.5)
    work = (0.88 * den + 0.12 * np.clip(z_src, -135.0, 150.0)).astype(np.float32, copy=False)

    valid_mask = (img_hu >= -125.0) & (img_hu <= 145.0)
    padded_work = _voronoi_fill_invalid(work, valid_mask)

    g1 = scipy.ndimage.gaussian_filter(padded_work, sigma=max(0.55, 0.80 * scale_px))
    g2 = scipy.ndimage.gaussian_filter(padded_work, sigma=max(1.2, 2.0 * scale_px))
    g3 = scipy.ndimage.gaussian_filter(padded_work, sigma=max(2.8, 4.6 * scale_px))
    g4 = scipy.ndimage.gaussian_filter(padded_work, sigma=max(6.5, 11.0 * scale_px))

    d0 = work - g1
    d1 = g1 - g2
    d2 = g2 - g3
    d3 = g3 - g4
    base = g4

    if is_hard:
        c0, c1 = 3.8, 2.4
        gd0, gd1, gd2, gd3 = 0.70, 2.65, 2.20, 1.55
        m_gain = 1.55
    else:
        c0, c1 = 4.2, 2.2
        gd0, gd1, gd2, gd3 = 0.50, 1.95, 1.65, 1.25
        m_gain = 1.25

    b0 = _musica_band_boost(d0, gd0, 0.85, 9.0, c0)
    b1 = _musica_band_boost(d1, gd1, 0.66, 10.0, c1)
    b2 = _musica_band_boost(d2, gd2, 0.70, 14.0, 0.0)
    b3 = _musica_band_boost(d3, gd3, 0.78, 20.0, 0.0)

    center = 38.0
    scale_macro = 28.0
    s_macro = center + np.tanh(((base - center) / scale_macro) * m_gain) * (scale_macro * 1.35)
    base_mapped = 0.65 * s_macro + 0.35 * (center + (base - center) * m_gain)
    fat_floor = center + (-25.0 - center) * m_gain
    base_mapped = np.where(base < -25.0, fat_floor + (base + 25.0) * 0.50, base_mapped)

    enh = np.clip(base_mapped + b0 + b1 + b2 + b3, -115.0, 175.0).astype(np.float32, copy=False)
    out_hu = img_hu * (1.0 - st_weight) + enh * st_weight
    return _restore_metal_core(out_hu, img_hu)


def _fast_patch_nlm_25d(
    img_hu: np.ndarray,
    neighbor_hu: list[np.ndarray] | None,
    lo: float = -135.0,
    hi: float = 150.0,
    search_r: int = 2,
    patch_r: int = 1,
    h_param: float = 13.5,
) -> np.ndarray:
    """
    2.5D Non-Local Means comparing 3x3 patch SSDs across a 5x5 window and +/-1 Z-slices.
    Preserves subtle 10-15 HU organ contacts while denoising along curvilinear folds.
    """
    src = np.clip(img_hu, lo, hi).astype(np.float32, copy=False)
    valid = ((img_hu >= lo - 15.0) & (img_hu <= hi + 25.0)).astype(np.float32)
    h, w = src.shape
    patch_sz = 2 * patch_r + 1
    inv_h2 = np.float32(1.0 / (h_param * h_param))

    pad_s = np.pad(src, search_r, mode="edge")
    pad_v = np.pad(valid, search_r, mode="constant", constant_values=0.0)

    acc = np.float32(0.7) * src * valid
    w_sum = np.float32(0.7) * valid

    for dy in range(-search_r, search_r + 1):
        for dx in range(-search_r, search_r + 1):
            if dy == 0 and dx == 0:
                continue
            y0, y1 = search_r + dy, search_r + dy + h
            x0, x1 = search_r + dx, search_r + dx + w
            nbr = pad_s[y0:y1, x0:x1]
            vn = pad_v[y0:y1, x0:x1]
            patch_mse = scipy.ndimage.uniform_filter((src - nbr) ** 2, size=patch_sz)
            wt = np.exp(-patch_mse * inv_h2) * vn
            acc += wt * nbr
            w_sum += wt

    if neighbor_hu:
        for n in neighbor_hu:
            if n is None or getattr(n, "shape", None) != img_hu.shape:
                continue
            n_arr = np.asarray(n, dtype=np.float32)
            n_src = np.clip(n_arr, lo, hi).astype(np.float32, copy=False)
            vn = ((n_arr >= lo - 15.0) & (n_arr <= hi + 25.0)).astype(np.float32)
            patch_mse = scipy.ndimage.uniform_filter((src - n_src) ** 2, size=patch_sz)
            wt = np.float32(0.85) * np.exp(-patch_mse * inv_h2) * vn
            acc += wt * n_src
            w_sum += wt

    return np.where(w_sum > 1e-4, acc / np.maximum(w_sum, 1e-6), src).astype(np.float32, copy=False)


def _enhance_patch_nlm(
    img_hu: np.ndarray,
    neighbor_hu: list[np.ndarray] | None,
    pixel_spacing: list[float] | tuple[float, ...] | None,
) -> np.ndarray:
    """2.5D Patch-NLM denoiser + wide regional compensation + masked DoG & clefts."""
    scale_px = _resolve_pixel_scale(pixel_spacing)
    st_weight = _soft_tissue_blend_weight(img_hu, lo_start=-125.0, lo_span=35.0, hi_end=150.0, hi_span=45.0)
    if not np.any(st_weight > 0.01):
        return img_hu.copy()

    den = _fast_patch_nlm_25d(img_hu, neighbor_hu=neighbor_hu, lo=-135.0, hi=150.0, search_r=2, patch_r=1, h_param=13.5)
    mask = ((img_hu >= -125.0) & (img_hu <= 145.0)).astype(np.float32)

    bg_wide = _masked_gaussian(den, mask, sigma=max(12.0, 22.0 * scale_px))
    boosted = den + np.float32(0.35) * np.clip(den - bg_wide, -25.0, 25.0)
    toned = _pelvic_tone_curve(boosted, gain=1.85, center=38.0)

    fine = _masked_gaussian(den, mask, sigma=max(0.7, 0.85 * scale_px))
    coarse = _masked_gaussian(den, mask, sigma=max(2.0, 2.8 * scale_px))
    detail = np.clip(fine - coarse, -14.0, 14.0)

    closed = scipy.ndimage.grey_closing(den, size=(5, 5))
    bh_mask = (den > -20.0).astype(np.float32)
    black_hat = np.clip(closed - den - 2.0, 0.0, 15.0) * bh_mask

    enh = np.clip(toned + 1.65 * detail - 1.35 * black_hat, -110.0, 170.0).astype(np.float32, copy=False)
    out_hu = img_hu * (1.0 - st_weight) + enh * st_weight
    return _restore_metal_core(out_hu, img_hu)


def enhance_soft_tissue_slice_v2(
    img_hu: np.ndarray,
    neighbor_hu: list[np.ndarray] | None = None,
    mode: str = "hard",
    use_z_filter: bool = False,
    pixel_spacing: list[float] | tuple[float, ...] | None = None,
) -> np.ndarray:
    """
    Dispatch soft-tissue contrast & boundary enhancement in pelvic HU space.
    Supports legacy ('normal', 'hard') and extended modes ('clamped_v2',
    'musica_normal', 'musica_hard', 'patch_nlm').
    """
    src_hu = np.asarray(img_hu, dtype=np.float32)
    if mode in ("normal", "hard"):
        return _enhance_legacy_v2(src_hu, neighbor_hu, mode=mode, use_z_filter=use_z_filter)
    if mode == "clamped_v2":
        return _enhance_clamped_v2(src_hu, neighbor_hu)
    if mode in ("musica_normal", "musica_hard"):
        return _enhance_musica(src_hu, neighbor_hu, pixel_spacing=pixel_spacing, is_hard=(mode == "musica_hard"))
    if mode == "patch_nlm":
        return _enhance_patch_nlm(src_hu, neighbor_hu, pixel_spacing=pixel_spacing)
    return _enhance_legacy_v2(src_hu, neighbor_hu, mode="hard", use_z_filter=use_z_filter)
