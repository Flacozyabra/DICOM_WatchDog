import json
import os
import sys
from core.config_utils import get_config_path

def get_locale_dir():
    if getattr(sys, "frozen", False):
        return os.path.join(sys._MEIPASS, "locales")
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "locales")

_translations = {}

def load_locales():
    global _translations
    locale_dir = get_locale_dir()
    for lang in ['ru', 'en']:
        path = os.path.join(locale_dir, f"{lang}.json")
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    _translations[lang] = json.load(f)
            except Exception as e:
                print(f"Error loading locale {lang}: {e}")
                _translations[lang] = {}
        else:
            _translations[lang] = {}

# Auto load on module import
load_locales()

def get_current_langs():
    config_path = get_config_path()
    interface_lang = 'ru'
    log_lang = 'ru'
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
                interface_lang = config.get('interface_lang', 'ru')
                log_lang = config.get('log_lang', 'ru')
        except Exception:
            pass
    return interface_lang, log_lang

def tr_ui(key, *args):
    lang, _ = get_current_langs()
    val = _translations.get(lang, {}).get(key, key)
    if args:
        try:
            return val.format(*args)
        except Exception:
            pass
    return val

def tr_log(key, *args):
    _, lang = get_current_langs()
    val = _translations.get(lang, {}).get(key, key)
    if args:
        try:
            return val.format(*args)
        except Exception:
            pass
    return val
