# -*- coding: utf-8 -*-
"""DICOM Viewer Settings Tab."""

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QLabel, 
                             QFormLayout, QSpinBox)

from ui.toggle_switch import ToggleSwitch
from core.locale_utils import tr_ui


def build_postprocessing_tab(dialog):
    postproc_widget = QWidget()
    postproc_layout = QVBoxLayout(postproc_widget)
    postproc_layout.setContentsMargins(15, 15, 15, 15)
    postproc_layout.setSpacing(10)

    postproc_form = QFormLayout()
    postproc_form.setSpacing(8)

    # Default viewer zoom (%)
    dialog.viewer_default_zoom_spin = QSpinBox()
    dialog.viewer_default_zoom_spin.setRange(50, 400)
    dialog.viewer_default_zoom_spin.setSingleStep(10)
    dialog.viewer_default_zoom_spin.setSuffix(" %")
    dialog.viewer_default_zoom_spin.setFixedWidth(110)
    try:
        zoom_pct = int(dialog.config.get('viewer_default_zoom', 150))
    except (TypeError, ValueError):
        zoom_pct = 150
    dialog.viewer_default_zoom_spin.setValue(max(50, min(400, zoom_pct)))
    dialog.lbl_viewer_default_zoom = QLabel()
    postproc_form.addRow(dialog.lbl_viewer_default_zoom, dialog.viewer_default_zoom_spin)

    # Auto-maximize window in MPR mode
    dialog.mpr_fullscreen_cb = ToggleSwitch()
    dialog.mpr_fullscreen_cb.setChecked(
        str(dialog.config.get('mpr_fullscreen_on_open', 'False')).lower() == 'true'
    )
    dialog.lbl_mpr_fullscreen = QLabel()
    postproc_form.addRow(dialog.lbl_mpr_fullscreen, dialog.mpr_fullscreen_cb)

    # Master toggle for postprocessing buttons in viewer
    dialog.postprocessing_buttons_cb = ToggleSwitch()
    dialog.postprocessing_buttons_cb.setChecked(
        str(dialog.config.get('image_postprocessing_enabled', 'False')).lower() == 'true'
    )
    dialog.lbl_postprocessing_buttons = QLabel()
    postproc_form.addRow(dialog.lbl_postprocessing_buttons, dialog.postprocessing_buttons_cb)

    postproc_layout.addLayout(postproc_form)
    postproc_layout.addStretch()

    return postproc_widget


def retranslate_postprocessing_tab(dialog):
    dialog.lbl_viewer_default_zoom.setText(tr_ui("settings_viewer_default_zoom"))
    dialog.lbl_viewer_default_zoom.setToolTip(tr_ui("tooltip_viewer_default_zoom"))
    dialog.viewer_default_zoom_spin.setToolTip(tr_ui("tooltip_viewer_default_zoom"))

    dialog.lbl_mpr_fullscreen.setText(tr_ui("settings_mpr_fullscreen"))
    dialog.lbl_mpr_fullscreen.setToolTip(tr_ui("tooltip_mpr_fullscreen"))
    dialog.mpr_fullscreen_cb.setToolTip(tr_ui("tooltip_mpr_fullscreen"))

    dialog.lbl_postprocessing_buttons.setText(tr_ui("settings_postprocessing_buttons"))
    dialog.lbl_postprocessing_buttons.setToolTip(tr_ui("tooltip_postprocessing_buttons"))
    dialog.postprocessing_buttons_cb.setToolTip(tr_ui("tooltip_postprocessing_buttons"))
