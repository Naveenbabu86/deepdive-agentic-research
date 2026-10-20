# AWS deployment: cost-aware options

## Important: there is no guaranteed always-free AWS host for this whole app

DeepDive runs a long, multi-agent HTTP request and also serves a Streamlit UI. AWS App Runner and ECS/Fargate charge for provisioned compute, including idle time; they are not a safe assumption for a $0 deployment. Lambda/API Gateway can have free allowances, but this synchronous multi-agent workflow may exceed API Gateway's request timeout and Streamlit itself is not a good fit for Lambda.

## Recommended lowest-risk path for a demo

1. Use Qdrant Cloud's free cluster for vectors (separate provider; free-tier limits and inactivity suspension/deletion apply).
2. Keep Streamlit on a free Streamlit Community Cloud deployment, if its current account/repository requirements are acceptable.
3. For AWS specifically, deploy the backend container on an EC2 instance **only if the AWS Billing/Free Tier page confirms your account has applicable credits/free usage**. This is not universally free for every account. Set a billing budget/alarm before launching; public IPv4, storage, data transfer, and usage beyond credits can still cost money.
4. If you need the entire app hosted only on AWS, use a small EC2 instance with Docker Compose and a reverse proxy/TLS, but treat it as potentially billable. Do not expose port 8000 publicly; expose HTTPS only and set `ENVIRONMENT=production`, a strong `DEEPDIVE_API_KEY`, and all secrets through instance environment/secrets management.

## Container notes

The root `Dockerfile` is a simple demo container launching FastAPI and Streamlit. It is useful for a small single-instance demo, not a production topology. For a real deployment, run them as separate processes/services, use TLS, central logs, and a persistent database. SQLite on ephemeral container storage is not durable across redeployments.

## Required environment variables

- `MESH_API_KEY`, `MESH_MODEL`, `MESH_BASE_URL=https://api.meshapi.ai/v1`
- `TAVILY_API_KEY`
- `QDRANT_URL`, `QDRANT_API_KEY`, optional `QDRANT_COLLECTION`, `QDRANT_TOP_K`
- `ENVIRONMENT=production`, `DEEPDIVE_API_KEY` (random, 32+ characters)
- `DATABASE_PATH` only if using persistent mounted storage; SQLite is currently local-file persistence

Never bake `.env` or credentials into a Docker image, source repository, or `.env.example`.
