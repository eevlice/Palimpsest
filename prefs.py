#!/usr/bin/env python3
"""
User preferences: a flat, schema-less key-value store for cross-session UI
choices - default model today, whatever joins it later.

~/.palimpsest/prefs.json - one JSON object, next to keys.enc.json and
spending.jsonl. New preferences are just new keys; this module never needs
to change to support one.
"""

import os
import json

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".palimpsest")
PREFS_FILE = os.path.join(CONFIG_DIR, "prefs.json")


def get_all():
    if not os.path.exists(PREFS_FILE):
        return {}
    try:
        with open(PREFS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def update(patch):
    """Merge patch into the stored prefs and persist the result."""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    current = get_all()
    current.update(patch)
    with open(PREFS_FILE, "w", encoding="utf-8") as f:
        json.dump(current, f, ensure_ascii=False, indent=2)
    return current
