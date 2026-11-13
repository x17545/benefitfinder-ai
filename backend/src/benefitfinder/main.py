"""ASGI entrypoint.

Run from the repository root with:

    uv run uvicorn benefitfinder.main:app --reload --reload-dir backend/src \
        --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import os

from benefitfinder.bootstrap import analytics_service_from_environment, create_app

app = create_app(
    explanation_model=os.environ.get("F13_OLLAMA_MODEL") or None,
    explanation_database_url=os.environ.get("DATABASE_URL") or None,
    policy_database_url=os.environ.get("POLICY_DATABASE_URL") or None,
    analytics_service=analytics_service_from_environment(os.environ),
)
