# -*- coding: utf-8 -*-
"""
Monaco Plan Kinematics & Deliverability Analyzer (Backwards-compatibility shim).

All implementation has been modularized into the `ui.plan_analysis` package:
- `ui.plan_analysis.kinematics`: PlanKinematicsAnalyzer
- `ui.plan_analysis.polar_arc_widget`: PolarArcWidget
- `ui.plan_analysis.timeline_widget`: ModulationTimelineWidget
- `ui.plan_analysis.dialog`: MonacoPlanAnalyzerDialog, open_plan_analyzer
- `ui.plan_analysis.help_dialog`: PlanAnalysisHelpDialog, show_plan_analysis_help
- `ui.plan_analysis.utils`: find_rtplan_file, apply_dark_title_bar
"""

from ui.plan_analysis import (
    PlanKinematicsAnalyzer,
    PolarArcWidget,
    ModulationTimelineWidget,
    PlanAnalysisHelpDialog,
    show_plan_analysis_help,
    find_rtplan_file,
    apply_dark_title_bar,
    MonacoPlanAnalyzerDialog,
    open_plan_analyzer,
)

__all__ = [
    'PlanKinematicsAnalyzer',
    'PolarArcWidget',
    'ModulationTimelineWidget',
    'PlanAnalysisHelpDialog',
    'show_plan_analysis_help',
    'find_rtplan_file',
    'apply_dark_title_bar',
    'MonacoPlanAnalyzerDialog',
    'open_plan_analyzer',
]
