"""tvui command line: setup, serve, open, key, install-service."""
import argparse
import getpass
import os
import re
import shutil
import socket
import subprocess
import sys

from tvui import __version__, config
from tvui.sony import TV, default_tiles, discover


def local_ip(toward):
    """The address of this machine as seen from the TV's network."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((toward, 80))
        return s.getsockname()[0]
    finally:
        s.close()


def cmd_setup(args):
    cfg = config.load()
    ip = args.tv
    if not ip:
        print("Looking for Sony TVs...")
        tvs = discover()
        if not tvs:
            sys.exit("No Sony TV found. Turn the TV on, connect it to the network, "
                     "or pass its IP: tvui setup --tv 192.168.1.50")
        for n, (addr, model) in enumerate(tvs, 1):
            print(f"  {n}. {model} at {addr}")
        ip = tvs[0][0] if len(tvs) == 1 else tvs[int(input("Which one? ")) - 1][0]
    cfg["tv"] = ip

    tv = TV(ip, psk=args.psk or "")
    if args.psk:
        cfg["psk"], cfg["auth"] = args.psk, ""
    else:
        tv.register()
        pin = input("Enter the 4-digit PIN shown on the TV: ").strip()
        token = tv.register(pin)
        if not token:
            sys.exit("Pairing failed. Check the PIN and try again.")
        cfg["auth"], cfg["psk"] = token, ""
        tv.auth = token

    cfg["tiles"] = default_tiles(tv)
    config.save(cfg)
    print(f"\nPaired with {ip}. Home screen tiles: " + ", ".join(t["name"] for t in cfg["tiles"]))
    print(f"Edit tiles in {config.PATH}")
    print("Next: tvui serve --open")


def cmd_serve(args):
    cfg = config.load()
    if not cfg["tv"]:
        sys.exit("Not set up yet. Run: tvui setup")
    if args.port:
        cfg["port"] = args.port
    from tvui.server import serve
    serve(cfg, open_on_tv=args.open)


def cmd_open(args):
    cfg = config.load()
    url = f"http://{local_ip(cfg['tv'])}:{cfg['port']}/"
    TV(cfg["tv"], cfg["auth"], cfg["psk"]).open_url(url)
    print(f"Opened {url} on the TV")


def cmd_key(args):
    cfg = config.load()
    TV(cfg["tv"], cfg["auth"], cfg["psk"]).key(args.name)


def cmd_info(args):
    cfg = config.load()
    ip = local_ip(cfg["tv"]) if cfg["tv"] else "<this-machine>"
    print(f"config:      {config.PATH}")
    print(f"TV:          {cfg['tv'] or '(not set up)'}")
    print(f"home screen: http://{ip}:{cfg['port']}/")
    print(f"phone remote http://{ip}:{cfg['port']}/remote")


def _digest(method, uri, user, password, challenge):
    """An HTTP Digest Authorization header (RFC 2617, MD5, qop=auth) for one request."""
    import hashlib
    import uuid
    f = dict(re.findall(r'(\w+)="?([^",]*)"?', challenge))
    md5 = lambda s: hashlib.md5(s.encode()).hexdigest()  # noqa: E731
    ha1, ha2 = md5(f"{user}:{f['realm']}:{password}"), md5(f"{method}:{uri}")
    nc, cnonce = "00000001", uuid.uuid4().hex[:16]
    if "qop" in f:
        resp = md5(f"{ha1}:{f['nonce']}:{nc}:{cnonce}:auth:{ha2}")
        extra = f', qop=auth, nc={nc}, cnonce="{cnonce}"'
    else:
        resp, extra = md5(f"{ha1}:{f['nonce']}:{ha2}"), ""
    opaque = f', opaque="{f["opaque"]}"' if "opaque" in f else ""
    return (f'Digest username="{user}", realm="{f["realm"]}", nonce="{f["nonce"]}", '
            f'uri="{uri}", response="{resp}"{extra}{opaque}')


def cmd_roku_install(args):
    """Sideload the tvui relay channel onto the Roku (needs Roku developer mode)."""
    import io
    import urllib.error
    import urllib.request
    import uuid
    import zipfile
    from tvui.sony import find_roku

    cfg = config.load()
    ip = args.roku or cfg.get("roku") or find_roku()
    if not ip:
        sys.exit("No Roku found. Pass its IP: tvui roku-install --roku 192.168.1.40 --password ...")
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "roku")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(src):
            for f in files:
                full = os.path.join(root, f)
                z.write(full, os.path.relpath(full, src).replace(os.sep, "/"))

    boundary = uuid.uuid4().hex
    crlf = "\r\n"
    head = (f"--{boundary}{crlf}Content-Disposition: form-data; name=\"mysubmit\"{crlf}{crlf}Install{crlf}"
            f"--{boundary}{crlf}Content-Disposition: form-data; name=\"archive\"; filename=\"tvui-relay.zip\"{crlf}"
            f"Content-Type: application/zip{crlf}{crlf}")
    body = head.encode() + buf.getvalue() + f"{crlf}--{boundary}--{crlf}".encode()
    try:
        # Get the digest challenge with an empty request first: the Roku drops the
        # connection if a 401 arrives while the archive is still uploading.
        try:
            urllib.request.urlopen(f"http://{ip}/plugin_install", timeout=10)
            challenge = ""
        except urllib.error.HTTPError as e:
            challenge = e.headers.get("WWW-Authenticate", "")
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
        if challenge:
            headers["Authorization"] = _digest("POST", "/plugin_install", "rokudev", args.password, challenge)
        req = urllib.request.Request(f"http://{ip}/plugin_install", data=body, headers=headers)
        page = urllib.request.urlopen(req, timeout=60).read().decode(errors="ignore")
    except urllib.error.HTTPError as e:
        sys.exit("The Roku rejected the password." if e.code == 401 else f"Install failed: {e}")
    except OSError as e:
        sys.exit(f"Couldn't reach the Roku's developer installer at {ip}. Is developer mode on? ({e})")
    if "Install Success" not in page and "Identical to previous version" not in page:
        sys.exit("The Roku didn't confirm the install: " + re.sub(r"<[^>]+>", " ", page)[:400])
    cfg["roku"], cfg["roku_relay"] = ip, True
    config.save(cfg)
    print(f"Installed the tvui relay on the Roku at {ip}. Restart tvui to start using it.")


UNIT = """[Unit]
Description=tvui home screen and remote
After=network-online.target
Wants=network-online.target

[Service]
User={user}
Environment=TVUI_CONFIG={config}
ExecStart={exe} serve
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""


def cmd_install_service(args):
    if not sys.platform.startswith("linux") or not shutil.which("systemctl"):
        sys.exit("install-service needs Linux with systemd. On other systems, "
                 "run `tvui serve` at startup instead.")
    exe = shutil.which("tvui") or f"{sys.executable} -m tvui"
    unit = UNIT.format(user=getpass.getuser(), config=config.PATH, exe=exe)
    path = "/etc/systemd/system/tvui.service"
    subprocess.run(["sudo", "tee", path], input=unit.encode(), stdout=subprocess.DEVNULL, check=True)
    subprocess.run(["sudo", "systemctl", "daemon-reload"], check=True)
    subprocess.run(["sudo", "systemctl", "enable", "--now", "tvui"], check=True)
    print("tvui is running and will start on boot.  Logs: journalctl -u tvui -f")


def main():
    p = argparse.ArgumentParser(prog="tvui", description="Custom home screen and phone remote for Sony Bravia TVs")
    p.add_argument("--version", action="version", version=f"tvui {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("setup", help="find and pair with your TV")
    s.add_argument("--tv", help="TV IP address (skips discovery)")
    s.add_argument("--psk", help="pre-shared key, for Android Bravias instead of a PIN")
    s.set_defaults(func=cmd_setup)

    s = sub.add_parser("serve", help="run the home screen and remote server")
    s.add_argument("--port", type=int)
    s.add_argument("--open", action="store_true", help="show the home screen on the TV at start")
    s.set_defaults(func=cmd_serve)

    sub.add_parser("open", help="show the home screen on the TV").set_defaults(func=cmd_open)
    sub.add_parser("info", help="show config and URLs").set_defaults(func=cmd_info)

    s = sub.add_parser("key", help="send a remote key, e.g. tvui key VolumeUp")
    s.add_argument("name")
    s.set_defaults(func=cmd_key)

    sub.add_parser("install-service", help="start on boot (Linux/systemd)").set_defaults(func=cmd_install_service)

    s = sub.add_parser("roku-install", help="install the relay so the Roku remote drives the home screen")
    s.add_argument("--password", required=True, help="the Roku developer-mode password")
    s.add_argument("--roku", help="Roku IP address (found automatically if omitted)")
    s.set_defaults(func=cmd_roku_install)

    args = p.parse_args()
    args.func(args)
