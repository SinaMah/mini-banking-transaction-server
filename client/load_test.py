import argparse
import json
import math
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from client.banking import ClientError, send_request, server_url
from server.storage import MAX_BALANCE


def run_load(server: str, account: str, action: str, amount: int, requests: int, workers: int, timeout: float) -> dict:
    _, before = send_request(server, {"action": "balance", "account": account}, timeout)
    if before["status"] != "success":
        raise ClientError(f"Cannot read initial balance: {before}")
    gate = Barrier(workers)

    def submit(index):
        if index < workers:
            gate.wait()
        try:
            status, response = send_request(server, {"action": action, "account": account, "amount": amount}, timeout)
            if status == 200 and response["status"] == "success" and response["account"] == account:
                return "success", str(response["balance"])
            code = response.get("error", {}).get("code", "invalid_response")
            expected_code = "insufficient_funds" if action == "withdraw" else "balance_limit"
            if status == 409 and code == expected_code:
                return "business_rejection", code
            return "unexpected_failure", code
        except (ClientError, ValueError) as exception:
            return "unexpected_failure", str(exception)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(submit, range(requests)))
    counts = Counter(category for category, detail in results)
    errors = Counter(detail for category, detail in results if category == "unexpected_failure")
    final_error = None
    actual = None
    try:
        _, after = send_request(server, {"action": "balance", "account": account}, timeout)
        if after["status"] != "success":
            final_error = f"Cannot read final balance: {after}"
        else:
            actual = after["balance"]
    except (ClientError, ValueError) as exception:
        final_error = str(exception)
    initial = before["balance"]
    successes = counts["success"]
    available = initial if action == "withdraw" else MAX_BALANCE - initial
    expected_successes = min(requests, available // amount)
    delta = amount if action == "deposit" else -amount
    expected = initial + delta * successes
    valid = (
        final_error is None
        and counts["unexpected_failure"] == 0
        and successes == expected_successes
        and counts["business_rejection"] == requests - expected_successes
        and actual == expected
        and 0 <= actual <= MAX_BALANCE
    )
    return {
        "account": account,
        "action": action,
        "amount": amount,
        "workers": workers,
        "requests_submitted": requests,
        "successful_transactions": successes,
        "expected_business_rejections": counts["business_rejection"],
        "unexpected_failures": counts["unexpected_failure"],
        "failure_details": dict(errors),
        "initial_balance": initial,
        "expected_successes_without_failures": expected_successes,
        "expected_final_balance_from_confirmed_successes": expected,
        "actual_final_balance": actual,
        "final_balance_error": final_error,
        "invariants_satisfied": valid,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Concurrent deposit or withdrawal test")
    parser.add_argument("--server", default="http://127.0.0.1:8000")
    parser.add_argument("--account", default="A1001")
    parser.add_argument("--action", choices=("deposit", "withdraw"), default="withdraw")
    parser.add_argument("--amount", type=int, default=100)
    parser.add_argument("--requests", type=int, default=30)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=10)
    arguments = parser.parse_args()
    if not 1 <= arguments.workers <= arguments.requests <= 10000:
        parser.error("require 1 <= workers <= requests <= 10000")
    if arguments.workers > 256 or not 1 <= arguments.amount <= MAX_BALANCE or not math.isfinite(arguments.timeout) or arguments.timeout <= 0:
        parser.error("workers must be <= 256; amount and timeout must be positive and supported")
    try:
        server = server_url(arguments.server)
        summary = run_load(server, arguments.account, arguments.action, arguments.amount,
                           arguments.requests, arguments.workers, arguments.timeout)
    except (ClientError, ValueError) as exception:
        print(f"Load test stopped: {exception}")
        return 2
    print(json.dumps(summary, indent=2))
    return 0 if summary["invariants_satisfied"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
