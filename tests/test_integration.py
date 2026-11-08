"""Exercise real WebSockets, the HTTP endpoint, and CLI process shutdown."""

import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import quote

from websockets.exceptions import ConnectionClosed
from websockets.sync.server import serve

from rust_rcon_exporter.config import Config
from rust_rcon_exporter.rcon import connect, request_serverinfo


class IntegrationTests(unittest.TestCase):
    def test_real_websocket_ignores_proxy_environment(self):
        paths = []

        def handler(ws):
            paths.append(ws.request.path)
            request = json.loads(ws.recv(timeout=3))
            ws.send(json.dumps({"Identifier": 0, "Message": "chat"}))
            ws.send(
                json.dumps(
                    {
                        "Identifier": request["Identifier"],
                        "Message": json.dumps({"Framerate": 60, "Players": 2, "EntityCount": 1000}),
                    }
                )
            )

        with serve(handler, "127.0.0.1", 0) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(thread.join, 3)
            password = "test/secret ?#%日本語"
            config = Config(password, rcon_port=server.socket.getsockname()[1])
            with patch.dict(os.environ, {"http_proxy": "http://127.0.0.1:1", "no_proxy": ""}):
                ws = connect(config)
                try:
                    self.assertEqual(request_serverinfo(ws, 42)["players_online"], 2)
                finally:
                    ws.close(timeout=1)
        self.assertEqual(paths, ["/" + quote(password, safe="")])

    def test_redirect_is_rejected_without_contacting_target(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        from websocket import WebSocketException

        contacted = threading.Event()

        def target_handler(ws):
            contacted.set()

        with serve(target_handler, "127.0.0.1", 0) as target:
            target_thread = threading.Thread(target=target.serve_forever, daemon=True)
            target_thread.start()
            self.addCleanup(target_thread.join, 3)
            target_port = target.socket.getsockname()[1]

            class RedirectHandler(BaseHTTPRequestHandler):
                def do_GET(self):
                    self.send_response(302)
                    self.send_header("Location", f"ws://127.0.0.1:{target_port}/redirected-secret")
                    self.end_headers()

                def log_message(self, *args):
                    pass

            with ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler) as redirect:
                redirect_thread = threading.Thread(target=redirect.serve_forever, daemon=True)
                redirect_thread.start()
                try:
                    with self.assertRaises(WebSocketException):
                        connect(Config("test-secret", rcon_port=redirect.server_port))
                finally:
                    redirect.shutdown()
                    redirect_thread.join(timeout=3)
        self.assertFalse(contacted.is_set())

    @unittest.skipIf(os.name == "nt", "Graceful SIGTERM delivery requires POSIX")
    def test_cli_success_failure_connection_reuse_and_shutdown(self):
        connections = []
        requests = []

        def handler(ws):
            connections.append(ws)
            try:
                for raw in ws:
                    request = json.loads(raw)
                    requests.append(request)
                    payload = {"Framerate": 60, "Players": 2, "EntityCount": 1000}
                    if len(requests) > 1:
                        payload = {}  # Force removal of the previous successful snapshot.
                    ws.send(
                        json.dumps(
                            {"Identifier": request["Identifier"], "Message": json.dumps(payload)}
                        )
                    )
            except ConnectionClosed:
                pass

        # Reserve an OS-selected HTTP port; closing it immediately before launch
        # leaves only a small race with other processes on the test machine.
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            metrics_port = reservation.getsockname()[1]

        with serve(handler, "127.0.0.1", 0) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(thread.join, 3)
            environ = {
                **os.environ,
                "RCON_HOST": "127.0.0.1",
                "RCON_PASSWORD": "test-secret",
                "RCON_PORT": str(server.socket.getsockname()[1]),
                "METRICS_PORT": str(metrics_port),
                "RCON_POLL_INTERVAL": "2",
            }
            process = subprocess.Popen(
                [sys.executable, "-m", "rust_rcon_exporter"],
                env=environ,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                success = self.wait_for_metric(process, metrics_port, "rcon_up 1\n")
                self.assertIn("server_fps 60\n", success)
                failure = self.wait_for_metric(process, metrics_port, "rcon_up 0\n")
                self.assertNotIn("server_fps", failure)
                self.assertIn("rcon_last_success_timestamp_seconds", failure)
                self.assertEqual(len(connections), 1)
                self.assertEqual(len(requests), 2)
                self.assertTrue(all(request["Message"] == "serverinfo" for request in requests))
                process.send_signal(signal.SIGTERM)
                stdout, stderr = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 0, stderr)
                self.assertNotIn("test-secret", stdout + stderr)
                self.assertNotIn("Traceback", stderr)
                self.assertIn("retry in 1s", stderr)
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=3)

    def wait_for_metric(self, process, port, expected):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            self.assertIsNone(process.poll(), "exporter exited before serving expected metrics")
            client = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
            try:
                client.request("GET", "/metrics")
                response = client.getresponse()
                payload = response.read().decode()
                if response.status == 200 and expected in payload:
                    return payload
            except OSError:
                pass
            finally:
                client.close()
            time.sleep(0.02)
        self.fail(f"timed out waiting for {expected!r}")
