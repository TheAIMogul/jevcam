#!/usr/bin/env python3
"""pi-07 app chooser.

Serves a launcher page on :8080 that lets you pick App 1 (JevCam, :8000) or
App 2 (PPE Monitor, :3001). The page opens the chosen app full-screen; this
server only reports which apps are up (the apps don't send CORS headers, so
the page can't ask them directly). Standard library only.
"""
import json
import os
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("PORT", "8080"))

with open(os.path.join(HERE, "apps.json")) as f:
    APPS = json.load(f)
POOL = ThreadPoolExecutor(max_workers=max(2, len(APPS)))


def probe(app):
    url = f"http://127.0.0.1:{app['port']}{app['health']}"
    try:
        with urllib.request.urlopen(url, timeout=1.5) as r:
            return {"id": app["id"], "up": r.status == 200}
    except Exception as e:  # noqa: BLE001
        return {"id": app["id"], "up": False, "error": str(e)[:160]}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code, ctype, body, cache="no-store"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj):
        self._send(200, "application/json", json.dumps(obj).encode())

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                return self._send(200, "text/html; charset=utf-8", f.read())
        if path == "/api/apps":
            return self._json(APPS)
        if path == "/api/status":
            return self._json(list(POOL.map(probe, APPS)))
        return self._send(404, "text/plain", b"not found")


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
