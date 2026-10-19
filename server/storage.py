import sqlite3
from collections.abc import Callable
from pathlib import Path


MAX_BALANCE = 2**63 - 1
INITIAL_ACCOUNTS = {"A1001": 1000, "A1002": 2000, "A1003": 500}


class BankingError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


def validate_request(payload: object) -> tuple[str, str, int | None]:
    if not isinstance(payload, dict):
        raise BankingError("invalid_request", "The JSON body must be an object.")
    for field in ("action", "account"):
        if field not in payload:
            raise BankingError("missing_field", f"Missing required field: {field}.")
    action, account = payload["action"], payload["account"]
    if not isinstance(action, str) or action not in {"balance", "deposit", "withdraw"}:
        raise BankingError("unsupported_action", "Use balance, deposit, or withdraw.")
    if not isinstance(account, str) or not account or len(account) > 32:
        raise BankingError("invalid_account", "Account must be a nonempty string of at most 32 characters.")
    try:
        account.encode("utf-8")
    except UnicodeEncodeError:
        raise BankingError("invalid_account", "Account must contain valid Unicode characters.") from None
    allowed = {"action", "account"} if action == "balance" else {"action", "account", "amount"}
    if payload.keys() - allowed:
        raise BankingError("invalid_request", "The request contains unsupported fields.")
    amount = None
    if action != "balance":
        if "amount" not in payload:
            raise BankingError("missing_field", "Missing required field: amount.")
        amount = payload["amount"]
        if type(amount) is not int or not 1 <= amount <= MAX_BALANCE:
            raise BankingError("invalid_amount", f"Amount must be an integer between 1 and {MAX_BALANCE}.")
    return action, account, amount


class Store:
    def __init__(self, path: Path, timeout: float = 5.0):
        self.path = path
        self.timeout = timeout

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=self.timeout, isolation_level=None)
        try:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA read_uncommitted=OFF")
            return connection
        except BaseException:
            connection.close()
            raise

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = self.connect()
        try:
            mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            if mode != "wal":
                raise RuntimeError("SQLite WAL mode is required.")
            connection.execute("BEGIN IMMEDIATE")
            initialized = connection.execute("PRAGMA user_version").fetchone()[0]
            connection.execute(
                "CREATE TABLE IF NOT EXISTS accounts ("
                "account TEXT PRIMARY KEY NOT NULL, "
                "balance INTEGER NOT NULL CHECK (typeof(balance) = 'integer' "
                f"AND balance BETWEEN 0 AND {MAX_BALANCE}))"
            )
            if initialized == 0:
                connection.executemany(
                    "INSERT INTO accounts(account, balance) VALUES (?, ?)", INITIAL_ACCOUNTS.items()
                )
                connection.execute("PRAGMA user_version=1")
            elif initialized != 1:
                raise RuntimeError("Unsupported database schema version.")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def execute(
        self, payload: object, before_commit: Callable[[], None] | None = None
    ) -> dict:
        action, account, amount = validate_request(payload)
        connection = self.connect()
        try:
            if action != "balance":
                connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT balance FROM accounts WHERE account=?", (account,)).fetchone()
            if row is None:
                raise BankingError("unknown_account", "Account does not exist.", 404)
            balance = row[0]
            if action == "deposit":
                if balance > MAX_BALANCE - amount:
                    raise BankingError("balance_limit", "Deposit would exceed the supported balance limit.", 409)
                balance += amount
            elif action == "withdraw":
                if balance < amount:
                    raise BankingError("insufficient_funds", "Insufficient funds.", 409)
                balance -= amount
            if action != "balance":
                connection.execute("UPDATE accounts SET balance=? WHERE account=?", (balance, account))
                if before_commit is not None:
                    before_commit()
                connection.commit()
            return {"status": "success", "account": account, "balance": balance}
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
