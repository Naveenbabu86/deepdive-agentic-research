"""Deterministic, transparent run metrics. These are proxies, not a truth oracle."""
from app.agents.synthesizer import reportable_claims
from app.schemas import ClaimRecord, SourceRecord


def evaluate_run(claims: list[ClaimRecord], sources: list[SourceRecord]) -> dict[str, int | float]:
    total = len(claims)
    counts = {status: sum(c.status == status for c in claims) for status in ("supported", "contradicted", "insufficient", "rejected")}
    reportable = reportable_claims(claims, sources)
    reportable_ids = {claim.claim_id for claim, _ in reportable}
    rejected_from_report = total - len(reportable_ids)
    cited_pairs = sum(len(ids) for _, ids in reportable)
    possible_pairs = sum(len(c.source_ids) for c in claims if c.status == "supported")
    return {
        "claims_evaluated": total,
        "claims_supported_by_verifier": counts["supported"],
        "claims_contradicted": counts["contradicted"],
        "claims_insufficient": counts["insufficient"],
        "claims_explicitly_rejected": counts["rejected"],
        "claims_in_final_report": len(reportable),
        "claims_excluded_from_final_report": rejected_from_report,
        "verifier_support_rate": round(counts["supported"] / total, 4) if total else 0.0,
        # Citation coverage measures source-matched quotes for verifier-supported claims.
        # It is not a semantic groundedness/accuracy score.
        "matched_citation_coverage": round(cited_pairs / possible_pairs, 4) if possible_pairs else 0.0,
        "source_matched_claim_rate": round(len(reportable) / total, 4) if total else 0.0,
        "source_count": len(sources),
    }
