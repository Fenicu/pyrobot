import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.healthcheck import check


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200 if self.path == "/healthz" else 503)
        self.end_headers()

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def server() -> Iterator[str]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield str(srv.server_address[1])
    srv.shutdown()


def test_check_by_status(server: str) -> None:
    assert check("/healthz", port=server)
    assert not check("/readyz", port=server)


def test_check_unreachable() -> None:
    assert not check("/healthz", port="1", timeout_s=0.5)
