from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import APIStatusError

from app import main
from app import security
from app.services.llm import MeshConfigurationError


API_KEY = "test-api-key-" + "x" * 32
RUN_ID = "11111111-1111-1111-1111-111111111111"


def _configure_api(
    monkeypatch,
    *,
    auth_required=True,
    mesh_configured=True,
    rate_limit_requests=100,
):
    settings = SimpleNamespace(
        auth_required=auth_required,
        deepdive_api_key=API_KEY if auth_required else "",
        mesh_api_key="configured" if mesh_configured else "",
        tavily_api_key="configured" if mesh_configured else "",
        rate_limit_requests=rate_limit_requests,
        rate_limit_window_seconds=60,
    )
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(security, "get_settings", lambda: settings)
    main._rate_events.clear()
    return settings


@pytest.fixture
def client():
    return TestClient(main.app)


def auth_headers():
    return {"X-API-Key": API_KEY}


def _successful_workflow(monkeypatch):
    class SuccessfulWorkflow:
        def run(self, question, max_research_rounds, run_id=None, conversation_id=None):
            return {
                "run_id": RUN_ID,
                "question": question,
                "status": "completed",
                "report_markdown": "# Report",
                "subquestions": [],
                "sources": [],
                "claims": [],
                "audit_events": [],
                "metrics": {},
            }

    monkeypatch.setattr(main, "DeepDiveWorkflow", SuccessfulWorkflow)


def test_health_is_public_and_returns_liveness_payload(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "deepdive"}


def test_ready_returns_503_when_required_configuration_is_missing(client, monkeypatch):
    _configure_api(monkeypatch, mesh_configured=False)

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": "Required upstream credentials are not configured"}


def test_ready_returns_503_when_persistence_dependency_fails(client, monkeypatch):
    _configure_api(monkeypatch)

    class FailingAuditStore:
        def __init__(self):
            raise OSError("private database detail")

    monkeypatch.setattr(main, "AuditStore", FailingAuditStore)

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": "Persistence is not ready"}
    assert "private database detail" not in response.text


def test_ready_reports_success_without_contacting_upstreams(client, monkeypatch):
    _configure_api(monkeypatch)
    monkeypatch.setattr(main, "AuditStore", lambda: object())

    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_research_accepts_valid_request_and_serializes_workflow_result(client, monkeypatch):
    _configure_api(monkeypatch)
    observed = {}

    class FakeWorkflow:
        def run(self, question, max_research_rounds, run_id=None, conversation_id=None):
            observed.update({
                "question": question,
                "max_research_rounds": max_research_rounds,
                "run_id": run_id,
                "conversation_id": conversation_id,
            })
            return {
                "run_id": RUN_ID,
                "question": question,
                "status": "completed",
                "report_markdown": "# Report",
                "subquestions": ["What is supported?"],
                "sources": [],
                "claims": [],
                "audit_events": [{"event_type": "request_completed"}],
                "metrics": {"claims_in_final_report": 0},
            }

    monkeypatch.setattr(main, "DeepDiveWorkflow", FakeWorkflow)
    response = client.post(
        "/research",
        headers={**auth_headers(), "X-Run-ID": RUN_ID},
        json={"question": "  Explain the evidence  ", "max_research_rounds": 1},
    )

    assert response.status_code == 200
    assert response.json()["run_id"] == RUN_ID
    assert response.json()["status"] == "completed"
    assert response.json()["report_markdown"] == "# Report"
    assert observed == {
        "question": "Explain the evidence",
        "max_research_rounds": 1,
        "run_id": RUN_ID,
        "conversation_id": None,
    }


def test_research_accepts_positive_bearer_authentication(client, monkeypatch):
    _configure_api(monkeypatch)
    _successful_workflow(monkeypatch)

    response = client.post(
        "/research",
        headers={"Authorization": f"Bearer {API_KEY}"},
        json={"question": "Explain the evidence"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "completed"


@pytest.mark.parametrize(
    "payload",
    [
        {"question": "short"},
        {"question": "        "},
        {"question": "A valid question", "max_research_rounds": 5},
    ],
)
def test_research_rejects_invalid_requests_before_workflow(client, monkeypatch, payload):
    _configure_api(monkeypatch)
    monkeypatch.setattr(main, "DeepDiveWorkflow", lambda: pytest.fail("workflow must not run"))

    response = client.post("/research", headers=auth_headers(), json=payload)

    assert response.status_code == 422
    assert "detail" in response.json()


@pytest.mark.parametrize("headers", [{}, {"X-API-Key": "wrong"}])
def test_research_rejects_missing_or_invalid_authentication(client, monkeypatch, headers):
    _configure_api(monkeypatch)
    monkeypatch.setattr(main, "DeepDiveWorkflow", lambda: pytest.fail("workflow must not run"))

    response = client.post(
        "/research",
        headers=headers,
        json={"question": "Explain the evidence"},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Valid API key required"}
    assert response.headers["www-authenticate"] == "Bearer"


def test_research_returns_429_when_rate_limit_is_exhausted(client, monkeypatch):
    _configure_api(monkeypatch, rate_limit_requests=1)
    _successful_workflow(monkeypatch)
    request = {"question": "Explain the evidence"}

    first = client.post("/research", headers=auth_headers(), json=request)
    second = client.post("/research", headers=auth_headers(), json=request)

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json() == {"detail": "Rate limit exceeded; retry after the current window"}
    assert second.headers["retry-after"] == "60"


def test_research_returns_503_when_all_concurrency_slots_are_busy(client, monkeypatch):
    _configure_api(monkeypatch)

    class BusySlots:
        def acquire(self, blocking=False):
            return False

    monkeypatch.setattr(main, "_run_slots", BusySlots())

    response = client.post(
        "/research",
        headers=auth_headers(),
        json={"question": "Explain the evidence"},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Research capacity is busy; retry shortly"}
    assert response.headers["retry-after"] == "5"


def _provider_status_error(status_code):
    response = httpx.Response(
        status_code,
        json={
            "error": {
                "code": "provider_limit",
                "type": "provider_error",
                "param": "messages",
                "message": "secret provider response",
            }
        },
        request=httpx.Request("POST", "https://api.meshapi.ai/v1/chat/completions"),
    )
    return APIStatusError(
        message="provider rejected request",
        response=response,
        body=response.json(),
    )


@pytest.mark.parametrize("provider_status", [413, 429])
def test_research_maps_provider_413_and_429_to_controlled_502(
    client, monkeypatch, provider_status
):
    _configure_api(monkeypatch)
    provider_error = _provider_status_error(provider_status)

    class FailingWorkflow:
        def run(self, *args, **kwargs):
            raise provider_error

    monkeypatch.setattr(main, "DeepDiveWorkflow", FailingWorkflow)

    response = client.post(
        "/research",
        headers=auth_headers(),
        json={"question": "Explain the evidence"},
    )

    assert response.status_code == 502
    assert response.json() == {
        "detail": "LLM provider rate/token limit reached. Reduce evidence size or wait for the provider rate window to reset."
    }
    assert "secret provider response" not in response.text


@pytest.mark.parametrize(
    ("error", "status_code", "detail"),
    [
        (MeshConfigurationError("secret mesh detail"), 503, "Mesh API is not configured; check server configuration"),
        (RuntimeError("secret upstream detail"), 502, "An upstream research service failed; consult the run audit trail and server logs"),
        (ValueError("secret application detail"), 500, "Research run failed. Check server logs and audit trail."),
    ],
)
def test_research_returns_controlled_errors_without_leaking_exception_text(
    client, monkeypatch, error, status_code, detail
):
    _configure_api(monkeypatch)

    class FailingWorkflow:
        def run(self, *args, **kwargs):
            raise error

    monkeypatch.setattr(main, "DeepDiveWorkflow", FailingWorkflow)

    response = client.post(
        "/research",
        headers=auth_headers(),
        json={"question": "Explain the evidence"},
    )

    assert response.status_code == status_code
    assert response.json() == {"detail": detail}
    assert "secret" not in response.text


class FakeAuditStore:
    def get_run_progress(self, run_id):
        return {"run_id": run_id, "status": "running", "events": 2}

    def get_run(self, run_id):
        return {"run_id": run_id, "status": "completed", "report_markdown": "# Report"}


def test_protected_run_lookup_endpoints_return_audit_data(client, monkeypatch):
    _configure_api(monkeypatch)
    monkeypatch.setattr(main, "AuditStore", FakeAuditStore)

    status_response = client.get(f"/runs/{RUN_ID}/status", headers=auth_headers())
    run_response = client.get(f"/runs/{RUN_ID}", headers=auth_headers())

    assert status_response.status_code == 200
    assert status_response.json() == {"run_id": RUN_ID, "status": "running", "events": 2}
    assert run_response.status_code == 200
    assert run_response.json() == {"run_id": RUN_ID, "status": "completed", "report_markdown": "# Report"}


def test_protected_run_lookup_rejects_malformed_ids_and_missing_auth(client, monkeypatch):
    _configure_api(monkeypatch)
    monkeypatch.setattr(main, "AuditStore", FakeAuditStore)

    malformed = client.get("/runs/not-a-uuid", headers=auth_headers())
    missing_auth = client.get(f"/runs/{RUN_ID}")

    assert malformed.status_code == 404
    assert malformed.json() == {"detail": "Research run not found"}
    assert missing_auth.status_code == 401


@pytest.mark.parametrize(
    ("path", "detail"),
    [
        (f"/runs/{RUN_ID}/status", "Run status is temporarily unavailable"),
        (f"/runs/{RUN_ID}", "Run storage is temporarily unavailable"),
    ],
)
def test_protected_run_lookup_returns_503_when_audit_store_fails(
    client, monkeypatch, path, detail
):
    _configure_api(monkeypatch)

    class FailingAuditStore:
        def get_run_progress(self, run_id):
            raise OSError("secret audit failure")

        def get_run(self, run_id):
            raise OSError("secret audit failure")

    monkeypatch.setattr(main, "AuditStore", FailingAuditStore)

    response = client.get(path, headers=auth_headers())

    assert response.status_code == 503
    assert response.json() == {"detail": detail}
    assert "secret audit failure" not in response.text
