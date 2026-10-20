"""Web search and guarded page extraction. Retrieved web content is untrusted evidence."""
import hashlib
import ipaddress
import re
import socket
from urllib.parse import urlparse
import httpx
from bs4 import BeautifulSoup
from app.config import get_settings
from app.schemas import SourceRecord

PRIMARY_HINTS = (".gov", ".gov.uk", ".europa.eu", "who.int", "worldbank.org", "oecd.org", "sec.gov", "europa.eu")
REPUTABLE_HINTS = ("reuters.com", "apnews.com", "bbc.com", "ft.com", "nature.com", "science.org")


def quality_for_url(url: str) -> str:
    host = urlparse(url).hostname or ""
    host = host.lower().removeprefix("www.")
    if any(host == h.lstrip(".") or host.endswith(h if h.startswith(".") else "." + h) for h in PRIMARY_HINTS):
        return "primary"
    if any(host == h or host.endswith("." + h) for h in REPUTABLE_HINTS):
        return "reputable_secondary"
    return "other" if host else "unknown"


def _is_public_http_url(url: str) -> bool:
    """Reject local/private/link-local/reserved targets before making outbound requests.

    Redirects are deliberately not followed, preventing redirect-based SSRF. DNS is checked
    before connection; high-risk deployments should additionally use an egress firewall/proxy.
    """
    try:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return False
        if parsed.username is not None or parsed.password is not None:
            return False
        host = parsed.hostname.rstrip(".").lower()
        if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost") or host.endswith(".local"):
            return False
        try:
            addresses = [ipaddress.ip_address(host)]
        except ValueError:
            addresses = [ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)]
        return bool(addresses) and all(ip.is_global for ip in addresses)
    except (ValueError, OSError, socket.gaierror):
        return False


class WebResearchService:
    def __init__(self, api_key: str | None = None, timeout: float | None = None):
        settings = get_settings()
        self.api_key = api_key if api_key is not None else settings.tavily_api_key
        self.timeout = timeout or settings.request_timeout_seconds
        self.max_results = settings.max_search_results_per_query

    def search(self, query: str, limit: int | None = None) -> list[SourceRecord]:
        if not self.api_key or self.api_key == "tvly-replace_me":
            raise RuntimeError("Set TAVILY_API_KEY in .env to enable real web research.")
        response = httpx.post(
            "https://api.tavily.com/search",
            json={"api_key": self.api_key, "query": query, "search_depth": "advanced", "max_results": max(1, min(limit or self.max_results, self.max_results)), "include_answer": False, "include_raw_content": True},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        records: list[SourceRecord] = []
        for item in payload.get("results", []):
            url = str(item.get("url", "")).strip()
            if not _is_public_http_url(url):
                continue
            title = (item.get("title") or url or "Untitled source").strip()[:500]
            snippet = (item.get("content") or "").strip()
            raw = (item.get("raw_content") or "").strip()
            content = raw or self._fetch_page(url) or snippet
            content = re.sub(r"\s+", " ", content)[:12000]
            if not content:
                continue
            source_id = "S-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
            records.append(SourceRecord(source_id=source_id, title=title, url=url, snippet=snippet[:1500], content=content, quality_tier=quality_for_url(url)))
        return records

    def _fetch_page(self, url: str) -> str:
        if not _is_public_http_url(url):
            return ""
        try:
            # No redirects: each redirect target would otherwise need a fresh SSRF check.
            with httpx.Client(timeout=min(self.timeout, 12), follow_redirects=False, headers={"User-Agent": "DeepDiveResearchBot/0.2"}) as client:
                response = client.get(url, headers={"Range": "bytes=0-1999999"})
            if response.status_code in {301, 302, 303, 307, 308} or response.status_code >= 400:
                return ""
            content_type = response.headers.get("content-type", "").lower()
            if content_type and not any(kind in content_type for kind in ("text/html", "text/plain", "application/xhtml+xml")):
                return ""
            body = response.content[:2_000_000]
            text = body.decode(response.encoding or "utf-8", errors="replace")
            soup = BeautifulSoup(text, "html.parser")
            for node in soup(["script", "style", "nav", "footer", "header", "noscript", "form", "iframe"]):
                node.decompose()
            return " ".join(soup.get_text(" ").split())[:12000]
        except (httpx.HTTPError, ValueError, UnicodeError):
            return ""
