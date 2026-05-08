"""
Configuration manager for Interview Assistant.
Handles loading/saving settings and Groq API key.
"""

import json
import os
import sys

CONFIG_FILE = "config.json"

DEFAULTS = {
    "groq_api_key": "",
    "interview_mode": "interview",
    "tesseract_path": r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    "ocr_language": "eng",
    "auto_scan_interval_seconds": 10,
    "overlay_display_seconds": 30,
    # NEW — persisted UI preferences
    "font_size": 12,
    "language": "Auto-Detect",
    "resume_text": "",
    # Hotkeys
    "hotkey_scan_screen": "ctrl+enter",
    "hotkey_toggle_audio": "ctrl+shift+a",
    "hotkey_toggle_auto_scan": "ctrl+shift+d",
    "hotkey_move_up": "ctrl+up",
    "hotkey_move_down": "ctrl+down",
    "hotkey_move_left": "ctrl+left",
    "hotkey_move_right": "ctrl+right",
    "hotkey_resize_up": "ctrl+shift+up",
    "hotkey_resize_down": "ctrl+shift+down",
    "hotkey_resize_left": "ctrl+shift+left",
    "hotkey_resize_right": "ctrl+shift+right",
}


def get_config_path():
    if getattr(sys, "frozen", False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, CONFIG_FILE)


def load_config():
    path = get_config_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {**DEFAULTS, **data}
        except (json.JSONDecodeError, IOError):
            return DEFAULTS.copy()
    else:
        save_config(DEFAULTS)
        return DEFAULTS.copy()


def save_config(config: dict):
    path = get_config_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4)


def get_api_key() -> str:
    return load_config().get("groq_api_key", "")


def set_api_key(key: str):
    config = load_config()
    config["groq_api_key"] = key
    save_config(config)


def get_mode() -> str:
    return load_config().get("interview_mode", "interview")


def set_mode(mode: str):
    config = load_config()
    config["interview_mode"] = mode
    save_config(config)