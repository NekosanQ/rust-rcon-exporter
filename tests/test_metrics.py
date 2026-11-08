import json
import unittest
from unittest.mock import patch

from prometheus_client.parser import text_string_to_metric_families

from rust_rcon_exporter.metrics import MetricsState, make_metrics, parse_serverinfo

CORE = {"Framerate": 59.5, "Players": 12, "EntityCount": 45678}


def samples(payload):
    return {
        sample.name: sample.value
        for family in text_string_to_metric_families(payload.decode())
        for sample in family.samples
    }


class MetricsTests(unittest.TestCase):
    def test_parses_object_and_json(self):
        expected = {"server_fps": 59.5, "players_online": 12, "entities_total": 45678}
        self.assertEqual(parse_serverinfo(CORE), expected)
        self.assertEqual(parse_serverinfo(json.dumps(CORE)), expected)

    def test_rejects_non_objects(self):
        for value in (None, [], "[]", '"text"', "not-json", 123):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_serverinfo(value)

    def test_core_fields_must_be_finite_nonnegative_numbers(self):
        for field in CORE:
            for value in (None, True, False, "60", -1, float("nan"), float("inf"), 10**400):
                with self.subTest(field=field, value=str(value)[:30]):
                    with self.assertRaises(ValueError):
                        parse_serverinfo({**CORE, field: value})
            with self.subTest(missing=field), self.assertRaises(ValueError):
                parse_serverinfo({key: value for key, value in CORE.items() if key != field})
        self.assertEqual(parse_serverinfo(dict.fromkeys(CORE, 0))["server_fps"], 0)

    def test_optional_fields_are_independent(self):
        result = parse_serverinfo(
            {
                **CORE,
                "Queued": 2,
                "Joining": False,
                "MaxPlayers": -1,
                "Uptime": 123.5,
                "Hostname": "private-hostname",
            }
        )
        self.assertEqual(result["players_queued"], 2)
        self.assertEqual(result["server_uptime_seconds"], 123.5)
        self.assertNotIn("players_joining", result)
        self.assertNotIn("players_capacity", result)
        self.assertNotIn("private-hostname", str(result))

    def test_initial_state(self):
        self.assertEqual(samples(MetricsState().render()), {"rcon_up": 0})

    def test_failure_clears_statistics_but_keeps_precise_success_time(self):
        state = MetricsState()
        timestamp = 1794182400.123456
        with patch("rust_rcon_exporter.metrics.time.time", return_value=timestamp):
            state.record_success(parse_serverinfo(CORE), 0.125)
        rendered = samples(state.render())
        self.assertEqual(rendered["rcon_up"], 1)
        self.assertEqual(rendered["rcon_response_seconds"], 0.125)
        self.assertEqual(rendered["rcon_last_success_timestamp_seconds"], timestamp)
        state.record_failure()
        self.assertEqual(
            samples(state.render()),
            {"rcon_up": 0, "rcon_last_success_timestamp_seconds": timestamp},
        )
        state.record_success(parse_serverinfo(CORE), 0.25)
        self.assertEqual(samples(state.render())["rcon_up"], 1)

    def test_success_replaces_missing_optional_fields(self):
        state = MetricsState()
        values = parse_serverinfo({**CORE, "Queued": 5})
        state.record_success(values, 0.1)
        values["players_queued"] = 999
        self.assertEqual(samples(state.render())["players_queued"], 5)
        state.record_success(parse_serverinfo(CORE), 0.1)
        self.assertNotIn("players_queued", samples(state.render()))

    def test_exposition_has_gauge_types_and_final_newline(self):
        payload = make_metrics(parse_serverinfo(CORE), 123.5)
        families = list(text_string_to_metric_families(payload.decode()))
        self.assertTrue(all(family.type == "gauge" for family in families))
        self.assertTrue(payload.endswith(b"\n"))
