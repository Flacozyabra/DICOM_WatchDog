#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Build script for the legacy Windows 7-compatible standalone EXE (PyQt5 + Python 3.8).

Intended to be run inside a Python 3.8 environment (virtual or global).
The output file is named *_Legacy to distinguish it from the standard PyQt5 build.
"""

import os
import sys
import subprocess
import shutil
from pathlib import Path

# Tell main.py to skip PyQt6 and use PyQt5
os.environ["FORCE_PYQT5"] = "1"


def print_banner(text):
    print("\n" + "=" * 80)
    print(f" {text}")
    print("=" * 80 + "\n")


def check_and_install_dependencies():
    print_banner("1. Checking and installing build dependencies")

    pip_cmd = [sys.executable, "-m", "pip"]
    print(f"[INFO] Using Python: {sys.executable} ({sys.version})")

    def install_package(package_name):
        print(f"[INFO] Installing {package_name}...")
        try:
            subprocess.check_call(pip_cmd + ["install", package_name])
            print(f"[OK] {package_name} installed successfully.")
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Fatal: could not install {package_name}: {e}")

    try:
        import PyInstaller  # noqa: F401
        print("[OK] PyInstaller already installed.")
    except ImportError:
        install_package("pyinstaller")

    try:
        from PIL import Image  # noqa: F401
        print("[OK] Pillow already installed.")
    except ImportError:
        install_package("pillow")

    try:
        import PyQt5  # noqa: F401
        print("[OK] PyQt5 already installed.")
    except ImportError:
        install_package("PyQt5")

    runtime_deps = [
        ("watchdog", "watchdog<4.0"),
        ("pydicom", "pydicom<3.0"),
        ("pynetdicom", "pynetdicom<2.1"),
        ("numpy", "numpy<2.0")
    ]
    for mod_name, pkg_spec in runtime_deps:
        try:
            __import__(mod_name)
            print(f"[OK] {mod_name} already installed.")
        except ImportError:
            install_package(pkg_spec)


def generate_ico_icon():
    print_banner("2. Generating application icon")
    png_path = Path("src") / "splashscreen_logo.png"
    ico_path = Path("src") / "app_icon.ico"

    if not png_path.exists():
        print(f"[WARNING] {png_path} not found. Building without custom icon.")
        return False

    try:
        from PIL import Image
        print(f"[INFO] Converting {png_path} -> {ico_path}...")
        img = Image.open(png_path)
        sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
        img.save(ico_path, format="ICO", sizes=sizes)
        print(f"[OK] Icon generated: {ico_path}")
        return True
    except Exception as e:
        print(f"[WARNING] Icon generation failed: {e}. Continuing with default icon.")
        return False


def build_executable(has_icon):
    print_banner("3. Compiling via PyInstaller")

    # Clean build directory before building to avoid PyInstaller caching issues
    build_dir = Path("build")
    if build_dir.exists():
        print(f"[INFO] Removing old build directory: {build_dir}")
        shutil.rmtree(build_dir, ignore_errors=True)

    from core.config_utils import VERSION

    args = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--windowed",
        "--add-data=src;src",
        "--add-data=themes;themes",
        "--add-data=locales;locales",
        "--name=DICOM_WatchDog_PyQt5_Legacy",
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

    # Collect hidden imports for pydicom / pynetdicom
    try:
        from PyInstaller.utils.hooks import collect_submodules
        pydicom_subs = collect_submodules("pydicom")
        pynetdicom_subs = collect_submodules("pynetdicom")
        print(f"[INFO] Collected {len(pydicom_subs)} pydicom submodules, {len(pynetdicom_subs)} pynetdicom submodules.")
        for m in pydicom_subs + pynetdicom_subs:
            args.append(f"--hidden-import={m}")
    except Exception as e:
        print(f"[WARNING] Could not auto-collect submodules: {e}. Using basic hidden imports.")
        args.append("--hidden-import=pydicom")
        args.append("--hidden-import=pynetdicom")

    # Exclude PyQt6 — this is a PyQt5-only build
    for m in ["PyQt6", "PyQt6.QtCore", "PyQt6.QtGui", "PyQt6.QtWidgets",
              "winotify"]:
        args.append(f"--exclude-module={m}")

    args.append("main.py")

    print(f"[INFO] Build command:\n{' '.join(args)}")
    subprocess.check_call(args)
    print("[OK] Legacy EXE compiled successfully.")


def main():
    try:
        os.chdir(Path(__file__).parent.resolve())
        check_and_install_dependencies()
        has_icon = generate_ico_icon()
        build_executable(has_icon)
        print_banner("LEGACY BUILD COMPLETE! EXE is in dist/")
    except Exception as e:
        print(f"\n[FATAL ERROR] Build failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
