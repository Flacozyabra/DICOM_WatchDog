#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Скрипт для сборки автономного Windows-клиента (.exe) DICOM WatchDog с PyQt6.
"""

import os
import sys
import subprocess
import shutil
from pathlib import Path

def print_banner(text):
    print("\n" + "=" * 80)
    print(f" {text}")
    print("=" * 80 + "\n")

def check_and_install_dependencies():
    print_banner("1. Проверка и установка сборочных зависимостей")
    
    venv_python = Path("venv") / "Scripts" / "python.exe"
    if not venv_python.exists():
        venv_python = "python"
        print("[WARNING] venv/Scripts/python.exe не найден! Будет использован глобальный python.")
        pip_cmd = ["python", "-m", "pip"]
    else:
        venv_python = str(venv_python)
        print(f"[INFO] Обнаружен python в виртуальном окружении: {venv_python}")
        pip_cmd = [venv_python, "-m", "pip"]
        
    def install_package(package_name):
        print(f"[INFO] Установка {package_name}...")
        try:
            subprocess.check_call(pip_cmd + ["install", package_name])
            print(f"[OK] {package_name} успешно установлен.")
            return True
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Критическая ошибка: Не удалось установить пакет {package_name}: {e}")

    try:
        import pyinstaller
        print("[OK] PyInstaller уже установлен.")
    except ImportError:
        print("[INFO] PyInstaller не найден. Запуск установки...")
        install_package("pyinstaller")
        
    try:
        from PIL import Image
        print("[OK] Pillow уже установлен.")
    except ImportError:
        print("[INFO] Pillow не найден. Запуск установки...")
        install_package("pillow")

def generate_ico_icon():
    print_banner("2. Генерация иконки приложения")
    png_path = Path("src") / "splashscreen_logo.png"
    ico_path = Path("src") / "app_icon.ico"
    
    if not png_path.exists():
        print(f"[WARNING] Файл {png_path} не найден! Сборка будет выполнена без кастомной иконки.")
        return False
        
    try:
        from PIL import Image
        print(f"[INFO] Конвертируем {png_path} в {ico_path}...")
        img = Image.open(png_path)
        sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
        img.save(ico_path, format="ICO", sizes=sizes)
        print(f"[OK] Иконка {ico_path} успешно сгенерирована.")
        return True
    except Exception as e:
        print(f"[WARNING] Ошибка генерации иконки: {e}. Сборка продолжится со стандартной иконкой.")
        return False

def generate_splash_with_bg():
    print_banner("2.1. Генерация сплэш-скрина со сплошным фоном")
    png_path = Path("src") / "splashscreen_logo.png"
    compiled_path = Path("src") / "splashscreen_compiled.png"
    
    if not png_path.exists():
        print(f"[WARNING] Файл {png_path} не найден! Будет использован стандартный.")
        return False
        
    try:
        from PIL import Image
        print(f"[INFO] Создаем сплэш-скрин со сплошным фоном #202020...")
        img = Image.open(png_path).convert("RGBA")
        
        target_size = (450, 500)
        logo_size = (350, 350)
        img_resized = img.resize(logo_size, Image.Resampling.LANCZOS)
        
        # Создаем фоновое изображение цвета #202020 (RGB: 32, 32, 32)
        bg = Image.new("RGBA", target_size, (32, 32, 32, 255))
        bg.paste(img_resized, (50, 30), img_resized)
        
        bg.convert("RGB").save(compiled_path, "PNG")
        print(f"[OK] Сплэш-скрин {compiled_path} успешно сгенерирован.")
        return True
    except Exception as e:
        print(f"[WARNING] Ошибка генерации сплэш-скрина: {e}. Будет использован оригинал.")
        return False


def build_executable(has_icon):
    print_banner("3. Запуск компиляции через PyInstaller")
    
    # Очистка папки build перед сборкой для избежания конфликтов кэша PyInstaller
    build_dir = Path("build")
    if build_dir.exists():
        print(f"[INFO] Удаление старой папки сборки: {build_dir}")
        shutil.rmtree(build_dir, ignore_errors=True)
        
    from core.config_utils import VERSION

    # Базовые аргументы (запускаем PyInstaller как модуль python)
    args = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--windowed",  # без консоли
        "--add-data=src;src",
        "--add-data=themes;themes",
        "--add-data=locales;locales",
        f"--name=DICOM_WatchDog_v{VERSION}_PyQt6",
        "--splash=src/splashscreen_compiled.png",
    ]

    # Явный поиск и добавление vcruntime140.dll для предотвращения ошибок на GitHub Actions
    vcruntime_path = Path(sys.base_prefix) / "vcruntime140.dll"
    if not vcruntime_path.exists():
        system32_path = Path(os.environ.get("SystemRoot", "C:\\Windows")) / "System32" / "vcruntime140.dll"
        if system32_path.exists():
            vcruntime_path = system32_path

    if vcruntime_path.exists():
        args.append(f"--add-data={vcruntime_path};.")
        print(f"[INFO] Явно добавлена библиотека рантайма: {vcruntime_path}")
    else:
        print("[WARNING] vcruntime140.dll не найдена! Сборка может быть неработоспособной.")
    
    if has_icon:
        args.append("--icon=src/app_icon.ico")
        
    # Собираем скрытые импорты для pydicom и pynetdicom
    try:
        from PyInstaller.utils.hooks import collect_submodules
        pydicom_subs = collect_submodules('pydicom')
        pynetdicom_subs = collect_submodules('pynetdicom')
        print(f"[INFO] Собрано {len(pydicom_subs)} подмодулей pydicom и {len(pynetdicom_subs)} pynetdicom.")
        for m in pydicom_subs + pynetdicom_subs:
            args.append(f"--hidden-import={m}")
    except Exception as e:
        print(f"[WARNING] Не удалось автоматически собрать подмодули: {e}. Используем базовые hidden-imports.")
        args.append("--hidden-import=pydicom")
        args.append("--hidden-import=pynetdicom")

    # Исключаем PyQt5, чтобы минимизировать размер и предотвратить конфликты
    excludes = [
        "PyQt5", "PyQt5.QtCore", "PyQt5.QtGui", "PyQt5.QtWidgets"
    ]
    for m in excludes:
        args.append(f"--exclude-module={m}")
        
    args.append("main.py")
    
    print(f"[INFO] Выполняется команда сборки:\n{' '.join(args)}")
    subprocess.check_call(args)
    print("[OK] Компиляция .exe на PyQt6 успешно завершена.")


def main():
    try:
        os.chdir(Path(__file__).parent.resolve())
        check_and_install_dependencies()
        has_icon = generate_ico_icon()
        generate_splash_with_bg()
        build_executable(has_icon)
        print_banner("СБОРКА PYQT6 УСПЕШНО ВЫПОЛНЕНА! EXE ФАЙЛ В ПАПКЕ dist/")
    except Exception as e:
        print(f"\n[FATAL ERROR] Произошел критический сбой при сборке: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
