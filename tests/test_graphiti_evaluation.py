from __future__ import annotations

from datetime import timezone
import importlib.util
import json
from pathlib import Path

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "experiments"
    / "graphiti"
    / "evaluate_graphiti.py"
)
SPEC = importlib.util.spec_from_file_location("marven_graphiti_evaluation", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
EVALUATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EVALUATOR)


def test_metric_helpers_score_graded_relevance():
    judgments = {"essential": 3.0, "relevant": 2.0, "marginal": 1.0}

    metrics = EVALUATOR._metrics_at(
        ["marginal", "essential", "irrelevant"],
        judgments,
        3,
    )

    assert metrics["recall"] == 0.5
    assert metrics["mrr"] == 0.5
    assert metrics["judged_precision"] == pytest.approx(1 / 3)
    assert 0.0 < metrics["ndcg"] < 1.0


@pytest.mark.parametrize(
    "url",
    [
        "https://api.example.com/v1",
        "bolt://graph.example.com:7687",
    ],
)
def test_remote_services_require_explicit_authorization(url):
    with pytest.raises(ValueError, match="refusing to send canonical memory"):
        EVALUATOR._ensure_endpoint_authorized(url, False, "test endpoint")


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:11434/v1",
        "http://127.0.0.1:8001/v1",
        "bolt://[::1]:7687",
    ],
)
def test_loopback_services_are_allowed_by_default(url):
    EVALUATOR._ensure_endpoint_authorized(url, False, "test endpoint")


def test_timestamp_parser_accepts_legacy_values_and_rejects_invalid_values():
    parsed = EVALUATOR._parse_timestamp("2026/09/21")

    assert parsed.tzinfo == timezone.utc
    assert parsed.isoformat() == "2026-09-21T00:00:00+00:00"
    with pytest.raises(ValueError, match="invalid canonical memory timestamp"):
        EVALUATOR._parse_timestamp("not-a-date")


def test_dataset_requires_plaintext_queries(tmp_path):
    dataset_path = tmp_path / "labels.json"
    dataset_path.write_text(
        json.dumps(
            {
                "schema": "marven.retrieval-labels.v1",
                "scope": {"workspace_id": "local", "owner_id": "primary"},
                "runs": [{"id": "hash-only", "query": ""}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="plaintext labeled queries"):
        EVALUATOR._load_dataset(dataset_path)


def test_run_filter_key_preserves_agent_session_and_policy_filters():
    run = {
        "agent_id": "research-agent",
        "session_id": "session-7",
        "retrieval_config": {
            "as_of": "2026-09-01T00:00:00Z",
            "time_start": "2026-01-01T00:00:00Z",
            "consent_scope": "local",
            "visibility": "private",
        },
    }

    assert EVALUATOR._run_filter_key(run) == (
        "research-agent",
        "session-7",
        "2026-09-01T00:00:00Z",
        "2026-01-01T00:00:00Z",
        "",
        "local",
        "private",
    )
