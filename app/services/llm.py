
"""Single LLM gateway for DeepDive.

All model calls go through this configured OpenAI-compatible client.
Supports JSON mode, bounded transient retries, and robust JSON parsing.
"""

import json
import random
import re
import time
from typing import Any

from app.config import get_settings
from app.services.redis_cache import (
    cache_key_for_request,
    get_cached_completion,
    store_cached_completion,
)


class MeshConfigurationError(RuntimeError):
    """Raised when required LLM configuration is missing."""


class LLMResponseError(RuntimeError):
    """Raised when the model returns an unusable response."""


def _extract_json_object(raw: str) -> dict[str, Any]:
    """Parse a JSON object, tolerating Markdown fences and surrounding text.

    This function never invents missing fields or repairs invalid JSON.
    """

    if not raw or not raw.strip():
        raise LLMResponseError("The model returned an empty response.")

    text = raw.strip()

    # Remove common Markdown code fences.
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, count=1, flags=re.IGNORECASE)
        text = re.sub(r"\s*```\s*$", "", text, count=1)

    # First try parsing the complete response.
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
        raise LLMResponseError("Expected a JSON object, not another JSON type.")
    except json.JSONDecodeError:
        pass

    # If explanatory text surrounds the JSON, extract the first balanced
    # object while respecting braces inside quoted strings.
    start = text.find("{")
    if start == -1:
        raise LLMResponseError(
            "The model response did not contain a JSON object."
        )

    depth = 0
    in_string = False
    escaped = False

    for index in range(start, len(text)):
        char = text[index]

        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1

            if depth == 0:
                candidate = text[start : index + 1]
                try:
                    parsed = json.loads(candidate)
                except json.JSONDecodeError as exc:
                    raise LLMResponseError(
                        "The model returned malformed JSON."
                    ) from exc

                if not isinstance(parsed, dict):
                    raise LLMResponseError(
                        "Expected a JSON object from the model."
                    )

                return parsed

    raise LLMResponseError(
        "The model response contained an incomplete or truncated JSON object."
    )


class MeshLLM:
    """LLM client configured through DeepDive application settings."""

    def __init__(self, client: Any | None = None):
        settings = get_settings()

        if client is None:
            if (
                not settings.mesh_api_key
                or settings.mesh_api_key == "rsk_replace_me"
            ):
                raise MeshConfigurationError(
                    "Set MESH_API_KEY in your local .env before running research."
                )

            from openai import OpenAI

            client = OpenAI(
                base_url=settings.mesh_base_url,
                api_key=settings.mesh_api_key,
                timeout=settings.request_timeout_seconds,
            )

        self.client = client
        self.model = settings.mesh_model

    def complete(
        self,
        *,
        system: str,
        user: str,
        temperature: float = 0.1,
        max_tokens: int | None = None,
        response_format: dict[str, str] | None = None,
    ) -> str:
        """Request a completion with bounded retries for transient errors."""

        from openai import (
            APIConnectionError,
            APITimeoutError,
            InternalServerError,
            RateLimitError,
        )

        transient_errors = (
            APIConnectionError,
            APITimeoutError,
            RateLimitError,
            InternalServerError,
        )

        request_args: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
        }

        if max_tokens is not None:
            request_args["max_tokens"] = max_tokens

        if response_format is not None:
            request_args["response_format"] = response_format

        settings = get_settings()
        cache_key = None
        # Cache only deterministic calls. Exact prompt/model/parameter matching
        # prevents a similar but different question from reusing prior output.
        if temperature == 0 and settings.redis_url:
            cache_key = cache_key_for_request(settings.mesh_base_url, request_args)
            cached = get_cached_completion(cache_key)
            if cached is not None:
                return cached

        response = None

        for attempt in range(3):
            try:
                response = self.client.chat.completions.create(**request_args)
                break
            except transient_errors:
                if attempt == 2:
                    raise

                delay = min(4.0, 0.5 * (2**attempt))
                time.sleep(delay + random.uniform(0, 0.2))

        if response is None:
            raise LLMResponseError("The LLM gateway returned no response.")

        choice = response.choices[0]
        content = choice.message.content

        if not content or not content.strip():
            raise LLMResponseError(
                f"The model returned empty content (finish_reason={choice.finish_reason!r})."
            )

        content = content.strip()
        if cache_key is not None:
            store_cached_completion(
                cache_key,
                content,
                settings.llm_cache_ttl_seconds,
            )
        return content

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int | None = 1200,
    ) -> dict[str, Any]:
        """Request and validate a JSON object.

        JSON mode is attempted first. If the gateway rejects JSON-mode
        validation or does not support response_format, retry once without it.
        """

        json_system = (
            system
            + "\nReturn exactly one valid JSON object. "
            "Do not include Markdown fences or explanatory text."
        )

        try:
            raw = self.complete(
                system=json_system,
                user=user,
                temperature=0,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
        except Exception as exc:
            # Only fall back when the error clearly concerns JSON output.
            # In particular, Mesh can reject a generated response with
            # json_validate_failed even though the request itself is valid.
            message = str(exc).lower()
            body = getattr(exc, "body", None)
            error_body = body.get("error", {}) if isinstance(body, dict) else {}
            if not isinstance(error_body, dict):
                error_body = {}
            error_code = (
                getattr(exc, "code", None)
                or error_body.get("code")
                or (body.get("code") if isinstance(body, dict) else None)
            )
            json_validation_failed = error_code == "json_validate_failed"
            unsupported_json_mode = (
                "response_format" in message
                or "json_object" in message
            ) and any(
                term in message
                for term in (
                    "unsupported",
                    "not supported",
                    "not allowed",
                    "unknown parameter",
                    "invalid parameter",
                    "unrecognized",
                )
            )

            if not (unsupported_json_mode or json_validation_failed):
                raise

            raw = self.complete(
                system=json_system,
                user=user,
                temperature=0,
                max_tokens=max_tokens,
            )

        return _extract_json_object(raw)
