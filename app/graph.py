"""LangGraph orchestration with a bounded verifier-to-research loop."""
from typing import Any, TypedDict
from uuid import uuid4
from time import monotonic
from langgraph.graph import StateGraph, END
from app.config import get_settings
from app.schemas import ClaimRecord, SourceRecord
from app.services.llm import MeshLLM
from app.services.search import WebResearchService
from app.services.audit import AuditStore
from app.agents.planner import plan
from app.agents.researcher import build_queries, collect_evidence
from app.agents.drafter import draft_claims
from app.agents.verifier import verify_claim
from app.agents.synthesizer import synthesize, reportable_claims
from app.services.evaluation import evaluate_run
from app.services.qdrant_evidence import QdrantEvidenceStore


class ResearchState(TypedDict, total=False):
    run_id: str
    question: str
    max_research_rounds: int
    research_round: int
    subquestions: list[str]
    sources: list[dict[str, Any]]
    claims: list[dict[str, Any]]
    gaps: list[str]
    search_events: list[dict[str, Any]]
    report_markdown: str
    status: str
    audit_events: list[dict[str, Any]]
    metrics: dict[str, int | float]
    historical_context: str


class DeepDiveWorkflow:
    def __init__(self, llm: MeshLLM | None = None, search: WebResearchService | None = None, audit: AuditStore | None = None):
        self.settings = get_settings()
        self.llm = llm or MeshLLM()
        self.search = search or WebResearchService()
        self.audit = audit or AuditStore()
        self.qdrant: QdrantEvidenceStore | None = None
        if QdrantEvidenceStore.enabled():
            try:
                self.qdrant = QdrantEvidenceStore()
            except Exception as exc:
                # Optional retrieval must not make the core research workflow unavailable.
                import logging
                logging.getLogger(__name__).warning("Qdrant unavailable; continuing without vector retrieval (%s)", type(exc).__name__)
        self.graph = self._build_graph()

    def _log(self, state: ResearchState, event_type: str, payload: dict[str, Any]):
        self.audit.log(state["run_id"], event_type, payload)

    def _build_graph(self):
        graph = StateGraph(ResearchState)

        def planner_node(state: ResearchState) -> dict:
            self._log(state, "agent_started", {"agent": "planner"})
            questions = plan(
                state["question"],
                self.llm,
                self.settings.max_subquestions,
                state.get("historical_context", ""),
            )
            self._log(state, "agent_completed", {"agent": "planner", "output_count": len(questions)})
            self._log(state, "plan_created", {"subquestions": questions})
            return {"subquestions": questions, "audit_events": state.get("audit_events", []) + [{"event_type": "plan_created", "payload": {"subquestions": questions}}]}

        def researcher_node(state: ResearchState) -> dict:
            self._log(state, "agent_started", {"agent": "researcher", "round": state.get("research_round", 0) + 1, "targeted_gaps": state.get("gaps", [])})
            old_sources = [SourceRecord.model_validate(s) for s in state.get("sources", [])]
            gaps = state.get("gaps", [])
            queries = build_queries(state["question"], state["subquestions"], gaps, self.llm)
            found, events = collect_evidence(queries, self.search)
            merged = {s.source_id: s for s in old_sources}
            merged.update({s.source_id: s for s in found})
            sources = list(merged.values())
            round_no = state.get("research_round", 0) + 1
            qdrant_event = {"enabled": self.qdrant is not None, "indexed_chunks": 0, "retrieved_source_count": 0}
            if self.qdrant is not None and sources:
                try:
                    qdrant_event["indexed_chunks"] = self.qdrant.index_sources(state["run_id"], sources)
                    ranked = self.qdrant.order_sources(
                        state["run_id"],
                        [state["question"], *state["subquestions"], *gaps],
                        sources,
                    )
                    qdrant_event["retrieved_source_count"] = min(len(ranked), self.settings.qdrant_top_k)
                    sources = ranked
                except Exception as exc:
                    # Preserve freshly searched evidence and verifier checks if the vector DB is down.
                    qdrant_event["error_type"] = type(exc).__name__
                    import logging
                    logging.getLogger(__name__).warning("Qdrant retrieval failed; using web evidence order (%s)", type(exc).__name__)
            for source in found:
                self.audit.save_source(state["run_id"], source.model_dump())
            self._log(state, "qdrant_retrieval", qdrant_event)
            self._log(state, "agent_completed", {"agent": "researcher", "output_count": len(found), "round": round_no})
            self._log(state, "research_round_completed", {"round": round_no, "queries": events, "new_sources": [s.source_id for s in found], "total_sources": len(sources)})
            return {"sources": [s.model_dump() for s in sources], "research_round": round_no, "search_events": state.get("search_events", []) + events, "audit_events": state.get("audit_events", []) + [{"event_type": "research_round_completed", "payload": {"round": round_no, "query_count": len(events), "new_source_count": len(found)}}]}

        def draft_node(state: ResearchState) -> dict:
            self._log(state, "agent_started", {"agent": "drafter", "source_count": len(state.get("sources", []))})
            sources = [SourceRecord.model_validate(s) for s in state.get("sources", [])]
            claims = draft_claims(state["question"], state["subquestions"], sources, self.llm)
            for claim in claims:
                self.audit.save_claim(state["run_id"], claim.model_dump())
            self._log(state, "agent_completed", {"agent": "drafter", "output_count": len(claims)})
            self._log(state, "draft_created", {"claim_count": len(claims), "claim_ids": [c.claim_id for c in claims]})
            return {"claims": [c.model_dump() for c in claims], "audit_events": state.get("audit_events", []) + [{"event_type": "draft_created", "payload": {"claim_count": len(claims)}}]}

        def verifier_node(state: ResearchState) -> dict:
            self._log(state, "agent_started", {"agent": "verifier", "claim_count": len(state.get("claims", []))})
            sources = [SourceRecord.model_validate(s) for s in state.get("sources", [])]
            claims = [ClaimRecord.model_validate(c) for c in state.get("claims", [])]
            verified = [verify_claim(c, sources, self.llm) for c in claims]
            for claim in verified:
                self.audit.save_claim(state["run_id"], claim.model_dump())
                self._log(state, "claim_verified", {"claim_id": claim.claim_id, "claim": claim.text, "status": claim.status, "source_ids": claim.source_ids, "evidence_quotes": claim.evidence_quotes, "reason": claim.reason})
            supported_text = " ".join(c.text.lower() for c in verified if c.status == "supported")
            # A sub-question is thin if no supported claim has any plausible lexical overlap with it.
            gaps = []
            for q in state["subquestions"]:
                terms = {w for w in q.lower().split() if len(w) > 4}
                if not terms or not any(any(t in c.text.lower() for t in terms) for c in verified if c.status == "supported"):
                    gaps.append(q)
            gaps.extend(c.text for c in verified if c.status in ("insufficient", "contradicted"))
            gaps = list(dict.fromkeys(gaps))[:10]
            statuses = {s: sum(1 for c in verified if c.status == s) for s in ("supported", "contradicted", "insufficient")}
            self._log(state, "agent_completed", {"agent": "verifier", "output_count": len(verified), "status_counts": statuses})
            self._log(state, "verification_round_completed", {"round": state.get("research_round", 0), "counts": statuses, "gaps": gaps})
            return {"claims": [c.model_dump() for c in verified], "gaps": gaps, "audit_events": state.get("audit_events", []) + [{"event_type": "verification_round_completed", "payload": {"counts": statuses, "gaps": gaps}}]}

        def route_after_verify(state: ResearchState) -> str:
            # Initial research + up to max_research_rounds additional attempts.
            if state.get("gaps") and state.get("research_round", 0) <= state.get("max_research_rounds", self.settings.max_research_rounds):
                self._log(state, "routing_decision", {"from": "verifier", "to": "researcher", "reason": "evidence gaps remain and research-round budget is available", "gap_count": len(state.get("gaps", []))})
                return "researcher"
            self._log(state, "routing_decision", {"from": "verifier", "to": "synthesizer", "reason": "no gaps remain or research-round budget is exhausted", "gap_count": len(state.get("gaps", []))})
            return "synthesizer"

        def synthesizer_node(state: ResearchState) -> dict:
            self._log(state, "agent_started", {"agent": "synthesizer", "claim_count": len(state.get("claims", []))})
            sources = [SourceRecord.model_validate(s) for s in state.get("sources", [])]
            claims = [ClaimRecord.model_validate(c) for c in state.get("claims", [])]
            report = synthesize(state["question"], state["subquestions"], claims, sources)
            metrics = evaluate_run(claims, sources)
            metrics.update({"research_rounds": state.get("research_round", 0), "subquestion_count": len(state.get("subquestions", [])), "subquestions_with_gaps": len(state.get("gaps", []))})
            reportable_ids = {claim.claim_id for claim, _ in reportable_claims(claims, sources)}
            excluded_ids = [claim.claim_id for claim in claims if claim.claim_id not in reportable_ids]
            self._log(state, "agent_completed", {"agent": "synthesizer", "output_count": len(reportable_ids)})
            self._log(state, "report_filter_applied", {"included_claim_ids": sorted(reportable_ids), "excluded_claim_ids": excluded_ids, "policy": "only verifier-supported claims with source-matched verbatim quotes are reportable"})
            self.audit.log(state["run_id"], "final_metrics", metrics)
            status = "completed" if metrics["claims_in_final_report"] else "insufficient_evidence"
            self.audit.finish_run(state["run_id"], status, report)
            return {"report_markdown": report, "status": status, "metrics": metrics, "audit_events": state.get("audit_events", []) + [{"event_type": "final_metrics", "payload": metrics}]}

        graph.add_node("planner", planner_node)
        graph.add_node("researcher", researcher_node)
        graph.add_node("drafter", draft_node)
        graph.add_node("verifier", verifier_node)
        graph.add_node("synthesizer", synthesizer_node)
        graph.set_entry_point("planner")
        graph.add_edge("planner", "researcher")
        graph.add_edge("researcher", "drafter")
        graph.add_edge("drafter", "verifier")
        graph.add_conditional_edges("verifier", route_after_verify, {"researcher": "researcher", "synthesizer": "synthesizer"})
        graph.add_edge("synthesizer", END)
        return graph.compile()

    def run(
        self,
        question: str,
        max_research_rounds: int | None = None,
        run_id: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        run_id = run_id or str(uuid4())
        started_monotonic = monotonic()
        self.audit.start_run(run_id, question)
        from app.services.redis_memory import RedisConversationMemory

        memory = RedisConversationMemory()
        initial: ResearchState = {"run_id": run_id, "question": question, "max_research_rounds": self.settings.max_research_rounds if max_research_rounds is None else max_research_rounds, "research_round": 0, "sources": [], "claims": [], "gaps": [], "search_events": [], "audit_events": [], "status": "running", "historical_context": memory.load_context(conversation_id)}
        try:
            result = self.graph.invoke(initial)
            metrics = dict(result.get("metrics", {}))
            metrics["latency_seconds"] = round(monotonic() - started_monotonic, 3)
            self.audit.log(run_id, "request_completed", {"latency_seconds": metrics["latency_seconds"]})
            result["metrics"] = metrics
            memory.remember_run(conversation_id, question, result.get("claims", []))
            return {**result, "audit_events": self.audit.get_events(run_id)}
        except Exception as exc:
            self.audit.log(run_id, "run_failed", {"error_type": type(exc).__name__, "message": "Run failed; inspect protected server logs for diagnostics."})
            self.audit.finish_run(run_id, "failed", "# Research failed\n\nDeepDive could not complete this run. Check the audit trail and application logs. No unverified report was emitted.")
            raise
