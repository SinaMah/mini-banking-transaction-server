import json
import os
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

import pytest
from fastapi.testclient import TestClient

from server.app import PROJECT_ROOT, Settings, create_app
from server.storage import Store


@pytest.fixture
def store(tmp_path):
    result = Store(tmp_path / "bank.sqlite3")
    result.initialize()
    return result


@pytest.fixture
def app(tmp_path):
    return create_app(Settings(tmp_path / "bank.sqlite3", tmp_path / "requests.jsonl"))


@pytest.fixture
def client(app):
    with TestClient(app) as result:
        yield result


@pytest.fixture
def live_server(tmp_path):
    processes = []
    streams = []
    database = tmp_path / "live.sqlite3"
    log = tmp_path / "live.jsonl"
    opener = build_opener(ProxyHandler({}))

    def start(limit=16, timeout=5, admission_signal=None, body_timeout=10):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        environment = os.environ.copy()
        environment.update(BANK_DB=str(database), BANK_LOG=str(log), BANK_MAX_CLIENTS=str(limit),
                           BANK_DB_TIMEOUT=str(timeout), BANK_BODY_TIMEOUT=str(body_timeout))
        output = open(tmp_path / f"process-{len(processes)}.txt", "w+")
        streams.append(output)
        command = [sys.executable, "-m", "server.app", "--host", "127.0.0.1", "--port", str(port)]
        if admission_signal is not None:
            program = (
                "import sys\n"
                "from pathlib import Path\n"
                "import uvicorn\n"
                "from server.app import create_app\n"
                "app = create_app()\n"
                "original = app.state.store.execute\n"
                "def observed_execute(payload):\n"
                "    Path(sys.argv[2]).write_text('admitted')\n"
                "    return original(payload)\n"
                "app.state.store.execute = observed_execute\n"
                "uvicorn.run(app, host='127.0.0.1', port=int(sys.argv[1]), proxy_headers=False)\n"
            )
            command = [sys.executable, "-c", program, str(port), str(admission_signal)]
        process = subprocess.Popen(
            command,
            cwd=PROJECT_ROOT, env=environment, stdout=output, stderr=subprocess.STDOUT
        )
        processes.append(process)
        url = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if process.poll() is not None:
                output.seek(0)
                pytest.fail(f"Server exited before startup: {output.read()}")
            try:
                with opener.open(url + "/health", timeout=0.2) as response:
                    if json.load(response)["status"] == "success":
                        return process, url, database, log
            except (URLError, OSError):
                time.sleep(0.02)
        pytest.fail("Server did not become ready within 10 seconds.")

    yield start
    for process in processes:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
    for stream in streams:
        stream.close()
