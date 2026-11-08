"""Command-line entry point and application lifecycle."""

from __future__ import annotations

import argparse
import logging
import signal
import threading
from collections.abc import Sequence
from http.server import ThreadingHTTPServer

from .config import METRICS_HOST, Config
from .http import make_http_handler
from .metrics import MetricsState
from .rcon import poll_loop

LOGGER = logging.getLogger(__name__)


def run(config: Config) -> None:
    stopping = threading.Event()
    previous_handlers = {}
    state = MetricsState()
    with ThreadingHTTPServer(
        (METRICS_HOST, config.metrics_port), make_http_handler(state)
    ) as server:
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, name="metrics-http", daemon=True)
        for sig in (signal.SIGTERM, signal.SIGINT):
            previous_handlers[sig] = signal.signal(sig, lambda *_: stopping.set())
        thread.start()
        LOGGER.info(
            "rust-rcon-exporter ready on %s:%s/metrics; polling every %gs",
            METRICS_HOST,
            config.metrics_port,
            config.poll_interval,
        )
        try:
            poll_loop(state, stopping, config)
        finally:
            stopping.set()
            server.shutdown()
            thread.join(timeout=2)
            for sig, handler in previous_handlers.items():
                signal.signal(sig, handler)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export local Rust WebRCON serverinfo as Prometheus metrics.",
        epilog="Configure via environment variables; see README.md. RCON_PASSWORD is required.",
    )
    parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    try:
        config = Config.from_env()
    except ValueError as exc:
        LOGGER.error("Configuration error: %s", exc)
        return 2
    try:
        # Fail before opening HTTP if the runtime dependency isn't installed.
        import websocket  # noqa: F401
    except ImportError:
        LOGGER.error("Missing websocket-client dependency; install with: python -m pip install .")
        return 2
    try:
        run(config)
    except OSError as exc:
        LOGGER.error(
            "Cannot run exporter (%s); check the metrics port and permissions", type(exc).__name__
        )
        return 1
    return 0
