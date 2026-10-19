import argparse

from server.app import Settings
from server.storage import Store


def main() -> int:
    parser = argparse.ArgumentParser(description="Local rollback demonstration; run on the server VM")
    parser.add_argument("--account", default="A1002")
    arguments = parser.parse_args()
    settings = Settings.from_environment()
    store = Store(settings.database, settings.db_timeout)
    store.initialize()
    balance_request = {"action": "balance", "account": arguments.account}
    before = store.execute(balance_request)["balance"]

    def fail_before_commit():
        print("Balance UPDATE executed; injecting an exception before COMMIT.")
        raise RuntimeError("Controlled demonstration failure")

    try:
        store.execute({"action": "withdraw", "account": arguments.account, "amount": 1}, fail_before_commit)
    except RuntimeError as exception:
        print(exception)
    after = store.execute(balance_request)["balance"]
    print(f"Before: {before}; after: {after}; rollback verified: {before == after}")
    return 0 if before == after else 1


if __name__ == "__main__":
    raise SystemExit(main())
