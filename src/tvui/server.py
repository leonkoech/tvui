"""HTTP server: home screen for the TV, remote for phones, and an API relay."""
import json
import os
import queue
import threading
import time
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from tvui import config
from tvui.sony import IRCC, TV, discover, find_roku, roku_active_app, roku_input as roku_send_input, roku_key, roku_launch

# Remote buttons that go to the Roku while the TV shows its input.
# Volume, mute and power always stay with the TV.
ROKU_KEYS = {"Up": "Up", "Down": "Down", "Left": "Left", "Right": "Right",
             "Confirm": "Select", "Return": "Back", "Home": "Home", "Options": "Info",
             "Play": "Play", "Pause": "Play", "Rewind": "Rev", "Forward": "Fwd"}

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
PAGES = {"/": "index.html", "/remote": "remote.html"}
TYPES = {".html": "text/html; charset=utf-8", ".png": "image/png", ".jpg": "image/jpeg",
         ".json": "application/manifest+json", ".svg": "image/svg+xml",
         ".woff": "font/woff", ".woff2": "font/woff2"}
RENEW_EVERY = 24 * 3600  # pairing cookies last ~14 days; renew daily


def make_handler(cfg, tv):
    def act(path, q):
        if path == "/api/tile":
            t = cfg["tiles"][int(q["i"])]
            if t["action"] == "app":
                watching["uri"] = None
                tv.launch(t["value"])
            elif t["action"] == "input":
                watching["uri"] = t["value"]
                tv.set_input(t["value"])
            elif t["action"] == "key":
                tv.key(t["value"])
            elif t["action"] == "roku":
                watching["uri"] = t["input"]
                tv.set_input(t["input"])
                launch_on_roku(t["value"])
        elif path == "/api/key":
            press(q["name"])
        elif path == "/api/home":
            watching["uri"] = None
            tv.open_url(q["url"])

    def on_roku(fn, *args):
        ip = cfg.get("roku") or find_roku()
        try:
            fn(ip, *args)
        except (urllib.error.URLError, OSError, TypeError):
            ip = find_roku()  # Roku may have a new address
            if not ip:
                raise RuntimeError("Roku not found on the network")
            fn(ip, *args)
        if cfg.get("roku") != ip:
            cfg["roku"] = ip
            config.save(cfg)

    def launch_on_roku(channel):
        on_roku(roku_launch, channel)

    def roku_input():
        return cfg.get("roku_input") or next(
            (t["input"] for t in cfg["tiles"] if t["action"] == "roku"), None)

    # The TV's current input, refreshed in the background so a key press never waits on it.
    watching = {"uri": None, "tv_on": False}

    def track_input():
        while True:
            try:
                watching["uri"] = tv.rpc("avContent", "getPlayingContentInfo")[0].get("uri")
                watching["tv_on"] = True
            except Exception:
                watching["uri"], watching["tv_on"] = None, False
            ensure_relay()
            time.sleep(1.5)

    # Roku relay: while the TV shows anything but the Roku's input (normally the tvui
    # home screen), keep the tvui relay channel in front on the Roku so the Roku
    # remote's presses come to us. Pressing Home on the Roku remote exits it; this
    # brings it back within a couple of seconds.
    from tvui.cli import local_ip
    relay = {"last": 0.0, "server": None, "told": False}

    def ensure_relay():
        if not cfg.get("roku_relay") or not watching["tv_on"] or roku_input() is None:
            return
        if watching["uri"] == roku_input() or time.time() - relay["last"] < 3:
            return
        try:
            relay["server"] = relay["server"] or f"http://{local_ip(cfg['tv'])}:{cfg['port']}"
            if roku_active_app(cfg.get("roku")) != "dev":
                relay["last"] = time.time()
                roku_launch(cfg.get("roku"), "dev", {"server": relay["server"]})
                relay["told"] = True
            elif not relay["told"]:
                # Already running (e.g. started by the installer): tell it where we are.
                roku_send_input(cfg.get("roku"), {"server": relay["server"]})
                relay["told"] = True
        except Exception as e:
            relay["last"] = time.time()
            print(f"relay: {e}", flush=True)

    threading.Thread(target=track_input, daemon=True).start()

    def watching_roku():
        return roku_input() is not None and watching["uri"] == roku_input()

    paused = {"tv": False}

    def press(name):
        if name == "PlayPause":
            # The Roku's Play key is a real toggle; the TV has none, so alternate.
            if not watching_roku():
                name = "Play" if paused["tv"] else "Pause"
                paused["tv"] = not paused["tv"]
            else:
                name = "Play"
        if name in ROKU_KEYS and watching_roku():
            try:
                on_roku(roku_key, ROKU_KEYS[name])
            except urllib.error.HTTPError as e:
                if e.code == 403:
                    raise RuntimeError("Roku blocks remote keys. On the Roku: Settings > System > "
                                       "Advanced system settings > Control by mobile apps > "
                                       "Network access > Permissive") from e
                raise
        else:
            tv.key(name)

    def act_or_rediscover(path, q):
        try:
            act(path, q)
        except (urllib.error.URLError, OSError) as e:
            if isinstance(e, urllib.error.HTTPError):
                raise
            # TV unreachable: it may have a new DHCP address. Find it and retry once.
            found = [ip for ip, _ in discover(timeout=3)]
            if not found or found[0] == tv.ip:
                raise
            print(f"TV moved {tv.ip} -> {found[0]}", flush=True)
            cfg["tv"] = tv.ip = found[0]
            config.save(cfg)
            act(path, q)

    keys, key_error = queue.Queue(), {"msg": None}

    def key_worker():
        while True:
            name = keys.get()
            try:
                act_or_rediscover("/api/key", {"name": name})
            except Exception as e:
                key_error["msg"] = str(e)
                print(f"key {name} failed: {e}", flush=True)

    threading.Thread(target=key_worker, daemon=True).start()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            name = PAGES.get(url.path) or os.path.basename(url.path)
            ext = os.path.splitext(name)[1]
            if ext in TYPES and os.path.isfile(os.path.join(STATIC, name)):
                with open(os.path.join(STATIC, name), "rb") as f:
                    return self._send(200, f.read(), TYPES[ext])
            if url.path == "/api/time":
                # The TV's own clock is often wrong; the page shows this machine's local time.
                now = time.time()
                offset = time.localtime(now).tm_gmtoff // 60
                return self._send(200, json.dumps({"epoch_ms": int(now * 1000), "utc_offset_min": offset}))
            if url.path == "/api/tiles":
                tiles = [{k: t.get(k) for k in ("name", "action", "label") if t.get(k)}
                         for t in cfg["tiles"]]
                return self._send(200, json.dumps(tiles))
            if url.path not in ("/api/tile", "/api/key", "/api/home"):
                return self._send(404, '{"error":"not found"}')
            if url.path == "/api/key" and q.get("name") not in IRCC and q.get("name") != "PlayPause":
                return self._send(400, '{"error":"unknown key"}')
            if url.path == "/api/key":
                keys.put(q["name"])
                warn, key_error["msg"] = key_error["msg"], None  # report the last failure, if any
                return self._send(200, json.dumps({"ok": True, "warn": warn} if warn else {"ok": True}))
            if url.path == "/api/home":
                q["url"] = f"http://{self.headers['Host']}/"
            try:
                act_or_rediscover(url.path, q)
                self._send(200, '{"ok":true}')
            except Exception as e:  # surface TV errors to the page
                self._send(502, json.dumps({"error": str(e)}))

        def log_message(self, fmt, *args):
            print("%s %s" % (self.address_string(), fmt % args), flush=True)

    return Handler


def _renew(cfg, tv):
    """Swap in a fresh pairing cookie. The TV revokes the old one when it issues this."""
    if not cfg["auth"]:
        return
    try:
        token = tv.register()
        if token and token != cfg["auth"]:
            cfg["auth"] = tv.auth = token
            config.save(cfg)
    except Exception as e:
        print(f"auth renew failed: {e}", flush=True)


def _renew_loop(cfg, tv):
    while True:
        time.sleep(RENEW_EVERY)
        _renew(cfg, tv)


def _web_app_open(tv, home_url):
    try:
        st = tv.rpc("appControl", "getWebAppStatus")[0]
        return st.get("active") and st.get("url", "").startswith(home_url)
    except Exception:
        return False


def _home_on_power_loop(cfg, tv, home_url):
    """Open the home screen whenever the TV turns on, instead of its last input."""
    was_on = tv.power_quick() == "active"
    while True:
        time.sleep(1)
        on = tv.power_quick() == "active"
        if on and not was_on:
            # Launch right away and keep retrying: a TV still booting may refuse the
            # launch, or accept it and then fall back to its last input, so confirm.
            start = time.time()
            while time.time() - start < 25:
                try:
                    tv.open_url(home_url)
                    # The TV takes a moment to report the page as open; don't relaunch too soon.
                    if any(time.sleep(1) or _web_app_open(tv, home_url) for _ in range(3)):
                        print(f"TV turned on: home screen open after {time.time() - start:.1f}s", flush=True)
                        break
                except Exception as e:
                    print(f"power-on: TV not ready yet ({e})", flush=True)
                    time.sleep(1)
            else:
                print("power-on: gave up opening the home screen after 25s", flush=True)
        was_on = on


def serve(cfg, open_on_tv=False):
    from tvui.cli import local_ip
    tv = TV(cfg["tv"], cfg["auth"], cfg["psk"])
    _renew(cfg, tv)  # before serving, so no request races the token swap
    threading.Thread(target=_renew_loop, args=(cfg, tv), daemon=True).start()
    home_url = f"http://{local_ip(cfg['tv'])}:{cfg['port']}/"
    if cfg.get("home_on_power_on", True):
        threading.Thread(target=_home_on_power_loop, args=(cfg, tv, home_url), daemon=True).start()
    httpd = ThreadingHTTPServer(("0.0.0.0", cfg["port"]), make_handler(cfg, tv))
    print(f"tvui serving on port {cfg['port']}  (TV {cfg['tv']})", flush=True)
    if open_on_tv:
        try:
            tv.open_url(home_url)
        except Exception as e:
            print(f"couldn't open on TV: {e}", flush=True)
    httpd.serve_forever()
