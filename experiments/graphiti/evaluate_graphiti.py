#!/usr/bin/env python3
"""Compare Graphiti with Marven on an explicitly labeled, owner-scoped dataset.

This experiment has no write path back to canonical memory. It creates
isolated Graphiti groups for captured retrieval scopes, ingests only eligible
canonical records, maps Graphiti episode references back to canonical IDs, and
writes a report that contains IDs and metrics rather than memory text.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
from importlib import metadata as importlib_metadata
import json
import math
import os
from pathlib import Path
import statistics
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence
from urllib.parse import urlparse
import uuid

from marven_local.memory import MemoryManager


DEFAULT_CUTOFFS = (1, 3, 5, 10)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path, help="marven.retrieval-labels.v1 JSON export")
    parser.add_argument("--root", type=Path, default=Path("."), help="Marven data root")
    parser.add_argument("--output", type=Path, required=True, help="Metrics report path")
    parser.add_argument("--cutoffs", type=int, nargs="+", default=list(DEFAULT_CUTOFFS))
    parser.add_argument("--limit", type=int, default=None, help="Optional labeled-run limit")
    parser.add_argument("--neo4j-uri", default=os.environ.get("NEO4J_URI", "bolt://localhost:7687"))
    parser.add_argument("--neo4j-user", default=os.environ.get("NEO4J_USER", "neo4j"))
    parser.add_argument("--neo4j-password", default=os.environ.get("NEO4J_PASSWORD", "password"))
    parser.add_argument(
        "--llm-base-url",
        default=os.environ.get("GRAPHITI_LLM_BASE_URL", "http://localhost:11434/v1"),
    )
    parser.add_argument(
        "--llm-model",
        default=os.environ.get("GRAPHITI_LLM_MODEL", "deepseek-r1:7b"),
    )
    parser.add_argument(
        "--embedding-model",
        default=os.environ.get("GRAPHITI_EMBEDDING_MODEL", "nomic-embed-text"),
    )
    parser.add_argument(
        "--embedding-dimension",
        type=int,
        default=int(os.environ.get("GRAPHITI_EMBEDDING_DIMENSION", "768")),
    )
    parser.add_argument(
        "--structured-output-mode",
        choices=("json_schema", "json_object"),
        default=os.environ.get("GRAPHITI_STRUCTURED_OUTPUT_MODE", "json_schema"),
        help="Graphiti extraction format; json_object can help compatible local models",
    )
    parser.add_argument(
        "--allow-remote-services",
        action="store_true",
        help="Explicitly permit canonical content to reach non-loopback model or graph services",
    )
    parser.add_argument(
        "--group-id",
        default=None,
        help="Optional isolated Graphiti group; defaults to a fresh non-identifying ID",
    )
    return parser.parse_args()


def _load_dataset(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict) or value.get("schema") != "marven.retrieval-labels.v1":
        raise ValueError("dataset must use schema marven.retrieval-labels.v1")
    scope = value.get("scope")
    if not isinstance(scope, dict) or not scope.get("workspace_id") or not scope.get("owner_id"):
        raise ValueError("dataset must contain a workspace and owner scope")
    runs = value.get("runs")
    if not isinstance(runs, list):
        raise ValueError("dataset runs must be a JSON list")
    if any(not isinstance(run, dict) or not str(run.get("query") or "").strip() for run in runs):
        raise ValueError("Graphiti evaluation requires plaintext labeled queries")
    return value


def _ensure_endpoint_authorized(url: str, allow_remote: bool, endpoint_name: str) -> None:
    host = (urlparse(url).hostname or "").lower()
    if host not in {"localhost", "127.0.0.1", "::1"} and not allow_remote:
        raise ValueError(
            f"refusing to send canonical memory to a remote {endpoint_name}; "
            "use --allow-remote-services only after reviewing consent and policy"
        )


def _parse_timestamp(value: Optional[str]) -> datetime:
    raw = str(value or "").strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        for timestamp_format in ("%Y/%m/%d (%a) %H:%M", "%Y/%m/%d", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(raw, timestamp_format)
                break
            except ValueError:
                pass
        else:
            raise ValueError(f"invalid canonical memory timestamp: {value}") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _judgments(run: Mapping[str, Any]) -> Dict[str, float]:
    values: Dict[str, List[int]] = {}
    for label in run.get("labels") or []:
        if not isinstance(label, Mapping):
            continue
        memory_id = str(label.get("memory_id") or "")
        try:
            grade = int(label.get("relevance"))
        except (TypeError, ValueError):
            continue
        if memory_id and grade in {0, 1, 2, 3}:
            values.setdefault(memory_id, []).append(grade)
    return {
        memory_id: sum(grades) / len(grades)
        for memory_id, grades in values.items()
        if grades
    }


def _run_filter_key(run: Mapping[str, Any]) -> tuple[str, ...]:
    """Return the canonical eligibility filters that must share one graph group."""

    config_value = run.get("retrieval_config")
    config = config_value if isinstance(config_value, Mapping) else {}
    return tuple(
        str(value or "").strip()
        for value in (
            run.get("agent_id"),
            run.get("session_id"),
            config.get("as_of"),
            config.get("time_start"),
            config.get("time_end"),
            config.get("consent_scope"),
            config.get("visibility"),
        )
    )


def _metrics_at(
    ranking: Sequence[str],
    judgments: Mapping[str, float],
    cutoff: int,
) -> Dict[str, Optional[float]]:
    top = list(ranking[:cutoff])
    relevant = {memory_id for memory_id, grade in judgments.items() if grade >= 2.0}
    hits = relevant.intersection(top)
    recall = len(hits) / len(relevant) if relevant else None
    reciprocal_rank: Optional[float] = None
    for index, memory_id in enumerate(top, start=1):
        if memory_id in relevant:
            reciprocal_rank = 1.0 / index
            break
    if relevant and reciprocal_rank is None:
        reciprocal_rank = 0.0

    dcg = sum(
        (2.0 ** judgments.get(memory_id, 0.0) - 1.0) / math.log2(index + 2)
        for index, memory_id in enumerate(top)
    )
    ideal_grades = sorted(judgments.values(), reverse=True)[:cutoff]
    ideal = sum(
        (2.0 ** grade - 1.0) / math.log2(index + 2)
        for index, grade in enumerate(ideal_grades)
    )
    return {
        "recall": recall,
        "mrr": reciprocal_rank,
        "ndcg": dcg / ideal if ideal else None,
        "judged_precision": (
            sum(1 for memory_id in top if judgments.get(memory_id, 0.0) >= 2.0) / len(top)
            if top
            else 0.0
        ),
    }


def _average(values: Iterable[Optional[float]]) -> Optional[float]:
    usable = [float(value) for value in values if value is not None]
    return sum(usable) / len(usable) if usable else None


def _aggregate(
    examples: Sequence[Mapping[str, Any]],
    system: str,
    cutoffs: Sequence[int],
) -> Dict[str, Any]:
    return {
        str(cutoff): {
            metric: _average(
                example["metrics"][system][str(cutoff)][metric]
                for example in examples
            )
            for metric in ("recall", "mrr", "ndcg", "judged_precision")
        }
        for cutoff in cutoffs
    }


def _percentile(values: Sequence[float], fraction: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1))
    return ordered[index]


def _build_graphiti(args: argparse.Namespace):
    try:
        from graphiti_core import Graphiti
        from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient
        from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
        from graphiti_core.llm_client.config import LLMConfig
        from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient
    except ImportError as exc:
        raise RuntimeError(
            "install experiments/graphiti/requirements.txt in a Python 3.10+ environment"
        ) from exc

    llm_config = LLMConfig(
        api_key="local-evaluation",
        model=args.llm_model,
        small_model=args.llm_model,
        base_url=args.llm_base_url,
    )
    llm_client = OpenAIGenericClient(
        config=llm_config,
        structured_output_mode=args.structured_output_mode,
    )
    embedder = OpenAIEmbedder(
        config=OpenAIEmbedderConfig(
            api_key="local-evaluation",
            embedding_model=args.embedding_model,
            embedding_dim=args.embedding_dimension,
            base_url=args.llm_base_url,
        )
    )
    reranker = OpenAIRerankerClient(config=llm_config)
    return Graphiti(
        args.neo4j_uri,
        args.neo4j_user,
        args.neo4j_password,
        llm_client=llm_client,
        embedder=embedder,
        cross_encoder=reranker,
        store_raw_episode_content=False,
    )


async def _evaluate(args: argparse.Namespace) -> Dict[str, Any]:
    _ensure_endpoint_authorized(
        args.llm_base_url,
        args.allow_remote_services,
        "model endpoint",
    )
    _ensure_endpoint_authorized(
        args.neo4j_uri,
        args.allow_remote_services,
        "graph database",
    )
    os.environ["GRAPHITI_TELEMETRY_ENABLED"] = "false"
    dataset = _load_dataset(args.dataset)
    scope = dataset["scope"]
    runs = list(dataset["runs"])
    if args.limit is not None:
        runs = runs[: max(0, args.limit)]
    if not runs:
        raise ValueError("the evaluation dataset has no labeled plaintext-query runs")
    cutoffs = sorted({max(1, int(value)) for value in args.cutoffs})

    manager = MemoryManager(
        args.root,
        workspace_id=scope["workspace_id"],
        owner_id=scope["owner_id"],
        agent_id="graphiti-evaluator",
    )
    filter_keys = sorted({_run_filter_key(run) for run in runs})
    memories_by_filter: Dict[tuple[str, ...], List[Dict[str, Any]]] = {}
    try:
        for filter_key in filter_keys:
            (
                agent_id,
                session_id,
                as_of,
                time_start,
                time_end,
                consent_scope,
                visibility,
            ) = filter_key
            memories = manager.list_eligible_memories(
                workspace_id=scope["workspace_id"],
                owner_id=scope["owner_id"],
                agent_id=agent_id or None,
                session_id=session_id or None,
                as_of=as_of or None,
                time_start=time_start or None,
                time_end=time_end or None,
                consent_scope=consent_scope or None,
                visibility=visibility or None,
            )
            if not memories:
                raise ValueError(
                    "a captured retrieval scope has no currently eligible canonical memories"
                )
            memories_by_filter[filter_key] = memories
    finally:
        manager.close()

    group_ids: Dict[tuple[str, ...], str] = {}
    for filter_key in filter_keys:
        suffix = hashlib.sha256(
            json.dumps(filter_key).encode("utf-8")
        ).hexdigest()[:12]
        group_ids[filter_key] = (
            f"{args.group_id}-{suffix}"
            if args.group_id and len(filter_keys) > 1
            else args.group_id or f"marven-eval-{uuid.uuid4().hex}"
        )

    graphiti = _build_graphiti(args)
    episode_to_memory: Dict[tuple[str, ...], Dict[str, str]] = {}
    canonical_ids = {
        memory["id"]
        for memories in memories_by_filter.values()
        for memory in memories
    }
    ingestion_count = sum(len(memories) for memories in memories_by_filter.values())
    ingestion_started = time.perf_counter()
    try:
        from graphiti_core.nodes import EpisodeType

        await graphiti.build_indices_and_constraints()
        for filter_key, memories in memories_by_filter.items():
            episode_to_memory[filter_key] = {}
            for memory in memories:
                approved_context = [memory["text"]]
                if memory.get("subject"):
                    approved_context.append(f"Subject: {memory['subject']}")
                if memory.get("tags"):
                    approved_context.append("Tags: " + ", ".join(memory["tags"]))
                result = await graphiti.add_episode(
                    name=f"marven-memory-{memory['id']}",
                    episode_body="\n".join(approved_context),
                    source=EpisodeType.text,
                    source_description="Approved Marven canonical memory",
                    reference_time=_parse_timestamp(memory.get("created_at")),
                    group_id=group_ids[filter_key],
                )
                episode_uuid = str(result.episode.uuid)
                episode_to_memory[filter_key][episode_uuid] = memory["id"]
        ingestion_seconds = time.perf_counter() - ingestion_started

        examples: List[Dict[str, Any]] = []
        latencies: List[float] = []
        unmapped_episode_references = 0
        for run in runs:
            filter_key = _run_filter_key(run)
            judgments = _judgments(run)
            started = time.perf_counter()
            edges = await graphiti.search(
                str(run["query"]),
                group_ids=[group_ids[filter_key]],
                num_results=max(20, max(cutoffs) * 4),
            )
            latencies.append(time.perf_counter() - started)
            graphiti_ranking: List[str] = []
            for edge in edges:
                for episode_uuid in getattr(edge, "episodes", []) or []:
                    memory_id = episode_to_memory[filter_key].get(str(episode_uuid))
                    if memory_id is None:
                        unmapped_episode_references += 1
                    elif memory_id not in graphiti_ranking:
                        graphiti_ranking.append(memory_id)

            baseline_ranking = [str(value) for value in run.get("result_ids") or []]
            examples.append(
                {
                    "run_id": run["id"],
                    "query_hash": run["query_hash"],
                    "scope_filter_hash": hashlib.sha256(
                        json.dumps(filter_key).encode("utf-8")
                    ).hexdigest(),
                    "label_coverage": (
                        len(set(baseline_ranking).intersection(judgments)) / len(baseline_ranking)
                        if baseline_ranking
                        else 0.0
                    ),
                    "judgment_count": len(judgments),
                    "rankings": {
                        "marven": baseline_ranking[: max(cutoffs)],
                        "graphiti": graphiti_ranking[: max(cutoffs)],
                    },
                    "metrics": {
                        system: {
                            str(cutoff): _metrics_at(ranking, judgments, cutoff)
                            for cutoff in cutoffs
                        }
                        for system, ranking in (
                            ("marven", baseline_ranking),
                            ("graphiti", graphiti_ranking),
                        )
                    },
                }
            )
    finally:
        await graphiti.close()

    aggregates = {
        system: _aggregate(examples, system, cutoffs)
        for system in ("marven", "graphiti")
    }
    largest_cutoff = str(max(cutoffs))
    marven_ndcg = aggregates["marven"][largest_cutoff]["ndcg"]
    graphiti_ndcg = aggregates["graphiti"][largest_cutoff]["ndcg"]
    marven_recall = aggregates["marven"][largest_cutoff]["recall"]
    graphiti_recall = aggregates["graphiti"][largest_cutoff]["recall"]
    average_coverage = _average(example["label_coverage"] for example in examples) or 0.0
    enough_labels = len(examples) >= 50 and average_coverage >= 0.8
    quality_gate = bool(
        enough_labels
        and marven_ndcg is not None
        and graphiti_ndcg is not None
        and marven_recall is not None
        and graphiti_recall is not None
        and graphiti_ndcg - marven_ndcg >= 0.05
        and graphiti_recall >= marven_recall - 0.02
    )
    if unmapped_episode_references:
        recommendation = "reject-scope-or-provenance-failure"
    elif not enough_labels:
        recommendation = "collect-more-labels"
    elif quality_gate:
        recommendation = "continue-isolated-graphiti-evaluation"
    else:
        recommendation = "retain-deterministic-baseline"

    return {
        "schema": "marven.graphiti-evaluation.v1",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "graphiti_version": importlib_metadata.version("graphiti-core"),
        "group_ids": list(group_ids.values()),
        "graphiti_group_count": len(group_ids),
        "scope_hash": hashlib.sha256(
            f"{scope['workspace_id']}\0{scope['owner_id']}".encode("utf-8")
        ).hexdigest(),
        "configuration": {
            "llm_model": args.llm_model,
            "embedding_model": args.embedding_model,
            "embedding_dimension": args.embedding_dimension,
            "structured_output_mode": args.structured_output_mode,
            "remote_services_explicitly_allowed": args.allow_remote_services,
        },
        "canonical_memory_count": len(canonical_ids),
        "graphiti_ingestion_count": ingestion_count,
        "labeled_run_count": len(examples),
        "average_label_coverage": average_coverage,
        "cutoffs": cutoffs,
        "aggregates": aggregates,
        "runtime": {
            "ingestion_seconds": ingestion_seconds,
            "seconds_per_ingestion": ingestion_seconds / ingestion_count,
            "query_latency_p50_seconds": statistics.median(latencies) if latencies else None,
            "query_latency_p95_seconds": _percentile(latencies, 0.95),
        },
        "safety": {
            "telemetry_disabled": os.environ.get("GRAPHITI_TELEMETRY_ENABLED") == "false",
            "raw_episode_storage_disabled": True,
            "write_back_to_canonical_memory": False,
            "unmapped_episode_references": unmapped_episode_references,
            "deletion_propagation": "not-tested-by-this-run",
        },
        "decision": {
            "recommendation": recommendation,
            "minimum_labeled_runs": 50,
            "minimum_average_label_coverage": 0.8,
            "required_ndcg_gain": 0.05,
            "maximum_recall_regression": 0.02,
            "quality_gate_passed": quality_gate,
        },
        "examples": examples,
    }


def main() -> None:
    args = _parse_args()
    report = asyncio.run(_evaluate(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["decision"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
