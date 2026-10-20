"""DeepDive HTTP API with API-key protection and bounded resource use."""
from collections import defaultdict, deque
from threading import BoundedSemaphore, Lock
from time import monotonic
import logging
import os

from fastapi import FastAPI, HTTPException, Request, Depends, Header
from fastapi.responses import JSONResponse
from uuid import UUID
from app.config import get_settings
from app.schemas import ResearchRequest, ResearchResponse
from app.services.audit import AuditStore
from app.services.llm import MeshConfigurationError
from app.security import require_api_key

logger = logging.getLogger("deepdive.api")
settings = get_settings()
# LangSmith is optional. Keep it disabled unless the process explicitly opts in;
# this avoids unauthenticated tracing calls interfering with local runs.
os.environ.setdefault("LANGSMITH_TRACING", "false")
os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")
from app.graph import DeepDiveWorkflow

app = FastAPI(title="DeepDive", version="0.2.0", description="A self-verifying multi-agent research analyst. All LLM calls route through Mesh API.", docs_url=None if settings.environment == "production" else "/docs", redoc_url=None if settings.environment == "production" else "/redoc", openapi_url=None if settings.environment == "production" else "/openapi.json")
_run_slots = BoundedSemaphore(settings.max_concurrent_research_runs)
_rate_lock = Lock()
_rate_events: dict[str, deque[float]] = defaultdict(deque)
_MAX_RATE_KEYS = 10000


def enforce_rate_limit(request: Request) -> None:
    """Process-local safety limit; use a shared gateway/Redis limiter for multi-worker production."""
    cfg = get_settings()
    host = request.client.host if request.client else "unknown"
    now = monotonic()
    with _rate_lock:
        if len(_rate_events) > _MAX_RATE_KEYS and host not in _rate_events:
            # Bound memory use under random source-IP traffic.
            oldest = min(_rate_events, key=lambda key: _rate_events[key][0] if _rate_events[key] else now)
            _rate_events.pop(oldest, None)
        events = _rate_events[host]
        cutoff = now - cfg.rate_limit_window_seconds
        while events and events[0] <= cutoff:
            events.popleft()
        if len(events) >= cfg.rate_limit_requests:
            raise HTTPException(status_code=429, detail="Rate limit exceeded; retry after the current window", headers={"Retry-After": str(cfg.rate_limit_window_seconds)})
        events.append(now)


@app.get("/health")
def health():
    # Liveness only; does not claim upstream providers or the database are healthy.
    return {"status": "ok", "service": "deepdive"}


@app.get("/ready")
def readiness():
    """Readiness check verifies configuration without making billable upstream calls."""
    cfg = get_settings()
    if not cfg.mesh_api_key or not cfg.tavily_api_key:
        raise HTTPException(status_code=503, detail="Required upstream credentials are not configured")
    try:
        AuditStore()
    except Exception as exc:
        logger.exception("Readiness check failed")
        raise HTTPException(status_code=503, detail="Persistence is not ready") from exc
    return {"status": "ready"}


@app.post("/research", response_model=ResearchResponse, dependencies=[Depends(require_api_key), Depends(enforce_rate_limit)])
def research(
    request: ResearchRequest,
    run_id: UUID | None = Header(default=None, alias="X-Run-ID"),
    conversation_id: UUID | None = Header(default=None, alias="X-Conversation-ID"),
):
    if not _run_slots.acquire(blocking=False):
        raise HTTPException(status_code=503, detail="Research capacity is busy; retry shortly", headers={"Retry-After": "5"})
    try:
        result = DeepDiveWorkflow().run(
            request.question,
            request.max_research_rounds,
            run_id=str(run_id) if run_id else None,
            conversation_id=str(conversation_id) if conversation_id else None,
        )
        return ResearchResponse(
            run_id=result["run_id"], question=result["question"], status=result.get("status", "unknown"),
            report_markdown=result.get("report_markdown", ""), subquestions=result.get("subquestions", []),
            sources=result.get("sources", []), claims=result.get("claims", []),
            audit_events=result.get("audit_events", []), metrics=result.get("metrics", {}),
        )
    except MeshConfigurationError as exc:
        logger.warning("Research unavailable: Mesh configuration error")
        raise HTTPException(status_code=503, detail="Mesh API is not configured; check server configuration") from exc
    except RuntimeError as exc:
        # Avoid leaking provider response bodies, request URLs, or credentials to clients.
        logger.warning("Research upstream failure: %s", type(exc).__name__)
        raise HTTPException(status_code=502, detail="An upstream research service failed; consult the run audit trail and server logs") from exc
    except Exception as exc:
        # Provider-side status failures are upstream errors, not application bugs.
        # Keep provider response bodies out of the public HTTP response.
        try:
            from openai import APIStatusError
        except ImportError:
            APIStatusError = ()
        if APIStatusError and isinstance(exc, APIStatusError):
            provider_status = getattr(exc, "status_code", None)
            body = getattr(exc, "body", None)
            error_body = body.get("error", {}) if isinstance(body, dict) else {}
            if not isinstance(error_body, dict):
                error_body = {}
            provider_code = getattr(exc, "code", None) or error_body.get("code")
            provider_type = error_body.get("type")
            provider_param = error_body.get("param")
            provider_message = error_body.get("message")
            if not isinstance(provider_message, str):
                provider_message = ""
            # Provider messages are useful for diagnosing rejected request fields,
            # but keep them bounded and only in server logs (never return raw bodies).
            provider_message = provider_message.replace("\n", " ")[:300]
            logger.warning(
                "LLM provider rejected a research request "
                "(status=%s code=%s type=%s param=%s message=%s)",
                provider_status,
                provider_code,
                provider_type,
                provider_param,
                provider_message or "<empty>",
            )
            if provider_status in (413, 429):
                detail = "LLM provider rate/token limit reached. Reduce evidence size or wait for the provider rate window to reset."
            else:
                detail = "LLM provider rejected the request. Check the server log for its error details."
            raise HTTPException(status_code=502, detail=detail) from exc
        logger.exception("Research run failed")
        raise HTTPException(status_code=500, detail="Research run failed. Check server logs and audit trail.") from exc
    finally:
        _run_slots.release()


def _normalize_run_id(run_id: str) -> str:
    # UUID shape limits arbitrary path input and avoids accidentally accepting huge strings.
    try:
        return str(UUID(run_id))
    except (ValueError, AttributeError):
        raise HTTPException(status_code=404, detail="Research run not found")


@app.get("/runs/{run_id}/status", dependencies=[Depends(require_api_key), Depends(enforce_rate_limit)])
def get_run_status(run_id: str):
    normalized_run_id = _normalize_run_id(run_id)
    try:
        progress = AuditStore().get_run_progress(normalized_run_id)
    except Exception as exc:
        logger.exception("Run status lookup failed")
        raise HTTPException(status_code=503, detail="Run status is temporarily unavailable") from exc
    if not progress:
        raise HTTPException(status_code=404, detail="Research run not found")
    return JSONResponse(progress)


@app.get("/runs/{run_id}", dependencies=[Depends(require_api_key), Depends(enforce_rate_limit)])
def get_run(run_id: str):
    normalized_run_id = _normalize_run_id(run_id)
    try:
        run = AuditStore().get_run(normalized_run_id)
    except Exception as exc:
        logger.exception("Run lookup failed")
        raise HTTPException(status_code=503, detail="Run storage is temporarily unavailable") from exc
    if not run:
        raise HTTPException(status_code=404, detail="Research run not found")
    return JSONResponse(run)
