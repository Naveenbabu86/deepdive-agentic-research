from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from app.security import require_api_key


class RequestStub:
    def __init__(self, headers):
        self.headers = headers


def test_production_requires_strong_configured_key(monkeypatch):
    import app.security as main
    monkeypatch.setattr(main, "get_settings", lambda: SimpleNamespace(auth_required=True, deepdive_api_key=""))
    with pytest.raises(HTTPException) as exc:
        require_api_key(RequestStub({}))
    assert exc.value.status_code == 503


def test_production_rejects_missing_or_invalid_key(monkeypatch):
    import app.security as main
    monkeypatch.setattr(main, "get_settings", lambda: SimpleNamespace(auth_required=True, deepdive_api_key="a" * 40))
    with pytest.raises(HTTPException) as missing:
        require_api_key(RequestStub({}))
    assert missing.value.status_code == 401
    with pytest.raises(HTTPException) as invalid:
        require_api_key(RequestStub({"x-api-key": "wrong"}))
    assert invalid.value.status_code == 401


def test_production_accepts_valid_key(monkeypatch):
    import app.security as main
    key = "x" * 40
    monkeypatch.setattr(main, "get_settings", lambda: SimpleNamespace(auth_required=True, deepdive_api_key=key))
    assert require_api_key(RequestStub({"x-api-key": key})) is None
