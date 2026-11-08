import http.client
import signal
import socket
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from rust_rcon_exporter.cli import main, run
from rust_rcon_exporter.config import Config
from rust_rcon_exporter.http import make_http_handler
from rust_rcon_exporter.metrics import MetricsState


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.state = MetricsState()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_http_handler(self.state))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = http.client.HTTPConnection(*self.server.server_address, timeout=3)

    def tearDown(self):
        self.client.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def test_metrics_is_available_before_first_poll(self):
        self.client.request("GET", "/metrics")
        response = self.client.getresponse()
        payload = response.read()
        self.assertEqual(response.status, 200)
        self.assertIn("version=0.0.4", response.getheader("Content-Type"))
        self.assertEqual(int(response.getheader("Content-Length")), len(payload))
        self.assertEqual(response.getheader("Cache-Control"), "no-store")
        self.assertIn(b"rcon_up 0\n", payload)

    def test_unknown_path(self):
        self.client.request("GET", "/")
        response = self.client.getresponse()
        response.read()
        self.assertEqual(response.status, 404)

    def test_post_cannot_trigger_commands(self):
        self.client.request("POST", "/metrics")
        response = self.client.getresponse()
        response.read()
        self.assertEqual(response.status, 501)


class CliTests(unittest.TestCase):
    def test_invalid_config_fails_before_listening(self):
        with (
            patch.dict("os.environ", {}, clear=True),
            patch("rust_rcon_exporter.cli.run") as runner,
            self.assertLogs("rust_rcon_exporter.cli", level="ERROR"),
        ):
            self.assertEqual(main([]), 2)
        runner.assert_not_called()

    def test_os_errors_are_redacted(self):
        with (
            patch.dict("os.environ", {"RCON_PASSWORD": "test-secret"}, clear=True),
            patch("rust_rcon_exporter.cli.run", side_effect=OSError("test-secret")),
            self.assertLogs("rust_rcon_exporter.cli", level="ERROR") as logs,
        ):
            self.assertEqual(main([]), 1)
        self.assertNotIn("test-secret", "\n".join(logs.output))

    def test_run_restores_signal_handlers_and_closes_http(self):
        previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
        addresses = []

        def make_server(*args):
            server = ThreadingHTTPServer(*args)
            addresses.append(server.server_address)
            return server

        def poll(state, stopping, config):
            signal.raise_signal(signal.SIGTERM)
            self.assertTrue(stopping.is_set())

        with (
            patch("rust_rcon_exporter.cli.poll_loop", side_effect=poll),
            patch("rust_rcon_exporter.cli.ThreadingHTTPServer", side_effect=make_server),
        ):
            run(Config("test", metrics_port=0))
        with self.assertRaises(OSError):
            socket.create_connection(addresses[0], timeout=1)
        for sig, handler in previous.items():
            self.assertEqual(signal.getsignal(sig), handler)
