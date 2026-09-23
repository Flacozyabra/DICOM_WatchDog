import json
import os
import sys
from core.config_utils import get_config_path

def get_locale_dir():
    if getattr(sys, "frozen", False):
        return os.path.join(sys._MEIPASS, "locales")
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "locales")

_ui_translations = {}
_log_translations = {}

def load_locales():
    global _ui_translations, _log_translations
    locale_dir = get_locale_dir()
    for lang in ['ru', 'en']:
        # Load UI Translations
        ui_path = os.path.join(locale_dir, f"{lang}.json")
        if os.path.exists(ui_path):
            try:
                with open(ui_path, "r", encoding="utf-8") as f:
                    _ui_translations[lang] = json.load(f)
            except Exception as e:
                print(f"Error loading UI locale {lang}: {e}")
                _ui_translations[lang] = {}
        else:
            _ui_translations[lang] = {}
            
        # Load LOG Translations
        log_path = os.path.join(locale_dir, f"log_{lang}.json")
        if os.path.exists(log_path):
            try:
                with open(log_path, "r", encoding="utf-8") as f:
                    _log_translations[lang] = json.load(f)
            except Exception as e:
                print(f"Error loading LOG locale {lang}: {e}")
                _log_translations[lang] = {}
        else:
            _log_translations[lang] = {}

# Auto load on module import
load_locales()

_current_interface_lang = 'en'
_current_log_lang = 'en'

def load_initial_langs():
    global _current_interface_lang, _current_log_lang
    config_path = get_config_path()
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
                _current_interface_lang = config.get('interface_lang', 'en')
                _current_log_lang = config.get('log_lang', 'en')
        except Exception:
            pass

load_initial_langs()

def get_current_langs():
    return _current_interface_lang, _current_log_lang

def set_current_langs(interface_lang, log_lang):
    global _current_interface_lang, _current_log_lang
    _current_interface_lang = interface_lang
    _current_log_lang = log_lang

def tr_ui(key, *args):
    lang, _ = get_current_langs()
    val = _ui_translations.get(lang, {}).get(key)
    if val is None and lang != 'ru':
        val = _ui_translations.get('ru', {}).get(key)
    if val is None and lang != 'en':
        val = _ui_translations.get('en', {}).get(key)
    if val is None:
        val = key

    if args:
        try:
            return val.format(*args)
        except Exception:
            pass
    return val

def tr_log(key, *args):
    _, lang = get_current_langs()
    val = _log_translations.get(lang, {}).get(key)
    if val is None and lang != 'ru':
        val = _log_translations.get('ru', {}).get(key)
    if val is None and lang != 'en':
        val = _log_translations.get('en', {}).get(key)
    if val is None:
        val = key

    if args:
        try:
            return val.format(*args)
        except Exception:
            pass
    return val
