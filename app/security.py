"""API security helpers shared by the FastAPI app and unit tests."""
import logging
from hmac import compare_digest
from fastapi import HTTPException, Request
from app.config import get_settings

logger = logging.getLogger("deepdive.api")

def require_api_key(request: Request) -> None:
    """Require a shared API key in staging/production; compare secrets in constant time."""
    cfg = get_settings()
    if not cfg.auth_required:
        return
    if not cfg.deepdive_api_key or len(cfg.deepdive_api_key) < 32:
        logger.error("API auth is required but DEEPDIVE_API_KEY is missing or too short")
        raise HTTPException(status_code=503, detail="API authentication is not configured")
    supplied = request.headers.get("x-api-key", "")
    authorization = request.headers.get("authorization", "")
    if not supplied and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    if not supplied or not compare_digest(supplied, cfg.deepdive_api_key):
        raise HTTPException(status_code=401, detail="Valid API key required", headers={"WWW-Authenticate": "Bearer"})
