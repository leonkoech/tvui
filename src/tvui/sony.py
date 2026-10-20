"""Sony Bravia network control: discovery, pairing, JSON-RPC and IRCC keys."""
import json
import re
import socket
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


def discover(timeout=4):
    """Return [(ip, model)] for Sony TVs answering SSDP on the local network."""
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
            m = re.search(r"SERVER:.*?(K[DL]{1,2}-\w+|XBR-\w+)", data.decode(errors="ignore"), re.I)
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
        req = urllib.request.Request(
            f"http://{self.ip}/sony/{service}", data=body.encode(),
            headers=self._headers({"Content-Type": "application/json"}))
        with urllib.request.urlopen(req, timeout=8) as r:
            res = json.loads(r.read())
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
        req = urllib.request.Request(
            f"http://{self.ip}/sony/IRCC", data=soap.encode(),
            headers=self._headers({
                "Content-Type": "text/xml; charset=UTF-8",
                "SOAPACTION": '"urn:schemas-sony-com:service:IRCC:1#X_SendIRCC"'}))
        urllib.request.urlopen(req, timeout=8).read()

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
