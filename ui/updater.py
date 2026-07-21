import os
import sys
import urllib.request
from PyQt6.QtCore import QThread, pyqtSignal, Qt
from PyQt6.QtWidgets import QProgressDialog, QMessageBox, QApplication
from core.config_utils import get_app_data_dir
from core.locale_utils import get_current_langs
from ui.settings_dialog import apply_dark_title_bar

_active_workers = set()

def tr(ru_text, en_text):
    lang, _ = get_current_langs()
    return ru_text if lang == 'ru' else en_text

class FileDownloadWorker(QThread):
    # Сигнал передает: (процент, скорость_строка, скачано_байт, всего_байт)
    progress = pyqtSignal(int, str, int, int)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, url, dest_path):
        super().__init__()
        self.url = url
        self.dest_path = dest_path

    def run(self):
        import time
        max_retries = 3
        retry_delay = 2
        
        for attempt in range(max_retries):
            try:
                req = urllib.request.Request(self.url, headers={'User-Agent': 'DICOM_WatchDog-Updater'})
                with urllib.request.urlopen(req, timeout=30) as response:
                    total_size = int(response.info().get('Content-Length', 0))
                    bytes_downloaded = 0
                    block_size = 1024 * 8
                    
                    start_time = time.time()
                    last_time = start_time
                    last_bytes = 0
                    speed_str = tr("вычисляется...", "calculating...")
                    
                    with open(self.dest_path, 'wb') as f:
                        while True:
                            buffer = response.read(block_size)
                            if not buffer:
                                break
                            bytes_downloaded += len(buffer)
                            f.write(buffer)
                            
                            current_time = time.time()
                            if current_time - last_time >= 0.5:
                                duration = current_time - last_time
                                bytes_diff = bytes_downloaded - last_bytes
                                speed_bytes_sec = bytes_diff / duration if duration > 0 else 0
                                
                                if speed_bytes_sec < 1024:
                                    speed_str = f"{speed_bytes_sec:.1f} B/s"
                                elif speed_bytes_sec < 1024 * 1024:
                                    speed_str = f"{speed_bytes_sec / 1024:.1f} KB/s"
                                else:
                                    speed_str = f"{speed_bytes_sec / (1024 * 1024):.1f} MB/s"
                                
                                last_time = current_time
                                last_bytes = bytes_downloaded
                                
                            percent = int((bytes_downloaded / total_size) * 100) if total_size > 0 else 0
                            self.progress.emit(percent, speed_str, bytes_downloaded, total_size)
                                
                self.progress.emit(100, speed_str, bytes_downloaded, total_size)
                self.finished.emit(self.dest_path)
                return
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    continue
                else:
                    self.error.emit(str(e))
                    self.finished.emit("")

def get_build_type():
    if not hasattr(sys, "frozen"):
        return "source"
    
    # 1. Если Python 3.8 — это сборка Legacy под Windows 7
    if sys.version_info.major == 3 and sys.version_info.minor == 8:
        return "legacy"
        
    # 2. Если Python 3.11+ — проверяем реальный модуль QApplication
    try:
        from PyQt6.QtWidgets import QApplication
        module_name = QApplication.__module__
        if "PyQt5" in module_name:
            return "pyqt5"
        else:
            return "pyqt6"
    except Exception:
        pass

    # Резервный вариант по имени файла, если модули не определились
    exe_name = os.path.basename(sys.executable).lower()
    if "legacy" in exe_name:
        return "legacy"
    elif "pyqt5" in exe_name:
        return "pyqt5"
    
    return "pyqt6"

def find_matching_asset(assets, build_type, latest_version):
    clean_version = latest_version.lower().lstrip('v')
    version_str = f"v{clean_version}"
    for name, url in assets.items():
        name_lower = name.lower()
        if version_str not in name_lower:
            continue
        if build_type == "legacy":
            if "legacy" in name_lower:
                return name, url
        elif build_type == "pyqt5":
            if "pyqt5" in name_lower and "legacy" not in name_lower:
                return name, url
        elif build_type == "pyqt6":
            if "pyqt6" in name_lower:
                return name, url
    return None, None

def run_auto_update(parent, latest_version, assets):
    build_type = get_build_type()
    
    if build_type == "source":
        msg = QMessageBox(parent)
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setWindowTitle(tr("Доступно обновление", "Update Available"))
        msg.setText(
            tr(
                f"Доступна новая версия: {latest_version}.\n\nВы запустили приложение из исходного кода. Пожалуйста, обновите репозиторий вручную (например, с помощью git pull).",
                f"A new version is available: {latest_version}.\n\nYou are running the application from source. Please update the repository manually (e.g., via git pull)."
            )
        )
        apply_dark_title_bar(msg)
        msg.exec()
        return

    asset_name, download_url = find_matching_asset(assets, build_type, latest_version)
    if not download_url:
        msg = QMessageBox(parent)
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setWindowTitle(tr("Ошибка обновления", "Update Error"))
        msg.setText(
            tr(
                f"Доступна новая версия {latest_version}, но не удалось найти подходящий файл сборки ({build_type}) среди ассетов релиза.\n\nПожалуйста, обновите программу вручную на GitHub.",
                f"Version {latest_version} is available, but no matching build file ({build_type}) was found in the release assets.\n\nPlease update the program manually on GitHub."
            )
        )
        apply_dark_title_bar(msg)
        msg.exec()
        return

    # Запрашиваем подтверждение
    msg = QMessageBox(parent)
    msg.setIcon(QMessageBox.Icon.Question)
    msg.setWindowTitle(tr("Автоматическое обновление", "Auto Update"))
    msg.setText(
        tr(
            f"Доступна новая версия {latest_version}.\nБудет скачан файл: {asset_name}\n\nХотите запустить автоматическое обновление прямо сейчас? (Программа перезапустится автоматически)",
            f"Version {latest_version} is available.\nAsset to download: {asset_name}\n\nDo you want to start the automatic update now? (The application will restart automatically)"
        )
    )
    msg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    msg.setDefaultButton(QMessageBox.StandardButton.Yes)
    apply_dark_title_bar(msg)
    
    if msg.exec() != QMessageBox.StandardButton.Yes:
        return

    # Начинаем скачивание
    temp_dir = get_app_data_dir()
    temp_exe_path = os.path.join(temp_dir, "update_new.exe")
    
    progress_dialog = QProgressDialog(
        tr("Скачивание обновления...", "Downloading update..."),
        tr("Отмена", "Cancel"),
        0, 100, parent
    )
    progress_dialog.setWindowModality(Qt.WindowModality.WindowModal)
    progress_dialog.setWindowTitle(tr("Обновление программы", "Software Update"))
    apply_dark_title_bar(progress_dialog)
    
    # Применение красивого темного стиля
    progress_dialog.setStyleSheet("""
        QProgressDialog {
            background-color: #202020;
        }
        QLabel {
            color: #ffffff;
            font-size: 13px;
            font-family: 'Segoe UI';
            margin-bottom: 5px;
        }
        QProgressBar {
            border: 1px solid #3d3d3d;
            border-radius: 6px;
            background-color: #0f0f0f;
            text-align: center;
            color: #ffffff;
            font-weight: bold;
            height: 20px;
        }
        QProgressBar::chunk {
            background-color: #1f538d;
            border-radius: 5px;
        }
        QPushButton {
            background-color: #2d2d2d;
            color: #ffffff;
            border: 1px solid #3d3d3d;
            border-radius: 4px;
            padding: 5px 15px;
            font-family: 'Segoe UI';
            min-width: 80px;
        }
        QPushButton:hover {
            background-color: #3d3d3d;
        }
        QPushButton:pressed {
            background-color: #1f538d;
        }
    """)
    
    progress_dialog.setValue(0)
    progress_dialog.show()

    worker = FileDownloadWorker(download_url, temp_exe_path)
    _active_workers.add(worker)
    
    def on_progress(percent, speed, downloaded, total):
        progress_dialog.setValue(percent)
        downloaded_mb = downloaded / (1024 * 1024)
        if total > 0:
            total_mb = total / (1024 * 1024)
            label_text = tr(
                f"Скачивание обновления...\nЗагружено: {downloaded_mb:.1f} МБ из {total_mb:.1f} МБ ({percent}%)\nСкорость: {speed}",
                f"Downloading update...\nDownloaded: {downloaded_mb:.1f} MB of {total_mb:.1f} MB ({percent}%)\nSpeed: {speed}"
            )
        else:
            label_text = tr(
                f"Скачивание обновления...\nЗагружено: {downloaded_mb:.1f} МБ\nСкорость: {speed}",
                f"Downloading update...\nDownloaded: {downloaded_mb:.1f} MB\nSpeed: {speed}"
            )
        progress_dialog.setLabelText(label_text)

    worker.progress.connect(on_progress)
    
    def on_finished(path):
        progress_dialog.close()
        _active_workers.discard(worker)
        if not path or not os.path.exists(path):
            return
            
        current_exe_path = sys.executable
        updater_ps1_path = os.path.join(temp_dir, "updater.ps1")
        current_pid = os.getpid()
        
        temp_exe_esc = temp_exe_path.replace("'", "''")
        current_exe_esc = current_exe_path.replace("'", "''")
        
        ps_content = f"""# PowerShell Updater Script for DICOM WatchDog
Start-Sleep -Milliseconds 500

# Завершаем старый процесс
Stop-Process -Id {current_pid} -Force -ErrorAction SilentlyContinue

$temp_path = '{temp_exe_esc}'
$dest_path = '{current_exe_esc}'

$success = $false
for ($i = 1; $i -le 15; $i++) {{
    try {{
        Copy-Item -Path $temp_path -Destination $dest_path -Force -ErrorAction Stop
        $success = $true
        break
    }} catch {{
        Start-Sleep -Seconds 1
    }}
}}

if ($success) {{
    Remove-Item -Path $temp_path -Force -ErrorAction SilentlyContinue
    Start-Process -FilePath $dest_path
}} else {{
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show(
        "Не удалось обновить файл программы. Возможно, файл заблокирован другим процессом или требуются права Администратора.",
        "Ошибка обновления",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Error
    )
}}

# Самоудаление скрипта
Remove-Item -Path $MyInvocation.MyCommand.Path -Force -ErrorAction SilentlyContinue
"""
        try:
            with open(updater_ps1_path, "w", encoding="utf-8") as f:
                f.write(ps_content)
                
            import subprocess
            cmd = [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy", "Bypass",
                "-WindowStyle", "Hidden",
                "-File", updater_ps1_path
            ]
            
            startupinfo = None
            if sys.platform == "win32":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = 0  # SW_HIDE
                
            subprocess.Popen(
                cmd,
                startupinfo=startupinfo,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            )
            QApplication.quit()
        except Exception as e:
            QMessageBox.critical(
                parent,
                tr("Ошибка обновления", "Update Error"),
                tr(f"Не удалось создать скрипт обновления:\n{e}", f"Failed to create update script:\n{e}")
            )

    def on_error(err_msg):
        _active_workers.discard(worker)
        QMessageBox.critical(
            parent,
            tr("Ошибка скачивания", "Download Error"),
            tr(f"Произошла ошибка при загрузке обновления:\n{err_msg}", f"An error occurred while downloading the update:\n{err_msg}")
        )

    def on_cancel():
        worker.terminate()
        _active_workers.discard(worker)

    worker.finished.connect(on_finished)
    worker.error.connect(on_error)
    progress_dialog.canceled.connect(on_cancel)
    
    worker.start()
