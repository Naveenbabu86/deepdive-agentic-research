from app.services.evaluation import evaluate_run
from app.schemas import ClaimRecord, SourceRecord


def test_metrics_are_source_matched_and_exclusions_are_counted():
    source = SourceRecord(source_id="S-1", title="Source", url="https://example.com", content="The sky appears blue.")
    claims = [
        ClaimRecord(claim_id="C1", text="The sky appears blue.", source_ids=["S-1"], evidence_quotes={"S-1": "The sky appears blue."}, status="supported"),
        ClaimRecord(claim_id="C2", text="The sky is green.", source_ids=["S-1"], evidence_quotes={}, status="insufficient"),
    ]
    metrics = evaluate_run(claims, [source])
    assert metrics["claims_in_final_report"] == 1
    assert metrics["claims_excluded_from_final_report"] == 1
    assert metrics["matched_citation_coverage"] == 1.0
    assert metrics["source_matched_claim_rate"] == 0.5


def test_metrics_are_zero_safe_when_no_claims():
    metrics = evaluate_run([], [])
    assert metrics["verifier_support_rate"] == 0.0
    assert metrics["matched_citation_coverage"] == 0.0
    assert metrics["claims_in_final_report"] == 0
