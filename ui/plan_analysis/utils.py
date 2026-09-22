# -*- coding: utf-8 -*-
"""
Utility functions for Monaco plan analysis.
"""

import os
import sys
from typing import Optional, Dict, Any
import pydicom
from PyQt6.QtWidgets import QWidget


def apply_dark_title_bar(widget: QWidget):
    """Enable native Windows 10/11 dark title bar."""
    if sys.platform == "win32":
        try:
            import ctypes
            hwnd = int(widget.winId())
            DWMWA_USE_IMMERSIVE_DARK_MODE = 20
            set_window_attribute = ctypes.windll.dwmapi.DwmSetWindowAttribute
            value = ctypes.c_int(2)
            set_window_attribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(value), ctypes.sizeof(value))
        except Exception:
            pass


_plan_file_cache: Dict[str, Any] = {}


def find_rtplan_file(folder_path: str) -> Optional[str]:
    """Find the first valid RTPLAN file in a patient study folder with fast caching."""
    if not folder_path or not os.path.isdir(folder_path):
        return None

    try:
        mtime = os.path.getmtime(folder_path)
    except Exception:
        mtime = 0.0

    if folder_path in _plan_file_cache:
        cached_mtime, cached_result = _plan_file_cache[folder_path]
        if cached_mtime == mtime:
            return cached_result

    candidates = []
    others = []
    for root, dirs, files in os.walk(folder_path):
        for f in files:
            fp = os.path.join(root, f)
            fl = f.lower()
            if 'plan' in fl or 'rp' in fl or 'rtp' in fl or 'srt' in fl:
                candidates.append(fp)
            elif fl.endswith('.dcm') or '.' not in fl:
                others.append(fp)

    found_plan = None
    for fp in candidates + others:
        try:
            ds = pydicom.dcmread(fp, stop_before_pixels=True, force=True, specific_tags=['Modality'])
            if str(getattr(ds, 'Modality', '')).upper() == 'RTPLAN':
                found_plan = fp
                break
        except Exception:
            pass

    _plan_file_cache[folder_path] = (mtime, found_plan)
    return found_plan
