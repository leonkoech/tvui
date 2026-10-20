"""HTTP server: home screen for the TV, remote for phones, and an API relay."""
import json
import os
import threading
import time
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from tvui import config
from tvui.sony import IRCC, TV, discover, find_roku, roku_launch

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
PAGES = {"/": "index.html", "/remote": "remote.html"}
TYPES = {".html": "text/html; charset=utf-8", ".png": "image/png",
         ".json": "application/manifest+json", ".svg": "image/svg+xml"}
RENEW_EVERY = 24 * 3600  # pairing cookies last ~14 days; renew daily


def make_handler(cfg, tv):
    def act(path, q):
        if path == "/api/tile":
            t = cfg["tiles"][int(q["i"])]
            if t["action"] == "app":
                tv.launch(t["value"])
            elif t["action"] == "input":
                tv.set_input(t["value"])
            elif t["action"] == "key":
                tv.key(t["value"])
            elif t["action"] == "roku":
                tv.set_input(t["input"])
                launch_on_roku(t["value"])
        elif path == "/api/key":
            tv.key(q["name"])
        elif path == "/api/home":
            tv.open_url(q["url"])

    def launch_on_roku(channel):
        ip = cfg.get("roku") or find_roku()
        try:
            roku_launch(ip, channel)
        except (urllib.error.URLError, OSError, TypeError):
            ip = find_roku()  # Roku may have a new address
            if not ip:
                raise RuntimeError("Roku not found on the network")
            roku_launch(ip, channel)
        if cfg.get("roku") != ip:
            cfg["roku"] = ip
            config.save(cfg)

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
            if url.path == "/api/tiles":
                tiles = [{k: t.get(k) for k in ("name", "action", "label") if t.get(k)}
                         for t in cfg["tiles"]]
                return self._send(200, json.dumps(tiles))
            if url.path not in ("/api/tile", "/api/key", "/api/home"):
                return self._send(404, '{"error":"not found"}')
            if url.path == "/api/key" and q.get("name") not in IRCC:
                return self._send(400, '{"error":"unknown key"}')
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


def _home_on_power_loop(cfg, tv, home_url):
    """Open the home screen whenever the TV turns on, instead of its last input."""
    was_on = tv.power() == "active"
    while True:
        time.sleep(4)
        on = tv.power() == "active"
        if on and not was_on:
            time.sleep(6)  # let the TV finish booting before it accepts app launches
            for _ in range(3):
                try:
                    tv.open_url(home_url)
                    print("TV turned on: opened home screen", flush=True)
                    break
                except Exception as e:
                    print(f"couldn't open home on power-on: {e}", flush=True)
                    time.sleep(5)
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
