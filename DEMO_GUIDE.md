# Five-minute demonstration guide

Target duration: 4 minutes 40 seconds. Record actual results, with both independent VM windows visible. This is a rehearsal plan; no video has been recorded. Install dependencies, copy files, arrange terminal windows, and verify networking before recording.

## Prepare

Use a dedicated demonstration configuration on the server so tests and previous practice balances do not interfere:

```bash
cd ~/mini-banking
source .venv/bin/activate
export BANK_DB=data/video.sqlite3
export BANK_LOG=logs/video.jsonl
export BANK_MAX_CLIENTS=16
python -m server.app --host 0.0.0.0 --port 8000
```

The first startup seeds only a newly initialized database. Reusing this file preserves earlier practice balances. For a new rehearsal, use another new filename rather than deleting the old database while the server runs. In a second server terminal, activate the environment and export the same three variables for the rollback/restart steps.

On the client VM, change into the copied project. The clients need only Python 3.10+:

```bash
export BANK_SERVER=http://192.168.56.10:8000
python3 -m client.banking --server "$BANK_SERVER" balance A1001
```

Replace the IP before starting. A1001 should begin at 1000 and A1002 at 2000 for the exact walkthrough below; otherwise use the observed starting values and calculate the expected changes. Prepare a terminal displaying `tail -f logs/video.jsonl` on the server. Increase terminal font size; hide unrelated windows and real personal data.

## Recording schedule

| Time | Action and evidence |
| --- | --- |
| 0:00-0:25 | Show two named VM windows. Run `hostname` and `ip -br -4 addr` in each. Point out that the client URL uses the server's actual IP. |
| 0:25-1:00 | From the client, query A1001, deposit 200, withdraw 200, and query again. Explain request/action/account/amount and committed reply. Fresh A1001 returns to 1000. |
| 1:00-1:40 | Run the 30-request, 8-worker withdrawal test below. Show counts and matching final balance; explain that writes are serialized by SQLite transactions despite concurrent HTTP handling. |
| 1:40-2:35 | Run the error demonstration below. Show unknown account, insufficient funds, negative, zero and nonnumeric amounts, unsupported action, missing amount, broken JSON and random bytes. Show that the final balance inquiry still succeeds. |
| 2:35-3:00 | Show real log lines with request ID, client IP, action, account, timestamp, result, latency and response. Explain null metadata for malformed bodies. State the active-request capacity of 16 and 503 saturation response. Explain that protocol warnings use `server-errors.log` because malformed HTTP can be rejected before middleware. |
| 3:00-3:35 | Query A1002 on the client, run the rollback script on the server, then query A1002 again on the client. Show UPDATE-before-COMMIT failure and the unchanged balance. Keep other clients idle. |
| 3:35-4:25 | Deposit 77 into A1002 from the client, show its successful returned balance, kill only the server PID with SIGKILL on the server VM, restart with the same exports, and query from the client. Show the confirmed balance is retained. |
| 4:25-4:40 | State the four mechanisms: rollback, validation/constraints, serialized writes/committed reads, durable COMMIT-before-success. Mention that bonuses are not implemented. Stop recording before five minutes. |

## Commands to keep ready

Client VM, normal operations:

```bash
python3 -m client.banking --server "$BANK_SERVER" balance A1001
python3 -m client.banking --server "$BANK_SERVER" deposit A1001 200
python3 -m client.banking --server "$BANK_SERVER" withdraw A1001 200
python3 -m client.banking --server "$BANK_SERVER" balance A1001
```

Client VM, concurrency and errors:

```bash
python3 -m client.load_test --server "$BANK_SERVER" --account A1001 --requests 30 --workers 8 --amount 100
python3 -m client.error_demo --server "$BANK_SERVER"
```

Client VM, atomicity and durability:

```bash
python3 -m client.banking --server "$BANK_SERVER" balance A1002
python3 -m client.banking --server "$BANK_SERVER" balance A1002
python3 -m client.banking --server "$BANK_SERVER" deposit A1002 77
python3 -m client.banking --server "$BANK_SERVER" balance A1002
```

Run the second balance command after the local rollback demonstration; run the last one after crash/restart. A fresh A1002 remains 2000 after rollback and becomes 2077 after the confirmed deposit and restart. These numbers are expected results; show the actual replies in the recording.

Server VM, rollback in a second terminal with the same exported configuration:

```bash
python -m server.rollback_demo --account A1002
```

This local script uses the server's real database and transaction code. It is not an HTTP failure endpoint. The automated suite additionally injects the same failure through the application during testing and checks the error response and unchanged balance.

Server VM, crash and restart:

```bash
ps -eo pid,args
kill -KILL <SERVER_PID>
python -m server.app --host 0.0.0.0 --port 8000
```

Identify the exact Python server PID before the recording. Do not kill the VM, desktop, shell, logging terminal or unrelated Python process. Leave the database and WAL files intact. A terminal's "Killed" message and the restarted server's startup lines make the process termination visible.

The mandatory malformed-request cases are demonstrated as broken JSON and random bytes inside valid HTTP requests. If discussing invalid HTTP framing, explain that Uvicorn emits a protocol-level warning and plain-text 400 before any application scope may exist. Do not attribute that warning to JSON middleware logging or invent a request ID/client IP for it. `logs/server-errors.log` also contains real server exceptions; it is separate from transaction durability.

## Rehearsal and submission checks

Rehearse one ordinary Ctrl+C restart outside the timed recording as well. Confirm that the final balance survives both ordinary restart and SIGKILL. If networking or a check fails, fix it and re-record; do not present failed or omitted VM checks as completed. Capture the entire error script output legibly rather than skipping lines. Submit the real screen recording, source/dependency files, GitHub link as required, and a report of no more than five A4 pages. Replace the draft's pending VM observations with your actual results.
