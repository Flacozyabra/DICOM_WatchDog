import os
from core.config_utils import get_resource_path

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
