from app.services.llm import MeshLLM

SYSTEM = """You are DeepDive's Planner Agent. Convert the user's research question into focused, non-overlapping, answerable sub-questions. Cover key dimensions, relevant dates, risks, evidence gaps, and opposing perspectives where appropriate. Do not answer the question. Return JSON: {\"subquestions\":[\"...\"]}."""


def plan(
    question: str,
    llm: MeshLLM,
    max_subquestions: int,
    historical_context: str = "",
) -> list[str]:
    user = f"Research question: {question}\nCreate at most {max_subquestions} sub-questions."
    if historical_context:
        user += (
            "\n\nHistorical context from earlier runs in this session follows. "
            "It may be outdated and is not evidence. Use it only to understand "
            "follow-up intent or avoid repetition; every factual claim must be "
            "supported by freshly retrieved sources.\n"
            f"{historical_context}"
        )
    data = llm.complete_json(system=SYSTEM, user=user)
    values = data.get("subquestions", [])
    if not isinstance(values, list):
        raise ValueError("Planner output must contain a subquestions list")
    cleaned = list(dict.fromkeys(str(x).strip() for x in values if str(x).strip()))[:max_subquestions]
    if not cleaned:
        raise ValueError("Planner produced no sub-questions")
    return cleaned
