"""DeepDive Streamlit interface."""

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import requests
import streamlit as st

from app.config import get_settings


st.set_page_config(page_title="DeepDive Research Analyst", page_icon="🔎", layout="wide")
settings = get_settings()
st.session_state.setdefault("deepdive_conversation_id", str(uuid4()))

st.markdown(
    """
    <style>
    .stApp { background: #0b1020; color: #e7edf7; }
    [data-testid="stAppViewContainer"] { background: #0b1020; }
    [data-testid="stHeader"] { background: rgba(11,16,32,.92); }
    [data-testid="stMainBlockContainer"] { max-width: 1120px; padding-top: 2rem; padding-bottom: 3rem; }
    .deepdive-hero {
        padding: 2rem 2.2rem; margin: .25rem 0 1.5rem; border-radius: 22px;
        color: #f4f7fb; background: linear-gradient(120deg, #111b2e 0%, #172b43 55%, #104c4c 100%);
        border: 1px solid #26394f; box-shadow: 0 18px 44px rgba(0, 0, 0, .28);
    }
    .deepdive-hero .eyebrow { color: #71ded2; font-size: .74rem; letter-spacing: .16em; font-weight: 750; }
    .deepdive-hero h1 { color: #f4f7fb; font-size: clamp(2.2rem, 4vw, 3.2rem); margin: .5rem 0 .4rem; }
    .deepdive-hero p { color: #bdcbd9; font-size: 1.02rem; margin: 0; max-width: 760px; }
    .section-kicker { color: #71ded2; font-size: .72rem; font-weight: 750; letter-spacing: .14em; text-transform: uppercase; }
    .run-chip { color: #71ded2; font-weight: 700; }
    div[data-testid="stMetric"] { background: #121d2f; border: 1px solid #26374c; border-radius: 15px; padding: .9rem 1rem; box-shadow: 0 6px 18px rgba(0,0,0,.16); }
    div[data-testid="stMetricLabel"] { color: #aab8c9; }
    div[data-testid="stMetricValue"] { color: #f1f5fa; }
    .stTextArea textarea, .stTextInput input { border-radius: 12px; border-color: #34445a; background: #111b2c; color: #edf3fa; }
    .stTextArea textarea:focus, .stTextInput input:focus { border-color: #53cbbd; box-shadow: 0 0 0 1px #53cbbd; }
    .stButton > button, .stDownloadButton > button { border-radius: 10px; font-weight: 650; min-height: 2.6rem; }
    .stButton > button[kind="primary"] { color: #071b1b; background: #66d7c9; border-color: #66d7c9; }
    .stButton > button[kind="primary"]:hover { color: #061414; background: #83e5d8; border-color: #83e5d8; }
    [data-baseweb="tab-list"] { gap: .4rem; border-bottom: 1px solid #26374c; }
    [data-baseweb="tab"] { color: #aab8c9; border-radius: 10px 10px 0 0; padding: .7rem 1rem; }
    [data-baseweb="tab"][aria-selected="true"] { color: #7be0d4; }
    div[data-testid="stExpander"] { border: 1px solid #26374c; border-radius: 12px; background: #121d2f; }
    div[data-testid="stAlert"] { border-radius: 12px; }
    hr { border-color: #26374c; }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="deepdive-hero">
      <div class="eyebrow">EVIDENCE-FIRST RESEARCH</div>
      <h1>DeepDive</h1>
      <p>Clear answers, traceable sources, and claim-by-claim verification. Explore what the evidence supports—and what it doesn’t.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown('<div class="section-kicker">NEW RESEARCH RUN</div>', unsafe_allow_html=True)
st.subheader("What would you like to understand?")
question = st.text_area(
    "Research question",
    placeholder="For example: What are the main trade-offs of vector databases for production RAG?",
    height=105,
    label_visibility="collapsed",
)
st.caption("Be specific about scope, time period, or comparison. DeepDive will gather fresh sources and verify each claim.")

with st.expander("Advanced settings", expanded=False):
    st.caption("Most local users can leave these at their defaults.")
    setting_cols = st.columns([2, 2, 1])
    api_url = setting_cols[0].text_input(
        "Backend URL",
        os.getenv("DEEPDIVE_API_URL", "http://127.0.0.1:8000"),
    ).rstrip("/")
    api_key = setting_cols[1].text_input(
        "Backend API key",
        os.getenv("DEEPDIVE_API_KEY", ""),
        type="password",
        help="Only needed for staging or production deployments.",
    )
    max_rounds = setting_cols[2].slider(
        "Extra retries",
        min_value=0,
        max_value=4,
        value=2,
        help="More retries can improve coverage, but add time and model usage.",
    )
    st.caption(
        "Redis cache and session memory are configured."
        if settings.redis_url
        else "Redis cache and session memory are off until REDIS_URL is set."
    )

if st.button("Start research", type="primary", use_container_width=True, disabled=len(question.strip()) < 8):
    st.session_state.pop("deepdive_result", None)
    headers = {
        "X-Run-ID": str(uuid4()),
        "X-Conversation-ID": st.session_state["deepdive_conversation_id"],
    }
    if api_key:
        headers["X-API-Key"] = api_key
    progress = None
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            response_future = executor.submit(
                requests.post,
                f"{api_url}/research",
                json={"question": question, "max_research_rounds": max_rounds},
                headers=headers,
                timeout=300,
            )
            with st.status("Starting your research…", expanded=True) as progress:
                last_message = None
                last_poll = 0.0
                while not response_future.done():
                    now = time.monotonic()
                    if now - last_poll >= 8:
                        last_poll = now
                        try:
                            status_response = requests.get(
                                f"{api_url}/runs/{headers['X-Run-ID']}/status",
                                headers=headers,
                                timeout=5,
                            )
                            if status_response.ok:
                                snapshot = status_response.json()
                                events = snapshot.get("audit_events", [])
                                if events:
                                    event = events[-1]
                                    payload = event.get("payload", {})
                                    event_type = event.get("event_type")
                                    agent = payload.get("agent", "").replace("_", " ").title()
                                    if event_type == "agent_started":
                                        message = f"Working: {agent}…"
                                    elif event_type == "agent_completed":
                                        message = f"Completed: {agent}. Continuing…"
                                    elif event_type == "qdrant_retrieval" and payload.get("error_type"):
                                        message = "Qdrant unavailable; continuing with fresh web evidence."
                                    elif event_type == "research_round_completed":
                                        message = (
                                            f"Research round {payload.get('round', '?')} complete · "
                                            f"{payload.get('total_sources', 0)} sources collected."
                                        )
                                    elif event_type == "claim_verified":
                                        message = f"Verifying claims… latest: {payload.get('status', 'pending')}"
                                    elif event_type == "routing_decision":
                                        message = f"Evidence review complete; moving to {payload.get('to', 'next stage')}."
                                    elif event_type == "run_finished":
                                        message = "Putting the final report together…"
                                    else:
                                        message = "Research is in progress…"
                                    if message != last_message:
                                        progress.write(message)
                                        last_message = message
                        except (requests.RequestException, ValueError):
                            pass
                    if not response_future.done():
                        time.sleep(0.25)

                response = response_future.result()
                response.raise_for_status()
                st.session_state["deepdive_result"] = response.json()
                progress.update(label="Research complete", state="complete", expanded=False)
    except requests.RequestException as exc:
        if progress is not None:
            progress.update(label="Research couldn’t be completed", state="error", expanded=True)
        st.error(f"Backend request failed: {exc}. Check the backend status and server logs for details.")

result = st.session_state.get("deepdive_result")
if result:
    claims = result.get("claims", [])
    sources = result.get("sources", [])
    events = result.get("audit_events", [])
    metrics = result.get("metrics", {})
    supported_count = sum(claim.get("status") == "supported" for claim in claims)
    qdrant_events = [event for event in events if event.get("event_type") == "qdrant_retrieval"]

    st.divider()
    st.markdown('<div class="section-kicker">YOUR RESEARCH BRIEF</div>', unsafe_allow_html=True)
    st.markdown(f"### {result.get('question', '')}")
    st.markdown(
        f"<span class='run-chip'>● {result.get('status', 'unknown').replace('_', ' ').title()}</span>"
        f" &nbsp; · &nbsp; Run `{result.get('run_id', 'unknown')}`",
        unsafe_allow_html=True,
    )

    if qdrant_events and not any(event.get("payload", {}).get("enabled") for event in qdrant_events):
        st.info("Qdrant is not configured for this run; DeepDive used fresh web evidence.")
    elif any(event.get("payload", {}).get("error_type") for event in qdrant_events):
        st.warning("Qdrant retrieval was unavailable for part of this run. DeepDive continued with fresh web evidence.")

    metric_cols = st.columns(4)
    metric_cols[0].metric("Verified findings", metrics.get("claims_in_final_report", supported_count))
    metric_cols[1].metric("Needs review", metrics.get("claims_excluded_from_final_report", max(0, len(claims) - supported_count)))
    metric_cols[2].metric("Sources", len(sources))
    metric_cols[3].metric(
        "Quote match coverage",
        f"{100 * metrics.get('matched_citation_coverage', 0):.0f}%",
        help="Measures whether quoted text appears in the cited passage; it does not prove the claim is true.",
    )

    report_tab, claims_tab, sources_tab, details_tab = st.tabs(
        ["Research report", "Claims & evidence", "Sources", "Run details"]
    )

    with report_tab:
        st.markdown(result.get("report_markdown", "No report was returned."))
        st.caption("Citation quote matching checks textual provenance, not logical entailment or truth.")

    with claims_tab:
        if not claims:
            st.info("No claims were drafted for this run.")
        else:
            claim_filter = st.selectbox(
                "Show claims",
                ["All claims", "Verified", "Needs review"],
                label_visibility="collapsed",
            )
            visible_claims = [
                claim for claim in claims
                if claim_filter == "All claims"
                or (claim_filter == "Verified" and claim.get("status") == "supported")
                or (claim_filter == "Needs review" and claim.get("status") != "supported")
            ]
            for claim in visible_claims:
                status = claim.get("status", "unknown")
                icon = "✅" if status == "supported" else "⚠️"
                with st.expander(f"{icon} {status.replace('_', ' ').title()} · {claim.get('claim_id', 'Claim')}"):
                    st.markdown(f"**{claim.get('text', '')}**")
                    if claim.get("reason"):
                        st.caption(claim["reason"])
                    source_ids = claim.get("source_ids", [])
                    st.caption("Sources: " + (", ".join(source_ids) if source_ids else "None"))
                    if claim.get("evidence_quotes"):
                        st.markdown("**Matched evidence**")
                        for source_id, quote in claim["evidence_quotes"].items():
                            st.markdown(f"> {quote}\n\n— `{source_id}`")

    with sources_tab:
        if not sources:
            st.info("No sources were retrieved.")
        else:
            quality_options = ["All quality levels"] + sorted(
                {source.get("quality_tier", "unknown") for source in sources}
            )
            quality_filter = st.selectbox("Filter by source hint", quality_options)
            visible_sources = [
                source for source in sources
                if quality_filter == "All quality levels"
                or source.get("quality_tier", "unknown") == quality_filter
            ]
            for source in visible_sources:
                title = source.get("title") or source.get("source_id", "Source")
                with st.expander(f"{source.get('quality_tier', 'unknown').replace('_', ' ').title()} · {title}"):
                    st.markdown(f"[{source.get('url', 'Open source')}]({source.get('url', '')})")
                    st.caption(source.get("source_id", ""))
                    st.write(source.get("snippet") or source.get("content", "No excerpt available."))

    with details_tab:
        st.markdown("#### Run metrics")
        st.json(metrics)
        decision_types = {
            "agent_started", "agent_completed", "routing_decision", "claim_verified",
            "report_filter_applied", "final_metrics", "run_failed",
        }
        decision_events = [event for event in events if event.get("event_type") in decision_types]
        st.markdown("#### Decision trail")
        if decision_events:
            for event in decision_events:
                payload = event.get("payload", {})
                label = event.get("event_type", "event").replace("_", " ").title()
                detail = payload.get("agent", payload.get("claim_id", payload.get("to", "run")))
                with st.expander(f"{label} · {detail}"):
                    st.json(event)
        else:
            st.info("No detailed decision events were returned.")
        with st.expander("Full audit trail"):
            st.json(events)
        download_cols = st.columns(2)
        download_cols[0].download_button(
            "Download report",
            result.get("report_markdown", ""),
            file_name=f"deepdive-{result.get('run_id', 'report')}.md",
            mime="text/markdown",
            use_container_width=True,
        )
        download_cols[1].download_button(
            "Download full run JSON",
            json.dumps(result, indent=2),
            file_name=f"deepdive-{result.get('run_id', 'run')}.json",
            mime="application/json",
            use_container_width=True,
        )
