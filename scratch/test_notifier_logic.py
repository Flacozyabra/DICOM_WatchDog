import sys
import os
import time

# Добавляем корневую директорию проекта в sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.notifier import show_notification
from core.config_utils import get_resource_path
from core.config_utils import get_app_data_dir

icon_path = os.path.join(get_app_data_dir(), "folder_notification.png")

print("--- Сценарий 1: Только виндоус_нотификэйшн (ожидается тост СО стандартным звуком Windows) ---")
show_notification(
    title="Тест 1 (Только тост)",
    msg="Должен быть стандартный звук Windows",
    durations="short",
    ico_path=icon_path,
    sound_setting="default",
    show_toast=True,
    play_sound=False
)
time.sleep(4)

print("\n--- Сценарий 2: Оба свича (ожидается тост БЕЗ стандартного звука, но с кастомным notification.wav) ---")
show_notification(
    title="Тест 2 (Тост + Кастомный звук)",
    msg="Должен играть notification.wav без стандартного звука Windows",
    durations="short",
    ico_path=icon_path,
    sound_setting="default",
    show_toast=True,
    play_sound=True
)
time.sleep(4)

print("\n--- Сценарий 3: Только саунд_нотификэйшн (ожидается только кастомный звук без тоста) ---")
show_notification(
    title="Тест 3 (Только звук)",
    msg="Не должно быть тоста, только кастомный звук",
    durations="short",
    ico_path=icon_path,
    sound_setting="default",
    show_toast=False,
    play_sound=True
)
time.sleep(3)

print("\nТестирование завершено. Пожалуйста, проверьте наличие записей в pacs_error.log при необходимости.")
