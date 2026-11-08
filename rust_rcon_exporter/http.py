"""Read-only HTTP endpoint backed by the in-memory metrics snapshot."""

from http.server import BaseHTTPRequestHandler

from .metrics import MetricsState


def make_http_handler(state: MetricsState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/metrics":
                self.send_error(404)
                return
            payload = state.render()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: object) -> None:
            # Do not log each scrape or arbitrary request paths.
            pass

    return Handler
