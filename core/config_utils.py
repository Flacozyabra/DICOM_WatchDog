import os
import sys
import shutil
import json
import urllib.request

VERSION = "1.4.15"

def get_app_data_dir():
    app_name = "DICOM_WatchDog"
    if sys.platform == "win32":
        base_dir = os.environ.get("LOCALAPPDATA", os.environ.get("APPDATA", os.path.expanduser("~")))
    else:
        base_dir = os.path.expanduser("~")
    
    app_data_path = os.path.normpath(os.path.join(base_dir, app_name))
    os.makedirs(app_data_path, exist_ok=True)
    return app_data_path

def get_resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    return os.path.normpath(os.path.join(base_path, relative_path))

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

    # Copy notification icons to persistent AppData so Windows Toast service can access them
    try:
        for icon_name in ["folder_notification.png", "pacs_notification.png", "splashscreen_logo.png"]:
            src_icon = get_resource_path(os.path.join("src", icon_name))
            dst_icon = os.path.join(app_data_dir, icon_name)
            if os.path.exists(src_icon):
                if not os.path.exists(dst_icon) or os.path.getsize(src_icon) != os.path.getsize(dst_icon):
                    shutil.copy2(src_icon, dst_icon)
    except Exception:
        pass

# Execute migration on module import
migrate_files()

def get_config_path():
    return os.path.join(get_app_data_dir(), "config.json")

def get_cache_path():
    return os.path.join(get_app_data_dir(), "archive_cache.json")

def get_log_path():
    return os.path.join(get_app_data_dir(), "pacs_error.log")



def check_github_updates():
    """
    Checks GitHub releases for the latest version.
    Returns (latest_tag_name, html_url, assets_dict) if successful, or (None, None, None).
    """
    url = "https://api.github.com/repos/Flacozyabra/DICOM_WatchDog/releases/latest"
    req = urllib.request.Request(url, headers={'User-Agent': 'DICOM_WatchDog'})
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            tag_name = data.get('tag_name', '')
            html_url = data.get('html_url', 'https://github.com/Flacozyabra/DICOM_WatchDog/releases')
            
            assets_dict = {}
            for asset in data.get('assets', []):
                name = asset.get('name', '')
                download_url = asset.get('browser_download_url', '')
                if name and download_url:
                    assets_dict[name] = download_url
                    
            return tag_name, html_url, assets_dict
    except Exception as e:
        print(f"Error checking for updates: {e}")
        return None, None, None

def is_newer_version(current_version, latest_version):
    if not latest_version:
        return False
    curr = current_version.lower().lstrip('v')
    late = latest_version.lower().lstrip('v')
    try:
        curr_parts = [int(p) for p in curr.split('.')]
        late_parts = [int(p) for p in late.split('.')]
        max_len = max(len(curr_parts), len(late_parts))
        curr_parts += [0] * (max_len - len(curr_parts))
        late_parts += [0] * (max_len - len(late_parts))
        return late_parts > curr_parts
    except ValueError:
        return late > curr
