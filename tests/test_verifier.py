from app.agents.verifier import verify_claim
from app.schemas import ClaimRecord, SourceRecord


class FakeLLM:
    def __init__(self, response):
        self.response = response
    def complete_json(self, **kwargs):
        return self.response


def source():
    return SourceRecord(source_id="S-abc123", title="Example", url="https://example.org", content="Lithium prices fluctuated during the year, affecting battery input costs.")


def test_verifier_rejects_quote_not_in_source():
    claim = ClaimRecord(claim_id="C001", text="Lithium prices increased by 80%.", source_ids=["S-abc123"])
    result = verify_claim(claim, [source()], FakeLLM({"status": "supported", "reason": "Looks supported", "source_id": "S-abc123", "evidence_quote": "Lithium prices increased by 80%"}))
    assert result.status == "insufficient"
    assert "matches the retrieved source text" in result.reason


def test_verifier_accepts_exact_quote_when_model_supports_claim():
    claim = ClaimRecord(claim_id="C001", text="Lithium price changes can affect battery input costs.", source_ids=["S-abc123"])
    quote = "Lithium prices fluctuated during the year, affecting battery input costs."
    result = verify_claim(claim, [source()], FakeLLM({"status": "supported", "reason": "The passage directly supports this", "source_id": "S-abc123", "evidence_quote": quote}))
    assert result.status == "supported"
    assert result.evidence_quotes["S-abc123"] == quote


def test_missing_source_is_insufficient():
    claim = ClaimRecord(claim_id="C001", text="An unsupported claim", source_ids=["S-missing"])
    result = verify_claim(claim, [source()], FakeLLM({}))
    assert result.status == "insufficient"


def test_exact_quote_does_not_automatically_support_claim():
    claim = ClaimRecord(
        claim_id="C004",
        text="Lithium prices increased by 80%.",
        source_ids=["S-abc123"],
    )
    quote = (
        "Lithium prices fluctuated during the year, "
        "affecting battery input costs."
    )

    result = verify_claim(
        claim,
        [source()],
        FakeLLM({
            "status": "insufficient",
            "reason": "The source gives no 80% increase figure.",
            "source_id": "S-abc123",
            "evidence_quote": quote,
        }),
    )

    assert result.status == "insufficient"
    assert result.evidence_quotes == {}
    assert "80%" in result.reason
