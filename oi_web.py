#!/usr/bin/env python3
"""Minimal backend for the oi monitor page (standard library only)."""

import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST = "0.0.0.0"
PORT = int(os.environ.get("OI_PORT", "8768"))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PAGE_FILE = os.environ.get("OI_PAGE", os.path.join(BASE_DIR, "oi_page.html"))
DATA_FILE = os.environ.get("OI_SCAN_JSON", "/home/tou/.hermes/oi_monitor/oi_scan.json")
SCAN_SCRIPT = os.environ.get("OI_SCAN_SCRIPT", "/home/tou/.hermes/scripts/oi_scan.py")

_refresh_lock = threading.Lock()
_refresh_proc = None


def parse_epoch(value):
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        try:
            dt = datetime.fromisoformat(value.rsplit("+", 1)[0].rstrip("Z"))
        except ValueError:
            return None
    try:
        return int(dt.timestamp())
    except (OverflowError, OSError, ValueError):
        return None


def refreshing():
    global _refresh_proc
    with _refresh_lock:
        if _refresh_proc is not None and _refresh_proc.poll() is not None:
            _refresh_proc = None
        return _refresh_proc is not None


def start_refresh():
    global _refresh_proc
    with _refresh_lock:
        if _refresh_proc is not None and _refresh_proc.poll() is not None:
            _refresh_proc = None
        if _refresh_proc is not None:
            return False
        try:
            _refresh_proc = subprocess.Popen(
                [sys.executable, SCAN_SCRIPT],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            _refresh_proc = None
            return False
        return True


class Handler(BaseHTTPRequestHandler):
    server_version = "oi_web"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, content_type):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, code, payload, extra_headers=None):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra_headers or []):
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            self.serve_page()
        elif path == "/api/scan":
            self.serve_scan()
        elif path == "/health":
            self._send_json(200, {"ok": True})
        else:
            self._send_json(404, {"error": "not found"})

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        length = self.headers.get("Content-Length")
        try:
            remaining = int(length) if length else 0
        except ValueError:
            remaining = 0
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 65536))
            if not chunk:
                break
            remaining -= len(chunk)

        if path == "/api/refresh":
            self._send_json(200, {"started": start_refresh()})
        else:
            self._send_json(404, {"error": "not found"})

    def serve_page(self):
        try:
            with open(PAGE_FILE, "rb") as fh:
                body = fh.read()
        except OSError:
            self._send(
                500,
                "oi_page.html not found",
                "text/plain; charset=utf-8",
            )
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def serve_scan(self):
        data = None
        error = None
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            error = "oi_scan.json not found"
        except OSError as exc:
            error = "cannot read oi_scan.json: %s" % exc
        except ValueError as exc:
            error = "invalid json: %s" % exc

        if error is not None:
            self._send_json(200, {"ok": False, "error": error})
            return

        if not isinstance(data, dict):
            self._send_json(200, {"ok": False, "error": "oi_scan.json is not an object"})
            return

        generated = parse_epoch(data.get("generated_at"))
        data["ok"] = True
        data["stale_sec"] = None if generated is None else int(time.time()) - generated
        data["refreshing"] = refreshing()
        self._send_json(200, data)


def main():
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    httpd.daemon_threads = True
    print("oi_web listening on %s:%d" % (HOST, PORT), flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
