import os
import sys
import json
import urllib.request
import shutil
try:
    from PyQt6.QtCore import QThread, pyqtSignal, Qt, QCoreApplication
    from PyQt6.QtWidgets import (
        QDialog, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar, QPushButton,
        QMessageBox, QApplication
    )
except ImportError:
    from PyQt5.QtCore import QThread, pyqtSignal, Qt, QCoreApplication
    from PyQt5.QtWidgets import (
        QDialog, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar, QPushButton,
        QMessageBox, QApplication
    )

DEFAULT_REPO = "Flacozyabra/DICOM_WatchDog"
_active_workers = set()


def tr(ru_text, en_text):
    """Локализация сообщений с безопасным фолбэком."""
    try:
        from core.locale_utils import get_current_langs
        lang, _ = get_current_langs()
        return ru_text if lang == 'ru' else en_text
    except Exception:
        return ru_text


def apply_dark_title_bar_safe(widget):
    """Применяет темную полосу заголовка окна с фолбэком."""
    try:
        from ui.settings_dialog import apply_dark_title_bar
        apply_dark_title_bar(widget)
    except Exception:
        pass


def cleanup_old_exe():
    """
    Очищает остаточные бинарники вида _old_*.exe, оставшиеся после бесшовного обновления.
    Вызывается при старте приложения.
    """
    try:
        current_exe_path = sys.executable
        dest_dir = os.path.dirname(current_exe_path)
        for filename in os.listdir(dest_dir):
            if filename.startswith("_old_") and filename.endswith(".exe"):
                old_path = os.path.join(dest_dir, filename)
                try:
                    os.remove(old_path)
                except Exception:
                    pass
    except Exception:
        pass


def check_github_updates(repo_name=DEFAULT_REPO):
    """
    Проверяет репозиторий GitHub на наличие последнего стабильного релиза.
    Возвращает (latest_tag_name, html_url, assets_dict) или (None, None, None).
    """
    url = f"https://api.github.com/repos/{repo_name}/releases/latest"
    req = urllib.request.Request(url, headers={'User-Agent': 'PyQt-App-Updater'})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode('utf-8'))
            tag_name = data.get('tag_name', '')
            html_url = data.get('html_url', f'https://github.com/{repo_name}/releases')
            
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
    """Сравнивает две строки версий (например '1.4.3' и '1.4.4')."""
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


class UpdateCheckWorker(QThread):
    """Фоновый поток для проверки обновлений на GitHub."""
    finished = pyqtSignal(str, str, object)

    def __init__(self, repo_name=DEFAULT_REPO, parent=None):
        super().__init__(parent)
        self.repo_name = repo_name

    def run(self):
        tag, url, assets = check_github_updates(self.repo_name)
        self.finished.emit(tag or "", url or "", assets or {})


def get_clean_env():
    """Возвращает очищенный словарь переменных окружения без служебных путей PyInstaller."""
    env = os.environ.copy()
    meipass = getattr(sys, '_MEIPASS', None)

    for key in ['_MEIPASS', '_MEIPASS2', 'PYTHONPATH', 'PYTHONHOME', 'PYINSTALLER_STRICT_UNPACK_MODE']:
        env.pop(key, None)

    if 'PATH' in env:
        path_list = env['PATH'].split(os.pathsep)
        cleaned_paths = [
            p for p in path_list
            if '_mei' not in p.lower() and (not meipass or os.path.normpath(p) != os.path.normpath(meipass))
        ]
        env['PATH'] = os.path.sep.join(cleaned_paths)

    return env


class DownloadProgressDialog(QDialog):
    """Стилизованный диалог скачивания обновления в темном дизайне приложения."""
    canceled = pyqtSignal()

    def __init__(self, parent=None, title=None):
        super().__init__(parent)
        if title is None:
            title = tr("Обновление программы", "Software Update")
        self.setWindowTitle(title)
        self.setMinimumWidth(440)
        self.setModal(True)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowCloseButtonHint)

        if sys.platform == "win32":
            import ctypes
            try:
                hwnd = int(self.winId())
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, 20, ctypes.byref(ctypes.c_int(1)), ctypes.sizeof(ctypes.c_int)
                )
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, 35, ctypes.byref(ctypes.c_int(0x002b2b2b)), ctypes.sizeof(ctypes.c_int)
                )
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, 36, ctypes.byref(ctypes.c_int(0x00ffffff)), ctypes.sizeof(ctypes.c_int)
                )
            except Exception:
                pass

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        self.label = QLabel(tr("Скачивание обновления...", "Downloading update..."))
        self.label.setStyleSheet("color: #ffffff; font-size: 13px; font-family: 'Segoe UI'; margin-bottom: 5px;")
        layout.addWidget(self.label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFixedHeight(22)
        self.progress.setStyleSheet("""
            QProgressBar {
                border: 1px solid #3d3d3d;
                border-radius: 6px;
                background-color: #0f0f0f;
                text-align: center;
                color: #ffffff;
                font-weight: bold;
                font-size: 11px;
            }
            QProgressBar::chunk {
                background-color: #1f538d;
                border-radius: 5px;
            }
        """)
        layout.addWidget(self.progress)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.cancel_btn = QPushButton(tr("Отмена", "Cancel"))
        self.cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #2d2d2d;
                color: #ffffff;
                border: 1px solid #3d3d3d;
                border-radius: 4px;
                padding: 6px 18px;
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
        self.cancel_btn.clicked.connect(self.on_cancel_clicked)
        btn_layout.addWidget(self.cancel_btn)

        layout.addLayout(btn_layout)
        self.setStyleSheet("QDialog { background-color: #202020; }")

    def on_cancel_clicked(self):
        self.canceled.emit()
        self.reject()

    def set_progress(self, percent, label_text):
        self.progress.setValue(percent)
        self.label.setText(label_text)
        QCoreApplication.processEvents()


class FileDownloadWorker(QThread):
    """Фоновый поток для скачивания файла обновления с отслеживанием прогресса и повторными попытками."""
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
                req = urllib.request.Request(self.url, headers={'User-Agent': 'PyQt-App-Updater'})
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
    """Определяет тип текущей скомпилированной сборки (legacy, pyqt5, pyqt6, source)."""
    if not hasattr(sys, "frozen"):
        return "source"
    
    if sys.version_info.major == 3 and sys.version_info.minor == 8:
        return "legacy"
        
    try:
        from PyQt6.QtWidgets import QApplication
        module_name = QApplication.__module__
        if "PyQt5" in module_name:
            return "pyqt5"
        else:
            return "pyqt6"
    except Exception:
        pass

    exe_name = os.path.basename(sys.executable).lower()
    if "legacy" in exe_name:
        return "legacy"
    elif "pyqt5" in exe_name:
        return "pyqt5"
    
    return "pyqt6"


def find_matching_asset(assets, build_type, latest_version=""):
    """Находит подходящий ассет для загрузки по типу сборки."""
    for name, url in assets.items():
        name_lower = name.lower()
        if build_type == "legacy":
            if "legacy" in name_lower:
                return name, url
        elif build_type == "pyqt5":
            if "pyqt5" in name_lower and "legacy" not in name_lower:
                return name, url
        elif build_type == "pyqt6":
            if "pyqt6" in name_lower or ("dicom_watchdog" in name_lower and "pyqt5" not in name_lower and "legacy" not in name_lower):
                return name, url
    return None, None


def run_auto_update(parent, latest_version, assets):
    """Выполняет процесс скачивания и бесшовной подмены бинарника на лету."""
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
        apply_dark_title_bar_safe(msg)
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
        apply_dark_title_bar_safe(msg)
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
    apply_dark_title_bar_safe(msg)
    
    if msg.exec() != QMessageBox.StandardButton.Yes:
        return

    # Начинаем скачивание
    current_exe_path = sys.executable
    dest_dir = os.path.dirname(current_exe_path)
    temp_exe_path = os.path.join(dest_dir, "update_new.tmp")
    
    progress_dialog = DownloadProgressDialog(parent)
    progress_dialog.show()

    worker = FileDownloadWorker(download_url, temp_exe_path)
    _active_workers.add(worker)
    
    def on_progress(percent, speed, downloaded, total):
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
        progress_dialog.set_progress(percent, label_text)

    worker.progress.connect(on_progress)
    
    def on_finished(path):
        progress_dialog.close()
        _active_workers.discard(worker)
        if not path or not os.path.exists(path):
            return
            
        current_exe_path = sys.executable
        dest_dir = os.path.dirname(current_exe_path)
        exe_basename = os.path.basename(current_exe_path)
        old_exe_path = os.path.join(dest_dir, f"_old_{exe_basename}")
        
        try:
            if os.path.exists(old_exe_path):
                os.remove(old_exe_path)
        except Exception:
            pass
            
        try:
            # Переименовываем работающий exe (это разрешено в Windows)
            os.rename(current_exe_path, old_exe_path)
        except Exception as e:
            show_update_error(
                parent,
                tr("Ошибка обновления", "Update Error"),
                tr(f"Не удалось подготовить файл к обновлению (ошибка переименования):\n{e}", f"Failed to prepare file for update (rename error):\n{e}")
            )
            try:
                os.remove(path)
            except Exception:
                pass
            return
            
        try:
            # Перемещаем скачанный файл на место оригинального exe
            shutil.move(path, current_exe_path)
        except Exception as e:
            # В случае неудачи возвращаем старый файл на место
            try:
                os.rename(old_exe_path, current_exe_path)
            except Exception:
                pass
            try:
                os.remove(path)
            except Exception:
                pass
            show_update_error(
                parent,
                tr("Ошибка обновления", "Update Error"),
                tr(f"Не удалось применить новую версию:\n{e}", f"Failed to apply the new version:\n{e}")
            )
            return
            
        try:
            import subprocess
            clean_env = get_clean_env()
            
            if sys.platform == "win32":
                # Запускаем новый exe с паузой 3 сек и рабочим каталогом, полностью изолируя от завершающегося процесса
                restart_cmd = f'ping 127.0.0.1 -n 4 > nul & start "" /D "{dest_dir}" "{current_exe_path}"'
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
                if hasattr(subprocess, "DETACHED_PROCESS"):
                    creationflags |= subprocess.DETACHED_PROCESS
                    
                subprocess.Popen(
                    ["cmd.exe", "/c", restart_cmd],
                    env=clean_env,
                    close_fds=True,
                    creationflags=creationflags
                )
            else:
                subprocess.Popen([current_exe_path], env=clean_env, close_fds=True)
                
            QApplication.quit()
        except Exception as e:
            show_update_error(
                parent,
                tr("Ошибка запуска", "Launch Error"),
                tr(
                    f"Обновление успешно применилось, но не удалось автоматически перезапустить программу:\n{e}\nПожалуйста, запустите её вручную.",
                    f"Update applied successfully, but failed to restart the application automatically:\n{e}\nPlease launch it manually."
                )
            )
            QApplication.quit()

    def on_error(err_msg):
        _active_workers.discard(worker)
        show_update_error(
            parent,
            tr("Ошибка скачивания", "Download Error"),
            tr(f"Произошла ошибка при загрузке обновления:\n{err_msg}", f"An error occurred while downloading the update:\n{err_msg}"),
            icon=QMessageBox.Icon.Critical
        )

    def on_cancel():
        worker.terminate()
        _active_workers.discard(worker)

    worker.finished.connect(on_finished)
    worker.error.connect(on_error)
    progress_dialog.canceled.connect(on_cancel)
    
    worker.start()
