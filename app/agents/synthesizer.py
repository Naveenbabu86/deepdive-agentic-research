"""Fail-closed report composer with source-level quote revalidation."""
import re
from app.schemas import ClaimRecord, SourceRecord


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def valid_evidence_source_ids(claim: ClaimRecord, source_by_id: dict[str, SourceRecord]) -> list[str]:
    """Return only citations whose stored quote still matches that exact source passage."""
    valid = []
    for source_id in claim.source_ids:
        source = source_by_id.get(source_id)
        quote = claim.evidence_quotes.get(source_id, "")
        if source and quote and _normalize(quote) in _normalize(source.content):
            valid.append(source_id)
    return valid


def reportable_claims(claims: list[ClaimRecord], sources: list[SourceRecord]) -> list[tuple[ClaimRecord, list[str]]]:
    source_by_id = {source.source_id: source for source in sources}
    result = []
    for claim in claims:
        if claim.status != "supported":
            continue
        valid_ids = valid_evidence_source_ids(claim, source_by_id)
        if valid_ids:
            result.append((claim, valid_ids))
    return result


def synthesize(question: str, subquestions: list[str], claims: list[ClaimRecord], sources: list[SourceRecord], llm=None) -> str:
    """Final factual sentences are copied from verified claim records, never freshly generated."""
    source_by_id = {s.source_id: s for s in sources}
    reportable = reportable_claims(claims, sources)
    reportable_ids = {claim.claim_id for claim, _ in reportable}
    unsupported = [c for c in claims if c.claim_id not in reportable_ids]
    lines = ["# DeepDive Research Report", "", f"**Research question:** {question}", "", "## Verified findings"]
    if reportable:
        for claim, valid_ids in reportable:
            citations = " ".join(f"[{sid}]" for sid in valid_ids)
            lines.append(f"- {claim.text} {citations}")
            # Expose the exact matched evidence beside the finding so judges can inspect support.
            for sid in valid_ids:
                quote = claim.evidence_quotes[sid].replace("\n", " ").strip()
                lines.append(f"  - Evidence from [{sid}]: “{quote}”")
    else:
        lines.append("No claims passed the verification policy with a source-matched evidence quote. DeepDive cannot provide a sufficiently grounded answer from the retrieved evidence.")

    lines.extend(["", "## Research coverage"])
    supported_text = " ".join(claim.text.lower() for claim, _ in reportable)
    for question_part in subquestions:
        words = [w.lower().strip("?.,:;") for w in question_part.split() if len(w) > 4]
        covered = bool(words) and any(word in supported_text for word in words)
        lines.append(f"- **{question_part}** — {'some supporting evidence found' if covered else 'evidence gap remains; treat as unresolved'}")

    lines.extend(["", "## Claims not included as verified facts"])
    if unsupported:
        for claim in unsupported:
            if claim.status == "supported":
                reason = "Excluded: no source citation retained a verbatim evidence quote matching its retrieved passage."
                status = "REJECTED_AT_REPORTING"
            else:
                reason = claim.reason or "Verifier did not establish sufficient evidence."
                status = claim.status.upper()
            lines.append(f"- **{status} — {claim.claim_id}:** {claim.text} — {reason}")
    else:
        lines.append("No rejected or unresolved claims in the final verification set.")

    lines.extend(["", "## Sources"])
    used_ids = {sid for _, ids in reportable for sid in ids}
    if used_ids:
        for sid in sorted(used_ids):
            source = source_by_id[sid]
            lines.append(f"- [{sid}] [{source.title}]({source.url}) — source quality hint: `{source.quality_tier}`")
    else:
        lines.append("No sources qualified for inclusion in verified findings.")

    lines.extend(["", "## Method and limitations", "- Each finding is copied from a claim reviewed by the Verifier; the Synthesizer independently rechecks that each cited evidence quote appears in that exact source passage.", "- Claims that fail verification or lose their source-matched quote are excluded from verified findings and listed above.", "- Quote matching confirms textual provenance, not logical entailment or truth. The model's support decision can still be wrong; source quality and human review matter, especially for high-impact conclusions.", "- Research coverage is a lexical heuristic, not a comprehensive measure of research completeness."])
    return "\n".join(lines)
