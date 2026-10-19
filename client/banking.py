import argparse
import http.client
import json
import math
import socket
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from server.storage import MAX_BALANCE


class ClientError(Exception):
    pass


def server_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Server must be an http:// or https:// address without credentials.")
    if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("Server address cannot contain a path, query, or fragment.")
    if parsed.port is not None and parsed.port == 0:
        raise ValueError("Server port must be between 1 and 65535.")
    return value.rstrip("/")


def send_request(server: str, payload: dict | bytes, timeout: float = 10.0) -> tuple[int, dict]:
    server = server_url(server)
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    request = Request(server + "/bank", data=body, headers={"Content-Type": "application/json"}, method="POST")
    opener = build_opener(ProxyHandler({}))
    try:
        try:
            response = opener.open(request, timeout=timeout)
        except HTTPError as exception:
            response = exception
        with response:
            status = response.code
            raw = response.read(65537)
        if len(raw) > 65536:
            raise ClientError("Server response is too large.")
        data = json.loads(raw)
        if not isinstance(data, dict) or not isinstance(data.get("status"), str) or data["status"] not in {"success", "error"}:
            raise ClientError("Server returned an unexpected response.")
        if data["status"] == "success":
            if not 200 <= status < 300 or not isinstance(data.get("account"), str) or type(data.get("balance")) is not int or not 0 <= data["balance"] <= MAX_BALANCE:
                raise ClientError("Server returned an invalid success response.")
            if isinstance(payload, dict) and data["account"] != payload.get("account"):
                raise ClientError("Server returned a response for a different account; check the balance before retrying.")
        elif not 400 <= status <= 599 or not isinstance(data.get("error"), dict) or not isinstance(data["error"].get("code"), str) or not isinstance(data["error"].get("message"), str):
            raise ClientError("Server returned an invalid error response.")
        return status, data
    except (socket.timeout, TimeoutError) as exception:
        raise ClientError("Request timed out; a write may already have committed. Check the balance before retrying.") from exception
    except URLError as exception:
        raise ClientError(f"Cannot reach server: {exception.reason}. A lost reply does not prove a write failed.") from exception
    except (OSError, ValueError, RecursionError, http.client.HTTPException) as exception:
        raise ClientError(f"Could not read the server response: {exception}. Check the balance before repeating a write.") from exception


def main() -> int:
    parser = argparse.ArgumentParser(description="Command-line banking client")
    parser.add_argument("--server", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("action", choices=("balance", "deposit", "withdraw"))
    parser.add_argument("account")
    parser.add_argument("amount", nargs="?", type=int)
    arguments = parser.parse_args()
    if not math.isfinite(arguments.timeout) or arguments.timeout <= 0:
        parser.error("timeout must be positive")
    if arguments.action != "balance" and arguments.amount is None:
        parser.error("deposit and withdraw require an amount")
    if arguments.action == "balance" and arguments.amount is not None:
        parser.error("balance does not take an amount")
    payload = {"action": arguments.action, "account": arguments.account}
    if arguments.amount is not None:
        payload["amount"] = arguments.amount
    try:
        status, response = send_request(arguments.server, payload, arguments.timeout)
    except (ClientError, ValueError) as exception:
        print(f"Client error: {exception}", file=sys.stderr)
        return 2
    if response["status"] == "error":
        error = response["error"]
        print(f"HTTP {status} - {error['code']}: {error.get('message', '')}", file=sys.stderr)
        return 1
    print(f"{response['account']} balance: {response['balance']} units")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
