FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY rust_rcon_exporter/ ./rust_rcon_exporter/
RUN python -m pip install --no-cache-dir --disable-pip-version-check .

USER 10001:10001

# Check HTTP availability independently of the Rust server's RCON status.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD ["python", "-c", "import http.client, os; c = http.client.HTTPConnection('127.0.0.1', int(os.environ.get('METRICS_PORT', '9187')), timeout=2); c.request('GET', '/metrics'); r = c.getresponse(); r.read(); c.close(); raise SystemExit(r.status != 200)"]

ENTRYPOINT ["rust-rcon-exporter"]
