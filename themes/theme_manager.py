import os

def load_theme(theme_name="dark"):
    """
    Loads QSS stylesheet file from the themes directory.
    """
    current_dir = os.path.dirname(os.path.abspath(__file__))
    qss_path = os.path.join(current_dir, f"{theme_name}.qss")
    
    if os.path.exists(qss_path):
        with open(qss_path, "r", encoding="utf-8") as f:
            return f.read()
    else:
        print(f"Warning: Theme file not found at {qss_path}")
        return ""
