import unittest

from rust_rcon_exporter.config import Config


class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        config = Config.from_env({"RCON_PASSWORD": "test-secret"})
        self.assertEqual(config.rcon_url, "ws://127.0.0.1:28016/test-secret")
        self.assertEqual(config.poll_interval, 5)
        self.assertEqual(config.metrics_port, 9187)
        self.assertNotIn("test-secret", repr(config))

    def test_password_is_required(self):
        for environ in ({}, {"RCON_PASSWORD": ""}):
            with self.subTest(environ=environ), self.assertRaisesRegex(ValueError, "RCON_PASSWORD"):
                Config.from_env(environ)

    def test_only_loopback_hosts_are_allowed(self):
        for host in ("0.0.0.0", "example.com", "127.0.0.2", "127.0.0.1/secret", ""):
            with self.subTest(host=host), self.assertRaisesRegex(ValueError, "RCON_HOST"):
                Config.from_env({"RCON_PASSWORD": "test", "RCON_HOST": host})
        for host in ("127.0.0.1", "localhost", "::1"):
            with self.subTest(host=host):
                config = Config.from_env({"RCON_PASSWORD": "test", "RCON_HOST": host})
                self.assertEqual(config.rcon_host, host)

    def test_ipv6_and_password_encoding(self):
        config = Config.from_env({"RCON_PASSWORD": "a/b ?#%日本語", "RCON_HOST": "::1"})
        self.assertEqual(
            config.rcon_url,
            "ws://[::1]:28016/a%2Fb%20%3F%23%25%E6%97%A5%E6%9C%AC%E8%AA%9E",
        )

    def test_ports_and_intervals_reject_bad_values_without_echoing_input(self):
        cases = {
            "RCON_PORT": ("0", "65536", "1.5", "secret-in-invalid-value", ""),
            "METRICS_PORT": ("1023", "65536", "1.5", "secret-in-invalid-value", ""),
            "RCON_POLL_INTERVAL": (
                "1.99",
                "60.01",
                "nan",
                "inf",
                "-inf",
                "secret-in-invalid-value",
            ),
        }
        for name, values in cases.items():
            for value in values:
                with self.subTest(name=name, value=value):
                    with self.assertRaises(ValueError) as caught:
                        Config.from_env({"RCON_PASSWORD": "test", name: value})
                    self.assertIn(name, str(caught.exception))
                    self.assertNotIn("secret-in-invalid-value", str(caught.exception))

    def test_boundary_values_and_fractional_interval(self):
        for rcon_port, metrics_port, interval in (
            (1, 1024, 2),
            (65535, 65535, 60),
            (28016, 9187, 2.5),
        ):
            with self.subTest(interval=interval):
                config = Config.from_env(
                    {
                        "RCON_PASSWORD": "test",
                        "RCON_PORT": str(rcon_port),
                        "METRICS_PORT": str(metrics_port),
                        "RCON_POLL_INTERVAL": str(interval),
                    }
                )
                self.assertEqual(
                    (config.rcon_port, config.metrics_port, config.poll_interval),
                    (rcon_port, metrics_port, interval),
                )
