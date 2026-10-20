
from types import SimpleNamespace

from app import graph as graph_module
from app.graph import DeepDiveWorkflow


class FakeAudit:
    def __init__(self):
        self.events = []

    def log(self, run_id, event_type, payload):
        self.events.append((event_type, payload))

    def save_source(self, run_id, source):
        pass

    def save_claim(self, run_id, claim):
        pass

    def finish_run(self, run_id, status, report):
        pass


def make_workflow(monkeypatch, resolve_beta_after=None, max_research_rounds=1):
    audit = FakeAudit()

    # Bypass __init__: it would construct real external-service clients.
    workflow = DeepDiveWorkflow.__new__(DeepDiveWorkflow)
    workflow.settings = SimpleNamespace(
        max_subquestions=3,
        max_research_rounds=max_research_rounds,
        qdrant_top_k=5,
    )
    workflow.llm = object()
    workflow.search = object()
    workflow.audit = audit
    workflow.qdrant = None

    research_calls = {"count": 0}

    def fake_plan(*args, **kwargs):
        return ["alpha evidence", "beta evidence"]

    def fake_build_queries(*args, **kwargs):
        return ["test query"]

    def fake_collect_evidence(*args, **kwargs):
        research_calls["count"] += 1
        return [], []

    def fake_draft_claims(*args, **kwargs):
        from app.schemas import ClaimRecord

        return [
            ClaimRecord(claim_id="C-alpha", text="alpha evidence"),
            ClaimRecord(claim_id="C-beta", text="beta evidence"),
        ]

    verify_calls = {"count": 0}
    def fake_verify_claim(claim, sources, llm):
        verify_calls["count"] += 1
        research_round = (verify_calls["count"] - 1) // 2 + 1
        claim.status = "supported" if claim.claim_id == "C-alpha" or (
            resolve_beta_after is not None and research_round >= resolve_beta_after
        ) else "insufficient"
        return claim

    def fake_synthesize(*args, **kwargs):
        return "# Test report"

    def fake_evaluate_run(*args, **kwargs):
        return {"claims_in_final_report": 0}

    monkeypatch.setattr(graph_module, "plan", fake_plan)
    monkeypatch.setattr(graph_module, "build_queries", fake_build_queries)
    monkeypatch.setattr(graph_module, "collect_evidence", fake_collect_evidence)
    monkeypatch.setattr(graph_module, "draft_claims", fake_draft_claims)
    monkeypatch.setattr(graph_module, "verify_claim", fake_verify_claim)
    monkeypatch.setattr(graph_module, "synthesize", fake_synthesize)
    monkeypatch.setattr(
        graph_module, "evaluate_run", fake_evaluate_run
    )
    monkeypatch.setattr(
        graph_module, "reportable_claims", lambda *args: []
    )

    # Simulate the verifier finding gaps on selected research rounds.
    # With no claims, the real verifier naturally marks the subquestion
    # as a gap. This wrapper changes only the verifier's returned gaps.
    original_build_graph = workflow._build_graph

    # Keep the real graph and real routing; use the real verifier logic.
    workflow.graph = original_build_graph()

    return workflow, audit, research_calls


def test_graph_synthesizes_when_no_gaps(monkeypatch):
    workflow, audit, calls = make_workflow(monkeypatch, resolve_beta_after=1)
    result = workflow.graph.invoke(
        {
            "run_id": "test-run",
            "question": "Explain a test research question",
            "max_research_rounds": 0,
            "research_round": 0,
            "sources": [],
            "claims": [],
            "gaps": [],
            "audit_events": [],
        }
    )

    assert result["status"] == "insufficient_evidence"
    assert calls["count"] == 1
    assert any(
        event == "routing_decision" and payload["to"] == "synthesizer"
        for event, payload in audit.events
    )


def test_graph_stops_after_initial_round_when_budget_is_zero(monkeypatch):
    workflow, audit, calls = make_workflow(monkeypatch, resolve_beta_after=2)
    result = workflow.graph.invoke(
        {
            "run_id": "test-run",
            "question": "Explain a test research question",
            "max_research_rounds": 0,
            "research_round": 0,
            "sources": [],
            "claims": [],
            "gaps": [],
            "audit_events": [],
        }
    )

    assert result["metrics"]["research_rounds"] == 1
    assert calls["count"] == 1


def test_graph_runs_one_additional_round_when_a_gap_remains(monkeypatch):
    workflow, audit, calls = make_workflow(monkeypatch, resolve_beta_after=2, max_research_rounds=1)
    result = workflow.graph.invoke({
        "run_id": "test-run", "question": "Explain a test research question",
        "max_research_rounds": 1, "research_round": 0, "sources": [],
        "claims": [], "gaps": [], "audit_events": [],
    })

    assert calls["count"] == 2
    assert result["metrics"]["research_rounds"] == 2
    assert result["metrics"]["subquestions_with_gaps"] == 0


def test_graph_runs_two_additional_rounds_when_gap_resolves_on_third_round(monkeypatch):
    workflow, audit, calls = make_workflow(monkeypatch, resolve_beta_after=3, max_research_rounds=2)
    result = workflow.graph.invoke({
        "run_id": "test-run", "question": "Explain a test research question",
        "max_research_rounds": 2, "research_round": 0, "sources": [],
        "claims": [], "gaps": [], "audit_events": [],
    })

    assert calls["count"] == 3
    assert result["metrics"]["research_rounds"] == 3
    assert result["metrics"]["subquestions_with_gaps"] == 0


def test_graph_stops_at_retry_limit_when_gap_never_resolves(monkeypatch):
    workflow, audit, calls = make_workflow(monkeypatch, resolve_beta_after=None, max_research_rounds=2)
    result = workflow.graph.invoke({
        "run_id": "test-run", "question": "Explain a test research question",
        "max_research_rounds": 2, "research_round": 0, "sources": [],
        "claims": [], "gaps": [], "audit_events": [],
    })

    assert calls["count"] == 3
    assert result["metrics"]["research_rounds"] == 3
    assert result["metrics"]["subquestions_with_gaps"] == 1
    assert any(
        event == "routing_decision"
        and payload["to"] == "synthesizer"
        and "budget" in payload["reason"]
        for event, payload in audit.events
    )
