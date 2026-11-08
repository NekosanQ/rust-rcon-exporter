#!/usr/bin/env python3
"""Compatibility entry point; also available as `rust-rcon-exporter`."""

from rust_rcon_exporter.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
