import json
import threading
import unittest
from unittest.mock import Mock, patch

from rust_rcon_exporter.config import Config
from rust_rcon_exporter.metrics import MetricsState
from rust_rcon_exporter.rcon import connect, poll_loop, request_serverinfo

CORE = {"Framerate": 60, "Players": 1, "EntityCount": 100}


class RconTests(unittest.TestCase):
    def test_request_ignores_unrelated_messages(self):
        ws = Mock()
        ws.recv.side_effect = [
            "console text",
            "[]",
            b"\xff",
            json.dumps({"Identifier": 0, "Message": "chat"}),
            json.dumps({"Identifier": 42, "Message": json.dumps(CORE)}),
        ]
        self.assertEqual(request_serverinfo(ws, 42)["server_fps"], 60)
        self.assertEqual(
            json.loads(ws.send.call_args.args[0]),
            {"Identifier": 42, "Message": "serverinfo", "Name": "rust-rcon-exporter"},
        )
        self.assertTrue(all(call.args[0] > 0 for call in ws.settimeout.call_args_list))

    def test_bad_matching_response_fails(self):
        ws = Mock()
        ws.recv.return_value = json.dumps({"Identifier": 42, "Message": "{}"})
        with self.assertRaises(ValueError):
            request_serverinfo(ws, 42)

    def test_closed_connection_fails_immediately(self):
        ws = Mock()
        ws.recv.return_value = ""
        with self.assertRaises(ConnectionError):
            request_serverinfo(ws, 42)
        self.assertEqual(ws.recv.call_count, 1)

    def test_unrelated_messages_do_not_extend_deadline(self):
        ws = Mock()
        ws.recv.return_value = "{}"
        with patch("rust_rcon_exporter.rcon.time.monotonic", side_effect=[0, 1, 2, 7]):
            with self.assertRaises(TimeoutError):
                request_serverinfo(ws, 42, timeout=6)
        self.assertEqual(ws.recv.call_count, 2)

    def test_connect_bypasses_proxy_and_redirects(self):
        with (
            patch("websocket.create_connection") as create,
            patch("rust_rcon_exporter.rcon.socket.create_connection") as transport,
        ):
            connect(Config("test"))
        options = create.call_args.kwargs
        self.assertEqual(options["redirect_limit"], 0)
        self.assertIs(options["socket"], transport.return_value)
        transport.assert_called_once_with(("127.0.0.1", 28016), timeout=6)

    def test_failed_handshake_closes_transport(self):
        with (
            patch("websocket.create_connection", side_effect=ValueError("bad-handshake")),
            patch("rust_rcon_exporter.rcon.socket.create_connection") as transport,
            self.assertRaises(ValueError),
        ):
            connect(Config("test"))
        transport.return_value.close.assert_called_once()

    def test_bad_responses_back_off_even_after_successful_handshakes(self):
        stopping = threading.Event()
        state = MetricsState()
        state.record_success({"server_fps": 60}, 0.1)
        waits = []
        ws = Mock()

        def wait(delay):
            self.assertEqual(ws.close.call_count, len(waits) + 1)
            waits.append(delay)
            if len(waits) == 7:
                stopping.set()

        with (
            patch("rust_rcon_exporter.rcon.connect", return_value=ws),
            patch(
                "rust_rcon_exporter.rcon.request_serverinfo", side_effect=ValueError("test-secret")
            ),
            patch.object(stopping, "wait", side_effect=wait),
            self.assertLogs("rust_rcon_exporter.rcon", level="WARNING") as logs,
        ):
            poll_loop(state, stopping, Config("test-secret"))
        self.assertEqual(waits, [1, 2, 4, 8, 16, 30, 30])
        self.assertNotIn("test-secret", "\n".join(logs.output))
        self.assertNotIn(b"server_fps", state.render())

    def test_backoff_resets_only_after_successful_poll(self):
        stopping = threading.Event()
        waits = []

        def wait(delay):
            waits.append(delay)
            if len(waits) == 4:
                stopping.set()

        with (
            patch("rust_rcon_exporter.rcon.connect", return_value=Mock()),
            patch(
                "rust_rcon_exporter.rcon.request_serverinfo",
                side_effect=[ValueError(), ValueError(), {"server_fps": 60}, ValueError()],
            ),
            patch.object(stopping, "wait", side_effect=wait),
            self.assertLogs("rust_rcon_exporter.rcon", level="WARNING"),
        ):
            poll_loop(MetricsState(), stopping, Config("test"))
        self.assertEqual(waits[:2], [1, 2])
        self.assertGreater(waits[2], 0)
        self.assertEqual(waits[3], 1)

    def test_stop_during_poll_interval_closes_connection(self):
        stopping = threading.Event()
        ws = Mock()
        with (
            patch("rust_rcon_exporter.rcon.connect", return_value=ws),
            patch("rust_rcon_exporter.rcon.request_serverinfo", return_value={"server_fps": 60}),
            patch.object(stopping, "wait", side_effect=lambda _: stopping.set()),
        ):
            poll_loop(MetricsState(), stopping, Config("test"))
        ws.close.assert_called_once_with(timeout=1)
