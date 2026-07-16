#!/usr/bin/env python3
"""
Encrypted local storage for provider API keys.

Keys live outside the repo, in the user's home directory, so they survive
a re-clone of the project and never end up in git by accident:

    ~/.palimpsest/secret.key    - random Fernet key, generated on first use, chmod 600
    ~/.palimpsest/keys.enc.json - {provider: encrypted_key}, chmod 600

No master password: the threat model here is "don't leave keys sitting
around in plaintext", not "protect against someone who already has your
user account" (which could read the OS keychain just as easily anyway).

Anthropic also keeps a fallback to the legacy key.txt / ANTHROPIC_API_KEY
so existing setups keep working untouched.
"""

import os
import json
from cryptography.fernet import Fernet, InvalidToken

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".palimpsest")
SECRET_KEY_FILE = os.path.join(CONFIG_DIR, "secret.key")
KEYS_FILE = os.path.join(CONFIG_DIR, "keys.enc.json")
LEGACY_KEY_FILE = os.path.join(HERE, "key.txt")

PROVIDERS = ("anthropic", "openai", "google")


def _ensure_config_dir():
    os.makedirs(CONFIG_DIR, exist_ok=True)
    try:
        os.chmod(CONFIG_DIR, 0o700)
    except OSError:
        pass


def _get_fernet():
    _ensure_config_dir()
    if os.path.exists(SECRET_KEY_FILE):
        with open(SECRET_KEY_FILE, "rb") as f:
            key = f.read().strip()
    else:
        key = Fernet.generate_key()
        with open(SECRET_KEY_FILE, "wb") as f:
            f.write(key)
        try:
            os.chmod(SECRET_KEY_FILE, 0o600)
        except OSError:
            pass
    return Fernet(key)


def _load_store():
    if not os.path.exists(KEYS_FILE):
        return {}
    try:
        with open(KEYS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def _save_store(store):
    _ensure_config_dir()
    with open(KEYS_FILE, "w", encoding="utf-8") as f:
        json.dump(store, f)
    try:
        os.chmod(KEYS_FILE, 0o600)
    except OSError:
        pass


def _legacy_anthropic_key():
    if os.path.exists(LEGACY_KEY_FILE):
        with open(LEGACY_KEY_FILE, "r", encoding="utf-8") as f:
            k = f.read().strip()
            if k:
                return k
    return os.environ.get("ANTHROPIC_API_KEY", "").strip()


def get_key(provider):
    """Return the plaintext API key for a provider, or '' if none is set."""
    store = _load_store()
    enc = store.get(provider)
    if enc:
        try:
            fernet = _get_fernet()
            return fernet.decrypt(enc.encode("utf-8")).decode("utf-8")
        except (InvalidToken, ValueError):
            pass
    if provider == "anthropic":
        return _legacy_anthropic_key()
    return ""


def set_key(provider, api_key):
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider: {provider}")
    api_key = (api_key or "").strip()
    fernet = _get_fernet()
    store = _load_store()
    if api_key:
        store[provider] = fernet.encrypt(api_key.encode("utf-8")).decode("utf-8")
    else:
        store.pop(provider, None)
    _save_store(store)


def clear_key(provider):
    store = _load_store()
    if provider in store:
        store.pop(provider)
        _save_store(store)


def key_status():
    """{provider: bool} - whether a usable key is currently available."""
    return {p: bool(get_key(p)) for p in PROVIDERS}
