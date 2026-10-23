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


def find_soundbars(near_ip, timeout=1.5):
    """SmartCast soundbars on near_ip's /24, as ["ip:9000", ...]. Used when the address changes."""
    import socket
    from concurrent.futures import ThreadPoolExecutor

    prefix = near_ip.rsplit(".", 1)[0]

    def probe(n):
        host = f"{prefix}.{n}"
        try:
            socket.create_connection((host, 9000), timeout=timeout).close()
            info = VizioSoundbar(host)._call("/state/device/deviceinfo")["ITEMS"][0]["VALUE"]
            return f"{host}:9000" if info.get("CAPABILITIES", {}).get("type") == "sb" else None
        except Exception:
            return None

    with ThreadPoolExecutor(64) as pool:
        return [h for h in pool.map(probe, range(1, 255)) if h]


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

    def set_power(self, on):
        """Make the soundbar on or off, sending nothing if it already is (never a toggle)."""
        try:
            if self.is_on() == on:
                return False
        except Exception:
            pass  # state unknown: send the discrete command anyway
        self.key("PowerOn" if on else "PowerOff")
        return True

    def is_on(self):
        return self._call("/state/device/power_mode")["ITEMS"][0]["VALUE"] == 1

    def volume(self):
        return self._call("/menu_native/dynamic/audio_settings/audio/volume")["ITEMS"][0]["VALUE"]

    def bt_pair(self):
        """Switch to the Bluetooth input and make the soundbar discoverable for pairing."""
        self.set_power(True)
        self.set_setting("input/current_input", "Bluetooth")
        url = "/menu_native/dynamic/audio_settings/audio/bluetooth_pair"
        item = self._call(url)["ITEMS"][0]
        self._call(url, {"REQUEST": "ACTION", "HASHVAL": item["HASHVAL"]}, method="PUT")

    def get_setting(self, path):
        return self._call("/menu_native/dynamic/audio_settings/" + path)["ITEMS"][0]["VALUE"]

    def adjust(self, path, delta, lo=-12, hi=12):
        """Nudge a level such as audio/bass by delta, within lo..hi."""
        v = max(lo, min(hi, int(self.get_setting(path)) + delta))
        self.set_setting(path, v)
        return v

    def state(self):
        g = self.get_setting
        return {"on": self.is_on(), "volume": self.volume(), "mute": g("audio/mute"),
                "input": g("input/current_input"), "eq": g("audio/eq"), "bass": g("audio/bass"),
                "treble": g("audio/treble"), "truvolume": g("audio/truvolume"),
                "trusurround": g("audio/trusurround")}

    def set_setting(self, path, value):
        """Change a menu setting, e.g. ("system/eco_power", "Off")."""
        url = "/menu_native/dynamic/audio_settings/" + path
        item = self._call(url)["ITEMS"][0]
        self._call(url, {"REQUEST": "MODIFY", "HASHVAL": item["HASHVAL"], "VALUE": value}, method="PUT")
