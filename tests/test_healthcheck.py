import threading
import urllib.error
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
    srv.server_close()


def test_check_by_status(server: str) -> None:
    assert check("/healthz", port=server)
    assert not check("/readyz", port=server)


def test_error_response_is_closed(server: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # Ответ не 2xx — HTTPError с открытым соединением; с 3.14 незакрытый даёт ResourceWarning.
    closed: list[int] = []
    original = urllib.error.HTTPError.close

    def close(self: urllib.error.HTTPError) -> None:
        closed.append(self.code)
        original(self)

    monkeypatch.setattr(urllib.error.HTTPError, "close", close)
    assert not check("/readyz", port=server)
    assert closed == [503]


def test_check_unreachable() -> None:
    assert not check("/healthz", port="1", timeout_s=0.5)
