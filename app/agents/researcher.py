from app.schemas import SourceRecord
from app.services.search import WebResearchService
from app.services.llm import MeshLLM

SYSTEM = """You are DeepDive's Researcher Agent. You organize research queries and summarize only what retrieved sources say. Never invent source URLs or facts. Prefer primary/official sources and independent corroboration. Return JSON {\"queries\":[\"...\"]}."""


def build_queries(question: str, subquestions: list[str], gaps: list[str], llm: MeshLLM) -> list[str]:
    user = f"Original question: {question}\nSub-questions: {subquestions}\nEvidence gaps requiring targeted research (if any): {gaps}\nReturn 1-5 precise web search queries, prioritizing gaps."
    data = llm.complete_json(system=SYSTEM, user=user)
    queries = data.get("queries", [])
    if not isinstance(queries, list):
        raise ValueError("Researcher output must contain a queries list")
    result = list(dict.fromkeys(str(q).strip() for q in queries if str(q).strip()))[:5]
    return result or subquestions[:3]


def collect_evidence(queries: list[str], search: WebResearchService) -> tuple[list[SourceRecord], list[dict]]:
    sources: dict[str, SourceRecord] = {}
    events: list[dict] = []
    for query in queries:
        try:
            found = search.search(query)
            events.append({"query": query, "result_count": len(found), "source_ids": [s.source_id for s in found]})
            for source in found:
                sources[source.source_id] = source
        except Exception as exc:
            # Keep an audit record and let orchestration decide whether the run can continue.
            events.append({"query": query, "result_count": 0, "error": type(exc).__name__})
    return list(sources.values()), events
