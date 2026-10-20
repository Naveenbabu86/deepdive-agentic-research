import httpx
import pytest
from openai import BadRequestError

from app.services.llm import MeshLLM


def test_complete_json_retries_without_json_mode_after_mesh_validation_error():
    response = httpx.Response(
        400,
        json={
            "error": {
                "code": "json_validate_failed",
                "message": "",
            }
        },
        request=httpx.Request("POST", "https://api.meshapi.ai/v1/chat/completions"),
    )
    provider_error = BadRequestError(
        message="JSON validation failed",
        response=response,
        body=response.json(),
    )
    llm = MeshLLM.__new__(MeshLLM)
    calls = []

    def fake_complete(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise provider_error
        return '{"claims": []}'

    llm.complete = fake_complete

    result = llm.complete_json(system="Return JSON.", user="Create claims.")

    assert result == {"claims": []}
    assert calls[0]["response_format"] == {"type": "json_object"}
    assert "response_format" not in calls[1]


def test_complete_json_does_not_retry_unrelated_provider_error():
    response = httpx.Response(
        400,
        json={"error": {"code": "invalid_api_key", "message": "Unauthorized"}},
        request=httpx.Request("POST", "https://api.meshapi.ai/v1/chat/completions"),
    )
    provider_error = BadRequestError(
        message="Invalid API key",
        response=response,
        body=response.json(),
    )
    llm = MeshLLM.__new__(MeshLLM)
    calls = []

    def fake_complete(**kwargs):
        calls.append(kwargs)
        raise provider_error

    llm.complete = fake_complete

    with pytest.raises(BadRequestError):
        llm.complete_json(system="Return JSON.", user="Create claims.")

    assert len(calls) == 1
