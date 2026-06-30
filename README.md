# DICOM WatchDog

![DICOM WatchDog Screenshot](src/screenshot.jpg)

## English

DICOM WatchDog is a lightweight desktop utility designed for automated monitoring, organization, and backup of DICOM studies (CT images) from scanning workstations and local archives, with optional integration with PACS servers.

### Key Features
- **Real-Time Monitoring**: Automatically scans folders for new CT scan files.
- **Smart Metadata Fixing**: Corrects invalid Patient IDs (removing dots, prefixes) inside DICOM tags.
- **Dynamic Renaming**: Renames study folders using customizable patterns: `Patient ID`, `Patient Name`, `Patient Name [Patient ID]`, or `[Patient ID] Patient Name`.
- **Auto-Archiving**: Archives old patient studies after a defined period and cleans up obsolete structures.
- **PACS Integration**: Search and download patient studies directly from PACS servers.
- **Cross-Platform Compatibility**: Full support for Windows 10/11 (PyQt6/PyQt5) and a legacy build for Windows 7.

### Quick Start
1. Install dependencies: `pip install -r requirements.txt`
2. Run the application: `python main.py`
3. Configure folder paths and PACS connection in settings.

---

## Русский

DICOM WatchDog — это легковесная утилита для автоматического мониторинга, сортировки и резервного копирования КТ-исследований (DICOM) с рабочих станций сканирования и локальных архивов с возможностью работы с PACS-серверами.

### Ключевые возможности
- **Мониторинг в реальном времени**: Автоматическое сканирование папок на наличие новых КТ-снимков.
- **Исправление метаданных**: Автоматическая коррекция неверных Patient ID (удаление точек, префиксов) в тегах DICOM.
- **Гибкое переименование папок**: Переименование папок пациентов по шаблонам: `Patient ID`, `Patient Name`, `Patient Name [Patient ID]`, или `[Patient ID] Patient Name`.
- **Автоархивирование**: Автоматическое перемещение старых исследований в архив по истечении заданных дней и очистка лишних структур.
- **PACS интеграция**: Быстрый поиск и скачивание исследований напрямую с PACS-серверов.
- **Широкая совместимость**: Поддержка Windows 10/11 (PyQt6/PyQt5) и отдельная legacy-версия для Windows 7.

### Быстрый старт
1. Установите зависимости: `pip install -r requirements.txt`
2. Запустите программу: `python main.py`
3. Настройте пути к папкам и подключение к PACS в меню настроек.
