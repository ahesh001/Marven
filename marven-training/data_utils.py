"""Strict, dependency-free validation for Marven's text-only SFT format."""
from __future__ import annotations

import hashlib
import json
import math
import random
import re
import unicodedata
from pathlib import Path


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prompt_key(row):
    # Ignore a repeated system preamble when detecting duplicate questions.
    messages = [{"role": m["role"], "content": " ".join(
        unicodedata.normalize("NFKC", m["content"]).casefold().split())}
        for m in row["prompt"] if m["role"] != "system"]
    return hashlib.sha256(json.dumps(messages, sort_keys=True).encode()).hexdigest()


def validate_rows(rows, label="dataset"):
    if not rows:
        raise ValueError(f"{label}: no examples")
    seen = set()
    identifiers = set()
    for i, row in enumerate(rows, 1):
        location = f"{label} row {i}"
        if not isinstance(row, dict) or set(row) - {"prompt", "completion", "id", "group_id", "category"}:
            raise ValueError(f"{location}: expected a text-only prompt/completion object")
        prompt, completion = row.get("prompt"), row.get("completion")
        if not isinstance(prompt, list) or not prompt:
            raise ValueError(f"{location}: prompt must be a nonempty message list")
        if not isinstance(completion, list) or len(completion) != 1:
            raise ValueError(f"{location}: completion must contain one assistant message")
        for message in prompt + completion:
            if not isinstance(message, dict) or set(message) != {"role", "content"}:
                raise ValueError(f"{location}: messages must contain only role and content")
            if not isinstance(message["content"], str) or not message["content"].strip():
                raise ValueError(f"{location}: content must be a nonempty string")
        roles = [m["role"] for m in prompt]
        if roles[0] == "system":
            roles = roles[1:]
        if not roles or roles[-1] != "user" or any(
            role != ("user" if j % 2 == 0 else "assistant") for j, role in enumerate(roles)
        ) or completion[0]["role"] != "assistant":
            raise ValueError(f"{location}: optional system then alternating user/assistant; end prompt with user")
        if "<think>" in completion[0]["content"] or "</think>" in completion[0]["content"]:
            raise ValueError(f"{location}: this profile trains final answers, not reasoning traces")
        for key in ("id", "group_id", "category"):
            if key in row and (not isinstance(row[key], str) or not row[key].strip()):
                raise ValueError(f"{location}: {key} must be a nonempty string")
        if "id" in row:
            if row["id"] in identifiers:
                raise ValueError(f"{location}: duplicate example id")
            identifiers.add(row["id"])
        key = prompt_key(row)
        if key in seen:
            raise ValueError(f"{location}: duplicate normalized prompt (possibly conflicting targets)")
        seen.add(key)


def read_rows(path):
    rows = []
    for i, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{i}: invalid JSON") from exc
    validate_rows(rows, str(path))
    return rows


def assert_disjoint(train, evaluation):
    if {prompt_key(r) for r in train} & {prompt_key(r) for r in evaluation}:
        raise ValueError("Training/evaluation prompt overlap")
    for key in ("id", "group_id"):
        if {r[key] for r in train if key in r} & {r[key] for r in evaluation if key in r}:
            raise ValueError(f"Training/evaluation {key} overlap")


def split_rows(rows, fraction, seed):
    if not 0 < fraction < 1:
        raise ValueError("eval-fraction must be between 0 and 1")
    groups = {}
    for row in rows:
        groups.setdefault(row.get("group_id", prompt_key(row)), []).append(row)
    keys = sorted(groups)
    if len(keys) < 2:
        raise ValueError("Need at least two independent groups for a validation split")
    random.Random(seed).shuffle(keys)
    count = min(len(keys) - 1, max(1, math.ceil(len(keys) * fraction)))
    evaluation = [r for key in keys[:count] for r in groups[key]]
    train = [r for key in keys[count:] for r in groups[key]]
    assert_disjoint(train, evaluation)
    return train, evaluation


def load_splits(args):
    rows = read_rows(args.data)
    if args.eval_data:
        train, evaluation = rows, read_rows(args.eval_data)
        assert_disjoint(train, evaluation)
    else:
        train, evaluation = split_rows(rows, args.eval_fraction, args.seed)
    meta = {
        "train_file": Path(args.data).name, "train_sha256": sha256_file(args.data),
        "eval_file": Path(args.eval_data).name if args.eval_data else "grouped split",
        "eval_sha256": sha256_file(args.eval_data) if args.eval_data else None,
        "train_prompt_hashes": [prompt_key(r) for r in train],
        "eval_prompt_hashes": [prompt_key(r) for r in evaluation],
        "grouped_by": "group_id, otherwise normalized prompt hash",
    }
    return train, evaluation, meta


def tokenize_rows(rows, tokenizer, max_length):
    """Build masks from an exact token-prefix check; never truncate supervision."""
    encoded = []
    for i, row in enumerate(rows, 1):
        prefix = tokenizer.apply_chat_template(row["prompt"], tokenize=True,
                    add_generation_prompt=True, enable_thinking=False)
        full = tokenizer.apply_chat_template(row["prompt"] + row["completion"], tokenize=True,
                    add_generation_prompt=False, enable_thinking=False)
        if full[:len(prefix)] != prefix:
            raise ValueError(f"Row {i}: chat-template prompt is not an exact token prefix")
        if len(full) > max_length:
            raise ValueError(f"Row {i}: {len(full)} tokens exceeds max-length {max_length}; edit/split the example")
        if len(full) <= len(prefix):
            raise ValueError(f"Row {i}: no supervised completion tokens")
        if tokenizer.eos_token_id not in full[len(prefix):]:
            raise ValueError(f"Row {i}: completion has no EOS token")
        encoded.append({"input_ids": full, "attention_mask": [1] * len(full),
                        "completion_mask": [0] * len(prefix) + [1] * (len(full) - len(prefix))})
    return encoded


def require_commit(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ValueError("Expected an immutable 40-character Hub commit SHA")
    return value
