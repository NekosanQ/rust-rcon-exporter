"""Parse serverinfo and expose a thread-safe Prometheus snapshot."""

from __future__ import annotations

import json
import math
import threading
import time

DESCRIPTIONS = {
    "rcon_up": "Rust WebRCON serverinfo polling succeeded (1) or failed (0).",
    "server_fps": "Current RustDedicated server framerate.",
    "players_online": "Currently connected Rust players.",
    "entities_total": "Current Rust server entity count.",
    "players_queued": "Rust players waiting in the connection queue.",
    "players_joining": "Rust players in the process of joining.",
    "players_capacity": "Maximum number of Rust player slots.",
    "server_uptime_seconds": "RustDedicated uptime in seconds.",
    "rcon_response_seconds": "Duration of the latest successful serverinfo command in seconds.",
    "rcon_last_success_timestamp_seconds": "Unix time of last successful serverinfo response.",
}

REQUIRED_FIELDS = {
    "Framerate": "server_fps",
    "Players": "players_online",
    "EntityCount": "entities_total",
}
OPTIONAL_FIELDS = {
    "Queued": "players_queued",
    "Joining": "players_joining",
    "MaxPlayers": "players_capacity",
    "Uptime": "server_uptime_seconds",
}


def _nonnegative_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) and number >= 0 else None


def parse_serverinfo(message: object) -> dict[str, float]:
    """Require core counters; omit missing or invalid optional counters."""
    data = json.loads(message) if isinstance(message, str) else message
    if not isinstance(data, dict):
        raise ValueError("serverinfo did not return a JSON object")

    result: dict[str, float] = {}
    for fields, required in ((REQUIRED_FIELDS, True), (OPTIONAL_FIELDS, False)):
        for field, metric in fields.items():
            number = _nonnegative_number(data.get(field))
            if number is not None:
                result[metric] = number
            elif required:
                raise ValueError(f"missing or invalid numeric serverinfo field: {field}")
    return result


def make_metrics(values: dict[str, float] | None, last_success: float | None) -> bytes:
    samples = {"rcon_up": 1.0 if values is not None else 0.0}
    if values is not None:
        samples.update(values)
    if last_success is not None:
        samples["rcon_last_success_timestamp_seconds"] = last_success

    lines = []
    for name, value in samples.items():
        lines.extend(
            (
                f"# HELP {name} {DESCRIPTIONS[name]}",
                f"# TYPE {name} gauge",
                # Six significant digits would round Unix times by many minutes.
                f"{name} {value:.17g}",
            )
        )
    return ("\n".join(lines) + "\n").encode("utf-8")


class MetricsState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: dict[str, float] | None = None
        self._last_success: float | None = None

    def record_success(self, values: dict[str, float], response_seconds: float) -> None:
        with self._lock:
            self._values = {**values, "rcon_response_seconds": response_seconds}
            self._last_success = time.time()

    def record_failure(self) -> None:
        with self._lock:
            self._values = None

    def render(self) -> bytes:
        with self._lock:
            return make_metrics(self._values, self._last_success)
