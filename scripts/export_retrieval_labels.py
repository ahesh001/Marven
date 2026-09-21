#!/usr/bin/env python3
"""Export scoped Marven retrieval judgments for offline evaluation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from marven_local.memory import MemoryManager


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="Destination JSON file")
    parser.add_argument("--root", type=Path, default=Path("."), help="Marven data root")
    parser.add_argument("--workspace", default=None, help="Required workspace boundary")
    parser.add_argument("--owner", default=None, help="Required owner boundary")
    parser.add_argument(
        "--include-hash-only",
        action="store_true",
        help="Include audit-only runs whose raw query was not retained",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    manager = MemoryManager(
        args.root,
        workspace_id=args.workspace,
        owner_id=args.owner,
    )
    try:
        dataset = manager.export_retrieval_labels(
            workspace_id=args.workspace,
            owner_id=args.owner,
            include_hash_only=args.include_hash_only,
        )
    finally:
        manager.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(dataset, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Exported {dataset['run_count']} run(s) to {args.output}")


if __name__ == "__main__":
    main()
