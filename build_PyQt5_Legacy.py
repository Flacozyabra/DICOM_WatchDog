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

    # Install remaining runtime dependencies (winotify excluded — not needed on Win 7)
    runtime_deps = ["watchdog", "pydicom", "pynetdicom", "numpy"]
    for dep in runtime_deps:
        try:
            __import__(dep)
            print(f"[OK] {dep} already installed.")
        except ImportError:
            install_package(dep)


def generate_ico_icon():
    print_banner("2. Generating application icon")
    png_path = Path("src") / "logo.png"
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

    pyinstaller_bin = Path(sys.executable).parent / "pyinstaller.exe"
    if not pyinstaller_bin.exists():
        pyinstaller_bin = "pyinstaller"
        print("[WARNING] pyinstaller.exe not found beside current python. Falling back to PATH.")
    else:
        pyinstaller_bin = str(pyinstaller_bin)
        print(f"[INFO] Found PyInstaller: {pyinstaller_bin}")

    args = [
        str(pyinstaller_bin),
        "--noconfirm",
        "--onefile",
        "--windowed",
        "--add-data=src;src",
        "--add-data=themes;themes",
        "--name=DICOM_WatchDog_PyQt5_Legacy",
    ]

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
