import os
import shutil
import yaml
from dataclasses import dataclass
from typing import Any, Dict, Tuple

DATA_DIR = os.path.expanduser("~/.ttracker")
DATA_FILE = os.path.join(DATA_DIR, "data.yaml")
DATA_BACKUP_FILE = os.path.join(DATA_DIR, "data-backup.yaml")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.yaml")


def ensure_dirs() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


@dataclass
class Storage:
    data: Dict[str, Any]
    settings: Dict[str, Any]


DEFAULT_SETTINGS = {
    "app_hotkey": "<Ctrl><Alt><Shift>T",  # show/hide window
    "task_hotkeys": {},  # task_id -> accel string
    "window": {"x": None, "y": None, "width": None, "height": None, "maximized": False},
}


def load_all() -> Storage:
    ensure_dirs()
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}
    else:
        data = {}
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
            settings = yaml.safe_load(f) or {}
    else:
        settings = {}
    merged_settings = DEFAULT_SETTINGS.copy()
    merged_settings.update(settings)
    if "task_hotkeys" not in merged_settings or not isinstance(merged_settings["task_hotkeys"], dict):
        merged_settings["task_hotkeys"] = {}
    return Storage(data=data, settings=merged_settings)


def save_data(data: Dict[str, Any]) -> None:
    ensure_dirs()
    # Backup previous data
    if os.path.exists(DATA_FILE):
        try:
            shutil.copy2(DATA_FILE, DATA_BACKUP_FILE)
        except Exception:
            pass
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)


def save_settings(settings: Dict[str, Any]) -> None:
    ensure_dirs()
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        yaml.safe_dump(settings, f, sort_keys=False, allow_unicode=True)


# Schema helpers

def new_empty_data() -> Dict[str, Any]:
    return {
        "version": 1,
        "tasks": [],  # list of task dicts recursively
        "active_task_id": None,
        "active_started_at": None,  # ISO timestamp
    }


def make_task_dict(task_id: str, name: str) -> Dict[str, Any]:
    return {
        "id": task_id,
        "name": name,
        "color": None,
        "children": [],
        "daily_goal_sec": None,  # int seconds or None
        "time_entries": [],  # list of {start: iso, end: iso}
        "adjustments": [],  # list of {ts: iso, delta_sec: int}
    }
