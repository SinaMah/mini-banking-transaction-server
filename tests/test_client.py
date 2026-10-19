import http.client
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.error import URLError

import pytest

from client.banking import ClientError, send_request, server_url
from client.load_test import run_load


class FakeResponse:
    def __init__(self, body, code=200):
        self.body = body
        self.code = code

    def __enter__(self):
        return self

    def __exit__(self, *arguments):
        return False

    def read(self, amount):
        return self.body[:amount]


def test_client_network_failure(monkeypatch):
    class Offline:
        def open(self, *arguments, **keywords):
            raise URLError("connection refused")

    monkeypatch.setattr("client.banking.build_opener", lambda *arguments: Offline())
    with pytest.raises(ClientError, match="Cannot reach server"):
        send_request("http://192.168.56.10:8000", {"action": "balance", "account": "A1001"})


def test_real_refused_connection():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        with pytest.raises(ClientError, match="Cannot reach server"):
            send_request(f"http://127.0.0.1:{port}", {"action": "balance", "account": "A1001"}, 0.5)


def test_real_socket_read_timeout():
    release = threading.Event()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(5)
        port = listener.getsockname()[1]

        def accept_without_reply():
            with listener.accept()[0]:
                assert release.wait(5)

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(accept_without_reply)
            try:
                with pytest.raises(ClientError, match="timed out"):
                    send_request(f"http://127.0.0.1:{port}", {"action": "deposit", "account": "A1001", "amount": 1}, 0.1)
            finally:
                release.set()
            future.result(timeout=5)


def test_incomplete_http_response(monkeypatch):
    class Interrupted(FakeResponse):
        def read(self, amount):
            raise http.client.IncompleteRead(b"partial", 10)

    class Broken:
        def open(self, *arguments, **keywords):
            return Interrupted(b"")

    monkeypatch.setattr("client.banking.build_opener", lambda *arguments: Broken())
    with pytest.raises(ClientError, match="Check the balance"):
        send_request("http://192.168.56.10:8000", {"action": "deposit", "account": "A1001", "amount": 1})


def test_client_timeout_does_not_retry(monkeypatch):
    calls = []

    class Slow:
        def open(self, *arguments, **keywords):
            calls.append(1)
            raise socket.timeout("timed out")

    monkeypatch.setattr("client.banking.build_opener", lambda *arguments: Slow())
    with pytest.raises(ClientError, match="may already have committed"):
        send_request("http://192.168.56.10:8000", {"action": "deposit", "account": "A1001", "amount": 10})
    assert len(calls) == 1


@pytest.mark.parametrize("body,code", [(b"not JSON", 200), (b"[]", 200), (b"x" * 65537, 200),
                                       (b'{"status":"success"}', 200),
                                       (b'{"status":"success","account":"A1001","balance":true}', 200),
                                       (b'{"status":"error","error":"bad"}', 400),
                                       (b'{"status":[]}', 200),
                                       (b'{"status":{}}', 200),
                                       (b"[" * 1200, 200),
                                       (b'{"status":"success","account":"A1001","balance":-1}', 200),
                                       (b'{"status":"success","account":"A1001","balance":9223372036854775808}', 200),
                                       (b'{"status":"success","account":"A1002","balance":1000}', 200),
                                       (b'{"status":"error","error":{"code":"bad","message":[]}}', 400),
                                       (b'{"status":"error","error":{"code":"bad","message":"bad"}}', 200)])
def test_unexpected_server_responses_are_readable_errors(monkeypatch, body, code):
    class Invalid:
        def open(self, *arguments, **keywords):
            return FakeResponse(body, code)

    monkeypatch.setattr("client.banking.build_opener", lambda *arguments: Invalid())
    with pytest.raises(ClientError):
        send_request("http://192.168.56.10:8000", {"action": "balance", "account": "A1001"})


@pytest.mark.parametrize("value", ["localhost:8000", "ftp://example.com", "http://", "http://example.com/bank",
                                  "http://user:pass@example.com", "http://example.com?x=1",
                                  "http://example.com:abc", "http://example.com:0", "http://example.com:65536"])
def test_invalid_server_addresses(value):
    with pytest.raises(ValueError):
        server_url(value)


def test_load_script_reports_uncertain_writes_without_retry(monkeypatch):
    requests = []

    def fake_send(server, payload, timeout):
        requests.append(payload)
        if payload["action"] == "balance":
            return 200, {"status": "success", "account": "A1001", "balance": 1000}
        raise ClientError("Lost reply after a possible commit")

    monkeypatch.setattr("client.load_test.send_request", fake_send)
    summary = run_load("http://example.com", "A1001", "withdraw", 100, 2, 1, 10)
    assert summary["unexpected_failures"] == 2
    assert summary["successful_transactions"] == 0
    assert not summary["invariants_satisfied"]
    assert len(requests) == 4


def test_load_report_retains_counts_if_final_inquiry_fails(monkeypatch):
    inquiries = 0

    def fake_send(server, payload, timeout):
        nonlocal inquiries
        if payload["action"] == "balance":
            inquiries += 1
            if inquiries == 2:
                raise ClientError("Server stopped before final balance inquiry")
            return 200, {"status": "success", "account": "A1001", "balance": 1000}
        return 200, {"status": "success", "account": "A1001", "balance": 900}

    monkeypatch.setattr("client.load_test.send_request", fake_send)
    summary = run_load("http://example.com", "A1001", "withdraw", 100, 1, 1, 10)
    assert summary["requests_submitted"] == 1 and summary["successful_transactions"] == 1
    assert summary["expected_final_balance_from_confirmed_successes"] == 900
    assert summary["actual_final_balance"] is None
    assert summary["final_balance_error"] == "Server stopped before final balance inquiry"
    assert not summary["invariants_satisfied"]
