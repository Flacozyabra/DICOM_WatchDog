import os
import sys
import shutil
import json
import urllib.request

VERSION = "1.8.4"


def get_app_data_dir():
    app_name = "DICOM_WatchDog"
    if sys.platform == "win32":
        base_dir = os.environ.get("LOCALAPPDATA", os.environ.get("APPDATA", os.path.expanduser("~")))
    else:
        base_dir = os.path.expanduser("~")
    
    app_data_path = os.path.normpath(os.path.join(base_dir, app_name))
    os.makedirs(app_data_path, exist_ok=True)
    return app_data_path

def get_logs_dir():
    logs_dir = os.path.normpath(os.path.join(get_app_data_dir(), "logs"))
    os.makedirs(logs_dir, exist_ok=True)
    return logs_dir

def get_resource_path(relative_path):
    rel_clean = os.path.normpath(relative_path)
    
    # 1. Попытка взять из sys._MEIPASS (PyInstaller)
    if hasattr(sys, '_MEIPASS'):
        candidate = os.path.normpath(os.path.join(sys._MEIPASS, rel_clean))
        if os.path.exists(candidate):
            return candidate

    # 2. Попытка взять из постоянного каталога ресурсов в AppData (защита от очистки Temp)
    app_res = os.path.normpath(os.path.join(get_app_data_dir(), "resources", rel_clean))
    if os.path.exists(app_res):
        return app_res

    # 3. Попытка взять напрямую из корня AppData (для ранее скопированных иконок)
    basename = os.path.basename(rel_clean)
    app_root_file = os.path.normpath(os.path.join(get_app_data_dir(), basename))
    if os.path.exists(app_root_file):
        return app_root_file

    # 4. Попытка взять относительно корня проекта (при запуске из исходников)
    proj_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    candidate_proj = os.path.normpath(os.path.join(proj_dir, rel_clean))
    if os.path.exists(candidate_proj):
        return candidate_proj

    if hasattr(sys, '_MEIPASS'):
        return os.path.normpath(os.path.join(sys._MEIPASS, rel_clean))
    return candidate_proj

def migrate_files():
    app_data_dir = get_app_data_dir()
    
    # Root directory of the project
    project_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    
    files_to_migrate = ["config.json", "archive_cache.json", "ct_images_cache.json"]
    
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

    # Migrate log files into logs/ subfolder
    logs_dir = get_logs_dir()
    for log_filename in ["error.log", "pacs_error.log"]:
        target_log = os.path.join(logs_dir, log_filename)
        app_log = os.path.join(app_data_dir, log_filename)
        proj_log = os.path.join(project_dir, log_filename)
        
        # 1. From app_data_dir root to logs_dir
        if os.path.exists(app_log) and os.path.isfile(app_log):
            try:
                if not os.path.exists(target_log):
                    shutil.move(app_log, target_log)
                else:
                    with open(app_log, "rb") as sf, open(target_log, "ab") as df:
                        df.write(sf.read())
                    os.remove(app_log)
            except Exception as e:
                print(f"Failed to move {log_filename} to logs: {e}")

        # 2. From project root to logs_dir
        if os.path.exists(proj_log) and os.path.isfile(proj_log) and not os.path.exists(target_log):
            try:
                shutil.copy2(proj_log, target_log)
            except Exception as e:
                print(f"Failed to copy {log_filename} from project root: {e}")

    # Copy notification icons and sounds to persistent AppData resources folder to survive Windows Temp cleanup
    try:
        res_dir = os.path.join(app_data_dir, "resources", "src")
        os.makedirs(res_dir, exist_ok=True)

        if hasattr(sys, '_MEIPASS'):
            source_src = os.path.join(sys._MEIPASS, "src")
        else:
            source_src = os.path.join(project_dir, "src")

        if os.path.isdir(source_src):
            for fname in os.listdir(source_src):
                if fname.lower().endswith(('.wav', '.png', '.ico', '.svg')):
                    s_file = os.path.join(source_src, fname)
                    d_file = os.path.join(res_dir, fname)
                    if os.path.isfile(s_file):
                        if not os.path.exists(d_file) or os.path.getsize(s_file) != os.path.getsize(d_file):
                            try:
                                shutil.copy2(s_file, d_file)
                            except Exception:
                                pass

        for icon_name in ["folder_notification.png", "pacs_notification.png", "splashscreen_logo.png"]:
            src_icon = get_resource_path(os.path.join("src", icon_name))
            dst_icon = os.path.join(app_data_dir, icon_name)
            if os.path.exists(src_icon):
                if not os.path.exists(dst_icon) or os.path.getsize(src_icon) != os.path.getsize(dst_icon):
                    try:
                        shutil.copy2(src_icon, dst_icon)
                    except Exception:
                        pass
    except Exception:
        pass

# Execute migration on module import
migrate_files()

def get_config_path():
    return os.path.join(get_app_data_dir(), "config.json")

def load_config():
    config_path = get_config_path()
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def get_cache_path():
    return os.path.join(get_app_data_dir(), "archive_cache.json")

def get_ct_cache_path():
    return os.path.join(get_app_data_dir(), "ct_images_cache.json")

def check_rotate_log(file_path, max_bytes=5 * 1024 * 1024, backup_count=3):
    try:
        if os.path.exists(file_path) and os.path.getsize(file_path) >= max_bytes:
            for i in range(backup_count - 1, 0, -1):
                sfn = f"{file_path}.{i}"
                dfn = f"{file_path}.{i + 1}"
                if os.path.exists(sfn):
                    if os.path.exists(dfn):
                        os.remove(dfn)
                    os.rename(sfn, dfn)
            dfn = f"{file_path}.1"
            if os.path.exists(dfn):
                os.remove(dfn)
            os.rename(file_path, dfn)
    except Exception:
        pass

def get_log_path():
    path = os.path.join(get_logs_dir(), "pacs_error.log")
    check_rotate_log(path)
    return path

def append_to_log(file_path: str, message: str) -> None:
    """Безопасно дописывает сообщение в файл лога со сбросом буфера и немедленным закрытием дескриптора."""
    try:
        check_rotate_log(file_path)
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(message)
            f.flush()
    except Exception:
        pass

def write_pacs_log(message: str) -> None:
    """Записывает сообщение в pacs_error.log без удержания дескриптора файла."""
    path = os.path.join(get_logs_dir(), "pacs_error.log")
    append_to_log(path, message)

def get_app_error_log_path():
    return os.path.join(get_logs_dir(), "error.log")

def save_config(config):
    config_path = get_config_path()
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=4)
        return True
    except Exception as e:
        print(f"Failed to save config: {e}")
        return False


