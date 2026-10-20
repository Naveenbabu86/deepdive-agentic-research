from app.agents.synthesizer import synthesize
from app.schemas import ClaimRecord, SourceRecord


def test_report_only_lists_sources_with_matched_evidence_quotes():
    sources = [
        SourceRecord(source_id="S-one", title="One", url="https://example.com/1", content="A fact."),
        SourceRecord(source_id="S-two", title="Two", url="https://example.com/2", content="Another fact."),
    ]
    claims = [ClaimRecord(claim_id="C001", text="A fact is reported.", source_ids=["S-one", "S-two"], evidence_quotes={"S-one": "A fact."}, status="supported")]
    report = synthesize("What is known?", ["What facts are known?"], claims, sources)
    assert "[S-one]" in report
    assert "[S-two]" not in report


def test_report_does_not_emit_supported_claim_without_quote():
    sources = [SourceRecord(source_id="S-one", title="One", url="https://example.com/1", content="A fact.")]
    claims = [ClaimRecord(claim_id="C001", text="A fact is reported.", source_ids=["S-one"], evidence_quotes={}, status="supported")]
    report = synthesize("What is known?", [], claims, sources)
    verified = report.split("## Claims not included as verified facts")[0]
    assert "A fact is reported." not in verified
    assert "REJECTED_AT_REPORTING — C001" in report


def test_unsupported_claim_is_removed_from_verified_findings_and_explicitly_listed():
    sources = [SourceRecord(source_id="S-real", title="Real source", url="https://example.com", content="Lithium prices fluctuated during the year.")]
    claims = [
        ClaimRecord(claim_id="C001", text="Lithium prices fluctuated during the year.", source_ids=["S-real"], evidence_quotes={"S-real": "Lithium prices fluctuated during the year."}, status="supported"),
        ClaimRecord(claim_id="C002", text="Lithium prices increased by 80%.", source_ids=["S-real"], evidence_quotes={"S-real": "Lithium prices increased by 80%."}, status="insufficient", reason="No matching evidence quote."),
    ]
    report = synthesize("What happened to lithium prices?", [], claims, sources)
    verified = report.split("## Claims not included as verified facts")[0]
    rejected = report.split("## Claims not included as verified facts")[1]
    assert "Lithium prices fluctuated during the year." in verified
    assert "Lithium prices increased by 80%." not in verified
    assert "C002" in rejected and "INSUFFICIENT" in rejected
    assert "Evidence from [S-real]" in report


def test_synthesizer_rechecks_quote_against_the_exact_source():
    sources = [SourceRecord(source_id="S-real", title="Real source", url="https://example.com", content="The report says sales were stable.")]
    claims = [ClaimRecord(claim_id="C001", text="Sales doubled.", source_ids=["S-real"], evidence_quotes={"S-real": "Sales doubled."}, status="supported")]
    report = synthesize("Sales?", [], claims, sources)
    verified = report.split("## Claims not included as verified facts")[0]
    assert "Sales doubled." not in verified
    assert "REJECTED_AT_REPORTING — C001" in report
