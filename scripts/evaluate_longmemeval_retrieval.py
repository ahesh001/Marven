#!/usr/bin/env python3
"""Evaluate Marven's flat and graph retrieval on a LongMemEval JSON file.

This adapter intentionally evaluates retrieval only. It does not call an LLM,
download benchmark data, or send memory outside the local machine.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import tempfile
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from marven_local.memory import MemoryManager


DEFAULT_CUTOFFS = (1, 3, 5, 10)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path, help="Path to a LongMemEval JSON file")
    parser.add_argument(
        "--granularity",
        choices=("round", "session"),
        default="round",
        help="Store one user/assistant round or one full session per canonical record",
    )
    parser.add_argument(
        "--mode",
        choices=("flat", "graph", "both"),
        default="both",
        help="Compare semantic retrieval with the graph-expanded projection",
    )
    parser.add_argument(
        "--cutoffs",
        type=int,
        nargs="+",
        default=list(DEFAULT_CUTOFFS),
        help="Session-level retrieval cutoffs",
    )
    parser.add_argument("--limit", type=int, default=None, help="Optional number of questions")
    parser.add_argument("--output", type=Path, default=None, help="Optional JSON report path")
    return parser.parse_args()


def _load_dataset(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, list):
        raise ValueError("LongMemEval input must be a JSON list")
    return data


def _format_turns(turns: Iterable[Dict[str, Any]]) -> str:
    return "\n".join(
        f"{str(turn.get('role', 'unknown')).strip()}: {str(turn.get('content', '')).strip()}"
        for turn in turns
        if str(turn.get("content", "")).strip()
    )


def _rounds(session: Sequence[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    rounds: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []
    for turn in session:
        if turn.get("role") == "user" and current:
            rounds.append(current)
            current = []
        current.append(turn)
    if current:
        rounds.append(current)
    return rounds


def _index_entry(manager: MemoryManager, entry: Dict[str, Any], granularity: str) -> None:
    sessions = entry.get("haystack_sessions") or []
    session_ids = entry.get("haystack_session_ids") or []
    dates = entry.get("haystack_dates") or []
    if not (len(sessions) == len(session_ids) == len(dates)):
        raise ValueError(f"misaligned history fields for {entry.get('question_id')}")

    for session, session_id, date in zip(sessions, session_ids, dates):
        items = [session] if granularity == "session" else _rounds(session)
        for item_index, turns in enumerate(items):
            roles = [str(turn.get("role") or "unknown") for turn in turns]
            manager.add_memory(
                _format_turns(turns),
                ts=str(date),
                subject="",
                memory_type="conversation-round" if granularity == "round" else "conversation-session",
                source="longmemeval",
                source_locator=str(session_id),
                episode_id=str(session_id),
                trust_status="confirmed",
                metadata={
                    "benchmark": "LongMemEval",
                    "item_index": item_index,
                    "roles": roles,
                    "session_date": date,
                },
                rebuild_graph=False,
            )
    manager.rebuild_graph_projection()


def _dedupe_sessions(results: Sequence[Dict[str, Any]]) -> List[str]:
    ranked: List[str] = []
    for result in results:
        session_id = str(result.get("source_locator") or "")
        if session_id and session_id not in ranked:
            ranked.append(session_id)
    return ranked


def _metrics_at(ranked: Sequence[str], answers: Sequence[str], cutoff: int) -> Dict[str, float]:
    expected = set(str(answer) for answer in answers)
    top = list(ranked[:cutoff])
    hits = expected.intersection(top)
    recall_any = 1.0 if hits else 0.0
    recall_all = 1.0 if expected and hits == expected else 0.0
    dcg = sum(
        1.0 / math.log2(index + 2)
        for index, session_id in enumerate(top)
        if session_id in expected
    )
    ideal_hits = min(len(expected), cutoff)
    ideal = sum(1.0 / math.log2(index + 2) for index in range(ideal_hits))
    ndcg = dcg / ideal if ideal else 0.0
    return {"recall_any": recall_any, "recall_all": recall_all, "ndcg": ndcg}


def _evaluate_mode(
    manager: MemoryManager,
    entry: Dict[str, Any],
    *,
    use_graph: bool,
    cutoffs: Sequence[int],
) -> Tuple[Dict[str, Dict[str, float]], List[str]]:
    max_cutoff = max(cutoffs)
    # Round granularity can return several records from one session, so request
    # extra candidates before deduplicating to session-level rankings.
    records = manager.search_evidence(
        str(entry.get("question") or ""),
        top_k=max_cutoff * 5,
        use_graph=use_graph,
        max_hops=2,
    )
    ranked = _dedupe_sessions(records)
    answers = entry.get("answer_session_ids") or []
    return (
        {str(cutoff): _metrics_at(ranked, answers, cutoff) for cutoff in cutoffs},
        ranked[:max_cutoff],
    )


def _average(rows: Sequence[Dict[str, Dict[str, float]]], cutoffs: Sequence[int]) -> Dict[str, Any]:
    if not rows:
        return {"evaluated_questions": 0, "metrics": {}}
    metrics: Dict[str, Dict[str, float]] = {}
    for cutoff in cutoffs:
        key = str(cutoff)
        metrics[key] = {
            metric: sum(row[key][metric] for row in rows) / len(rows)
            for metric in ("recall_any", "recall_all", "ndcg")
        }
    return {"evaluated_questions": len(rows), "metrics": metrics}


def evaluate(
    dataset: Sequence[Dict[str, Any]],
    *,
    granularity: str,
    mode: str,
    cutoffs: Sequence[int],
) -> Dict[str, Any]:
    modes = ("flat", "graph") if mode == "both" else (mode,)
    aggregates: Dict[str, List[Dict[str, Dict[str, float]]]] = {name: [] for name in modes}
    examples: List[Dict[str, Any]] = []
    skipped_abstention = 0

    for entry in dataset:
        question_id = str(entry.get("question_id") or "")
        answers = entry.get("answer_session_ids") or []
        if question_id.endswith("_abs") or not answers:
            skipped_abstention += 1
            continue
        with tempfile.TemporaryDirectory(prefix="marven-longmemeval-") as temp_dir:
            manager = MemoryManager(
                Path(temp_dir),
                workspace_id="benchmark",
                owner_id=question_id or "unknown-question",
                agent_id="longmemeval-evaluator",
            )
            _index_entry(manager, entry, granularity)
            example = {
                "question_id": question_id,
                "question_type": entry.get("question_type"),
                "answer_session_ids": answers,
                "rankings": {},
            }
            for name in modes:
                metrics, ranking = _evaluate_mode(
                    manager,
                    entry,
                    use_graph=name == "graph",
                    cutoffs=cutoffs,
                )
                aggregates[name].append(metrics)
                example["rankings"][name] = ranking
            examples.append(example)
            manager.close()

    return {
        "benchmark": "LongMemEval",
        "granularity": granularity,
        "cutoffs": list(cutoffs),
        "skipped_abstention_questions": skipped_abstention,
        "results": {name: _average(rows, cutoffs) for name, rows in aggregates.items()},
        "examples": examples,
    }


def main() -> None:
    args = _parse_args()
    cutoffs = sorted({max(1, int(value)) for value in args.cutoffs})
    dataset = _load_dataset(args.dataset)
    if args.limit is not None:
        dataset = dataset[: max(0, args.limit)]
    report = evaluate(
        dataset,
        granularity=args.granularity,
        mode=args.mode,
        cutoffs=cutoffs,
    )
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
