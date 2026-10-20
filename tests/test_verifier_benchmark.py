"""Offline checks for benchmark scoring and core verification edge cases."""
from scripts.evaluate_verifier import score
from app.agents.verifier import verify_claim
from app.schemas import ClaimRecord, SourceRecord


class FakeLLM:
    def __init__(self, responses):
        self.responses = iter(responses)

    def complete_json(self, **kwargs):
        return next(self.responses)


def make_source(source_id: str, content: str) -> SourceRecord:
    return SourceRecord(source_id=source_id, title="Test source", url=f"https://example.org/{source_id}", content=content)


def test_verifier_marks_conflicting_cited_sources_insufficient():
    sources = [
        make_source("S1", "The bridge opened to traffic in 2021."),
        make_source("S2", "The bridge opened to traffic in 2022."),
    ]
    claim = ClaimRecord(claim_id="C1", text="The bridge opened in 2021.", source_ids=["S1", "S2"])
    llm = FakeLLM([
        {"status": "supported", "reason": "Matches the opening year", "evidence_quote": "The bridge opened to traffic in 2021."},
        {"status": "contradicted", "reason": "Source states 2022", "evidence_quote": "The bridge opened to traffic in 2022."},
    ])
    result = verify_claim(claim, sources, llm)
    assert result.status == "insufficient"
    assert "Sources conflict" in result.reason


def test_unknown_model_status_fails_closed_to_insufficient():
    source = make_source("S1", "The library opened in 2020.")
    claim = ClaimRecord(claim_id="C1", text="The library opened in 2020.", source_ids=["S1"])
    result = verify_claim(claim, [source], FakeLLM([{"status": "probably", "reason": "unclear", "evidence_quote": "The library opened in 2020."}]))
    assert result.status == "insufficient"
    assert result.evidence_quotes == {}


def test_benchmark_scoring_uses_expected_labels_not_quote_presence():
    rows = [
        {"expected_status": "supported", "actual_status": "supported"},
        {"expected_status": "contradicted", "actual_status": "supported"},
        {"expected_status": "insufficient", "actual_status": "insufficient"},
    ]
    metrics = score(rows)
    assert metrics["cases"] == 3
    assert metrics["exact_status_accuracy"] == 0.6667
    assert metrics["supported_claim_precision"] == 0.5
    assert metrics["unsupported_claim_rejection_rate"] == 0.5
    assert metrics["contradiction_detection_rate"] == 0.0


def test_contradiction_without_matching_quote_is_not_accepted():
    source = make_source("S1", "The library opened in 2018.")
    claim = ClaimRecord(claim_id="C1", text="The library opened in 2020.", source_ids=["S1"])
    result = verify_claim(claim, [source], FakeLLM([{"status": "contradicted", "reason": "Different year", "evidence_quote": "The library opened in 2020."}]))
    assert result.status == "insufficient"
    assert result.evidence_quotes == {}
