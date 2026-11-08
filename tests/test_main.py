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
from pathlib import Path
from unittest.mock import Mock, patch

from prometheus_client.parser import text_string_to_metric_families
from websockets.exceptions import ConnectionClosed
from websockets.sync.server import serve

import main

SERVERINFO = {"Framerate": 59.5, "Players": 3, "EntityCount": 1234}


class ExporterTests(unittest.TestCase):
    def test_prometheus_output(self):
        families = list(
            text_string_to_metric_families(main.to_metrics(SERVERINFO).decode())
        )
        samples = {
            sample.name: sample.value
            for family in families
            for sample in family.samples
        }
        self.assertEqual(
            samples,
            {
                "rcon_up": 1,
                "server_fps": 59.5,
                "players_online": 3,
                "entities_total": 1234,
            },
        )
        self.assertTrue(all(family.type == "gauge" for family in families))
        self.assertEqual(main.to_metrics(None), b"# TYPE rcon_up gauge\nrcon_up 0\n")

    def test_invalid_required_values_are_rejected(self):
        for field in SERVERINFO:
            for value in (None, True, "60", -1, float("nan"), float("inf"), 10**400):
                with (
                    self.subTest(field=field, value=str(value)[:20]),
                    self.assertRaises(ValueError),
                ):
                    main.to_metrics({**SERVERINFO, field: value})

    def test_optional_fields(self):
        output = main.to_metrics({**SERVERINFO, "Queued": 0, "Joining": "invalid"})
        self.assertIn(b"players_queued 0\n", output)
        self.assertNotIn(b"players_joining", output)

    def test_settings_and_password_encoding(self):
        with patch.dict(
            os.environ, {"RCON_PASSWORD": "a/b?#", "RCON_HOST": "::1"}, clear=True
        ):
            self.assertEqual(main.settings(), ("ws://[::1]:28016/a%2Fb%3F%23", 5, 9187))
        for overrides in (
            {"RCON_PASSWORD": ""},
            {"RCON_HOST": "example.com"},
            {"RCON_PORT": "0"},
            {"METRICS_PORT": "secret-value"},
            {"RCON_POLL_INTERVAL": "nan"},
        ):
            with (
                self.subTest(overrides=overrides),
                patch.dict(
                    os.environ, {"RCON_PASSWORD": "test", **overrides}, clear=True
                ),
            ):
                with self.assertRaises(ValueError) as caught:
                    main.settings()
                self.assertNotIn("secret-value", str(caught.exception))

    def test_request_ignores_unrelated_messages(self):
        ws = Mock()
        ws.recv.side_effect = [
            "console text",
            "[]",
            json.dumps({"Identifier": 0}),
            json.dumps({"Identifier": 42, "Message": json.dumps(SERVERINFO)}),
        ]
        self.assertEqual(main.request_serverinfo(ws, 42), SERVERINFO)
        self.assertEqual(json.loads(ws.send.call_args.args[0])["Message"], "serverinfo")

    def test_unrelated_messages_do_not_extend_timeout(self):
        ws = Mock()
        ws.recv.return_value = "{}"
        with (
            patch("main.time.monotonic", side_effect=[0, 1, 7]),
            self.assertRaises(TimeoutError),
        ):
            main.request_serverinfo(ws, 42)

    @unittest.skipIf(os.name == "nt", "SIGTERM process shutdown test requires POSIX")
    def test_http_conversion_failure_recovery_and_shutdown(self):
        invalid = threading.Event()
        requests = []

        def handler(ws):
            try:
                for raw in ws:
                    request = json.loads(raw)
                    requests.append(request)
                    data = {} if invalid.is_set() else SERVERINFO
                    ws.send(
                        json.dumps(
                            {
                                "Identifier": request["Identifier"],
                                "Message": json.dumps(data),
                            }
                        )
                    )
            except ConnectionClosed:
                pass

        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]

        def scrape(path="/metrics"):
            client = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
            try:
                client.request("GET", path)
                response = client.getresponse()
                return response.status, response.read().decode()
            except OSError:
                return 0, ""
            finally:
                client.close()

        def wait_for(expected):
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                self.assertIsNone(process.poll(), "exporter exited early")
                status, body = scrape()
                if status == 200 and expected in body:
                    return body
                time.sleep(0.02)
            self.fail(f"Timed out waiting for {expected!r}")

        with serve(handler, "127.0.0.1", 0) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(thread.join, 3)
            environ = {
                **os.environ,
                "RCON_PASSWORD": "test-secret",
                "RCON_HOST": "127.0.0.1",
                "RCON_PORT": str(server.socket.getsockname()[1]),
                "METRICS_PORT": str(port),
                "RCON_POLL_INTERVAL": "2",
            }
            process = subprocess.Popen(
                [sys.executable, str(Path(main.__file__).resolve())],
                env=environ,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                self.assertIn("players_online 3\n", wait_for("rcon_up 1\n"))
                self.assertEqual(scrape("/")[0], 404)
                invalid.set()
                self.assertNotIn("server_fps", wait_for("rcon_up 0\n"))
                invalid.clear()
                self.assertIn("server_fps 59.5\n", wait_for("rcon_up 1\n"))
                self.assertTrue(
                    all(request["Message"] == "serverinfo" for request in requests)
                )
                process.send_signal(signal.SIGTERM)
                stdout, stderr = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 0, stderr)
                self.assertNotIn("test-secret", stdout + stderr)
                self.assertNotIn("Traceback", stderr)
            finally:
                if process.poll() is None:
                    process.kill()
                process.communicate(timeout=3)
