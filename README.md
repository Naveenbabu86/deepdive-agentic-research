# DeepDive — Self-Verifying Research Analyst

DeepDive is a research prototype designed around one principle: **a citation is not accepted just because a URL exists**. Claims are drafted from retrieved evidence, independently checked against the cited source text, and either supported, contradicted, or marked insufficient. Weak research areas trigger a bounded research loop. Each run stores a traceable audit trail.

> Competition requirement: all LLM/AI model calls must route through Mesh API. The shared `MeshLLM` client uses `https://api.meshapi.ai/v1` and `MESH_API_KEY`. Do not add direct provider SDK calls elsewhere in the codebase.

## What is implemented

- FastAPI `/research` and `/health` endpoints, request validation, and run lookup.
- Five distinct LangGraph roles coordinated through explicit edges: Planner → Researcher → Claim Drafter → Verifier → (targeted Researcher retry or Synthesizer). The agents pass structured state—sub-questions, sources, claims, statuses, and evidence quotes—between nodes.
- Real web search through Tavily plus best-effort source page extraction.
- Optional Qdrant Cloud semantic evidence index: source passages are chunked and embedded locally with FastEmbed, stored in Qdrant, and ranked for the current research run. Qdrant outages fall back to fresh web evidence; only current-run payloads are retrieved to avoid silently reusing stale evidence across research questions.
- Optional Redis cache for deterministic LLM completions and session-scoped follow-up memory. Cache keys are hashes of the exact model, messages, and request parameters; prior-run memory is planner context only and never evidence. Redis is optional and the workflow continues without it if unavailable.
- Stable source IDs, source-quality hints, retained source passages, and source URLs.
- Claim-by-claim verification against each cited source.
- A deterministic quote-presence check: a model's “supported” decision is downgraded unless its evidence quote appears in the retrieved passage (whitespace/case normalized). The Synthesizer independently repeats this check against the exact cited source before including a claim.
- Bounded verifier-to-research loop for thinly supported areas.
- SQLite audit events, source records, claim decisions, and final run status.
- Streamlit UI showing the report, sources, claim decisions, visible evidence quotes, agent/routing decision trail, self-measured metrics, and JSON/Markdown exports.
- A report filter that excludes unsupported claims from “Verified findings” and explicitly lists excluded claim IDs and reasons, making rejection inspectable in a demo.
- Offline unit tests for schemas, audit persistence, and quote-check enforcement.

## Security and production hardening

The application now includes the following defensive controls:

- `ENVIRONMENT=staging` or `ENVIRONMENT=production` requires `DEEPDIVE_API_KEY` (at least 32 characters); provide it as `X-API-Key` or a Bearer token. The `/health` endpoint remains public and `/ready` checks required credentials and persistence.
- API-key comparison uses constant-time comparison; production API errors avoid returning raw exception messages or provider response bodies.
- Per-process request rate limiting and a bounded number of concurrent research runs protect against accidental overload. For multi-worker or public deployments, put a shared gateway/WAF/Redis rate limiter in front; per-process limits are not a distributed quota system.
- Retrieved URLs are restricted to HTTP(S), local/private/reserved IP targets are rejected, and redirects are not followed. DNS checks alone cannot fully eliminate DNS-rebinding risk; use an outbound egress firewall or controlled fetch proxy in production.
- Mesh API calls use bounded retries for transient connection, timeout, rate-limit, and server errors. Search failures are recorded by error type, not raw exception strings.
- SQLite uses WAL mode, a busy timeout, and indexes for common audit queries. Use managed PostgreSQL or another shared database before horizontal multi-instance deployment.
- The UI supports an API key field for protected backend deployments.

Local learning mode defaults to `ENVIRONMENT=development` and does not require an API key. Do not expose that mode directly to the public internet.

## Important limitations before final submission

This is a competition-oriented implementation, not a guarantee of factual correctness. LLM-based verification can make mistakes. Exact quote matching prevents fabricated quotations but does not by itself prove that a quotation logically entails a claim. The lexical coverage heuristic is intentionally simple and should be upgraded/tested. Source-quality classification is a transparent domain-hint heuristic, not a definitive trust score. Search/page extraction varies by site, and some pages block automated retrieval.

Before final submission, run an independently labeled evaluation set and report measured citation-support precision, unsupported-claim rejection rate, contradiction detection, coverage, and latency. The runtime metrics added here are transparent proxies: `matched_citation_coverage` checks whether citation quotes occur in their cited source passages, and `source_matched_claim_rate` measures claims included after deterministic quote checks. Neither is a semantic entailment score or a guarantee of truth. Do not describe unmeasured results as achievements. Live provider integration, penetration testing, and load testing remain required before production deployment.

## Architecture

```text
Streamlit UI
    | HTTP
FastAPI (/research)
    |
LangGraph coordinator
    ├── Planner: decomposes the user question
    ├── Researcher: plans queries and calls Tavily web search
    ├── Claim Drafter: turns retrieved evidence into atomic claims
    ├── Verifier: checks each claim against each cited passage
    |      └── thin/unsupported coverage -> bounded research retry
    └── Synthesizer: builds a report from supported claims only
    |
Mesh API shared client (all LLM calls)
    |
SQLite audit store: runs, events, sources, claims
```

## Requirements

- Python 3.11 recommended
- A Mesh API key (`MESH_API_KEY`) for model calls
- A Tavily API key (`TAVILY_API_KEY`) for real web search
- Internet access from the environment running DeepDive

## Setup (Windows PowerShell or macOS/Linux shell)

1. Create and activate a virtual environment:

   ```bash
   python -m venv .venv
   ```

   Windows PowerShell:

   ```powershell
   .\.venv\Scripts\Activate.ps1
   ```

   macOS/Linux:

   ```bash
   source .venv/bin/activate
   ```

2. Install dependencies:

   ```bash
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   ```

3. Create your local `.env` from `.env.example`, then set real credentials:

   ```env
   MESH_API_KEY=rsk_your_real_key
   MESH_MODEL=openai/gpt-4o
   MESH_BASE_URL=https://api.meshapi.ai/v1
   TAVILY_API_KEY=tvly_your_real_key
   ENVIRONMENT=development
   # For staging/production, set ENVIRONMENT=production and generate a secret >= 32 chars.
   DEEPDIVE_API_KEY=replace_with_a_random_secret_at_least_32_characters
   ```

   Never commit `.env`. `.gitignore` already excludes it. Verify that the chosen model ID is available to your Mesh account.

   Optional Redis caching and follow-up memory are disabled by default. To enable them, set `REDIS_URL` to a trusted Redis endpoint. `LLM_CACHE_TTL_SECONDS` defaults to 1800; `REDIS_MEMORY_TTL_SECONDS` defaults to 30 days and `REDIS_MEMORY_MAX_RUNS` to 5 per Streamlit session. Only exact temperature-zero completions are cached. Memory contains prior questions and supported-claim summaries, is used only to help the planner interpret follow-ups, and is never passed to the drafter/verifier as evidence. Every run still searches for fresh sources. Redis may contain prompt-derived outputs and history; secure it with authentication, network restrictions, and TLS when remote.

4. Start FastAPI in terminal 1:

   ```bash
   uvicorn app.main:app --reload
   ```

5. Check `http://127.0.0.1:8000/health` and open `http://127.0.0.1:8000/docs`.

6. Start the UI in terminal 2:

   ```bash
   streamlit run streamlit_app.py
   ```

7. Submit a research question in the UI. A full run can take time because the system calls multiple agents and performs real web research.


## Qdrant Cloud semantic evidence retrieval (optional)

1. Create a free Qdrant Cloud cluster in the Qdrant console and copy its cluster URL and API key. The free tier is limited and may suspend after inactivity; see the official [Qdrant pricing page](https://qdrant.tech/pricing/).
2. Add `QDRANT_URL`, `QDRANT_API_KEY`, `QDRANT_COLLECTION=deepdive_evidence`, and `QDRANT_TOP_K=8` to your local `.env`. Do not add credentials to Git.
3. Install the optional vector dependencies: `python -m pip install "qdrant-client[fastembed]>=1.14,<2.0"`.
4. Restart FastAPI and run a research question. Inspect the run audit trail for the `qdrant_retrieval` event. If Qdrant is blank or unavailable, the workflow falls back to the existing web-source order.

FastEmbed downloads/uses the embedding model locally; no separate paid embedding API is used. Vector retrieval ranks sources already found in the current run; it does not replace Tavily search or the verifier's exact-source quote checks. This first implementation intentionally does not reuse old-run evidence, to reduce stale-source risk.

## Redis cache and follow-up memory (optional)

Install dependencies from `requirements.txt`, start or provision Redis, then set `REDIS_URL` in `.env` (for a local Redis service, commonly `redis://localhost:6379/0`). The LLM cache uses a SHA-256 key over the exact endpoint, model, messages, and completion parameters; raw prompts are not used as Redis key names. Only deterministic temperature-zero completions are cached, while search results remain fresh. Follow-up memory is scoped to the Streamlit browser session and stores only prior questions plus supported-claim text. The planner may use it as historical context; it is not supplied as evidence to later agents. Both features fail open when Redis is unavailable.

## AWS cost-aware deployment

Read [`deploy/aws-cost-aware-deployment.md`](deploy/aws-cost-aware-deployment.md) before deploying. There is no guaranteed always-free AWS service that comfortably hosts both the long-running FastAPI research workflow and Streamlit UI. App Runner/ECS can incur charges while provisioned. For a low-cost demo, consider Streamlit Community Cloud for the UI and AWS for a backend only if your account's Free Tier/credits make the actual usage free. If both must be on AWS, a small EC2 Docker deployment may be the simplest, but it is not universally free; configure billing alarms first.

## API example

```bash
curl -X POST http://127.0.0.1:8000/research \
  -H "Content-Type: application/json" \
  -d '{"question":"What are the major risks in the EV battery market in 2026?","max_research_rounds":2}'
```

For staging/production, add `-H "X-API-Key: $DEEPDIVE_API_KEY"` to API requests.

Response contains `run_id`, report Markdown, sub-questions, sources, claims with verification statuses, audit events, and a metrics snapshot. Fetch a saved run later with `GET /runs/{run_id}`.

## Test and compile

Offline tests do not require live credentials:

```bash
python -m compileall -q app streamlit_app.py
pytest -q
```

Live integration testing requires working Mesh and Tavily keys. Never put credentials in test fixtures or logs.

## Runtime metrics and visible rejection

The app computes the following metrics from each run and shows them in the UI:

- `claims_evaluated`, `claims_supported_by_verifier`, `claims_contradicted`, and `claims_insufficient` count verifier outcomes.
- `claims_in_final_report` and `claims_excluded_from_final_report` show the report filter decision.
- `matched_citation_coverage` is the fraction of source citations attached to verifier-supported claims whose stored evidence quotes still match the exact cited source passage after normalization.
- `source_matched_claim_rate` is the fraction of all evaluated claims that survive the verifier status and deterministic source-quote gate.
- `verifier_support_rate` is the fraction of claims the model verifier labels supported, before the final report filter.

These metrics are self-measured implementation signals, not externally validated accuracy scores. To demonstrate rejection, inspect a `report_filter_applied` audit event and a claim listed under **Claims not included as verified facts**; it must not appear under **Verified findings**. The exact quote is displayed beside each included finding so reviewers can inspect the citation relationship.

## Labeled verifier benchmark (new)

A small, versioned synthetic benchmark is included at `benchmarks/verification_cases.json`. It contains manually labeled supported, unsupported, contradicted, partial-support, negation, number/date, entity, forecast, and conflicting-source cases. It is designed to reveal common failure modes, not to stand in for an independent real-world benchmark.

Run the live model evaluation from the repository root after configuring `MESH_API_KEY`:

```bash
python -m scripts.evaluate_verifier --output artifacts/verification-benchmark-results.json
```

This makes one or more live Mesh API calls per case and may incur API cost. The command writes per-case expected/actual labels and summary measures: exact status accuracy, supported-claim precision, unsupported-claim rejection rate, and contradiction detection rate. It exits non-zero if any case differs from its expected label; inspect the individual rows rather than optimizing solely for a perfect score on this small set. Keep the raw result artifact with the submission and report the benchmark version, case count, model, date, and any failures. Never fabricate or hand-edit measured scores.

The offline unit tests check the scorer and fail-closed aggregation behavior using controlled test doubles; they do **not** measure the real model's semantic verification ability. In particular, a model can still incorrectly label a contradictory claim as supported if it provides a quote that exists in the passage. The live labeled benchmark is intended to expose that risk, not eliminate it.

## Self-evaluation plan

Use a versioned test set containing multiple domains and examples of:

1. A claim directly supported by a passage.
2. A claim with a real citation that does not support it.
3. A contradicted claim.
4. An unsupported number/date/forecast.
5. Conflicting reputable sources.
6. A sub-question with initially thin evidence that improves after retry.
7. A question where evidence remains insufficient after the retry budget.

Have a human label each claim/source pair before evaluating. Report at minimum:

- **Citation support precision:** accepted claims actually supported / all accepted claims.
- **Unsupported-claim rejection rate:** known unsupported claims rejected / all known unsupported claims.
- **Contradiction detection rate:** known contradictions flagged / all known contradictions.
- **Sub-question coverage:** adequately supported sub-questions / total sub-questions.
- **Loop success rate:** initially thin areas adequately supported after retries / initially thin areas.
- Median and p95 run latency, source count, and model-call count.

Include sample size and test-set construction in the README. Keep a few manually inspected example runs and show at least one unsupported claim rejected in the demo. Do not claim a target score unless measured.

## GitHub and hackathon checklist

- [ ] Create a public repository and push the project source.
- [ ] Add GitHub Actions secrets `MESH_API_KEY` and `SUBMISSION_TOKEN` (and `TAVILY_API_KEY` for live research tests if needed).
- [ ] Download the organizer-provided workflow from `https://careerapi-production.krishnaik.in/api/ci/hackathons/agent-arena-2026/workflow.yml` and save it as `.github/workflows/agent-arena-2026-checks.yml`. The included `ci.yml` is a project test workflow and **is not a substitute** for the organizer's official checks.
- [ ] Push a commit and inspect both GitHub Actions and the submission dashboard.
- [ ] Run a live end-to-end test with valid Mesh/Tavily credentials.
- [ ] Deploy only behind HTTPS and a shared rate limiter/WAF; configure a strong `DEEPDIVE_API_KEY` and a network egress policy.
- [ ] Decide on audit-data retention and access policies; SQLite currently stores research questions, source passages, claims, and reports in plaintext on disk.
- [ ] Add actual self-evaluation results after executing the fixed test set.
- [ ] Record a short demo that visibly shows a claim rejected or marked insufficient and the system re-researching a gap.
- [ ] Review license/terms of the search provider and source websites before deployment.

## Security notes

- `.env` is ignored; use repository secrets in CI.
- Do not return stack traces or API keys to clients.
- The demo is not hardened for anonymous public production use. Add auth, rate limits, request quotas, robust retry/backoff, and monitoring before public deployment.
- Treat retrieved web pages as untrusted input. The agents are instructed to treat content as evidence, not instructions; additional prompt-injection defenses and source sanitization should be tested before deployment.


## Troubleshooting: oversized model prompts and LangSmith warnings

If a provider reports an error such as `413 Request too large` or a tokens-per-minute (TPM) limit, the Drafter now packs at most eight relevance-ranked sources, bounds each evidence passage, caps the response at 1,200 tokens, and asks for at most 12 claims. This reduces prompt size while preserving source IDs for verification. TPM is also a time-window quota shared across calls, so if the provider still returns a rate-limit error, wait for the window to reset and reduce concurrent runs. The API returns a controlled `502` for provider status errors rather than reporting them as an application `500`.

DeepDive's hackathon configuration requires model requests to pass through Mesh. In your local `.env`, use the Mesh endpoint and a model supported by your Mesh account, for example:

```dotenv
MESH_BASE_URL=https://api.meshapi.ai/v1
MESH_API_KEY=REDACTED
MESH_MODEL=openai/gpt-4o
LANGSMITH_TRACING=false
LANGCHAIN_TRACING_V2=false
```

Do not use a direct provider endpoint such as `https://api.groq.com/openai/v1` for this submission if the rule is that every model call must route through Mesh. If you intentionally choose direct Groq for local debugging, the call no longer demonstrates the required Mesh routing. The exact model name must be available through your Mesh account.

The LangSmith `401 Unauthorized` messages are optional tracing failures, separate from the model's 413/TPM error. Leave tracing disabled as above unless you have configured a valid `LANGSMITH_API_KEY`. On Windows Command Prompt, you can disable it for the current terminal session with:

```bat
set LANGSMITH_TRACING=false
set LANGCHAIN_TRACING_V2=false
```

Restart Uvicorn after changing environment variables. Never paste API keys into logs, source code, or screenshots.
