"""Optional Redis cache for deterministic LLM completions."""

import hashlib
import json
import logging
from functools import lru_cache
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_redis_client():
    settings = get_settings()
    if not settings.redis_url:
        return None

    try:
        import redis
    except ImportError:
        logger.warning("Redis caching is configured but redis-py is not installed")
        return None

    try:
        return redis.Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
    except Exception as exc:
        logger.warning("Redis URL is invalid; cache and memory are disabled (%s)", type(exc).__name__)
        return None


def cache_key_for_request(base_url: str, request_args: dict[str, Any]) -> str:
    """Build a prompt-safe key; the raw prompt and credentials are never key text."""
    canonical_request = json.dumps(
        {"base_url": base_url, "request": request_args},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    digest = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
    return f"deepdive:llm:v1:{digest}"


def get_cached_completion(key: str) -> str | None:
    client = get_redis_client()
    if client is None:
        return None
    try:
        return client.get(key)
    except Exception as exc:  # Redis is an optimization, not a workflow dependency.
        logger.warning("Redis cache read failed; calling the model (%s)", type(exc).__name__)
        return None


def store_cached_completion(key: str, value: str, ttl_seconds: int) -> None:
    client = get_redis_client()
    if client is None:
        return
    try:
        client.set(key, value, ex=ttl_seconds)
    except Exception as exc:  # Cache write failures must not fail a research run.
        logger.warning("Redis cache write failed (%s)", type(exc).__name__)
