"""Persistent WebRCON polling with bounded retries and redacted errors."""

from __future__ import annotations

import json
import logging
import socket
import threading
import time
from typing import Protocol

from .config import Config
from .metrics import MetricsState, parse_serverinfo

LOGGER = logging.getLogger(__name__)
RESPONSE_TIMEOUT = 6.0
MAX_BACKOFF = 30.0


class WebSocketConnection(Protocol):
    def send(self, payload: str) -> object: ...

    def recv(self) -> str | bytes: ...

    def settimeout(self, timeout: float) -> None: ...

    def close(self, *, timeout: float) -> None: ...


def connect(config: Config) -> WebSocketConnection:
    import websocket

    # Supplying the transport bypasses all proxy-environment handling in the
    # dependency. http_no_proxy alone isn't honored by every supported version.
    transport = socket.create_connection(
        (config.rcon_host, config.rcon_port), timeout=RESPONSE_TIMEOUT
    )
    try:
        return websocket.create_connection(
            config.rcon_url,
            timeout=RESPONSE_TIMEOUT,
            socket=transport,
            # A local endpoint must never redirect the password to another host.
            redirect_limit=0,
        )
    except Exception:
        transport.close()
        raise


def request_serverinfo(
    ws: WebSocketConnection, identifier: int, timeout: float = RESPONSE_TIMEOUT
) -> dict[str, float]:
    deadline = time.monotonic() + timeout
    ws.settimeout(timeout)
    ws.send(
        json.dumps(
            {"Identifier": identifier, "Message": "serverinfo", "Name": "rust-rcon-exporter"}
        )
    )
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("WebRCON serverinfo response timed out")
        ws.settimeout(remaining)
        message = ws.recv()
        if not message:
            raise ConnectionError("WebRCON connection closed")
        try:
            response = json.loads(message)
        except (ValueError, UnicodeDecodeError):
            continue
        if not isinstance(response, dict) or response.get("Identifier") != identifier:
            continue  # Ignore unsolicited console output and chat.
        return parse_serverinfo(response.get("Message"))


def poll_loop(state: MetricsState, stopping: threading.Event, config: Config) -> None:
    backoff = 1.0
    identifier = 1000
    while not stopping.is_set():
        ws = None
        failed = False
        try:
            ws = connect(config)
            while not stopping.is_set():
                started = time.monotonic()
                identifier += 1
                values = request_serverinfo(ws, identifier)
                state.record_success(values, time.monotonic() - started)
                # A handshake alone doesn't mean that polling recovered.
                backoff = 1.0
                stopping.wait(max(0.0, config.poll_interval - (time.monotonic() - started)))
        except Exception as exc:
            state.record_failure()
            failed = True
            if not stopping.is_set():
                # Exception text and tracebacks can contain credentials or server output.
                LOGGER.warning(
                    "WebRCON unavailable (%s); retry in %gs", type(exc).__name__, backoff
                )
        finally:
            if ws is not None:
                try:
                    ws.close(timeout=1)
                except Exception:
                    pass
        if failed:
            stopping.wait(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF)
