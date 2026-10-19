import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest

from server.storage import BankingError, Store


def balance(store, account="A1001"):
    return store.execute({"action": "balance", "account": account})["balance"]


def test_initialization_does_not_reseed(store):
    store.execute({"action": "deposit", "account": "A1001", "amount": 50})
    store.initialize()
    replacement = Store(store.path)
    replacement.initialize()
    assert balance(replacement) == 1050
    assert balance(replacement, "A1002") == 2000
    assert balance(replacement, "A1003") == 500


def test_concurrent_withdrawals_exhaust_exactly_available_funds(store):
    gate = Barrier(8)

    def withdraw(index):
        if index < 8:
            gate.wait(timeout=5)
        try:
            store.execute({"action": "withdraw", "account": "A1001", "amount": 100})
            return "success"
        except BankingError as exception:
            return exception.code

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(withdraw, range(30)))
    assert results.count("success") == 10
    assert results.count("insufficient_funds") == 20
    assert balance(store) == 0


def test_mixed_same_account_transactions_have_no_lost_updates(store):
    gate = Barrier(8)

    def operation(index):
        if index < 8:
            gate.wait(timeout=5)
        return store.execute({"action": "deposit" if index % 2 == 0 else "withdraw",
                              "account": "A1001", "amount": 10})

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(operation, range(80)))
    assert len(results) == 80
    assert balance(store) == 1000


def test_failure_after_update_rolls_back(store):
    def fail():
        raise RuntimeError("Failure before COMMIT")

    with pytest.raises(RuntimeError):
        store.execute({"action": "withdraw", "account": "A1001", "amount": 200}, fail)
    assert balance(store) == 1000
    assert store.execute({"action": "withdraw", "account": "A1001", "amount": 200})["balance"] == 800


@pytest.mark.parametrize("commit", [True, False])
def test_readers_never_observe_uncommitted_update(store, commit):
    updated, released = Event(), Event()

    def hold():
        updated.set()
        if not released.wait(timeout=5):
            raise RuntimeError("Test did not release writer")
        if not commit:
            raise RuntimeError("Rollback test")

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(store.execute, {"action": "deposit", "account": "A1001", "amount": 25}, hold)
        try:
            assert updated.wait(timeout=5)
            assert balance(store) == 1000
        finally:
            released.set()
        if commit:
            assert future.result(timeout=5)["balance"] == 1025
        else:
            with pytest.raises(RuntimeError):
                future.result(timeout=5)
    assert balance(store) == (1025 if commit else 1000)


@pytest.mark.parametrize("invalid", [-1, 1.5, None, "abc"])
def test_database_constraints_reject_invalid_balances(store, invalid):
    connection = store.connect()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE accounts SET balance=? WHERE account='A1001'", (invalid,))
        assert balance(store) == 1000
    finally:
        connection.close()


def test_total_changes_only_by_successful_deposit_and_withdrawal(store):
    store.execute({"action": "deposit", "account": "A1002", "amount": 40})
    store.execute({"action": "withdraw", "account": "A1003", "amount": 90})
    with pytest.raises(BankingError):
        store.execute({"action": "withdraw", "account": "A1003", "amount": 99999})
    assert sum(balance(store, account) for account in ("A1001", "A1002", "A1003")) == 3450


def test_sqlite_durability_and_isolation_settings(store):
    connection = store.connect()
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert connection.execute("PRAGMA read_uncommitted").fetchone()[0] == 0
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        connection.close()
