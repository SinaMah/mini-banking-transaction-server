# Final audit changes

## Bug fixes

- Added a bounded `/bank` body-reading deadline (`BANK_BODY_TIMEOUT`, default 10 seconds). Stalled bodies now receive 408 and release capacity; oversized bodies close the connection without buffering the oversized chunk. Database execution is not wrapped in this timeout.
- Hardened CLI response parsing against non-string status fields, deeply nested JSON, invalid balances, mismatched accounts and inconsistent error replies. Invalid ports are rejected before connecting.
- Retained concurrent-test transaction counts when the final balance inquiry fails; the report now includes `final_balance_error` and fails verification without inventing a balance.
- Reject unpaired Unicode surrogates in account identifiers as 400 input errors rather than allowing a SQLite encoding exception to become 500.

## Logging and verification

- Persist Uvicorn's existing warnings/errors to a small rotating `server-errors.log` beside the request log. Protocol warnings remain separate from application JSON records; no missing peer/request metadata are fabricated.
- Identify cancelled tasks in request logs and propagate cancellation. Added capacity-recovery checks for exceptions, disconnects, cancellation, send failure, oversized bodies and timeout, including real HTTP timeout/disconnect tests.
- Kept every dependency version. Binary-wheel resolution checks passed for CPython 3.10-3.14 on Linux x86_64; runtime tests used Python 3.12.14 only.

## Documentation improvements

- Shortened the README while retaining setup, tests, ACID, VM networking, troubleshooting and safe removal instructions.
- Corrected response-delivery language: a logged success does not prove client receipt, and an admitted write can commit after disconnection.
- Prepared a four-page report layout with explicit actual-VM placeholders; clarified protocol logging in the 4:40 demonstration guide.
- Preserved warnings about dynamic-disk allocation and linked-clone dependencies. Cleanup does not promise automatic Windows disk-space restoration.

The existing project layout, SQLite schema, `BEGIN IMMEDIATE` transaction sequence, WAL/FULL durability settings, integer balances and active-request admission policy are preserved. No dependencies, bonus services, GUI or external database were added. Actual two-VM deployment and recording remain pending. Final test results are in `REQUIREMENTS_CHECKLIST.md`.
