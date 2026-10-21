"""Vizio SmartCast soundbar control over the local network (no pairing needed for soundbars).

The SmartCast API is HTTPS with a self-signed certificate on port 9000.
"""
import json
import ssl
import urllib.request

# (codeset, code) pairs from Vizio's SmartCast key command table.
KEYS = {
    "VolumeDown": (5, 0),
    "VolumeUp": (5, 1),
    "Mute": (5, 4),  # toggle
    "PowerOff": (11, 0),
    "PowerOn": (11, 1),
}

_INSECURE = ssl.create_default_context()
_INSECURE.check_hostname = False
_INSECURE.verify_mode = ssl.CERT_NONE  # the soundbar's certificate is self-signed


class VizioSoundbar:
    def __init__(self, host):
        self.base = f"https://{host if ':' in host else host + ':9000'}"

    def _call(self, path, body=None, method="GET"):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=4, context=_INSECURE) as r:
            res = json.loads(r.read())
        if res.get("STATUS", {}).get("RESULT") != "SUCCESS":
            raise RuntimeError(f"soundbar {path}: {res.get('STATUS')}")
        return res

    def key(self, name):
        codeset, code = KEYS[name]
        self._call("/key_command/", {"KEYLIST": [{"CODESET": codeset, "CODE": code, "ACTION": "KEYPRESS"}]},
                   method="PUT")

    def is_on(self):
        return self._call("/state/device/power_mode")["ITEMS"][0]["VALUE"] == 1

    def volume(self):
        return self._call("/menu_native/dynamic/audio_settings/audio/volume")["ITEMS"][0]["VALUE"]

    def set_setting(self, path, value):
        """Change a menu setting, e.g. ("system/eco_power", "Off")."""
        url = "/menu_native/dynamic/audio_settings/" + path
        item = self._call(url)["ITEMS"][0]
        self._call(url, {"REQUEST": "MODIFY", "HASHVAL": item["HASHVAL"], "VALUE": value}, method="PUT")
