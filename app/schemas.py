from typing import Literal
from pydantic import BaseModel, Field, HttpUrl, field_validator


class ResearchRequest(BaseModel):
    question: str = Field(min_length=8, max_length=2000, description="Question to investigate")
    max_research_rounds: int | None = Field(default=None, ge=0, le=4)

    @field_validator("question")
    @classmethod
    def question_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must not be blank")
        return value.strip()


class SourceRecord(BaseModel):
    source_id: str
    title: str
    url: str
    snippet: str = ""
    content: str = ""
    published_date: str | None = None
    quality_tier: Literal["primary", "reputable_secondary", "other", "unknown"] = "unknown"


class ClaimRecord(BaseModel):
    claim_id: str
    text: str
    source_ids: list[str] = Field(default_factory=list)
    evidence_quotes: dict[str, str] = Field(default_factory=dict)
    status: Literal["pending", "supported", "contradicted", "insufficient", "rejected"] = "pending"
    reason: str = ""


class ResearchResponse(BaseModel):
    run_id: str
    question: str
    status: str
    report_markdown: str
    subquestions: list[str]
    sources: list[SourceRecord]
    claims: list[ClaimRecord]
    audit_events: list[dict]
    metrics: dict[str, float | int]
