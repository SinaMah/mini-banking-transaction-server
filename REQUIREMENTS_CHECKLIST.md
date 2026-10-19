# Requirements and verification

Source: the five-page Assignment 1 PDF supplied with this task. Pages 1-3 define the mandatory assignment; pages 4-5 are optional bonuses. The PDF's report limit is five A4 pages and its video limit is five minutes.

## Mandatory PDF requirements

| PDF location and requirement | Implementation or deliverable | Verification |
| --- | --- | --- |
| p.1: fictional accounts and initial balances | `INITIAL_ACCOUNTS` and first-schema transaction in `server/storage.py` | `test_initialization_does_not_reseed`; no real customer data |
| p.1: balance inquiry | `POST /bank`, action `balance` | `test_normal_operations`, real CLI test |
| p.1: deposit | Validated integer deposit transaction | `test_normal_operations`, real concurrent deposits |
| p.1: withdraw | Sufficient-funds check within write transaction | Normal operations and concurrent exhaustion tests |
| p.1: consistent documented communication format | HTTP JSON, `/bank`, generated response request ID | README protocol section and HTTP tests |
| p.2: multiple concurrent clients | Async request handling with threaded database calls | `test_real_http_concurrency_and_log_integrity`, mixed same-account storage test |
| p.2: configurable simultaneous-client maximum | `BANK_MAX_CLIENTS`, single-process active-request admission | Capacity tests at 1 and 3; real HTTP saturation; recovery after exception, oversized body, disconnect, cancellation, send failure and timeout |
| p.2: shared account state | One project-local SQLite database accessed by all handlers | Mixed concurrent writes and real HTTP requests |
| p.2: correct simultaneous same-account operations | `BEGIN IMMEDIATE` before read/check/update; one connection per operation | 30 concurrent withdrawals, mixed deposits/withdrawals, no negative balance/lost update |
| p.2: atomicity | Explicit commit/rollback; no success before commit | Failure after UPDATE tests at storage and HTTP level; pre-commit subprocess kill |
| p.2: consistency | Positive integer amounts, nonnegative integer SQL constraint, overflow prevention | Invalid input matrix, direct SQL constraints, total-balance and overflow tests |
| p.2: isolation / no dirty reads | SQLite writer serialization; committed-state reads | Paused writer/read tests for both later commit and rollback; mixed same-account writes |
| p.2: durability | WAL, FULL synchronous mode, commit-before-reply | Pragma assertions; real HTTP graceful restart and SIGKILL restart tests |
| p.2: demo many concurrent withdrawals | `client/load_test.py` | Automated real HTTP test; two-VM recording pending |
| p.2: demo failure halfway with unchanged balance | `server/rollback_demo.py`, local callback after UPDATE | Storage and HTTP failure tests; two-VM recording pending |
| p.2: demo kill/restart retains confirmed writes | Manual SIGKILL workflow in README and demo guide | Automated real server kill/restart passes; actual VM recording pending |
| p.3: unknown account | `404 unknown_account` | Invalid request matrix and error-demo CLI |
| p.3: insufficient funds | `409 insufficient_funds` | Matrix, exhaustion test and error-demo CLI |
| p.3: negative or invalid amount, including zero and abc | `400 invalid_amount` | Integer/type/range matrix and error-demo CLI |
| p.3: unsupported action | `400 unsupported_action` | Matrix and error-demo CLI |
| p.3: missing fields | `400 missing_field` | Missing action, account, amount matrix; error-demo CLI |
| p.3: malformed JSON / socket bytes | Bounded JSON parsing; Uvicorn framing parser and persisted diagnostic warning | Malformed-body matrix, random bytes, actual wire-level 400 with diagnostic file and no fabricated middleware record |
| p.3: meaningful errors and server stays running | Structured JSON application errors; protocol errors handled by Uvicorn | Every invalid-input test follows with a successful request; CLI error demonstration |
| p.3: request and response logging | Application JSON Lines plus separate Uvicorn warning/error diagnostics | Field/content checks, concurrent logs, protocol warning persistence; protocol-layer metadata limitations stated below |
| p.3: request ID | Generated UUID plus response header and log field | Normal operation/header and log correlation tests |
| p.3: client IP | Direct socket peer from ASGI scope | Real HTTP log IP assertions |
| p.3: action, account, timestamp, result, latency | Parsed metadata, UTC timestamp, outcome/status, elapsed time | Logging field/content tests; unreadable or unparsed metadata is null |
| p.3: server and client on separate connected VMs | Manual VirtualBox procedure in README | Pending verification on student's computer; local subprocess tests do not satisfy this deployment item |
| p.3: real server VM IP, not localhost | `--server` accepts arbitrary HTTP(S) server origin | Address tests and example private IP; actual guest-to-guest connection pending |
| p.3: complete client/server source | `server/`, `client/`, tests, configuration in ZIP | Source/CLI review; no environment/data/logs in archive |
| p.3: dependency file with versions | Fully pinned `requirements.txt`, conditional Python 3.10 support packages | Installation and `python -m pip check` |
| p.3: report <= five A4 pages with required explanations | Four-page `REPORT_DRAFT.pdf` and editable `REPORT_DRAFT.md` | PDF page count/layout checked; actual VM observations must be added before submission |
| p.3: video <= five minutes, both VMs and all prescribed cases | 4:40 rehearsal in `DEMO_GUIDE.md` | Recording pending; all requested demonstrations scheduled |
| p.1: LMS and GitHub submission | Source ZIP and submission instructions | Student must submit; no LMS upload or GitHub repository creation performed |

For HTTP, “simultaneous clients” means admitted active requests, with one outstanding request per supplied client worker. Idle connected sockets are not counted. Capacity rejects before body parsing, so banking metadata is null. Cleanup runs in `finally`; a ten-second configurable body deadline prevents unfinished `/bank` bodies from holding slots indefinitely. It does not time out database transactions or incomplete headers.

Malformed HTTP rejected before a valid ASGI scope cannot be logged by middleware. Existing Uvicorn warnings/errors are persisted in a small rotating diagnostic file as well as normal server output. These generic warnings lack request ID, client IP, action/account and request latency; no values are invented. This is a stated transport-level observability limitation, not a claim of complete application metadata for invalid wire bytes. An application success record means a response was handed to ASGI, not proof of client receipt. A disconnect after a full request can still be followed by commit.

## Additional prompt requirements

| Requirement | Evidence |
| --- | --- |
| No floating-point money | Strict `type(amount) is int`, integer SQL storage, 64-bit bounds |
| Test-only/local failure injection | Python callback and local CLI only; extra HTTP fields rejected |
| CLI handles network failures and timeouts | Real refused-connection and read-timeout tests; lost-reply guidance; no automatic writes retried |
| Actual concurrent counts and final invariants | Live-response counts, explicit unexpected failures; final inquiry failure preserves counts and marks actual balance unavailable |
| Disposable, project-local test files | pytest `.test-data` base; subprocess DB/log paths under that directory |
| Local environment and small runtime footprint | Project-local venv, standard-library clients, three accounts, bounded log rotation |
| Cleanup without removing existing Ubuntu | README cleanup and VM dependency instructions; no destructive commands executed |
| Natural, focused source and English README | Cohesive modules, no Python source comments/docstrings, no unrelated features |
| Bonus feasibility only | `BONUS_FEASIBILITY.md`; no bonus claims or infrastructure |
| Critical review | Existing Store transaction code and dependency definitions compared unchanged; focused deadline, logging, CLI and documentation corrections listed in CHANGELOG.md |

## Observed development verification

Final audit on 2026-10-12: `python -m pytest -q` completed with **112 passed, 1 warning in 7.08 seconds**. `python -m pip check` reported **No broken requirements found**. Python 3.10 syntax, absence of source comments/docstrings, documented module paths, four A4 pages and report placeholders were checked. The report was rendered and visually inspected. Execution uses Linux x86_64, Python 3.12.14 and SQLite 3.53.1. No execution on the student's VMs or other Python versions is claimed.

The original requirements file is unchanged. Binary-only pip resolution passed for CPython 3.10, 3.11, 3.12, 3.13 and 3.14 on Linux x86_64, including the relevant Python 3.10 conditional packages and matching Pydantic Core wheels. This checks available distributions and dependency compatibility; it is not a runtime test on five interpreters. Installation remains project-local and lightweight.

Observed live HTTP correctness checks: 30 withdrawals of 100 with 8 workers from a fresh balance of 1000 yielded 10 successes, 20 insufficient-funds rejections, zero unexpected failures and final balance 0. Forty deposits of 5 with 8 workers from 1000 yielded 40 successes and final balance 1200. A near-limit deposit check confirmed five successes and three balance-limit rejections without overflow. These are test observations, not performance claims for the student's hardware.

The single warning is a third-party deprecation warning: Starlette's test client uses the `anyio.abc.BlockingPortal` alias with the pinned AnyIO version. It does not indicate a failed assertion or a server error. Two-VM deployment, host power loss, the student's hardware performance, video recording and submission remain unverified.
