"""Optional Redis conversation memory for follow-up research questions.

Memory is planner context only. It never supplies citations or evidence to a run.
"""

import json
import logging
from typing import Any

from app.config import get_settings
from app.services.redis_cache import get_redis_client

logger = logging.getLogger(__name__)
_MAX_CONTEXT_CHARS = 5000
_MAX_FINDINGS_PER_RUN = 5


class RedisConversationMemory:
    def _key(self, conversation_id: str) -> str:
        return f"deepdive:memory:v1:{conversation_id}"

    def load_context(self, conversation_id: str | None) -> str:
        if not conversation_id:
            return ""
        client = get_redis_client()
        if client is None:
            return ""
        try:
            raw_records = client.lrange(
                self._key(conversation_id),
                -get_settings().redis_memory_max_runs,
                -1,
            )
            records = [json.loads(record) for record in raw_records]
        except Exception as exc:
            logger.warning(
                "Redis memory read failed; starting without history (%s)",
                type(exc).__name__,
            )
            return ""

        sections = []
        for record in records:
            question = str(record.get("question", ""))[:500]
            findings = record.get("verified_findings", [])
            section = [f"Prior question: {question}"]
            if findings:
                section.append("Previously supported findings (historical context only):")
                section.extend(
                    f"- {str(finding)[:400]}"
                    for finding in findings[:_MAX_FINDINGS_PER_RUN]
                )
            sections.append("\n".join(section))
        return "\n\n".join(sections)[-_MAX_CONTEXT_CHARS:]

    def remember_run(
        self,
        conversation_id: str | None,
        question: str,
        claims: list[dict[str, Any]],
    ) -> None:
        if not conversation_id:
            return
        client = get_redis_client()
        if client is None:
            return

        settings = get_settings()
        record = {
            "question": question[:500],
            "verified_findings": [
                str(claim.get("text", ""))[:400]
                for claim in claims
                if claim.get("status") == "supported" and claim.get("text")
            ][:_MAX_FINDINGS_PER_RUN],
        }
        try:
            key = self._key(conversation_id)
            client.rpush(key, json.dumps(record, ensure_ascii=False))
            client.ltrim(key, -settings.redis_memory_max_runs, -1)
            client.expire(key, settings.redis_memory_ttl_seconds)
        except Exception as exc:
            logger.warning(
                "Redis memory write failed; run remains available (%s)",
                type(exc).__name__,
            )
