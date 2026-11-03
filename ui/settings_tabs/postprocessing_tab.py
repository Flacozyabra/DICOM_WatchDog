# -*- coding: utf-8 -*-
"""Image Postprocessing Settings Tab."""

from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QLabel, 
                             QFormLayout)

from ui.toggle_switch import ToggleSwitch
from core.locale_utils import tr_ui


def build_postprocessing_tab(dialog):
    postproc_widget = QWidget()
    postproc_layout = QVBoxLayout(postproc_widget)
    postproc_layout.setContentsMargins(15, 15, 15, 15)
    postproc_layout.setSpacing(10)

    postproc_form = QFormLayout()
    postproc_form.setSpacing(8)

    # Master toggle for postprocessing buttons in viewer
    dialog.postprocessing_buttons_cb = ToggleSwitch()
    dialog.postprocessing_buttons_cb.setChecked(
        dialog.config.get('image_postprocessing_enabled', 'False').lower() == 'true'
    )
    dialog.lbl_postprocessing_buttons = QLabel()
    postproc_form.addRow(dialog.lbl_postprocessing_buttons, dialog.postprocessing_buttons_cb)

    postproc_layout.addLayout(postproc_form)
    postproc_layout.addStretch()

    return postproc_widget


def retranslate_postprocessing_tab(dialog):
    dialog.lbl_postprocessing_buttons.setText(tr_ui("settings_postprocessing_buttons"))
    dialog.lbl_postprocessing_buttons.setToolTip(tr_ui("tooltip_postprocessing_buttons"))
    dialog.postprocessing_buttons_cb.setToolTip(tr_ui("tooltip_postprocessing_buttons"))
