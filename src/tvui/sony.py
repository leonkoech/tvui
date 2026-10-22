"""Sony Bravia network control: discovery, pairing, JSON-RPC and IRCC keys."""
import http.client
import json
import re
import socket
import threading
import urllib.error
import urllib.request

CLIENT_ID = "tvui:home"
NICKNAME = "tvui"

IRCC = {
    "Up": "AAAAAQAAAAEAAAB0Aw==",
    "Down": "AAAAAQAAAAEAAAB1Aw==",
    "Left": "AAAAAQAAAAEAAAA0Aw==",
    "Right": "AAAAAQAAAAEAAAAzAw==",
    "Confirm": "AAAAAQAAAAEAAABlAw==",
    "Return": "AAAAAgAAAJcAAAAjAw==",
    "Home": "AAAAAQAAAAEAAABgAw==",
    "Options": "AAAAAgAAAJcAAAA2Aw==",
    "Play": "AAAAAgAAAJcAAAAaAw==",
    "Pause": "AAAAAgAAAJcAAAAZAw==",
    "Rewind": "AAAAAgAAAJcAAAAbAw==",
    "Forward": "AAAAAgAAAJcAAAAcAw==",
    "VolumeUp": "AAAAAQAAAAEAAAASAw==",
    "VolumeDown": "AAAAAQAAAAEAAAATAw==",
    "Mute": "AAAAAQAAAAEAAAAUAw==",
    "PowerOff": "AAAAAQAAAAEAAAAvAw==",
    "Netflix": "AAAAAgAAABoAAAB8Aw==",
}


def wake(mac):
    """Wake-on-LAN magic packet, sent to every local subnet's broadcast. The TV only
    listens for it in standby with "Remote Start" on (and most reliably when wired)."""
    payload = bytes([0xFF] * 6) + bytes.fromhex(mac.replace(":", "").replace("-", "")) * 16
    addrs = {a[4][0] for a in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)}
    targets = {"255.255.255.255"} | {a.rsplit(".", 1)[0] + ".255" for a in addrs if not a.startswith("127.")}
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    try:
        for t in targets:
            for port in (9, 7):
                try:
                    s.sendto(payload, (t, port))
                except OSError:
                    pass
    finally:
        s.close()


def discover(timeout=4, pattern=r"SERVER:.*?(K[DL]{1,2}-\w+|XBR-\w+)"):
    """Return [(ip, model)] for devices whose SSDP reply matches pattern (default: Sony TVs)."""
    msg = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n"
           'MAN: "ssdp:discover"\r\nMX: 2\r\nST: ssdp:all\r\n\r\n').encode()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    # Send out every interface: the default route may be a VPN like Tailscale.
    addrs = {a[4][0] for a in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)}
    found = {}
    try:
        for addr in addrs | {"0.0.0.0"}:
            try:
                s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(addr))
                for _ in range(2):
                    s.sendto(msg, ("239.255.255.250", 1900))
            except OSError:
                pass
        while True:
            data, (ip, _) = s.recvfrom(4096)
            m = re.search(pattern, data.decode(errors="ignore"), re.I)
            if m:
                found[ip] = m.group(1)
    except socket.timeout:
        pass
    finally:
        s.close()
    return sorted(found.items())


class TV:
    def __init__(self, ip, auth="", psk=""):
        self.ip, self.auth, self.psk = ip, auth, psk
        # One kept-alive connection: a fresh TCP connection per request costs the
        # TV 100-500ms, a reused one a steady ~100ms.
        self._conn, self._conn_ip = None, None
        self._lock = threading.Lock()

    def _post(self, path, body, headers):
        with self._lock:
            for attempt in (1, 2):
                if self._conn is None or self._conn_ip != self.ip:
                    self._conn = http.client.HTTPConnection(self.ip, 80, timeout=8)
                    self._conn_ip = self.ip
                try:
                    self._conn.request("POST", path, body, headers)
                    r = self._conn.getresponse()
                    data = r.read()
                    break
                except (http.client.HTTPException, OSError):
                    self._conn.close()
                    self._conn = None
                    if attempt == 2:
                        raise
        if r.status != 200:
            raise urllib.error.HTTPError(f"http://{self.ip}{path}", r.status, r.reason, r.headers, None)
        return data

    def _headers(self, extra=None):
        h = {}
        if self.auth:
            h["Cookie"] = "auth=" + self.auth
        if self.psk:
            h["X-Auth-PSK"] = self.psk
        h.update(extra or {})
        return h

    def rpc(self, service, method, params=None, version="1.0"):
        body = json.dumps({"method": method, "params": params or [], "id": 1, "version": version})
        res = json.loads(self._post(f"/sony/{service}", body.encode(),
                                    self._headers({"Content-Type": "application/json"})))
        if "error" in res:
            raise RuntimeError(f"{service}.{method}: {res['error']}")
        return res.get("result", [])

    def register(self, pin=None):
        """PIN pairing. With no PIN (and no cookie) the TV shows one on screen.

        With the current cookie set it renews silently. Returns the new token or None.
        """
        body = json.dumps({
            "method": "actRegister", "id": 1, "version": "1.0",
            "params": [{"clientid": CLIENT_ID, "nickname": NICKNAME, "level": "private"},
                       [{"value": "yes", "function": "WOL"}]]})
        extra = {"Content-Type": "application/json"}
        if pin:
            import base64
            extra["Authorization"] = "Basic " + base64.b64encode(f":{pin}".encode()).decode()
        req = urllib.request.Request(f"http://{self.ip}/sony/accessControl",
                                     data=body.encode(), headers=self._headers(extra))
        try:
            with urllib.request.urlopen(req, timeout=8) as r:
                m = re.search(r"auth=([0-9a-f]+)", r.headers.get("Set-Cookie", ""))
                return m.group(1) if m else None
        except urllib.error.HTTPError as e:
            if e.code == 401:
                return None  # PIN is now on screen
            raise

    def key(self, name):
        soap = (
            '<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
            '<u:X_SendIRCC xmlns:u="urn:schemas-sony-com:service:IRCC:1">'
            f"<IRCCCode>{IRCC[name]}</IRCCCode></u:X_SendIRCC></s:Body></s:Envelope>")
        self._post("/sony/IRCC", soap.encode(), self._headers({
            "Content-Type": "text/xml; charset=UTF-8",
            "SOAPACTION": '"urn:schemas-sony-com:service:IRCC:1#X_SendIRCC"'}))

    def power(self):
        """"active", "standby", or "off" when the TV doesn't answer (network sleeps in standby)."""
        try:
            return self.rpc("system", "getPowerStatus")[0]["status"]
        except Exception:
            return "off"

    def power_quick(self, timeout=1.5):
        """Like power(), on its own short-lived connection so a TV in standby (network
        asleep) can't stall the shared connection that key presses use."""
        body = json.dumps({"method": "getPowerStatus", "params": [], "id": 1, "version": "1.0"})
        conn = http.client.HTTPConnection(self.ip, 80, timeout=timeout)
        try:
            conn.request("POST", "/sony/system", body, self._headers({"Content-Type": "application/json"}))
            return json.loads(conn.getresponse().read())["result"][0]["status"]
        except Exception:
            return "off"
        finally:
            conn.close()

    def apps(self):
        return self.rpc("appControl", "getApplicationList")[0]

    def inputs(self):
        return self.rpc("avContent", "getCurrentExternalInputsStatus")[0]

    def launch(self, uri):
        self.rpc("appControl", "setActiveApp", [{"uri": uri, "data": ""}])

    def set_input(self, uri):
        self.rpc("avContent", "setPlayContent", [{"uri": uri}])

    def open_url(self, url):
        from urllib.parse import quote
        self.launch("localapp://webappruntime?url=" + quote(url, safe=""))


def roku_launch(ip, channel, params=None):
    """Start a channel on a Roku via its External Control Protocol."""
    from urllib.parse import urlencode
    query = "?" + urlencode(params) if params else ""
    req = urllib.request.Request(f"http://{ip}:8060/launch/{channel}{query}", data=b"", method="POST")
    urllib.request.urlopen(req, timeout=6).read()


def roku_input(ip, params):
    """Send parameters to the channel running on the Roku (an roInputEvent)."""
    from urllib.parse import urlencode
    req = urllib.request.Request(f"http://{ip}:8060/input?{urlencode(params)}", data=b"", method="POST")
    urllib.request.urlopen(req, timeout=4).read()


def roku_active_app(ip):
    """The id of the channel in front on the Roku ("dev" for a sideloaded one),
    "home" for the Roku's own home screen, or None."""
    with urllib.request.urlopen(f"http://{ip}:8060/query/active-app", timeout=4) as r:
        xml = r.read().decode(errors="ignore")
    m = re.search(r'<app\b([^>]*)>', xml)
    if not m:
        return None
    attrs = m.group(1)
    if 'type="home"' in attrs or 'id=' not in attrs:  # older Roku OS: <app>Roku</app>
        return "home"
    return re.search(r'id="([^"]+)"', attrs).group(1)


def roku_key(ip, key):
    """Press a key on a Roku (ECP names: Up, Select, Back, Play, Rev, Fwd, Info, ...)."""
    req = urllib.request.Request(f"http://{ip}:8060/keypress/{key}", data=b"", method="POST")
    urllib.request.urlopen(req, timeout=6).read()


def find_roku():
    found = discover(timeout=3, pattern=r"SERVER:.*?(Roku)/")
    return found[0][0] if found else None


def default_tiles(tv):
    """Connected HDMI devices first, then common streaming apps the TV has."""
    tiles = []
    inputs = tv.inputs()
    cec = {re.search(r"port=(\d+)", i["uri"]).group(1): i["label"]
           for i in inputs if i["uri"].startswith("extInput:cec") and i.get("label")}
    for i in inputs:
        if i["uri"].startswith("extInput:hdmi") and i.get("connection"):
            port = re.search(r"port=(\d+)", i["uri"]).group(1)
            name = i.get("label") or cec.get(port) or i["title"]
            tiles.append({"name": name, "action": "input", "value": i["uri"],
                          "label": f"{i['title']} · Switch the TV to this input."})
    apps = tv.apps()
    for w in ["YouTube", "Netflix", "Prime Video", "Hulu", "Disney", "Plex", "Max"]:
        app = next((a for a in apps if w.lower() in a["title"].lower()), None)
        name = "Prime" if w == "Prime Video" else w
        if app:
            tiles.append({"name": name, "action": "app", "value": app["uri"]})
        elif w == "Netflix":  # often missing from the app list but has a remote key
            tiles.append({"name": name, "action": "key", "value": "Netflix"})
    return tiles
