import os
import sys
import urllib.request
from PyQt6.QtCore import QThread, pyqtSignal, Qt
from PyQt6.QtWidgets import QProgressDialog, QMessageBox, QApplication
from core.config_utils import get_app_data_dir
from core.locale_utils import get_current_langs
from ui.settings_dialog import apply_dark_title_bar

def tr(ru_text, en_text):
    lang, _ = get_current_langs()
    return ru_text if lang == 'ru' else en_text

class FileDownloadWorker(QThread):
    progress = pyqtSignal(int)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, url, dest_path):
        super().__init__()
        self.url = url
        self.dest_path = dest_path

    def run(self):
        try:
            req = urllib.request.Request(self.url, headers={'User-Agent': 'DICOM_WatchDog-Updater'})
            with urllib.request.urlopen(req, timeout=15) as response:
                total_size = int(response.info().get('Content-Length', 0))
                bytes_downloaded = 0
                block_size = 1024 * 8
                
                with open(self.dest_path, 'wb') as f:
                    while True:
                        buffer = response.read(block_size)
                        if not buffer:
                            break
                        bytes_downloaded += len(buffer)
                        f.write(buffer)
                        if total_size > 0:
                            percent = int((bytes_downloaded / total_size) * 100)
                            self.progress.emit(percent)
                            
            self.progress.emit(100)
            self.finished.emit(self.dest_path)
        except Exception as e:
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

def find_matching_asset(assets, build_type):
    for name, url in assets.items():
        name_lower = name.lower()
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

    asset_name, download_url = find_matching_asset(assets, build_type)
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
    apply_dark_title_bar(progress_dialog)
    progress_dialog.setValue(0)
    progress_dialog.show()

    worker = FileDownloadWorker(download_url, temp_exe_path)
    worker.progress.connect(progress_dialog.setValue)
    
    def on_finished(path):
        progress_dialog.close()
        if not path or not os.path.exists(path):
            return
            
        current_exe_path = sys.executable
        updater_bat_path = os.path.join(temp_dir, "updater.bat")
        
        bat_content = f"""@echo off
chcp 65001 > nul
echo ========================================================
echo  DICOM WatchDog Update -> {latest_version}
echo ========================================================
echo {tr("Ожидание завершения работы программы...", "Waiting for the application to exit...")}
timeout /t 2 /nobreak > nul

echo {tr("Замена файлов...", "Replacing files...")}
copy /y "{temp_exe_path}" "{current_exe_path}"
if errorlevel 1 (
    echo.
    echo {tr("ОШИБКА: Не удалось заменить файл.", "ERROR: Failed to replace the file.")} {os.path.basename(current_exe_path)}.
    echo {tr("Возможно, программа не закрылась или требуется запуск от имени Администратора.", "Perhaps the application is still running or Administrator privileges are required.")}
    echo.
    pause
    exit
)

echo {tr("Очистка временных файлов...", "Cleaning up temporary files...")}
del "{temp_exe_path}"

echo {tr("Запуск новой версии...", "Launching the new version...")}
start "" "{current_exe_path}"

:: Самоудаление батника
(goto) 2>nul & del "%~f0"
"""
        try:
            with open(updater_bat_path, "w", encoding="utf-8") as f:
                f.write(bat_content)
                
            os.startfile(updater_bat_path)
            QApplication.quit()
        except Exception as e:
            QMessageBox.critical(
                parent,
                tr("Ошибка обновления", "Update Error"),
                tr(f"Не удалось создать скрипт обновления:\n{e}", f"Failed to create update script:\n{e}")
            )

    def on_error(err_msg):
        QMessageBox.critical(
            parent,
            tr("Ошибка скачивания", "Download Error"),
            tr(f"Произошла ошибка при загрузке обновления:\n{err_msg}", f"An error occurred while downloading the update:\n{err_msg}")
        )

    worker.finished.connect(on_finished)
    worker.error.connect(on_error)
    progress_dialog.canceled.connect(worker.terminate)
    
    worker.start()
