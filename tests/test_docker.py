"""Opt-in integration test against the production Docker Compose service."""

import http.client
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from urllib.parse import quote

from websockets.exceptions import ConnectionClosed
from websockets.sync.server import serve

IMAGE = os.environ.get("RUST_RCON_TEST_IMAGE")
ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(IMAGE and shutil.which("docker"), "Set RUST_RCON_TEST_IMAGE to a built image")
class DockerTests(unittest.TestCase):
    def docker(self, *args, check=True):
        result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=60)
        if check:
            self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout + result.stderr if args[0] == "logs" else result.stdout

    def wait_for(self, predicate, description):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.1)
        self.fail(f"Timed out waiting for {description}")

    def test_compose_polling_recovery_health_and_shutdown(self):
        fail_response = threading.Event()
        paths = []
        requests = []

        def handler(ws):
            paths.append(ws.request.path)
            try:
                for raw in ws:
                    request = json.loads(raw)
                    requests.append(request)
                    values = (
                        {}
                        if fail_response.is_set()
                        else {
                            "Framerate": 60,
                            "Players": 3,
                            "EntityCount": 1234,
                        }
                    )
                    ws.send(
                        json.dumps(
                            {
                                "Identifier": request["Identifier"],
                                "Message": json.dumps(values),
                            }
                        )
                    )
            except ConnectionClosed:
                pass

        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            metrics_port = reservation.getsockname()[1]

        def metric_payload():
            client = http.client.HTTPConnection("127.0.0.1", metrics_port, timeout=1)
            try:
                client.request("GET", "/metrics")
                response = client.getresponse()
                return response.read().decode() if response.status == 200 else ""
            except OSError:
                return ""
            finally:
                client.close()

        with tempfile.TemporaryDirectory(prefix="rust-rcon-docker-") as temporary:
            directory = Path(temporary)
            # Dollar signs, quotes, spaces, hashes and Unicode must survive env_file unchanged.
            password = 'docker-test/$UNDEFINED " #?%日本語'
            override = directory / "override.json"
            override.write_text(
                json.dumps(
                    {
                        "services": {
                            "exporter": {
                                "image": IMAGE,
                                "healthcheck": {"interval": "1s", "start_period": "0s"},
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            compose = [
                "compose",
                "--project-name",
                "rust-rcon-test-" + uuid.uuid4().hex[:12],
                "--project-directory",
                str(directory),
                "-f",
                str(ROOT / "compose.yaml"),
                "-f",
                str(override),
            ]
            with serve(handler, "127.0.0.1", 0) as server:
                server_thread = threading.Thread(target=server.serve_forever, daemon=True)
                server_thread.start()
                self.addCleanup(server_thread.join, 3)
                (directory / "exporter.env").write_text(
                    f"RCON_PASSWORD={password}\nRCON_HOST=127.0.0.1\n"
                    f"RCON_PORT={server.socket.getsockname()[1]}\nRCON_POLL_INTERVAL=2\n"
                    f"METRICS_PORT={metrics_port}\n",
                    encoding="utf-8",
                )
                try:
                    resolved = json.loads(self.docker(*compose, "config", "--format", "json"))
                    self.assertEqual(list(resolved["services"]), ["exporter"])
                    self.docker(*compose, "up", "--detach", "--no-build", "--pull", "never")
                    container = self.docker(*compose, "ps", "--quiet", "exporter").strip()
                    self.assertTrue(container)

                    def inspect(field):
                        return json.loads(
                            self.docker("inspect", "--format", "{{json " + field + "}}", container)
                        )

                    self.assertEqual(inspect(".Config.User"), "10001:10001")
                    self.assertIn("RCON_PASSWORD=" + password, inspect(".Config.Env"))
                    self.assertTrue(inspect(".HostConfig.ReadonlyRootfs"))
                    self.assertIn("ALL", inspect(".HostConfig.CapDrop"))
                    self.assertEqual(inspect(".HostConfig.NetworkMode"), "host")
                    self.assertFalse(inspect(".Mounts"))
                    self.docker(
                        "exec",
                        container,
                        "python",
                        "-c",
                        "from pathlib import Path; root = Path('/app'); "
                        "assert not any((root / name).exists() for name in "
                        "('.git', 'exporter.env', '.env', 'tests')); "
                        "assert all(path.suffix == '.py' for path in "
                        "(root / 'rust_rcon_exporter').rglob('*') if path.is_file())",
                    )
                    self.wait_for(lambda: "rcon_up 1\n" in metric_payload(), "successful polling")
                    self.assertIn("players_online 3\n", metric_payload())
                    self.wait_for(
                        lambda: inspect(".State.Health.Status") == "healthy", "HTTP health"
                    )
                    fail_response.set()
                    self.wait_for(lambda: "rcon_up 0\n" in metric_payload(), "failed polling")
                    self.assertNotIn("server_fps", metric_payload())
                    self.assertEqual(inspect(".State.Health.Status"), "healthy")
                    fail_response.clear()
                    self.wait_for(lambda: "rcon_up 1\n" in metric_payload(), "RCON recovery")
                    self.assertTrue(all(path == "/" + quote(password, safe="") for path in paths))
                    self.assertTrue(all(request["Message"] == "serverinfo" for request in requests))
                    self.assertEqual(inspect(".RestartCount"), 0)
                    self.docker(*compose, "stop", "exporter")
                    self.assertEqual(inspect(".State.ExitCode"), 0)
                    logs = self.docker("logs", container)
                    self.assertNotIn(password, logs)
                    self.assertNotIn(quote(password, safe=""), logs)
                    self.assertNotIn("Traceback", logs)
                finally:
                    self.docker(*compose, "down", "--timeout", "15", check=False)
