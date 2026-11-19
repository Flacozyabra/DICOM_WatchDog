from __future__ import annotations

import os
from core.config_utils import get_resource_path

BUTTON_VARIANT_STYLES: dict[str, dict[str, str]] = {
    "neutral": {
        "bg": "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #4a4a4a, stop:1 #414141)",
        "fg": "#e2e5ea",
        "border": "#575757",
        "hover_bg": "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #595959, stop:1 #4f4f4f)",
        "hover_fg": "#ffffff",
        "hover_border": "#6b6b6b",
        "pressed_bg": "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #3b3b3b, stop:1 #333333)",
        "pressed_fg": "#dce1e8",
        "pressed_border": "#4a4a4a",
        "disabled_bg": "#303134",
        "disabled_fg": "#70747c",
        "disabled_border": "#3c3e42",
    },
    "primary": {
        "bg": "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #2562a6, stop:1 #1c4c82)",
        "fg": "#ffffff",
        "border": "#3878c0",
        "hover_bg": "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #2d70ba, stop:1 #225895)",
        "hover_fg": "#ffffff",
        "hover_border": "#4b8bd3",
        "pressed_bg": "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1d518a, stop:1 #163f6d)",
        "pressed_fg": "#e8eef5",
        "pressed_border": "#2e67a7",
        "disabled_bg": "#303134",
        "disabled_fg": "#70747c",
        "disabled_border": "#3c3e42",
    },
    "danger": {
        "bg": "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #b91c3c, stop:1 #881337)",
        "fg": "#ffffff",
        "border": "#d43554",
        "hover_bg": "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #cf2347, stop:1 #9f1239)",
        "hover_fg": "#ffffff",
        "hover_border": "#e74c6b",
        "pressed_bg": "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #881337, stop:1 #6b0f2b)",
        "pressed_fg": "#ffe4e8",
        "pressed_border": "#a81c40",
        "disabled_bg": "#303134",
        "disabled_fg": "#70747c",
        "disabled_border": "#3c3e42",
    },
    "danger_subtle": {
        "bg": "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #452124, stop:1 #36191b)",
        "fg": "#fca5a5",
        "border": "#7f2f34",
        "hover_bg": "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #5c282c, stop:1 #471d20)",
        "hover_fg": "#ffffff",
        "hover_border": "#a33b42",
        "pressed_bg": "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #321618, stop:1 #261012)",
        "pressed_fg": "#fecaca",
        "pressed_border": "#682428",
        "disabled_bg": "#303134",
        "disabled_fg": "#70747c",
        "disabled_border": "#3c3e42",
    },
    "help": {
        "bg": "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #4a4a4a, stop:1 #414141)",
        "fg": "#38bdf8",
        "border": "#575757",
        "hover_bg": "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #595959, stop:1 #4f4f4f)",
        "hover_fg": "#7dd3fc",
        "hover_border": "#38bdf8",
        "pressed_bg": "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #3b3b3b, stop:1 #333333)",
        "pressed_fg": "#38bdf8",
        "pressed_border": "#0284c7",
        "disabled_bg": "#303134",
        "disabled_fg": "#70747c",
        "disabled_border": "#3c3e42",
    },
}


def button_qss(
    variant: str = "neutral",
    *,
    selector: str = "QPushButton",
    radius: int = 6,
    padding: str | None = "6px 14px",
    font_size: int | None = 13,
    font_weight: str | None = None,
    extra_rules: str = "",
    hover_variant: str | None = None,
    disabled_bg: str | None = None,
    disabled_fg: str | None = None,
    disabled_border: str | None = None,
) -> str:
    """Build consistent QSS matching the segmented tab bar button palette."""
    v = BUTTON_VARIANT_STYLES[variant]
    hv = BUTTON_VARIANT_STYLES[hover_variant] if hover_variant else v

    base_lines = [
        f"background-color: {v['bg']};",
        f"color: {v['fg']};",
        f"border: 1px solid {v['border']};",
        f"border-radius: {radius}px;",
    ]
    if padding is not None:
        base_lines.append(f"padding: {padding};")
    if font_size is not None:
        base_lines.append(f"font-size: {font_size}px;")
    if font_weight is not None:
        base_lines.append(f"font-weight: {font_weight};")
    if extra_rules:
        base_lines.append(extra_rules.strip())

    hover_bg = hv["bg"] if hover_variant else v["hover_bg"]
    hover_fg = hv["fg"] if hover_variant else v["hover_fg"]
    hover_border = hv["border"] if hover_variant else v["hover_border"]
    pressed_bg = hv["pressed_bg"] if hover_variant else v["pressed_bg"]
    pressed_fg = hv["pressed_fg"] if hover_variant else v["pressed_fg"]
    pressed_border = hv["pressed_border"] if hover_variant else v["pressed_border"]

    hover_sel = ", ".join(f"{s.strip()}:hover" for s in selector.split(","))
    pressed_sel = ", ".join(f"{s.strip()}:pressed" for s in selector.split(","))
    disabled_sel = ", ".join(f"{s.strip()}:disabled" for s in selector.split(","))

    return (
        f"{selector} {{\n"
        f"    {' '.join(base_lines)}\n"
        f"}}\n"
        f"{hover_sel} {{\n"
        f"    background-color: {hover_bg};\n"
        f"    color: {hover_fg};\n"
        f"    border: 1px solid {hover_border};\n"
        f"}}\n"
        f"{pressed_sel} {{\n"
        f"    background-color: {pressed_bg};\n"
        f"    color: {pressed_fg};\n"
        f"    border: 1px solid {pressed_border};\n"
        f"}}\n"
        f"{disabled_sel} {{\n"
        f"    background-color: {disabled_bg or v['disabled_bg']};\n"
        f"    color: {disabled_fg or v['disabled_fg']};\n"
        f"    border: 1px solid {disabled_border or v['disabled_border']};\n"
        f"}}"
    )


def load_theme(theme_name="dark"):
    """
    Loads QSS stylesheet file from the themes directory.
    """
    qss_path = get_resource_path(os.path.join("themes", f"{theme_name}.qss"))
    
    if os.path.exists(qss_path):
        with open(qss_path, "r", encoding="utf-8") as f:
            return f.read()
    else:
        print(f"Warning: Theme file not found at {qss_path}")
        return ""
