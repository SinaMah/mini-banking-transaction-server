import asyncio
import json
import sqlite3
import threading

import httpx
import pytest

from server.app import Settings, create_app, local_path
from server.storage import MAX_BALANCE


def test_normal_operations(client):
    for action, amount, balance in [("balance", None, 1000), ("deposit", 200, 1200), ("withdraw", 400, 800)]:
        payload = {"action": action, "account": "A1001"}
        if amount is not None:
            payload["amount"] = amount
        response = client.post("/bank", json=payload)
        assert response.status_code == 200
        assert response.json() == {"status": "success", "account": "A1001", "balance": balance}
        assert len(response.headers["x-request-id"]) == 32


@pytest.mark.parametrize("payload,status,code", [
    ({"action": "balance", "account": "Z9999"}, 404, "unknown_account"),
    ({"action": "withdraw", "account": "A1001", "amount": 5000}, 409, "insufficient_funds"),
    ({"action": "deposit", "account": "A1001", "amount": -50}, 400, "invalid_amount"),
    ({"action": "deposit", "account": "A1001", "amount": 0}, 400, "invalid_amount"),
    ({"action": "deposit", "account": "A1001", "amount": "abc"}, 400, "invalid_amount"),
    ({"action": "deposit", "account": "A1001", "amount": "100"}, 400, "invalid_amount"),
    ({"action": "deposit", "account": "A1001", "amount": 1.5}, 400, "invalid_amount"),
    ({"action": "deposit", "account": "A1001", "amount": 1.0}, 400, "invalid_amount"),
    ({"action": "deposit", "account": "A1001", "amount": True}, 400, "invalid_amount"),
    ({"action": "deposit", "account": "A1001", "amount": None}, 400, "invalid_amount"),
    ({"action": "deposit", "account": "A1001", "amount": MAX_BALANCE + 1}, 400, "invalid_amount"),
    ({"action": "transfer", "account": "A1001", "amount": 1}, 400, "unsupported_action"),
    ({"action": [], "account": "A1001"}, 400, "unsupported_action"),
    ({"account": "A1001"}, 400, "missing_field"),
    ({"action": "balance"}, 400, "missing_field"),
    ({"action": "withdraw", "account": "A1001"}, 400, "missing_field"),
    ({"action": "balance", "account": 1001}, 400, "invalid_account"),
    ({"action": "balance", "account": ""}, 400, "invalid_account"),
    ({"action": "balance", "account": "\ud800"}, 400, "invalid_account"),
    ({"action": "deposit", "account": "A1001", "amount": 1, "fail": True}, 400, "invalid_request"),
    ({"action": "balance", "account": "A1001", "amount": 10}, 400, "invalid_request"),
    ([], 400, "invalid_request"),
    (None, 400, "invalid_request"),
])
def test_invalid_requests_leave_state_unchanged(client, payload, status, code):
    response = client.post("/bank", content=json.dumps(payload), headers={"Content-Type": "application/json"})
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert client.post("/bank", json={"action": "balance", "account": "A1001"}).json()["balance"] == 1000


@pytest.mark.parametrize("body", [b'{"action":', b"\xff\x00", b"", b"random text",
                                  b'{"action":"balance","action":"deposit"}',
                                  b'{"amount":NaN}', b'{"amount":Infinity}', b"[" * 1200])
def test_malformed_json(client, body):
    response = client.post("/bank", content=body, headers={"Content-Type": "application/json"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "malformed_json"
    assert client.get("/health").status_code == 200


def test_http_errors_and_size_limit(client):
    assert client.post("/bank", content="hello").status_code == 415
    assert client.get("/bank").status_code == 405
    assert client.get("/missing").status_code == 404
    response = client.post("/bank", content=b"x" * 4097, headers={"Content-Type": "application/json"})
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "request_too_large"
    assert client.get("/health").status_code == 200


def test_deposit_overflow_does_not_change_state(client):
    response = client.post("/bank", json={"action": "deposit", "account": "A1001", "amount": MAX_BALANCE})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "balance_limit"
    assert client.post("/bank", json={"action": "balance", "account": "A1001"}).json()["balance"] == 1000


@pytest.mark.parametrize("capacity", [1, 3])
def test_capacity_enforced_without_arbitrary_delays(tmp_path, capacity):
    app = create_app(Settings(tmp_path / "bank.sqlite3", tmp_path / "requests.jsonl", capacity))
    original = app.state.store.execute
    released = threading.Event()
    entered = [threading.Event() for _ in range(capacity)]
    counter = 0
    guard = threading.Lock()

    def blocked(payload):
        nonlocal counter
        with guard:
            position = counter
            counter += 1
        entered[position].set()
        if not released.wait(5):
            raise RuntimeError("Test did not release admitted requests.")
        return original(payload)

    app.state.store.execute = blocked

    async def scenario():
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                tasks = [asyncio.create_task(client.post("/bank", json={"action": "balance", "account": "A1001"}))
                         for _ in range(capacity)]
                try:
                    for event in entered:
                        assert await asyncio.to_thread(event.wait, 5)
                    rejected = await client.post("/bank", json={"action": "deposit", "account": "A1001", "amount": 50})
                    assert rejected.status_code == 503
                    assert rejected.json()["error"]["code"] == "server_busy"
                    assert counter == capacity
                finally:
                    released.set()
                    responses = await asyncio.gather(*tasks)
                assert all(response.status_code == 200 for response in responses)
                app.state.store.execute = original
                next_response = await client.post("/bank", json={"action": "balance", "account": "A1001"})
                assert next_response.json()["balance"] == 1000

    asyncio.run(scenario())
    entries = [json.loads(line) for line in (tmp_path / "requests.jsonl").read_text().splitlines()]
    assert len(entries) == capacity + 2
    assert sum(entry["http_status"] == 503 for entry in entries) == 1


def test_database_busy_response_and_server_survival(client, app):
    app.state.store.timeout = 0.01
    connection = app.state.store.connect()
    connection.execute("BEGIN IMMEDIATE")
    try:
        response = client.post("/bank", json={"action": "deposit", "account": "A1001", "amount": 5})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "database_busy"
    finally:
        connection.rollback()
        connection.close()
    assert client.post("/bank", json={"action": "balance", "account": "A1001"}).json()["balance"] == 1000


def test_internal_exception_rolls_back_and_is_logged(client, app, monkeypatch):
    original = app.state.store.execute

    def fail():
        raise RuntimeError("Injected failure after UPDATE")

    def execute(payload):
        return original(payload, before_commit=fail)

    monkeypatch.setattr(app.state.store, "execute", execute)
    response = client.post("/bank", json={"action": "deposit", "account": "A1001", "amount": 5})
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    entries = [json.loads(line) for line in app.state.settings.log.read_text().splitlines()]
    assert entries[-1]["result"] == "error"
    assert entries[-1]["response"]["error"]["code"] == "internal_error"
    monkeypatch.setattr(app.state.store, "execute", original)
    assert client.post("/bank", json={"action": "balance", "account": "A1001"}).json()["balance"] == 1000


def test_storage_io_error_rolls_back(client, app, monkeypatch):
    original = app.state.store.execute

    def fail():
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(app.state.store, "execute", lambda payload: original(payload, before_commit=fail))
    response = client.post("/bank", json={"action": "deposit", "account": "A1001", "amount": 5})
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "storage_error"
    monkeypatch.setattr(app.state.store, "execute", original)
    assert client.post("/bank", json={"action": "balance", "account": "A1001"}).json()["balance"] == 1000


def test_python_310_lock_error_without_errorcode(client, app, monkeypatch):
    def busy(payload):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(app.state.store, "execute", busy)
    response = client.post("/bank", json={"action": "deposit", "account": "A1001", "amount": 1})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "database_busy"


def test_missing_peer_metadata_is_logged(tmp_path):
    app = create_app(Settings(tmp_path / "bank.sqlite3", tmp_path / "requests.jsonl"))

    async def scenario():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=None), base_url="http://test") as client:
                assert (await client.get("/health")).status_code == 200

    asyncio.run(scenario())
    entry = json.loads((tmp_path / "requests.jsonl").read_text())
    assert entry["client_ip"] is None and entry["result"] == "success"


def test_logging_valid_invalid_and_unrouted_requests(client, app):
    first = client.post("/bank", json={"action": "deposit", "account": "A1001", "amount": 10})
    client.post("/bank", content="broken", headers={"Content-Type": "application/json"})
    client.get("/missing")
    lines = [json.loads(line) for line in app.state.settings.log.read_text().splitlines()]
    assert len(lines) == 3
    required = {"request_id", "client_ip", "action", "account", "timestamp", "result", "latency_ms", "response"}
    assert all(required <= entry.keys() for entry in lines)
    assert len({entry["request_id"] for entry in lines}) == 3
    assert lines[0]["request_id"] == first.headers["x-request-id"]
    assert lines[0]["response"] == first.json()
    assert lines[0]["action"] == "deposit" and lines[0]["account"] == "A1001"
    assert lines[0]["client_ip"] == "testclient"
    assert lines[1]["action"] is None and lines[1]["account"] is None
    assert lines[1]["response"]["error"]["code"] == "malformed_json"
    assert lines[2]["http_status"] == 404
    assert all(entry["latency_ms"] >= 0 for entry in lines)


@pytest.mark.parametrize("value", [0, -1, 257, True])
def test_invalid_capacity_settings(tmp_path, value):
    with pytest.raises(ValueError):
        Settings(tmp_path / "bank.sqlite3", tmp_path / "requests.jsonl", value)


def test_environment_configuration_and_local_paths(monkeypatch):
    monkeypatch.setenv("BANK_MAX_CLIENTS", "7")
    monkeypatch.setenv("BANK_DB_TIMEOUT", "2.5")
    monkeypatch.setenv("BANK_BODY_TIMEOUT", "4")
    monkeypatch.setenv("BANK_DB", "data/example.sqlite3")
    settings = Settings.from_environment()
    assert settings.max_clients == 7 and settings.db_timeout == 2.5
    assert settings.body_timeout == 4
    assert settings.database == local_path("data/example.sqlite3")
    with pytest.raises(ValueError):
        local_path("../outside.sqlite3")


@pytest.mark.parametrize("outcome", ["exception", "oversized", "disconnect", "cancel", "timeout", "send_failure"])
def test_single_capacity_slot_released_after_request_failure(tmp_path, outcome):
    app = create_app(Settings(tmp_path / "bank.sqlite3", tmp_path / "requests.jsonl", 1, body_timeout=0.05))
    entered = asyncio.Event()
    received = False
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "scheme": "http",
        "server": ("test", 80), "client": ("127.0.0.1", 1234), "path": "/bank", "raw_path": b"/bank",
        "root_path": "", "query_string": b"", "method": "POST",
        "headers": [(b"content-type", b"application/json")],
    }
    sent = []

    async def receive():
        nonlocal received
        if not received:
            received = True
            if outcome == "send_failure":
                return {"type": "http.request", "body": b'{"action":"balance","account":"A1001"}', "more_body": False}
            return {"type": "http.request", "body": b"{", "more_body": True}
        entered.set()
        if outcome == "disconnect":
            return {"type": "http.disconnect"}
        await asyncio.Event().wait()

    async def send(message):
        if outcome == "send_failure":
            raise OSError("Connection lost while sending response")
        sent.append(message)

    async def scenario():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                if outcome == "exception":
                    def fail(payload):
                        raise RuntimeError("Injected handler failure")
                    app.state.store.execute = fail
                    response = await client.post("/bank", json={"action": "balance", "account": "A1001"})
                    assert response.status_code == 500
                elif outcome == "oversized":
                    response = await client.post("/bank", content=b"x" * 4097, headers={"Content-Type": "application/json"})
                    assert response.status_code == 413
                    assert response.headers["connection"] == "close"
                elif outcome == "cancel":
                    task = asyncio.create_task(app(scope, receive, send))
                    await asyncio.wait_for(entered.wait(), 2)
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                else:
                    await asyncio.wait_for(app(scope, receive, send), 2)
                    if outcome == "timeout":
                        start = next(message for message in sent if message["type"] == "http.response.start")
                        assert start["status"] == 408
                        assert (b"connection", b"close") in start["headers"]
                assert (await client.get("/health")).status_code == 200

    asyncio.run(scenario())
    entries = [json.loads(line) for line in (tmp_path / "requests.jsonl").read_text().splitlines()]
    assert len(entries) == 2
    assert entries[-1]["result"] == "success"
    if outcome in {"disconnect", "cancel", "send_failure"}:
        assert entries[0]["result"] == ("cancelled" if outcome == "cancel" else "disconnected")
    if outcome == "timeout":
        assert entries[0]["response"]["error"]["code"] == "request_timeout"


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), 61])
def test_invalid_body_timeout(tmp_path, timeout):
    with pytest.raises(ValueError):
        Settings(tmp_path / "bank.sqlite3", tmp_path / "requests.jsonl", body_timeout=timeout)
