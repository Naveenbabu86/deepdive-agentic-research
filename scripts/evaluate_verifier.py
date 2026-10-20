"""Run a small, labeled claim-verification benchmark through the configured Mesh API.

Usage: python -m scripts.evaluate_verifier --output benchmark-results.json
This makes live model calls and may incur API cost. Results are diagnostic only.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.agents.verifier import verify_claim
from app.schemas import ClaimRecord, SourceRecord
from app.services.llm import MeshLLM

VALID_LABELS = ("supported", "contradicted", "insufficient")


def score(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    correct = sum(row["actual_status"] == row["expected_status"] for row in rows)
    accepted = [row for row in rows if row["actual_status"] == "supported"]
    accepted_correct = sum(row["expected_status"] == "supported" for row in accepted)
    known_unsupported = [row for row in rows if row["expected_status"] != "supported"]
    unsupported_rejected = sum(row["actual_status"] != "supported" for row in known_unsupported)
    known_contradictions = [row for row in rows if row["expected_status"] == "contradicted"]
    contradictions_found = sum(row["actual_status"] == "contradicted" for row in known_contradictions)
    return {
        "cases": total,
        "exact_status_accuracy": round(correct / total, 4) if total else 0.0,
        "supported_claim_precision": round(accepted_correct / len(accepted), 4) if accepted else None,
        "unsupported_claim_rejection_rate": round(unsupported_rejected / len(known_unsupported), 4) if known_unsupported else None,
        "contradiction_detection_rate": round(contradictions_found / len(known_contradictions), 4) if known_contradictions else None,
        "supported_claims_accepted": len(accepted),
        "note": "Small synthetic diagnostic set; scores are not independent validation or a guarantee of real-world accuracy."
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default="benchmarks/verification_cases.json", help="Path to labeled benchmark JSON")
    parser.add_argument("--output", default="benchmark-results.json", help="Where to write the result JSON")
    args = parser.parse_args()

    benchmark = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    cases = benchmark.get("cases", [])
    if not cases:
        raise SystemExit("Benchmark has no cases")
    for case in cases:
        if case.get("expected_status") not in VALID_LABELS:
            raise SystemExit(f"Invalid expected_status in case {case.get('id')}")
        if not case.get("sources"):
            raise SystemExit(f"No sources in case {case.get('id')}")

    llm = MeshLLM()
    rows: list[dict[str, Any]] = []
    for case in cases:
        sources = [SourceRecord.model_validate(source) for source in case["sources"]]
        claim = ClaimRecord(
            claim_id=case["id"],
            text=case["claim"],
            source_ids=[source.source_id for source in sources],
        )
        result = verify_claim(claim, sources, llm)
        row = {
            "case_id": case["id"],
            "claim": case["claim"],
            "expected_status": case["expected_status"],
            "actual_status": result.status,
            "correct": result.status == case["expected_status"],
            "reason": result.reason,
            "evidence_quote_source_ids": sorted(result.evidence_quotes.keys()),
        }
        rows.append(row)
        print(f"{'PASS' if row['correct'] else 'FAIL'} {case['id']}: expected={row['expected_status']} actual={row['actual_status']}")

    report = {
        "benchmark_name": benchmark.get("benchmark_name", "unnamed"),
        "benchmark_version": benchmark.get("version", "unknown"),
        "run_at_utc": datetime.now(timezone.utc).isoformat(),
        "model": llm.model,
        "cases_file": args.cases,
        "summary": score(rows),
        "results": rows,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))
    print(f"Detailed results written to: {output}")
    return 0 if all(row["correct"] for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
