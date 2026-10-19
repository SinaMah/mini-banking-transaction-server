import argparse

from client.banking import ClientError, send_request


CASES = [
    ("Unknown account", {"action": "balance", "account": "Z9999"}, "unknown_account"),
    ("Insufficient funds", {"action": "withdraw", "account": "A1003", "amount": 9223372036854775807}, "insufficient_funds"),
    ("Negative amount", {"action": "deposit", "account": "A1001", "amount": -50}, "invalid_amount"),
    ("Zero amount", {"action": "withdraw", "account": "A1001", "amount": 0}, "invalid_amount"),
    ("Nonnumeric amount", {"action": "deposit", "account": "A1001", "amount": "abc"}, "invalid_amount"),
    ("Unsupported action", {"action": "transfer", "account": "A1001", "amount": 1}, "unsupported_action"),
    ("Missing amount", {"action": "withdraw", "account": "A1001"}, "missing_field"),
    ("Malformed JSON", b'{"action":', "malformed_json"),
    ("Random bytes", b"\xff\x00broken", "malformed_json"),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Show mandatory error cases without changing balances")
    parser.add_argument("--server", default="http://127.0.0.1:8000")
    arguments = parser.parse_args()
    passed = True
    try:
        for name, payload, expected in CASES:
            status, response = send_request(arguments.server, payload)
            code = response.get("error", {}).get("code")
            correct = status >= 400 and code == expected
            passed = passed and correct
            print(f"{name}: HTTP {status}, {response}, matched={correct}")
        status, response = send_request(arguments.server, {"action": "balance", "account": "A1001"})
        passed = passed and status == 200 and response["status"] == "success"
        print(f"Server still responding: {response}")
    except (ClientError, ValueError) as exception:
        print(f"Error demonstration stopped: {exception}")
        return 2
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
