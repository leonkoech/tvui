"""Config lives at ~/.config/tvui/config.json (override with TVUI_CONFIG)."""
import json
import os

PATH = os.environ.get("TVUI_CONFIG") or os.path.join(
    os.path.expanduser("~"), ".config", "tvui", "config.json")

DEFAULTS = {
    "tv": "",          # TV IP address
    "auth": "",        # cookie token from PIN pairing (2013-2015 Bravias)
    "psk": "",         # pre-shared key (Android Bravias)
    "port": 8765,
    "tiles": [],       # [{"name": "...", "action": "app|input|key", "value": "..."}]
}


def load():
    cfg = dict(DEFAULTS)
    try:
        with open(PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except FileNotFoundError:
        pass
    return cfg


def save(cfg):
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, PATH)
    try:
        os.chmod(PATH, 0o600)  # holds the TV auth token
    except OSError:
        pass
