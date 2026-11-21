from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import litellm
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from litellm.caching import Cache
from pydantic import BaseModel, ConfigDict, Field, field_validator
from fastapi.encoders import jsonable_encoder

logger = logging.getLogger("chatbot")
litellm.set_verbose = False


@dataclass(frozen=True)
class Settings:
    api_keys: tuple[str, ...]
    database_url: str
    allowed_origins: tuple[str, ...]
    enable_failure_simulation: bool
    model: str
    default_system_prompt: str
    cache_ttl_seconds: int
    provider_timeout_seconds: int = 25
    max_history_messages: int = 20
    max_output_tokens: int = 512

    @classmethod
    def from_env(cls) -> Settings:
        keys = tuple(key.strip() for key in os.getenv("LLM_API_KEYS", "").split(",") if key.strip())
        origins = tuple(
            origin.strip()
            for origin in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")
            if origin.strip()
        )
        return cls(
            api_keys=keys,
            database_url=os.getenv("DATABASE_URL", "sqlite:////data/app.db"),
            allowed_origins=origins,
            enable_failure_simulation=os.getenv("ENABLE_FAILURE_SIMULATION", "false").lower() == "true",
            model=os.getenv("LLM_MODEL", "gemini/gemini-3.5-flash-lite"),
            default_system_prompt=os.getenv(
                "DEFAULT_SYSTEM_PROMPT",
                "You are a helpful, concise assistant. Use the conversation history when answering.",
            ),
            cache_ttl_seconds=int(os.getenv("CACHE_TTL_SECONDS", "3600")),
            provider_timeout_seconds=int(os.getenv("PROVIDER_TIMEOUT_SECONDS", "25")),
            max_history_messages=int(os.getenv("MAX_HISTORY_MESSAGES", "20")),
            max_output_tokens=int(os.getenv("MAX_OUTPUT_TOKENS", "512")),
        )


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1, max_length=128)
    message: str
    system_prompt: str | None = Field(default=None, max_length=4000)

    @field_validator("session_id")
    @classmethod
    def validate_session_id(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 128:
            raise ValueError("session_id must be between 1 and 128 characters")
        return value

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 4000:
            raise ValueError("message must be between 1 and 4000 characters after trimming")
        return value


def _database_path(database_url: str) -> Path:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise ValueError("DATABASE_URL must use SQLite (sqlite:///...)")
    path = database_url[len(prefix) :]
    if not path or path == ":memory:":
        raise ValueError("DATABASE_URL must point to a persistent SQLite file")
    return Path(path)


def _connect(database_url: str) -> sqlite3.Connection:
    path = _database_path(database_url)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


def _initialize_database(database_url: str) -> None:
    with _connect(database_url) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                system_prompt TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                metadata TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS messages_session_id_id
                ON messages (session_id, id);
            """
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _usage_value(response: Any, name: str, model: str, *, messages: list[dict[str, str]], reply: str) -> int:
    usage = getattr(response, "usage", None)
    value = getattr(usage, name, None) if usage is not None else None
    if value is not None:
        return int(value)
    if name == "prompt_tokens":
        return int(litellm.token_counter(model=model, messages=messages))
    return int(litellm.token_counter(model=model, text=reply))


def _cache_hit(response: Any) -> bool:
    hidden = getattr(response, "_hidden_params", None) or {}
    value = hidden.get("cache_hit", False)
    return bool(getattr(value, "value", value))


def _simulated_failures(header_value: str | None, settings: Settings) -> dict[str, int]:
    if not settings.enable_failure_simulation or not header_value:
        return {}
    failures: dict[str, int] = {}
    for pair in header_value.split(","):
        match = re.fullmatch(r"\s*(\*|key_[1-9]\d*)\s*=\s*(429|500|502|503)\s*", pair)
        if not match:
            raise HTTPException(status_code=422, detail="invalid X-Simulate-Failure header")
        failures[match.group(1)] = int(match.group(2))
    return failures


def _get_history(database_url: str, session_id: str) -> tuple[bool, str | None, list[dict[str, str]]]:
    with _connect(database_url) as connection:
        session = connection.execute(
            "SELECT system_prompt FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if session is None:
            return False, None, []
        rows = connection.execute(
            "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id ASC", (session_id,)
        ).fetchall()
    return True, session["system_prompt"], [{"role": row["role"], "content": row["content"]} for row in rows]


def _store_turn(
    database_url: str,
    session_id: str,
    system_prompt: str,
    user_message: str,
    reply: str,
    metadata: dict[str, Any],
    session_exists: bool,
) -> None:
    created_at = _now()
    with _connect(database_url) as connection:
        if session_exists:
            connection.execute(
                "UPDATE sessions SET system_prompt = ? WHERE session_id = ?",
                (system_prompt, session_id),
            )
        else:
            connection.execute(
                "INSERT INTO sessions (session_id, system_prompt, created_at) VALUES (?, ?, ?)",
                (session_id, system_prompt, created_at),
            )
        connection.executemany(
            """
            INSERT INTO messages (session_id, role, content, metadata, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (session_id, "user", user_message, None, created_at),
                (session_id, "assistant", reply, json.dumps(metadata), created_at),
            ],
        )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    if settings.cache_ttl_seconds <= 0:
        raise ValueError("CACHE_TTL_SECONDS must be greater than zero")
    # LiteLLM's local cache is process-local and needs no external service.
    litellm.cache = Cache(type="local", ttl=settings.cache_ttl_seconds)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        _initialize_database(settings.database_url)
        yield

    app = FastAPI(
        title="Resilient LLM Chatbot",
        version="1.0.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Simulate-Failure"],
    )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": jsonable_encoder(exc.errors())})

    @app.exception_handler(Exception)
    async def unexpected_error_handler(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled request error", exc_info=exc)
        return JSONResponse(status_code=500, content={"detail": "internal server error"})

    @app.get("/health")
    def health() -> JSONResponse:
        try:
            with _connect(settings.database_url) as connection:
                connection.execute("SELECT 1").fetchone()
        except (OSError, sqlite3.Error, ValueError):
            logger.exception("Health check could not reach SQLite")
            return JSONResponse(status_code=503, content={"detail": "database unavailable"})
        if len(settings.api_keys) < 2:
            return JSONResponse(status_code=503, content={"detail": "at least two LLM API keys are required"})
        return JSONResponse(
            status_code=200,
            content={"status": "ok", "keys_configured": len(settings.api_keys), "cache_enabled": True},
        )

    @app.post("/chat")
    def chat(
        payload: ChatRequest,
        x_simulate_failure: str | None = Header(default=None, alias="X-Simulate-Failure"),
    ) -> dict[str, Any]:
        if len(settings.api_keys) < 2:
            raise HTTPException(status_code=503, detail="at least two LLM API keys are required")

        simulated = _simulated_failures(x_simulate_failure, settings)
        known_ids = {f"key_{index}" for index in range(1, len(settings.api_keys) + 1)}
        unknown_ids = set(simulated) - {"*"} - known_ids
        if unknown_ids:
            raise HTTPException(status_code=422, detail="X-Simulate-Failure references an unknown key")

        session_exists, stored_prompt, history = _get_history(settings.database_url, payload.session_id)
        system_prompt = (
            payload.system_prompt
            if payload.system_prompt is not None
            else stored_prompt or settings.default_system_prompt
        )
        if settings.max_history_messages > 0:
            history = history[-settings.max_history_messages:]
        messages = [{"role": "system", "content": system_prompt}, *history, {"role": "user", "content": payload.message}]

        started = time.perf_counter()
        response: Any = None
        key_id: str | None = None
        failed_attempts = 0
        has_simulation = bool(simulated)
        for index, api_key in enumerate(settings.api_keys, start=1):
            current_key_id = f"key_{index}"
            if "*" in simulated or current_key_id in simulated:
                failed_attempts += 1
                logger.info("Simulated provider failure for %s: %s", current_key_id, simulated.get("*", simulated.get(current_key_id)))
                continue
            try:
                response = litellm.completion(
                    model=settings.model,
                    messages=messages,
                    api_key=api_key,
                    caching=not has_simulation,
                    timeout=settings.provider_timeout_seconds,
                    max_tokens=settings.max_output_tokens,
                    temperature=0.2,
                    **({"reasoning_effort": "low"} if settings.model.startswith("gemini/") else {}),
                )
                key_id = current_key_id
                break
            except Exception as exc:
                failed_attempts += 1
                logger.warning("Provider attempt failed for %s (%s)", current_key_id, type(exc).__name__)

        if response is None or key_id is None:
            raise HTTPException(status_code=503, detail="all providers unavailable")

        try:
            reply = response.choices[0].message.content
            if not isinstance(reply, str) or not reply.strip():
                raise ValueError("provider returned an empty reply")
            reply = reply.strip()
            input_tokens = _usage_value(
                response, "prompt_tokens", settings.model, messages=messages, reply=reply
            )
            output_tokens = _usage_value(
                response, "completion_tokens", settings.model, messages=messages, reply=reply
            )
        except (AttributeError, IndexError, TypeError, ValueError) as exc:
            logger.warning("Provider response was invalid (%s)", type(exc).__name__)
            raise HTTPException(status_code=503, detail="all providers unavailable") from exc

        cache_hit = _cache_hit(response)
        latency_ms = max(0, int((time.perf_counter() - started) * 1000))
        metadata = {
            "resolution_source": "LLM_CACHE" if cache_hit else "LIVE_INFERENCE",
            "model": settings.model,
            "latency_ms": latency_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "key_id": None if cache_hit else key_id,
            "fallback_attempts": 0 if cache_hit else failed_attempts,
        }

        _store_turn(
            settings.database_url,
            payload.session_id,
            system_prompt,
            payload.message,
            reply,
            metadata,
            session_exists,
        )
        return {"session_id": payload.session_id, "reply": reply, "metadata": metadata}

    @app.get("/sessions/{session_id}/messages")
    def get_messages(session_id: str) -> dict[str, Any]:
        with _connect(settings.database_url) as connection:
            session = connection.execute(
                "SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
            if session is None:
                raise HTTPException(status_code=404, detail="session not found")
            rows = connection.execute(
                """
                SELECT role, content, metadata
                FROM messages
                WHERE session_id = ?
                ORDER BY id ASC
                """,
                (session_id,),
            ).fetchall()
        messages = []
        for row in rows:
            item: dict[str, Any] = {"role": row["role"], "content": row["content"]}
            if row["metadata"] is not None:
                item["metadata"] = json.loads(row["metadata"])
            messages.append(item)
        return {"session_id": session_id, "messages": messages}

    return app


app = create_app()
