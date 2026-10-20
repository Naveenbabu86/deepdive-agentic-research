"""Keep offline tests from initializing LangSmith hosted tracing."""

import os


os.environ.setdefault("LANGSMITH_TRACING", "false")
os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")
