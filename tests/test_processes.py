import json
import os
import selectors
import socket
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from client.banking import ClientError, send_request
from client.load_test import run_load
from server.app import PROJECT_ROOT
from server.storage import MAX_BALANCE, Store


@pytest.mark.parametrize("abrupt", [False, True])
def test_confirmed_http_writes_survive_restart(live_server, abrupt):
    process, url, database, log = live_server()
    status, response = send_request(url, {"action": "deposit", "account": "A1001", "amount": 123})
    assert status == 200 and response["balance"] == 1123
    status, response = send_request(url, {"action": "withdraw", "account": "A1001", "amount": 23})
    assert status == 200 and response["balance"] == 1100
    process.kill() if abrupt else process.terminate()
    process.wait(timeout=10)
    restarted, new_url, _, _ = live_server()
    assert send_request(new_url, {"action": "balance", "account": "A1001"})[1]["balance"] == 1100
    assert send_request(new_url, {"action": "balance", "account": "A1002"})[1]["balance"] == 2000
    connection = sqlite3.connect(database)
    try:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        connection.close()


def test_kill_between_update_and_commit_leaves_balance_unchanged(store):
    program = (
        "import sys\n"
        "from pathlib import Path\n"
        "from server.storage import Store\n"
        "store = Store(Path(sys.argv[1]))\n"
        "def wait_before_commit():\n"
        "    print('UPDATED_NOT_COMMITTED', flush=True)\n"
        "    sys.stdin.readline()\n"
        "store.execute({'action':'withdraw','account':'A1001','amount':200}, wait_before_commit)\n"
    )
    process = subprocess.Popen([sys.executable, "-u", "-c", program, str(store.path)], cwd=PROJECT_ROOT,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    selector = selectors.DefaultSelector()
    try:
        selector.register(process.stdout, selectors.EVENT_READ)
        assert selector.select(timeout=5), "Writer did not reach the pre-commit boundary."
        assert process.stdout.readline().strip() == b"UPDATED_NOT_COMMITTED"
        assert store.execute({"action": "balance", "account": "A1001"})["balance"] == 1000
        process.kill()
        process.wait(timeout=5)
    finally:
        selector.close()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()
    recovered = Store(store.path)
    recovered.initialize()
    assert recovered.execute({"action": "balance", "account": "A1001"})["balance"] == 1000
    assert recovered.execute({"action": "withdraw", "account": "A1001", "amount": 200})["balance"] == 800


def test_real_http_concurrency_and_log_integrity(live_server):
    process, url, database, log = live_server()
    summary = run_load(url, "A1001", "withdraw", 100, 30, 8, 10)
    assert summary["requests_submitted"] == 30
    assert summary["successful_transactions"] == 10
    assert summary["expected_business_rejections"] == 20
    assert summary["unexpected_failures"] == 0
    assert summary["actual_final_balance"] == 0
    assert summary["invariants_satisfied"]
    process.terminate()
    process.wait(timeout=10)
    entries = [json.loads(line) for line in log.read_text().splitlines()]
    transactions = [entry for entry in entries if entry["action"] == "withdraw"]
    assert len(transactions) == 30
    assert len({entry["request_id"] for entry in entries}) == len(entries)
    assert sum(entry["result"] == "success" for entry in transactions) == 10
    assert all(entry["client_ip"] == "127.0.0.1" for entry in entries)


def test_real_http_concurrent_deposits(live_server):
    process, url, database, log = live_server()
    summary = run_load(url, "A1001", "deposit", 5, 40, 8, 10)
    assert summary["successful_transactions"] == 40
    assert summary["actual_final_balance"] == 1200
    assert summary["invariants_satisfied"]


def test_real_http_concurrent_deposits_at_integer_limit(live_server):
    process, url, database, log = live_server()
    assert send_request(url, {"action": "deposit", "account": "A1001", "amount": MAX_BALANCE - 1005})[1]["balance"] == MAX_BALANCE - 5
    summary = run_load(url, "A1001", "deposit", 1, 8, 4, 10)
    assert summary["successful_transactions"] == 5
    assert summary["expected_business_rejections"] == 3
    assert summary["unexpected_failures"] == 0
    assert summary["actual_final_balance"] == MAX_BALANCE
    assert summary["invariants_satisfied"]


def test_malformed_wire_http_does_not_kill_server(live_server):
    process, url, database, log = live_server()
    port = int(url.rsplit(":", 1)[1])
    with socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
        connection.sendall(b"NOT HTTP\r\n\r\n")
        response = connection.recv(4096)
    assert response.startswith(b"HTTP/1.1 400")
    assert send_request(url, {"action": "balance", "account": "A1001"})[1]["balance"] == 1000
    protocol_output = log.with_name("server-errors.log").read_text()
    assert "WARNING Invalid HTTP request received." in protocol_output
    process.terminate()
    process.wait(timeout=10)
    assert all(json.loads(line)["http_status"] != 400 for line in log.read_text().splitlines())


def test_all_cli_programs_on_real_http(live_server):
    process, url, database, log = live_server()

    def run(module, arguments):
        return subprocess.run([sys.executable, "-m", module, "--server", url, *arguments],
                              cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=20)

    result = run("client.banking", ["balance", "A1001"])
    assert result.returncode == 0 and "1000 units" in result.stdout
    result = run("client.banking", ["deposit", "A1001", "5"])
    assert result.returncode == 0 and "1005 units" in result.stdout
    result = run("client.banking", ["withdraw", "A1001", "5"])
    assert result.returncode == 0 and "1000 units" in result.stdout
    result = run("client.banking", ["withdraw", "A1001", "-1"])
    assert result.returncode == 1 and "invalid_amount" in result.stderr
    result = run("client.error_demo", [])
    assert result.returncode == 0 and "Server still responding" in result.stdout
    result = run("client.load_test", ["--requests", "30", "--workers", "8"])
    assert result.returncode == 0
    assert json.loads(result.stdout)["actual_final_balance"] == 0


def test_local_rollback_demo_uses_configured_database(live_server):
    process, url, database, log = live_server()
    environment = os.environ.copy()
    environment.update(BANK_DB=str(database), BANK_LOG=str(log))
    result = subprocess.run([sys.executable, "-m", "server.rollback_demo", "--account", "A1002"],
                            cwd=PROJECT_ROOT, env=environment, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0
    assert "UPDATE executed" in result.stdout
    assert "Before: 2000; after: 2000; rollback verified: True" in result.stdout
    assert send_request(url, {"action": "balance", "account": "A1002"})[1]["balance"] == 2000


def test_real_http_capacity_excess_is_explicit(live_server, tmp_path):
    signal = tmp_path / "admitted.txt"
    process, url, database, log = live_server(limit=1, timeout=5, admission_signal=signal)
    blocker = Store(database).connect()
    blocker.execute("BEGIN IMMEDIATE")
    admitted = None
    with ThreadPoolExecutor(max_workers=1) as executor:
        admitted = executor.submit(send_request, url, {"action": "deposit", "account": "A1001", "amount": 5})
        try:
            deadline = time.monotonic() + 5
            while not signal.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert signal.exists(), "Test writer did not reach the admitted database operation."
            status, response = send_request(url, {"action": "balance", "account": "A1001"})
            assert status == 503 and response["error"]["code"] == "server_busy"
        finally:
            blocker.rollback()
            blocker.close()
        assert admitted.result(timeout=10)[1]["balance"] == 1005
    assert send_request(url, {"action": "balance", "account": "A1001"})[1]["balance"] == 1005


def test_real_http_stalled_body_times_out_and_releases_slot(live_server):
    process, url, database, log = live_server(limit=1, body_timeout=0.2)
    port = int(url.rsplit(":", 1)[1])
    with socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
        connection.sendall(b"POST /bank HTTP/1.1\r\nHost: test\r\nContent-Type: application/json\r\nContent-Length: 100\r\n\r\n{")
        chunks = []
        while chunk := connection.recv(4096):
            chunks.append(chunk)
    response = b"".join(chunks)
    assert response.startswith(b"HTTP/1.1 408")
    assert b"connection: close" in response.lower()
    assert json.loads(response.split(b"\r\n\r\n", 1)[1])["error"]["code"] == "request_timeout"
    assert send_request(url, {"action": "balance", "account": "A1001"})[1]["balance"] == 1000
    process.terminate()
    process.wait(timeout=10)
    entries = [json.loads(line) for line in log.read_text().splitlines()]
    assert sum(entry["http_status"] == 408 for entry in entries) == 1


def test_disconnect_during_admitted_write_can_still_commit(live_server, tmp_path):
    signal = tmp_path / "admitted.txt"
    process, url, database, log = live_server(limit=1, admission_signal=signal)
    store = Store(database)
    blocker = store.connect()
    blocker.execute("BEGIN IMMEDIATE")
    port = int(url.rsplit(":", 1)[1])
    body = b'{"action":"deposit","account":"A1001","amount":5}'
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
            headers = f"POST /bank HTTP/1.1\r\nHost: test\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n".encode()
            connection.sendall(headers + body)
            deadline = time.monotonic() + 5
            while not signal.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert signal.exists()
    finally:
        blocker.rollback()
        blocker.close()
    deadline = time.monotonic() + 5
    while store.execute({"action": "balance", "account": "A1001"})["balance"] != 1005:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    deadline = time.monotonic() + 5
    while True:
        status, response = send_request(url, {"action": "balance", "account": "A1001"})
        if status == 200:
            assert response["balance"] == 1005
            break
        assert status == 503 and time.monotonic() < deadline
        time.sleep(0.01)
