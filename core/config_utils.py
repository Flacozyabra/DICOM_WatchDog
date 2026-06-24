import os
import sys
import shutil

def get_app_data_dir():
    app_name = "DICOM_Explorer"
    if sys.platform == "win32":
        base_dir = os.environ.get("LOCALAPPDATA", os.environ.get("APPDATA", os.path.expanduser("~")))
    else:
        base_dir = os.path.expanduser("~")
    
    app_data_path = os.path.normpath(os.path.join(base_dir, app_name))
    os.makedirs(app_data_path, exist_ok=True)
    return app_data_path

def migrate_files():
    app_data_dir = get_app_data_dir()
    
    # Root directory of the project
    project_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    
    files_to_migrate = ["config.json", "archive_cache.json", "pacs_error.log"]
    
    for filename in files_to_migrate:
        src = os.path.join(project_dir, filename)
        dst = os.path.join(app_data_dir, filename)
        
        # Migrate only if file exists in project root but not in the AppData directory
        if os.path.exists(src) and not os.path.exists(dst):
            try:
                shutil.copy2(src, dst)
                print(f"Migrated {filename} from {src} to {dst}")
            except Exception as e:
                print(f"Failed to migrate {filename}: {e}")

# Execute migration on module import
migrate_files()

def get_config_path():
    return os.path.join(get_app_data_dir(), "config.json")

def get_cache_path():
    return os.path.join(get_app_data_dir(), "archive_cache.json")

def get_log_path():
    return os.path.join(get_app_data_dir(), "pacs_error.log")

def get_resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    return os.path.normpath(os.path.join(base_path, relative_path))
