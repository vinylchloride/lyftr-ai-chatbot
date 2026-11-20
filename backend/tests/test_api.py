from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

import litellm
from app.main import Settings, create_app


class FakeCompletion:
    calls: list[dict[str, Any]] = []
    failures: dict[str, Exception] = {}

    @classmethod
    def reset(cls) -> None:
        cls.calls = []
        cls.failures = {}

    @classmethod
    def complete(cls, **kwargs: Any) -> Any:
        cls.calls.append(kwargs)
        key = kwargs["api_key"]
        if key in cls.failures:
            raise cls.failures[key]
        content = kwargs["messages"][-1]["content"]
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=f"Answer: {content}"))],
            usage=SimpleNamespace(prompt_tokens=23, completion_tokens=7),
            _hidden_params={"cache_hit": False},
        )


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    FakeCompletion.reset()
    monkeypatch.setattr("app.main.litellm.completion", FakeCompletion.complete)
    settings = Settings(
        api_keys=("test-key-1", "test-key-2"),
        database_url=f"sqlite:///{tmp_path / 'chat.db'}",
        allowed_origins=("http://localhost:3000",),
        enable_failure_simulation=True,
        model="gemini/gemini-3.6-flash",
        default_system_prompt="Be useful.",
        cache_ttl_seconds=60,
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def chat(client: TestClient, session_id: str, message: str, **kwargs: Any):
    return client.post("/chat", json={"session_id": session_id, "message": message, **kwargs})


def test_health_and_successful_chat_include_per_request_metadata(client: TestClient) -> None:
    paths = {
        (route.path, method)
        for route in client.app.routes
        for method in getattr(route, "methods", set())
        if method in {"GET", "POST"}
    }
    assert paths == {
        ("/health", "GET"),
        ("/chat", "POST"),
        ("/sessions/{session_id}/messages", "GET"),
    }

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok", "keys_configured": 2, "cache_enabled": True}

    response = chat(client, "s-1", " hello ")
    assert response.status_code == 200
    assert response.json()["reply"] == "Answer: hello"
    assert response.json()["metadata"] == {
        "resolution_source": "LIVE_INFERENCE",
        "model": "gemini/gemini-3.6-flash",
        "latency_ms": response.json()["metadata"]["latency_ms"],
        "input_tokens": 23,
        "output_tokens": 7,
        "key_id": "key_1",
        "fallback_attempts": 0,
    }
    assert FakeCompletion.calls[0]["messages"] == [
        {"role": "system", "content": "Be useful."},
        {"role": "user", "content": "hello"},
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {"session_id": "", "message": "hello"},
        {"session_id": "s-1", "message": "  "},
        {"session_id": "s-1", "message": "x" * 4001},
    ],
)
def test_invalid_chat_is_not_stored(client: TestClient, payload: dict[str, str]) -> None:
    response = client.post("/chat", json=payload)
    assert response.status_code == 422
    assert "detail" in response.json()
    assert client.get("/sessions/s-1/messages").status_code == 404


def test_history_is_persisted_in_order_and_context_is_sent(client: TestClient) -> None:
    assert chat(client, "s-history", "First").status_code == 200
    assert chat(client, "s-history", "Second").status_code == 200

    history = client.get("/sessions/s-history/messages")
    assert history.status_code == 200
    messages = history.json()["messages"]
    assert [(item["role"], item["content"]) for item in messages] == [
        ("user", "First"),
        ("assistant", "Answer: First"),
        ("user", "Second"),
        ("assistant", "Answer: Second"),
    ]
    assert "metadata" in messages[1]
    assert [item["content"] for item in FakeCompletion.calls[1]["messages"]] == [
        "Be useful.",
        "First",
        "Answer: First",
        "Second",
    ]


def test_simulated_failure_fails_over_and_records_actual_key(client: TestClient) -> None:
    response = client.post(
        "/chat",
        headers={"X-Simulate-Failure": "key_1=429"},
        json={"session_id": "s-failover", "message": "try the backup"},
    )
    assert response.status_code == 200
    assert response.json()["metadata"]["key_id"] == "key_2"
    assert response.json()["metadata"]["fallback_attempts"] == 1
    assert [call["api_key"] for call in FakeCompletion.calls] == ["test-key-2"]


def test_all_simulated_keys_fail_without_storing_turn(client: TestClient) -> None:
    response = client.post(
        "/chat",
        headers={"X-Simulate-Failure": "*=429"},
        json={"session_id": "s-outage", "message": "unavailable"},
    )
    assert response.status_code == 503
    assert response.json() == {"detail": "all providers unavailable"}
    assert FakeCompletion.calls == []
    assert client.get("/sessions/s-outage/messages").status_code == 404


def test_provider_failure_uses_next_key(client: TestClient) -> None:
    FakeCompletion.failures["test-key-1"] = RuntimeError("quota exhausted")
    response = chat(client, "s-provider-fallback", "backup please")
    assert response.status_code == 200
    assert response.json()["metadata"]["key_id"] == "key_2"
    assert response.json()["metadata"]["fallback_attempts"] == 1


def test_unknown_session_and_invalid_simulation_header(client: TestClient) -> None:
    assert client.get("/sessions/missing/messages").status_code == 404
    response = client.post(
        "/chat",
        headers={"X-Simulate-Failure": "key_1=400"},
        json={"session_id": "s-invalid", "message": "hello"},
    )
    assert response.status_code == 422


def test_prompt_override_persists_for_session(client: TestClient) -> None:
    assert chat(client, "s-prompt", "First", system_prompt="Be brief.").status_code == 200
    assert chat(client, "s-prompt", "Second").status_code == 200
    assert FakeCompletion.calls[1]["messages"][0] == {"role": "system", "content": "Be brief."}


def test_litellm_cache_reuses_identical_first_turn_across_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_completion = litellm.completion
    live_calls = 0

    def mocked_provider(**kwargs: Any) -> Any:
        nonlocal live_calls
        response = real_completion(**kwargs, mock_response="A cached answer.")
        if not (getattr(response, "_hidden_params", None) or {}).get("cache_hit"):
            live_calls += 1
        return response

    monkeypatch.setattr("app.main.litellm.completion", mocked_provider)
    settings = Settings(
        api_keys=("cache-key-1", "cache-key-2"),
        database_url=f"sqlite:///{tmp_path / 'cache.db'}",
        allowed_origins=("http://localhost:3000",),
        enable_failure_simulation=False,
        model="gpt-4o-mini",
        default_system_prompt="Be useful.",
        cache_ttl_seconds=60,
    )
    with TestClient(create_app(settings)) as test_client:
        first = chat(test_client, "s-cache-1", "same question").json()
        second = chat(test_client, "s-cache-2", "same question").json()
        different = chat(test_client, "s-cache-3", "different question").json()

    assert first["reply"] == second["reply"] == "A cached answer."
    assert first["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert second["metadata"]["resolution_source"] == "LLM_CACHE"
    assert second["metadata"]["key_id"] is None
    assert second["metadata"]["input_tokens"] == first["metadata"]["input_tokens"]
    assert different["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert live_calls == 2
