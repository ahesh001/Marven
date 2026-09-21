#!/usr/bin/env python3
"""Collect local, graded relevance labels for Marven memory retrieval."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable, List, Optional

from marven_local.memory import MemoryManager


GRADE_HELP = "0=irrelevant, 1=marginal, 2=relevant, 3=essential, s=skip"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."), help="Marven data root")
    parser.add_argument("--workspace", default=None, help="Required workspace boundary")
    parser.add_argument("--owner", default=None, help="Required owner boundary")
    parser.add_argument("--agent", default=None, help="Optional agent filter")
    parser.add_argument("--session", default=None, help="Optional session filter")
    parser.add_argument("--labeler", default="", help="Optional reviewer identifier")
    parser.add_argument("--top-k", type=int, default=10, help="Candidates to review")
    parser.add_argument("--no-graph", action="store_true", help="Disable graph expansion")
    parser.add_argument(
        "--query-storage",
        choices=("plaintext", "hash-only"),
        default="plaintext",
        help="Store reusable query text or only its SHA-256 hash",
    )
    parser.add_argument(
        "--query",
        action="append",
        default=[],
        help="Query to label; repeat for multiple queries, or omit for an interactive loop",
    )
    return parser.parse_args()


def _queries(configured: Iterable[str]) -> Iterable[str]:
    values = [value.strip() for value in configured if value.strip()]
    if values:
        yield from values
        return
    while True:
        value = input("\nQuery (blank to finish): ").strip()
        if not value:
            return
        yield value


def _prompt_grade(prompt: str) -> Optional[int]:
    while True:
        value = input(prompt).strip().lower()
        if value in {"", "s", "skip"}:
            return None
        if value in {"0", "1", "2", "3"}:
            return int(value)
        print(f"Enter {GRADE_HELP}.")


def _review_query(manager: MemoryManager, query: str, args: argparse.Namespace) -> str:
    results = manager.search_evidence(
        query,
        top_k=max(1, min(args.top_k, 100)),
        workspace_id=args.workspace,
        owner_id=args.owner,
        agent_id=args.agent,
        session_id=args.session,
        use_graph=not args.no_graph,
    )
    run_id = manager.record_retrieval_run(
        query,
        results,
        workspace_id=args.workspace,
        owner_id=args.owner,
        agent_id=args.agent,
        session_id=args.session,
        query_storage=args.query_storage,
        top_k=max(1, min(args.top_k, 100)),
        retrieval_config={
            "use_graph": not args.no_graph,
            "source": "interactive-cli",
        },
    )

    print(f"\nRun {run_id}: {query}")
    print(f"Review each result ({GRADE_HELP}).")
    for rank, result in enumerate(results, start=1):
        text = " ".join(str(result["text"]).split())
        preview = text if len(text) <= 180 else text[:177] + "..."
        print(
            f"\n{rank}. {result['id']}  score={float(result['score']):.4f}"
            f"  subject={result['subject'] or '-'}\n   {preview}"
        )
        grade = _prompt_grade("   relevance: ")
        if grade is None:
            continue
        manager.label_retrieval_result(
            run_id,
            result["id"],
            grade,
            workspace_id=args.workspace,
            owner_id=args.owner,
            labeler_id=args.labeler,
        )

    while True:
        missed_id = input(
            "\nCanonical ID for relevant evidence that was missed (blank for none): "
        ).strip()
        if not missed_id:
            break
        grade = _prompt_grade("   missed relevance (2 or 3): ")
        if grade is None:
            continue
        if grade < 2:
            print("Missed evidence must be labeled 2 or 3.")
            continue
        try:
            manager.label_retrieval_result(
                run_id,
                missed_id,
                grade,
                workspace_id=args.workspace,
                owner_id=args.owner,
                labeler_id=args.labeler,
            )
        except ValueError as exc:
            print(f"Could not save that judgment: {exc}")
    return run_id


def main() -> None:
    args = _parse_args()
    manager = MemoryManager(
        args.root,
        workspace_id=args.workspace,
        owner_id=args.owner,
        agent_id=args.agent,
    )
    completed: List[str] = []
    try:
        for query in _queries(args.query):
            completed.append(_review_query(manager, query, args))
    finally:
        manager.close()
    print(f"\nSaved {len(completed)} labelable retrieval run(s).")


if __name__ == "__main__":
    main()
