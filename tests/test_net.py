"""Testes do HTTP mínimo (qijournal.net) contra um servidor local."""

from __future__ import annotations

import gzip
import socket
import threading

import pytest

from qijournal import net


class ShortBodyServer:
    """Servidor TCP que anuncia um Content-Length maior que o corpo enviado e fecha a conexão."""

    def __init__(self, body: bytes, declared: int, headers: str = "") -> None:
        self.body, self.declared, self.headers = body, declared, headers
        self.requests = 0
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                conn.recv(65536)
                self.requests += 1
                head = (
                    "HTTP/1.1 200 OK\r\nContent-Type: application/rss+xml\r\n"
                    f"Content-Length: {self.declared}\r\n{self.headers}Connection: close\r\n\r\n"
                )
                conn.sendall(head.encode() + self.body)

    def close(self) -> None:
        self.sock.close()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/feed"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(net.time, "sleep", lambda seconds: None)


def test_truncated_body_is_retried_and_then_fails() -> None:
    server = ShortBodyServer(b"<rss><channel><item>metade", declared=5000)
    try:
        with pytest.raises(net.FetchError, match="corpo truncado"):
            net.fetch(server.url, timeout=5, retries=2)
        assert server.requests == 3  # tentativa + 2 novas
    finally:
        server.close()


def test_truncated_gzip_is_a_transient_error() -> None:
    compressed = gzip.compress(b"<rss>" + b"x" * 5000 + b"</rss>")
    cut = compressed[: len(compressed) // 2]
    server = ShortBodyServer(cut, declared=len(cut), headers="Content-Encoding: gzip\r\n")
    try:
        with pytest.raises(net.FetchError):
            net.fetch(server.url, timeout=5, retries=1)
        assert server.requests == 2
    finally:
        server.close()


def test_complete_body_is_returned() -> None:
    server = ShortBodyServer(b"<rss>ok</rss>", declared=13)
    try:
        response = net.fetch(server.url, timeout=5, retries=0)
        assert response.content == b"<rss>ok</rss>" and server.requests == 1
    finally:
        server.close()
