"""tvui command line: setup, serve, open, key, install-service."""
import argparse
import getpass
import os
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

    args = p.parse_args()
    args.func(args)
