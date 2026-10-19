# Mini Banking Transaction Server

Distributed Systems Assignment 1: balance inquiry, deposit and withdrawal over HTTP for three fictional accounts. Initial balances are A1001=1000, A1002=2000 and A1003=500 integer units. Actual two-VM deployment and recording remain to be completed.

## Design and files

FastAPI handles JSON requests in one Uvicorn process. Blocking SQLite calls run in a thread pool; each operation uses its own connection to one shared database. Clients use Python's standard library. pytest and HTTPX support tests.

```text
server/                   HTTP API, SQLite storage, local rollback demo
client/                   banking CLI, concurrent client, error demonstration
tests/                    disposable tests
requirements.txt          pinned dependencies
config.example.env        server configuration example
README.md                 setup, protocol, deployment and cleanup
REQUIREMENTS_CHECKLIST.md  requirement mapping and observed checks
REPORT_DRAFT.md/.pdf       editable report and four-page A4 layout
DEMO_GUIDE.md              recording sequence under five minutes
BONUS_FEASIBILITY.md       proposed bonuses, not implemented
CHANGELOG.md               final-audit corrections
```

## Ubuntu setup

Check `python3 --version`: the source requires Python 3.10+. Unchanged pins resolve to Linux x86_64 binary wheels for CPython 3.10-3.14; execution was verified on 3.12.14. Use Ubuntu's maintained Python packages. Resolution checks do not prove execution on other versions or on your VM.

Extract to `~/mini-banking` or adjust the path. Use an ordinary user account:

```bash
cd ~/mini-banking
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --no-cache-dir -r requirements.txt
python -m pip check
cp config.example.env config.env
set -a
source config.env
set +a
```

If venv is unavailable, install Ubuntu's matching `python3-venv` package only if needed. Record this system change; do not remove a preexisting package during cleanup. Install dependencies inside `.venv`, without `sudo pip`. Download caching is disabled above.

Run commands from the project root. `config.env` is not loaded automatically: repeat the export commands in each server/demo terminal, or use defaults. No persistent service or shell-startup modification is installed.

## Server and configuration

Local preparation:

```bash
python -m server.app --host 127.0.0.1 --port 8000
```

Server VM:

```bash
python -m server.app --host 0.0.0.0 --port 8000
```

Keep it in the foreground; Ctrl+C stops it. Use exactly one server process. Multi-worker/reload modes are outside this configuration: admission counts and log rotation are per process.

| Variable | Default | Meaning |
| --- | --- | --- |
| `BANK_MAX_CLIENTS` | `16` | Maximum admitted active HTTP requests, 1-256 |
| `BANK_DB` | `data/bank.sqlite3` | SQLite file |
| `BANK_LOG` | `logs/requests.jsonl` | Application request log |
| `BANK_DB_TIMEOUT` | `5` | SQLite lock wait, greater than 0 and at most 60 seconds |
| `BANK_BODY_TIMEOUT` | `10` | Total `/bank` body-reading deadline, greater than 0 and at most 60 seconds |

Paths resolve against the project root; environment paths outside it are refused. First initialization seeds the accounts and records a schema version; restarts preserve committed balances.

Capacity counts admitted requests until the handler exits, including body reading, validation, thread-pool queuing and SQLite lock waiting. It applies to `/health` too. Each client worker has one outstanding request. Idle keep-alive sockets and requests rejected before reaching ASGI do not occupy application slots. Excess requests receive `503 server_busy` without queuing or retries. SQLite still permits only one writer at a time.

Slots are released in `finally` on completion, errors, detected disconnects and cancellation. Body timeout releases its slot and closes the connection. A client timeout does not necessarily cancel server work: an admitted write may still commit. The body deadline does not time out transactions or incomplete HTTP headers. Uvicorn's two-second keep-alive setting concerns idle connections between requests.

## Client and JSON protocol

In another terminal, activate `.venv` and run:

```bash
python -m client.banking balance A1001
python -m client.banking deposit A1001 200
python -m client.banking withdraw A1001 400
```

On the client VM, no third-party packages are needed:

```bash
python3 -m client.banking --server http://192.168.56.10:8000 --timeout 10 balance A1001
```

Replace the example IP with the real server VM address. Exit codes: 0=success, 1=server rejection, 2=client/transport failure. A lost reply may follow a commit; check balances before repeating writes. There are no idempotency keys or automatic retries.

Use `POST /bank` and `Content-Type: application/json`. Balance requires `action` and `account`; deposit/withdraw also require a positive integer `amount`. Extra fields are rejected. Maximum body size is 4096 bytes. `GET /health` reports liveness and capacity, not disk health.

Example request and response for a fresh A1001:

```json
{"action":"withdraw","account":"A1001","amount":200}
```

```json
{"status":"success","account":"A1001","balance":800}
```

Error shape:

```json
{"status":"error","error":{"code":"insufficient_funds","message":"Insufficient funds."}}
```

Application responses include `X-Request-ID` for log correlation.

| Case | HTTP status / code |
| --- | --- |
| Unknown account | 404 / `unknown_account` |
| Insufficient funds; deposit overflow | 409 / `insufficient_funds`; `balance_limit` |
| Negative, zero, nonnumeric, float, boolean, null or out-of-range amount | 400 / `invalid_amount` |
| Unsupported action; missing field | 400 / `unsupported_action`; `missing_field` |
| Invalid account; non-object or extra fields | 400 / `invalid_account`; `invalid_request` |
| Broken JSON, invalid bytes, duplicate keys, nonstandard numbers | 400 / `malformed_json` |
| Wrong content type; oversized body | 415 / `invalid_content_type`; 413 / `request_too_large` |
| Body deadline exceeded | 408 / `request_timeout` |
| Admission full; SQLite lock timeout | 503 / `server_busy`; `database_busy` |
| Storage failure; unexpected exception | 500 / `storage_error`; `internal_error` |
| Unknown route; wrong method | 404 or 405 / `http_error` |

Oversized-body and timeout responses close the connection. Show every professor error case:

```bash
python3 -m client.error_demo --server http://192.168.56.10:8000
```

## Concurrent client

```bash
python3 -m client.load_test --server http://192.168.56.10:8000 --account A1001 --requests 30 --workers 8 --amount 100
```

The script reads the initial balance, sends concurrent requests, counts actual replies and checks the final balance. From 1000, the expected result is 10 successful withdrawals, 20 insufficient-funds rejections and balance 0. Deposit tests recognize the integer balance limit as an expected rejection.

Keep unrelated writes off that account and usually set workers no greater than capacity. Capacity, transport and protocol failures are reported separately and make verification fail. If the final inquiry fails, transaction counts remain available, actual balance is null and `final_balance_error` explains why. No writes are silently retried.

## ACID and recovery

| Property | Mechanism and verification |
| --- | --- |
| Atomicity | `BEGIN IMMEDIATE`, update, explicit commit; exceptions roll back and connections close. Tests fail after UPDATE and kill a writer before COMMIT. |
| Consistency | Strict positive integer amounts, funds/overflow checks, SQL nonnegative integer constraint. Input, direct-constraint and total-balance tests. |
| Isolation | Independent connections and SQLite writer serialization prevent lost updates. A single SELECT reads committed state; paused-writer tests check dirty reads. |
| Durability | WAL with `synchronous=FULL` per connection; success follows COMMIT. Ordinary restart and SIGKILL tests after confirmed HTTP writes. |

Readers can see the previous committed balance during an unfinished write, a valid sequential ordering. Account correctness uses database transactions rather than an application mutex.

On the server VM, with clients idle and A1002 holding at least one unit:

```bash
python -m server.rollback_demo --account A1002
```

This local script raises an exception after UPDATE and checks the unchanged committed balance. No failure-injection endpoint exists. Query the same balance from the client VM afterwards.

For durability, note a successful deposit reply, identify the exact server PID, then kill and restart with the same database/configuration:

```bash
ps -eo pid,args
kill -KILL <SERVER_PID>
python -m server.app --host 0.0.0.0 --port 8000
```

Replace `<SERVER_PID>` with only the `python -m server.app` PID. Query from the client VM. Test ordinary restart separately with Ctrl+C. Preserve SQLite `-wal`/`-shm` files; do not copy only the main database while live. Process tests assume working storage synchronization and do not simulate host power loss, defective storage or reversal through a VM snapshot.

## Logs and troubleshooting

```bash
tail -n 10 logs/requests.jsonl
tail -f logs/requests.jsonl
tail -n 10 logs/server-errors.log
```

Request JSON Lines contain ID, direct peer IP, parsed action/account/amount, UTC arrival time, route/method, result, status, latency and response. Unavailable metadata is null; capacity rejection happens before parsing. Standard logging serializes writes. Each log has two rotating backups: approximately 2 MiB per request file and 512 KiB per diagnostic file.

Invalid HTTP rejected before a valid ASGI scope never reaches middleware. Uvicorn's existing warnings/errors are also saved to `server-errors.log`, beside `BANK_LOG`, with UTC time and severity. These warnings do not supply request ID, client IP or banking fields and are not JSON request entries. Protocol errors after scope creation may also leave an aborted application record. Parser responses can be plain-text 400s.

`disconnected` records an observed abort/send failure; `cancelled` records task cancellation. Diagnostic 499 is not sent as an HTTP response. A success log means the application handed a response to ASGI, not proof of client receipt; Uvicorn can discard replies after disconnection. Logs may lose the last entry on process kill and old entries rotate. SQLite's WAL supplies recovery.

For connection refusal, check the process, `0.0.0.0` binding and `ss -ltn`. For timeouts, check both guest IPs/network and any scoped TCP/8000 firewall rule. For `server_busy`, reduce workers or wait for handlers; for `database_busy`, check for another writer. Preserve database/WAL files. Use a user-owned folder to solve permission problems rather than running as root.

## Automated tests

```bash
python -m pytest -q
python -m pytest -q tests/test_processes.py
```

Tests use disposable databases/logs below `.test-data/`, which pytest clears on its next run. Do not put personal files there. Demonstration balances are untouched. Checks cover operations/errors, ACID, real HTTP concurrency, capacity recovery, diagnostics and CLI failures. Actual results are in `REQUIREMENTS_CHECKLIST.md`.

## Two separate VirtualBox VMs

The required deployment uses a server VM and a separate client VM connected to the server's real IP. A Windows-host client or two processes in one VM does not satisfy it. These steps require manual verification; no VMs or settings are changed by this project.

1. Keep the existing Ubuntu server VM with its 2.5 GB RAM. Check Windows free space and Ubuntu's `df -h` separately. About 10 GB free on the host does not guarantee room for a full clone, ISO, snapshots and growth.
2. When space permits, prepare a minimal client VM, initially about 1 GB RAM and one CPU, with a suitable dynamically allocated disk. It needs Python only. A full clone copies allocated data; a linked clone saves initial space but depends on its base disk/snapshot, and both disks can grow. Use new MAC addresses, distinct VM names/hostnames and IPs. A fresh minimal VM on a drive with enough space is easier to remove independently.
3. Record current settings and power off both guests. Preserve the original NAT/internet adapter; add unused host-only adapters on the same network, with DHCP as needed. Reachable bridged networking is also acceptable. Separate plain NAT adapters alone do not provide direct guest-to-guest access.
4. Boot both guests; run `hostname` and `ip -br -4 addr`. Find the shared-network server IP, not localhost or its plain NAT address. Enable DHCP on the added interface if needed without changing the existing adapter. Example IPs here are not assigned addresses.
5. Copy the project through an existing shared-folder/file-transfer facility. On the client, use `python3 -m client.banking --server http://<SERVER_IP>:8000 balance A1001`. An optional client-local venv needs no server packages.
6. Start the server bound to `0.0.0.0`; check `ping -c 3 <SERVER_IP>` and a banking HTTP request from the client. Blocked ping alone is inconclusive. If UFW is active, record and add only a TCP/8000 rule scoped to the actual client IP; do not disable the firewall. Remove only your added rule later.
7. Follow `DEMO_GUIDE.md` for normal operations, concurrency, all errors, logs, rollback and crash/restart. Show both VM windows and fill the report's actual-observation fields.

## References

- [SQLite isolation](https://www.sqlite.org/isolation.html)
- [SQLite synchronization](https://www.sqlite.org/pragma.html#pragma_synchronous)
- [FastAPI concurrency](https://fastapi.tiangolo.com/async/)
- [Uvicorn behavior](https://uvicorn.dev/server-behavior/)
- [VirtualBox networking](https://docs.oracle.com/en/virtualization/virtualbox/7.1/user/networkingdetails.html)
