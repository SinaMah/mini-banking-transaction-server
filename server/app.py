import argparse
import asyncio
import json
import logging
import os
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.requests import ClientDisconnect

from server.storage import BankingError, Store


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAX_BODY = 4096


def local_path(value: str) -> Path:
    path = (PROJECT_ROOT / value).resolve()
    if PROJECT_ROOT not in path.parents:
        raise ValueError("Data and log paths must be files inside the project directory.")
    return path


@dataclass(frozen=True)
class Settings:
    database: Path
    log: Path
    max_clients: int = 16
    db_timeout: float = 5.0
    body_timeout: float = 10.0

    def __post_init__(self):
        if type(self.max_clients) is not int or not 1 <= self.max_clients <= 256:
            raise ValueError("BANK_MAX_CLIENTS must be an integer from 1 to 256.")
        if not 0 < self.db_timeout <= 60:
            raise ValueError("BANK_DB_TIMEOUT must be greater than 0 and at most 60 seconds.")
        if not 0 < self.body_timeout <= 60:
            raise ValueError("BANK_BODY_TIMEOUT must be greater than 0 and at most 60 seconds.")

    @classmethod
    def from_environment(cls):
        return cls(
            local_path(os.environ.get("BANK_DB", "data/bank.sqlite3")),
            local_path(os.environ.get("BANK_LOG", "logs/requests.jsonl")),
            int(os.environ.get("BANK_MAX_CLIENTS", "16")),
            float(os.environ.get("BANK_DB_TIMEOUT", "5")),
            float(os.environ.get("BANK_BODY_TIMEOUT", "10")),
        )


def error_response(code: str, message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"status": "error", "error": {"code": code, "message": message}}, status_code=status)


def parse_json(body: bytes) -> object:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON field.")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError("Nonstandard JSON number.")

    return json.loads(body, object_pairs_hook=pairs, parse_constant=invalid_constant)


async def read_body(request: Request) -> bytes:
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_BODY:
            raise BankingError("request_too_large", f"JSON body cannot exceed {MAX_BODY} bytes.", 413)
        body.extend(chunk)
    return bytes(body)


class RequestMiddleware:
    def __init__(self, app, settings: Settings, logger: logging.Logger):
        self.app = app
        self.limit = settings.max_clients
        self.active = 0
        self.logger = logger

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        request_id = uuid.uuid4().hex
        body = bytearray()
        response_body = bytearray()
        status_code = 500
        response_started = False
        response_complete = False
        cancelled = False
        admitted = self.active < self.limit
        if admitted:
            self.active += 1

        async def read_message():
            message = await receive()
            if message["type"] == "http.request":
                body.extend(message.get("body", b"")[: max(0, MAX_BODY - len(body))])
            return message

        async def send_message(message):
            nonlocal status_code, response_started, response_complete
            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_started = True
                message = dict(message)
                message["headers"] = list(message.get("headers", [])) + [(b"x-request-id", request_id.encode())]
            if message["type"] == "http.response.body":
                response_body.extend(message.get("body", b"")[: max(0, 8192 - len(response_body))])
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                response_complete = True

        try:
            if admitted:
                await self.app(scope, read_message, send_message)
            else:
                response = error_response("server_busy", "Maximum active requests reached; try again later.", 503)
                await response(scope, receive, send_message)
        except ClientDisconnect:
            status_code = 499
        except asyncio.CancelledError:
            status_code = 499
            cancelled = True
            raise
        except Exception:
            logging.getLogger("uvicorn.error").exception("Request %s failed", request_id)
            if not response_started:
                await error_response("internal_error", "Request could not be completed.", 500)(scope, receive, send_message)
        finally:
            if admitted:
                self.active -= 1
            try:
                request_data = parse_json(bytes(body))
                if not isinstance(request_data, dict):
                    request_data = {}
            except (ValueError, UnicodeError, RecursionError):
                request_data = {}
            try:
                response_data = json.loads(response_body)
            except (ValueError, UnicodeError):
                response_data = None
            action = request_data.get("action")
            account = request_data.get("account")
            result = "cancelled" if cancelled else "disconnected" if not response_complete else "success" if status_code < 400 else "error"
            entry = {
                "request_id": request_id,
                "client_ip": (scope.get("client") or (None,))[0],
                "action": action if isinstance(action, str) else None,
                "account": account if isinstance(account, str) else None,
                "amount": request_data.get("amount") if type(request_data.get("amount")) is int else None,
                "timestamp": timestamp,
                "method": scope["method"],
                "path": scope["path"],
                "result": result,
                "http_status": status_code,
                "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                "response": response_data,
            }
            self.logger.info(json.dumps(entry, separators=(",", ":")))


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_environment()
    store = Store(settings.database, settings.db_timeout)
    logger = logging.getLogger(f"banking.requests.{uuid.uuid4().hex}")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    @asynccontextmanager
    async def lifespan(app):
        await run_in_threadpool(store.initialize)
        settings.log.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(settings.log, maxBytes=2 * 1024 * 1024, backupCount=2, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        error_logger = logging.getLogger("uvicorn.error")
        error_handler = RotatingFileHandler(settings.log.with_name("server-errors.log"),
                                           maxBytes=512 * 1024, backupCount=2, encoding="utf-8")
        error_handler.setLevel(logging.WARNING)
        error_format = logging.Formatter("%(asctime)sZ %(levelname)s %(message)s", "%Y-%m-%dT%H:%M:%S")
        error_format.converter = time.gmtime
        error_handler.setFormatter(error_format)
        error_logger.addHandler(error_handler)
        try:
            yield
        finally:
            logger.removeHandler(handler)
            handler.close()
            error_logger.removeHandler(error_handler)
            error_handler.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store
    app.state.settings = settings
    app.add_middleware(RequestMiddleware, settings=settings, logger=logger)

    @app.exception_handler(HTTPException)
    async def http_error(request, exception):
        return error_response("http_error", str(exception.detail), exception.status_code)

    @app.get("/health")
    async def health():
        return {"status": "success", "max_clients": settings.max_clients}

    @app.post("/bank")
    async def bank(request: Request):
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            return error_response("invalid_content_type", "Use Content-Type: application/json.", 415)
        try:
            body = await asyncio.wait_for(read_body(request), timeout=settings.body_timeout)
        except asyncio.TimeoutError:
            response = error_response("request_timeout", "Timed out while receiving the JSON body.", 408)
            response.headers["Connection"] = "close"
            return response
        except BankingError as exception:
            response = error_response(exception.code, str(exception), exception.status)
            response.headers["Connection"] = "close"
            return response
        try:
            payload = parse_json(body)
        except (ValueError, UnicodeError, RecursionError):
            return error_response("malformed_json", "Request body is not valid JSON.")
        try:
            return await run_in_threadpool(store.execute, payload)
        except BankingError as exception:
            return error_response(exception.code, str(exception), exception.status)
        except sqlite3.OperationalError as exception:
            code = getattr(exception, "sqlite_errorcode", 0) & 255
            if code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) or str(exception) in {"database is locked", "database table is locked"}:
                return error_response("database_busy", "Database is busy; try again later.", 503)
            logging.getLogger("uvicorn.error").exception("SQLite operation failed")
            return error_response("storage_error", "Storage operation failed; check server output.", 500)

    return app


def main():
    parser = argparse.ArgumentParser(description="Mini banking HTTP server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    arguments = parser.parse_args()
    uvicorn.run(create_app(), host=arguments.host, port=arguments.port, workers=1, proxy_headers=False,
                timeout_keep_alive=2, timeout_graceful_shutdown=10)


if __name__ == "__main__":
    main()
