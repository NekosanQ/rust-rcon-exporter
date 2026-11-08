#!/usr/bin/env python3
"""Read Rust WebRCON serverinfo and expose it as Prometheus text at /metrics."""

import json
import math
import os
import signal
import socket
import sys
import threading
import time
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, urlsplit

import websocket

FIELDS = {
    "Framerate": "server_fps",
    "Players": "players_online",
    "EntityCount": "entities_total",
    "Queued": "players_queued",
    "Joining": "players_joining",
    "MaxPlayers": "players_capacity",
    "Uptime": "server_uptime_seconds",
}
REQUIRED_FIELDS = {"Framerate", "Players", "EntityCount"}
TIMEOUT = 6


def to_metrics(data):
    """Convert serverinfo fields to gauges; a failed poll exposes only rcon_up."""
    values = {"rcon_up": int(data is not None)}
    if data is not None:
        if not isinstance(data, dict):
            raise ValueError("serverinfo must be a JSON object")
        for field, name in FIELDS.items():
            value = data.get(field)
            try:
                number = float(value) if type(value) in (int, float) else -1
            except OverflowError:
                number = -1
            if math.isfinite(number) and number >= 0:
                values[name] = number
            elif field in REQUIRED_FIELDS:
                raise ValueError(f"Missing or invalid serverinfo field: {field}")
    return "".join(
        f"# TYPE {name} gauge\n{name} {value:.17g}\n" for name, value in values.items()
    ).encode("utf-8")


class MetricsHandler(BaseHTTPRequestHandler):
    # The poller replaces this immutable snapshot; scrapes never send RCON commands.
    payload = to_metrics(None)

    def do_GET(self):
        if self.path != "/metrics":
            self.send_error(404)
            return
        payload = self.payload
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


def connect(url):
    address = urlsplit(url)
    # Open the socket directly so proxy environment variables cannot intercept RCON.
    transport = socket.create_connection((address.hostname, address.port), TIMEOUT)
    try:
        return websocket.create_connection(
            url, socket=transport, timeout=TIMEOUT, redirect_limit=0
        )
    except Exception:
        transport.close()
        raise


def request_serverinfo(ws, identifier):
    deadline = time.monotonic() + TIMEOUT
    ws.settimeout(TIMEOUT)
    ws.send(
        json.dumps(
            {
                "Identifier": identifier,
                "Message": "serverinfo",
                "Name": "rust-rcon-exporter",
            }
        )
    )
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("serverinfo response timed out")
        ws.settimeout(remaining)
        message = ws.recv()
        if not message:
            raise ConnectionError("WebRCON connection closed")
        try:
            response = json.loads(message)
        except ValueError:
            continue
        if isinstance(response, dict) and response.get("Identifier") == identifier:
            data = response.get("Message")
            data = json.loads(data) if isinstance(data, str) else data
            if not isinstance(data, dict):
                raise ValueError("serverinfo must be a JSON object")
            return data


def poll(url, interval, stopping):
    identifier = 0
    while not stopping.is_set():
        ws = None
        try:
            ws = connect(url)
            while not stopping.is_set():
                identifier += 1
                MetricsHandler.payload = to_metrics(request_serverinfo(ws, identifier))
                stopping.wait(interval)
        except (OSError, ValueError, websocket.WebSocketException) as exc:
            MetricsHandler.payload = to_metrics(None)
            # Never print the exception text: it can contain the password or URL.
            print(f"RCON poll failed ({type(exc).__name__})", file=sys.stderr)
        finally:
            if ws is not None:
                with suppress(OSError, websocket.WebSocketException):
                    ws.close(timeout=1)
        stopping.wait(interval)


def settings():
    password = os.environ.get("RCON_PASSWORD", "")
    host = os.environ.get("RCON_HOST", "127.0.0.1")
    if not password:
        raise ValueError("RCON_PASSWORD is required")
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("RCON_HOST must be 127.0.0.1, localhost, or ::1")
    try:
        rcon_port = int(os.environ.get("RCON_PORT", "28016"))
        metrics_port = int(os.environ.get("METRICS_PORT", "9187"))
        interval = float(os.environ.get("RCON_POLL_INTERVAL", "5"))
    except ValueError:
        raise ValueError("Ports and polling interval must be numeric") from None
    if not 1 <= rcon_port <= 65535 or not 1024 <= metrics_port <= 65535:
        raise ValueError("Invalid RCON_PORT or METRICS_PORT")
    if not 2 <= interval <= 60:
        raise ValueError("RCON_POLL_INTERVAL must be between 2 and 60 seconds")
    host = f"[{host}]" if ":" in host else host
    return f"ws://{host}:{rcon_port}/{quote(password, safe='')}", interval, metrics_port


def main():
    try:
        url, interval, port = settings()
        server = ThreadingHTTPServer(("127.0.0.1", port), MetricsHandler)
    except (ValueError, OSError) as exc:
        message = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        print(f"Cannot start exporter: {message}", file=sys.stderr)
        return 1

    def stop(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    stopping = threading.Event()
    worker = threading.Thread(target=poll, args=(url, interval, stopping), daemon=True)
    worker.start()
    print(f"Serving http://127.0.0.1:{port}/metrics")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stopping.set()
        server.server_close()
        worker.join(timeout=TIMEOUT + 2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
