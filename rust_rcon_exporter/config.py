"""Validated environment configuration with loopback-only networking."""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import quote

LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
METRICS_HOST = "127.0.0.1"


def _port(environ: Mapping[str, str], name: str, default: str, minimum: int) -> int:
    try:
        value = int(environ.get(name, default))
    except ValueError:
        raise ValueError(f"{name} must be an integer between {minimum} and 65535") from None
    if not minimum <= value <= 65535:
        raise ValueError(f"{name} must be between {minimum} and 65535")
    return value


@dataclass(frozen=True)
class Config:
    # Keep the secret out of dataclass reprs as well as application logs.
    rcon_password: str = field(repr=False)
    rcon_host: str = "127.0.0.1"
    rcon_port: int = 28016
    poll_interval: float = 5.0
    metrics_port: int = 9187

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Config:
        environ = os.environ if environ is None else environ
        password = environ.get("RCON_PASSWORD", "")
        if not password:
            raise ValueError("RCON_PASSWORD must be configured")
        host = environ.get("RCON_HOST", "127.0.0.1")
        if host not in LOOPBACK_HOSTS:
            raise ValueError("RCON_HOST must be 127.0.0.1, localhost, or ::1")
        try:
            interval = float(environ.get("RCON_POLL_INTERVAL", "5"))
        except ValueError:
            raise ValueError("RCON_POLL_INTERVAL must be between 2 and 60 seconds") from None
        if not math.isfinite(interval) or not 2.0 <= interval <= 60.0:
            raise ValueError("RCON_POLL_INTERVAL must be between 2 and 60 seconds")
        return cls(
            rcon_password=password,
            rcon_host=host,
            rcon_port=_port(environ, "RCON_PORT", "28016", 1),
            poll_interval=interval,
            metrics_port=_port(environ, "METRICS_PORT", "9187", 1024),
        )

    @property
    def rcon_url(self) -> str:
        host = f"[{self.rcon_host}]" if ":" in self.rcon_host else self.rcon_host
        return f"ws://{host}:{self.rcon_port}/{quote(self.rcon_password, safe='')}"
