# DeepDive — Evidence-Grounded Research Platform

DeepDive is a multi-agent research application that turns a question into a structured report with traceable sources and claim-level evidence checks. It is designed to make the research process inspectable: users can review the source passages behind findings, see which claims were supported or rejected, and follow the system's research decisions.

The platform combines a FastAPI service, a LangGraph workflow, web search, optional semantic retrieval, a source-aware verification pipeline, and a Streamlit interface. It emphasizes evidence traceability and explicit uncertainty rather than treating the presence of a citation as proof that a claim is correct.

> **Deployment status:** DeepDive is a production-oriented software project with defensive controls and automated tests. It should not be considered production-certified until live provider integration, security, privacy, load, and operational testing have been completed in the intended deployment environment.

## Core capabilities

- **Multi-agent research workflow:** Planner, Researcher, Claim Drafter, Verifier, and Synthesizer roles operate through an explicit LangGraph state graph.
- **Source-grounded claims:** Research findings retain source identifiers, URLs, and retrieved passages so users can inspect the evidence.
- **Claim-level verification:** The Verifier evaluates claims against cited passages. Deterministic quote-presence checks downgrade a supported decision when its quoted evidence cannot be found in the retrieved passage.
- **Bounded research retry:** Thinly supported areas can trigger a limited additional research round rather than an unbounded agent loop.
- **Conservative report synthesis:** The Synthesizer rechecks quote presence and excludes claims that do not pass the report's verification rules.
- **Inspectable decision trail:** The interface displays research results, sources, claim decisions, evidence quotes, audit events, and runtime metrics.
- **Web research:** Tavily provides search results, with best-effort extraction of source-page content.
- **Optional semantic evidence retrieval:** Qdrant Cloud can index passages discovered during the current research run. If vector retrieval is unavailable, the workflow can continue with web evidence.
- **Optional Redis features:** Redis can provide deterministic completion caching and session-scoped follow-up context. Historical context helps interpret follow-up questions; it is not treated as evidence for new claims.
- **Audit persistence:** SQLite stores run status, source records, claim decisions, and audit events.
- **Developer-friendly API and UI:** FastAPI exposes research and run lookup endpoints; Streamlit provides an interactive interface and report exports.

## Architecture

```text
Streamlit interface
       | HTTP
       v
FastAPI service
  /research, /runs/{run_id}, health/readiness
       |
       v
LangGraph coordinator
  Planner
    -> Researcher -> Claim Drafter -> Verifier
                                      |
                         bounded retry when needed
                                      |
                                      v
                                 Synthesizer
       |
       +--> Tavily web search and source extraction
       +--> Optional Qdrant semantic evidence retrieval
       +--> Optional Redis cache and follow-up context
       +--> SQLite audit store
       |
       v
Shared Mesh API client for all LLM requests
```

### Agent responsibilities

| Component | Responsibility |
|---|---|
| Planner | Interprets the question and organizes it into research sub-questions. |
| Researcher | Searches for relevant web sources and gathers source passages. |
| Claim Drafter | Converts retrieved evidence into discrete, source-linked claims. |
| Verifier | Evaluates each claim against the source passage and records a verification status. |
| Synthesizer | Produces a readable report from claims that pass the configured verification and quote checks. |

## Evidence handling and known limitations

DeepDive includes deterministic checks, but no automated verification system can guarantee factual correctness. In particular:

- Finding an exact quotation in a source does **not** prove that the quotation logically entails the associated claim.
- Model-based verification can misclassify support, contradiction, or uncertainty.
- Source-quality hints are heuristic indicators, not definitive trust scores.
- Web search coverage and page extraction vary by publisher; some websites block automated access or return incomplete content.
- Qdrant retrieval is an optional aid for ranking passages found during the current run. It does not replace web search or claim verification.
- The included benchmark is a small synthetic test set. It is useful for regression testing but is not an independent measure of real-world factual accuracy.

Before using DeepDive for high-impact decisions, evaluate it on a representative, human-labeled dataset. Measure citation-support precision, unsupported-claim rejection, contradiction detection, sub-question coverage, and end-to-end latency. Record the dataset construction, sample size, model, date, and failures. Do not interpret the application's runtime proxy metrics as validated accuracy scores.

## Security and operational controls

The application includes several defensive measures:

- Staging and production environments require a configured `DEEPDIVE_API_KEY` of at least 32 characters. Requests can provide it through `X-API-Key` or a Bearer token.
- The health endpoint remains public; the readiness endpoint checks required configuration and persistence.
- API-key comparison uses constant-time comparison. Production API errors avoid exposing raw exception messages or provider response bodies.
- Per-process request rate limiting and a cap on concurrent research runs reduce accidental overload. These controls are not a distributed quota system.
- Retrieved URLs are restricted to HTTP(S), local/private/reserved IP targets are rejected, and redirects are not followed. DNS validation alone cannot eliminate all DNS-rebinding risks.
- LLM requests use bounded retries for selected transient failures. Search errors are recorded by type rather than raw exception strings.
- SQLite uses WAL mode, a busy timeout, and indexes for common audit queries.
- Redis is optional; the workflow can continue when it is unavailable.

**Before a public or multi-instance deployment:**

1. Put the service behind HTTPS and a gateway, WAF, or shared rate limiter.
2. Apply outbound network controls or a controlled fetch proxy to reduce SSRF and DNS-rebinding risk.
3. Use a managed shared database such as PostgreSQL for multi-instance operation; local SQLite is intended for a single-instance setup.
4. Configure secrets through a secret manager or protected environment configuration, never in source code or container images.
5. Add centralized logs, monitoring, alerts, backups, retention controls, and a recovery procedure.
6. Test prompt-injection defenses, provider failures, concurrency limits, authentication, authorization, and abuse scenarios.
7. Review privacy and retention requirements because research questions, retrieved passages, claims, and reports may be persisted.

Local development defaults to `ENVIRONMENT=development` and does not require an API key. **Do not expose development mode directly to the public internet.**

## Technology stack

- Python 3.11 or newer
- FastAPI and Uvicorn
- LangGraph for workflow orchestration
- Mesh API through a shared OpenAI-compatible client for LLM requests
- Tavily for web search
- Streamlit for the user interface
- SQLite for local audit persistence
- Optional Qdrant Cloud with local FastEmbed embeddings
- Optional Redis for caching and session-scoped follow-up context
- Pytest for automated tests

All runtime LLM calls use the configured Mesh API endpoint. Keep the shared client as the single integration point for model requests.

## Prerequisites

- Python 3.11+
- A Mesh API key and a model identifier available to your account
- A Tavily API key for live web search
- Internet access from the machine running the application

Qdrant Cloud and Redis are optional. You can run the base workflow without configuring either service.

## Local setup

### 1. Create and activate a virtual environment

**Windows PowerShell**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**macOS/Linux**

```bash
python -m venv .venv
source .venv/bin/activate
```

### 2. Install dependencies

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 3. Configure environment variables

Copy `.env.example` to `.env` and set your local credentials. The following illustrates the main settings; use the exact model identifier supported by your Mesh account.

```dotenv
MESH_API_KEY=your_mesh_api_key
MESH_MODEL=openai/gpt-4o
MESH_BASE_URL=https://api.meshapi.ai/v1
TAVILY_API_KEY=your_tavily_api_key
ENVIRONMENT=development
DEEPDIVE_API_KEY=replace_with_a_random_secret_at_least_32_characters
```

Never commit `.env` or put real credentials in `.env.example`, logs, screenshots, or container images. For staging or production, set `ENVIRONMENT=production` and configure a strong `DEEPDIVE_API_KEY`.

### 4. Start the API

```bash
uvicorn app.main:app --reload
```

Check service health at [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health). Interactive API documentation is available at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

### 5. Start the Streamlit interface

In a second terminal with the virtual environment activated:

```bash
streamlit run streamlit_app.py
```

Submit a research question in the interface. A complete run can take time because multiple agents and external web research are involved.

## API usage

Example request:

```bash
curl -X POST http://127.0.0.1:8000/research \
  -H "Content-Type: application/json" \
  -d '{"question":"What are the major risks in the EV battery market in 2026?","max_research_rounds":2}'
```

For staging or production, include the configured API key:

```bash
curl -X POST http://127.0.0.1:8000/research \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_DEEPDIVE_API_KEY" \
  -d '{"question":"Summarize recent developments in grid-scale energy storage.","max_research_rounds":2}'
```

The response includes a run identifier, report Markdown, sub-questions, sources, claims and verification statuses, audit events, and a metrics snapshot. Retrieve a saved run using `GET /runs/{run_id}`.

## Optional Qdrant Cloud retrieval

Qdrant adds semantic ranking for source passages gathered during the current research run.

1. Create a Qdrant Cloud cluster and obtain its URL and API key. Check the provider's current pricing and usage limits before relying on a free tier.
2. Add these values to `.env`:

   ```dotenv
   QDRANT_URL=your_qdrant_cluster_url
   QDRANT_API_KEY=your_qdrant_api_key
   QDRANT_COLLECTION=deepdive_evidence
   QDRANT_TOP_K=8
   ```

3. Install the optional vector dependency:

   ```bash
   python -m pip install "qdrant-client[fastembed]>=1.14,<2.0"
   ```

4. Restart the API and run a research query. Inspect the audit trail for the `qdrant_retrieval` event.

FastEmbed runs the embedding model locally. Qdrant is an optional retrieval aid and does not replace fresh web research or the verifier's source-level checks. If Qdrant is unavailable, the workflow falls back to its web-source path.

## Optional Redis cache and follow-up context

Set `REDIS_URL` in `.env` to enable Redis-backed features. For a local Redis service, a typical URL is `redis://localhost:6379/0`.

- The completion cache hashes the endpoint, model, messages, and request parameters. Only deterministic temperature-zero completions are cached; web search remains fresh.
- Follow-up context is scoped to the Streamlit session and stores prior questions and supported-claim summaries. It can help the Planner interpret a follow-up but is not supplied to later agents as evidence.
- Both features fail open when Redis is unavailable.

Remote Redis should use authentication, network restrictions, and TLS. Treat cached content and session history as potentially sensitive.

## Testing and evaluation

Run the offline test suite:

```bash
python -m compileall -q app streamlit_app.py
pytest -q
```

Offline tests use controlled test doubles and do not validate live provider behavior or semantic factual accuracy. Live integration testing requires valid Mesh and Tavily credentials.

A small labeled synthetic benchmark is included at `benchmarks/verification_cases.json`. After configuring `MESH_API_KEY`, run:

```bash
python -m scripts.evaluate_verifier --output artifacts/verification-benchmark-results.json
```

This command makes live model calls and may incur API costs. It records expected and actual labels and computes summary measures such as status accuracy, supported-claim precision, unsupported-claim rejection, and contradiction detection. Inspect per-case results; a small synthetic benchmark should not be treated as proof of general accuracy.

### Runtime metrics

The application displays implementation metrics in the UI, including:

- `claims_evaluated`, `claims_supported_by_verifier`, `claims_contradicted`, and `claims_insufficient`
- `claims_in_final_report` and `claims_excluded_from_final_report`
- `matched_citation_coverage`, a deterministic measure of whether stored evidence quotes still match cited passages
- `source_matched_claim_rate`, the fraction of evaluated claims that pass the configured verifier-status and quote-presence checks
- `verifier_support_rate`, the fraction of claims labeled supported by the model verifier before the final report filter

These are diagnostic signals, not externally validated factual-accuracy scores. For an auditable example, inspect a `report_filter_applied` event and confirm that excluded claims do not appear under **Verified findings**.

## Deployment notes

The root Dockerfile provides a convenient containerized starting point. It is not a complete multi-service production topology. For a persistent deployment, run the API and UI as separate services, provide durable storage, configure HTTPS, central logs and monitoring, and use a shared database when scaling beyond one instance.

See [the cost-aware AWS deployment guide](deploy/aws-cost-aware-deployment.md) before provisioning cloud resources. Compute, storage, networking, public IPv4, and managed-service costs depend on account eligibility and actual usage. Check current pricing and configure billing alerts before creating resources.

## Troubleshooting

### Model prompt or rate-limit errors

Research invokes multiple agents, so one user request can result in several model calls. If the provider reports an oversized prompt or rate limit, reduce concurrent runs and research scope, then retry after the provider's quota window resets. Provider limits depend on the model and account.

### LangSmith tracing errors

LangSmith tracing is optional. If it is not configured, disable tracing in the local environment rather than placing an invalid tracing key in the project. Tracing authentication errors are separate from Mesh model-request errors.

### Missing or incomplete source pages

Some websites block automated extraction, require scripts, or return partial content. Review the saved source passage and URL in the interface. A URL alone is not sufficient evidence for a verified finding.

## Project layout

```text
app/
  agents/       Planner, Researcher, Drafter, Verifier, Synthesizer
  services/     LLM client, search, evidence retrieval, cache, audit, evaluation
  config.py     Validated application settings
  graph.py      LangGraph workflow
  main.py       FastAPI endpoints
  schemas.py    Request and response models
streamlit_app.py
benchmarks/     Synthetic verification cases
tests/          Offline automated tests
scripts/        Evaluation utilities
deploy/         Deployment guidance
Dockerfile
requirements.txt
```

## Responsible use

DeepDive is a research-assistance tool, not a substitute for expert review. Verify important findings against original sources, pay attention to contradictory evidence, and retain human review for decisions with legal, financial, medical, safety, or other significant consequences.