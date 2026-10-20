import socket
from app.services.search import _is_public_http_url, quality_for_url


def test_rejects_local_and_private_targets():
    assert not _is_public_http_url("http://127.0.0.1/admin")
    assert not _is_public_http_url("http://localhost/")
    assert not _is_public_http_url("http://192.168.1.10/")
    assert not _is_public_http_url("file:///etc/passwd")
    assert not _is_public_http_url("http://user:pass@example.com/")


def test_rejects_hostname_resolving_to_private_ip(monkeypatch):
    def fake_getaddrinfo(*args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.8", 80))]
    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    assert not _is_public_http_url("https://example.com/article")


def test_quality_hint_uses_hostname_boundaries():
    assert quality_for_url("https://www.who.int/news") == "primary"
    assert quality_for_url("https://notwho.int.example.com/") == "other"
    assert quality_for_url("https://www.reuters.com/world") == "reputable_secondary"
