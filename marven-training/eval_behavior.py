#!/usr/bin/env python3
"""Reproducible, constrained smoke checks; not a semantic safety benchmark."""
from __future__ import annotations

import argparse
import json
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from data_utils import sha256_file

ROOT = Path(__file__).resolve().parent


def strict_json(text):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("duplicate JSON key")
            value[key] = item
        return value
    def constant(value):
        raise ValueError(f"invalid JSON constant: {value}")
    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


def grade(case, text):
    try:
        actual = strict_json(text)
    except (ValueError, TypeError):
        return False, ["Response was not one strict JSON object"]
    if not isinstance(actual, dict):
        return False, ["Response must be an object"]
    if json.dumps(actual, sort_keys=True) != json.dumps(case["expected"], sort_keys=True):
        return False, ["JSON values or keys differ from the expected contract"]
    return True, []


def request_completion(base_url, model, case, seed, api_key_env):
    payload = {"model": model, "messages": case["messages"], "temperature": 0.0,
               "seed": seed, "max_tokens": 256,
               "chat_template_kwargs": {"enable_thinking": False}}
    headers = {"Content-Type": "application/json"}
    key = os.environ.get(api_key_env)
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=60) as response:
        result = json.load(response)
    choice = result["choices"][0]
    if choice.get("finish_reason") != "stop":
        raise ValueError("Generation did not stop normally (may be truncated)")
    content = choice["message"].get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("No textual response")
    return content.strip()


def evaluate(base_url, model, cases, seed, api_key_env):
    results = []
    for case in cases:
        try:
            response = request_completion(base_url, model, case, seed, api_key_env)
            ok, problems = grade(case, response)
            result = {"name": case["name"], "passed": ok, "problems": problems, "response": response}
        except Exception as exc:
            # Do not record credentials, request bodies, or arbitrary server error pages.
            result = {"name": case["name"], "passed": False, "error_type": type(exc).__name__}
        results.append(result)
        print(f"{model}: {case['name']}: {'PASS' if result['passed'] else 'FAIL'}")
    return {"model": model, "passed": sum(r["passed"] for r in results), "total": len(results), "cases": results}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base-url", default="http://127.0.0.1:8001/v1")
    p.add_argument("--model", required=True)
    p.add_argument("--baseline-model", help="Compare base and adapter on the same running server")
    p.add_argument("--cases", type=Path, default=ROOT / "eval/behavior_v1.json")
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--api-key-env", default="MARVEN_EVAL_API_KEY")
    args = p.parse_args()
    if args.output.exists():
        p.error("output already exists; keep evaluation runs versioned")
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases or len({c['name'] for c in cases}) != len(cases):
        p.error("cases must be a nonempty list with unique names")
    report = {"format_version": 2, "suite": "public constrained smoke checks",
              "suite_sha256": sha256_file(args.cases), "seed": args.seed,
              "enable_thinking": False, "temperature": 0, "max_tokens": 256,
              "created_at": datetime.now(timezone.utc).isoformat(), "promotion_decision": "human_review_required"}
    if args.baseline_model:
        report["baseline"] = evaluate(args.base_url, args.baseline_model, cases, args.seed, args.api_key_env)
    report["candidate"] = evaluate(args.base_url, args.model, cases, args.seed, args.api_key_env)
    regressions = []
    if "baseline" in report:
        regressions = [a["name"] for a, b in zip(report["baseline"]["cases"], report["candidate"]["cases"])
                       if a["passed"] and not b["passed"]]
        report["regressions"] = regressions
        report["pass_count_delta"] = report["candidate"]["passed"] - report["baseline"]["passed"]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Report: {args.output}")
    raise SystemExit(0 if report["candidate"]["passed"] == len(cases) and not regressions else 1)


if __name__ == "__main__":
    main()
