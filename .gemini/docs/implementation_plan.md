# План инициализации Git-репозитория и изменения названия приложения

Этот документ описывает план создания локального репозитория, связывания его с удаленным репозиторием на GitHub, создания файла `.gitignore` и обновления заголовка окна приложения на «DICOM Explorer».

## Предложенные изменения

### 1. Изменение названия в коде ([main.py](file:///c:/Users/Falco/Desktop/Patient%20list/main.py))

* Изменить заголовок главного окна в конструкторе `__init__` класса `MainWindow` (строка 26) с `"Patient List"` на `"DICOM Explorer"`:
  ```python
  self.setWindowTitle("DICOM Explorer")
  ```

### 2. Создание файла [.gitignore](file:///c:/Users/Falco/Desktop/Patient%20list/.gitignore)

* Создать файл `.gitignore`, чтобы исключить из системы контроля версий временные файлы, виртуальное окружение, логи и папки с тестовыми КТ-снимками пациентов:
  ```
  # Виртуальное окружение
  venv/
  .venv/

  # Кэш Python
  __pycache__/
  *.pyc

  # Папки с медицинскими данными (тестовые снимки)
  ct_images/
  ct_archive/
  Client_dir/

  # Локальные файлы конфигурации и логи
  config.txt
  *.log
  ```

### 3. Инициализация Git-репозитория

* Выполнить следующие команды в терминале:
  1. `git init` — инициализация репозитория.
  2. Добавить удаленный репозиторий:
     ```bash
     git remote add origin https://github.com/Flacozyabra/DICOM_Explorer
     ```
  3. Переименовать ветку по умолчанию в `main`:
     ```bash
     git branch -M main
     ```
  4. Добавить все файлы (кроме игнорируемых) и сделать первый коммит:
     ```bash
     git add .
     git commit -m "feat: initial commit with DICOM Explorer codebase"
     ```
  5. Предложить или выполнить отправку кода на GitHub (`git push -u origin main`).

---

## План верификации

### Автоматические тесты
* Проверка синтаксиса `main.py` через `py_compile`.
* Проверка статуса репозитория через `git status`.
