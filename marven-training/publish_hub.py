#!/usr/bin/env python3
"""Prepare an allowlisted Marven release; optionally commit to the existing private Hub repo."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from data_utils import require_commit, sha256_file
from train_marven_lora import DEFAULT_BASE_MODEL, DEFAULT_REVISION

ROOT = Path(__file__).resolve().parent
REPO_ID = "ahesh001/marven"
SOURCE_FILES = (
    "README.md", "requirements.txt", "train_marven_lora.py", "data_utils.py",
    "eval_behavior.py", "publish_hub.py", "infer_adapter.py", "data/public_seed_v1.jsonl", "data/README.md",
    "eval/behavior_v1.json", "eval/README.md", "tests/test_training.py", "tests/test_trainer_integration.py", "VALIDATION.md",
)
ADAPTER_FILES = {"adapter_config.json", "adapter_model.safetensors", "tokenizer.json", "tokenizer_config.json",
                 "special_tokens_map.json", "vocab.json", "merges.txt", "added_tokens.json", "chat_template.jinja",
                 "marven_training_manifest.json"}


def validate_adapter(folder):
    folder = Path(folder)
    for name in ("adapter_config.json", "adapter_model.safetensors", "marven_training_manifest.json"):
        if not (folder / name).is_file() or (folder / name).is_symlink():
            raise ValueError(f"Missing regular adapter artifact: {name}")
    manifest = json.loads((folder / "marven_training_manifest.json").read_text())
    config = json.loads((folder / "adapter_config.json").read_text())
    if manifest.get("status") != "trained_unpromoted" or not manifest.get("metrics", {}).get("train"):
        raise ValueError("Adapter needs a completed training manifest with metrics")
    require_commit(manifest.get("base_model_revision"))
    if config.get("peft_type") != "LORA" or config.get("base_model_name_or_path") != manifest.get("base_model"):
        raise ValueError("Adapter configuration does not match the training manifest")
    if config.get("revision") != manifest["base_model_revision"]:
        raise ValueError("Adapter base revision mismatch")
    for name in ("adapter_model.safetensors", "adapter_config.json"):
        if manifest.get("artifacts", {}).get(name) != sha256_file(folder / name):
            raise ValueError(f"Artifact hash mismatch: {name}")
    for name in ADAPTER_FILES - {"marven_training_manifest.json"}:
        if (folder / name).exists() and manifest.get("artifacts", {}).get(name) != sha256_file(folder / name):
            raise ValueError(f"Artifact hash mismatch: {name}")
    # Parse Safetensors, rather than trusting an extension or placeholder file.
    from safetensors import safe_open
    with safe_open(folder / "adapter_model.safetensors", framework="numpy") as tensors:
        keys = list(tensors.keys())
        if not keys or not any("lora_A" in k for k in keys) or not any("lora_B" in k for k in keys):
            raise ValueError("No LoRA A/B tensors found")
    return manifest


def prepare(output, adapter_dir=None):
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError("Package directory must be empty to prevent stale or private files being uploaded")
    manifest = validate_adapter(adapter_dir) if adapter_dir else None
    output.mkdir(parents=True, exist_ok=True)
    for name in SOURCE_FILES:
        source = ROOT / name
        if not source.is_file() or source.is_symlink():
            raise ValueError(f"Missing regular source file: {name}")
        target = output / "training" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    shutil.copyfile(ROOT / "hub/README.md", output / "README.md")
    shutil.copyfile(ROOT.parent / "LICENSE", output / "LICENSE")
    # Include the model card template so the staged package can be prepared again.
    (output / "training/hub").mkdir()
    shutil.copyfile(ROOT / "hub/README.md", output / "training/hub/README.md")
    if adapter_dir:
        for name in ADAPTER_FILES:
            source = Path(adapter_dir) / name
            if source.is_file():
                if source.is_symlink():
                    raise ValueError(f"Symlink not allowed: {name}")
                shutil.copyfile(source, output / name)
        card = (output / "README.md").read_text()
        card = card.replace("**Status: training recipe only. No trained adapter weights are included.**",
                            "**Status: trained candidate adapter, not promoted. See the training manifest; quality improvements remain unverified.**")
        card = card.replace(DEFAULT_BASE_MODEL, manifest["base_model"]).replace(DEFAULT_REVISION, manifest["base_model_revision"])
        (output / "README.md").write_text(card)
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL).strip()
        dirty = bool(subprocess.check_output(["git", "status", "--porcelain", "--", "."], cwd=ROOT, text=True))
    except (OSError, subprocess.CalledProcessError):
        commit, dirty = None, None
    status = {"schema_version": 1, "repo_id": REPO_ID, "expected_private": True,
        "status": "trained_unpromoted" if manifest else "training_recipe_only",
        "weights_present": bool(manifest), "quality_improvement_demonstrated": False,
        "base_model": manifest["base_model"] if manifest else DEFAULT_BASE_MODEL,
        "base_model_revision": manifest["base_model_revision"] if manifest else DEFAULT_REVISION,
        "source_repository": "https://github.com/ahesh001/Marven", "source_pr": 66,
        "source_commit": commit, "source_worktree_dirty": dirty,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "excluded": ["private memories", "private corpora", "credentials", "optimizer checkpoints", "base-model weights"]}
    (output / "release_status.json").write_text(json.dumps(status, indent=2) + "\n")
    hashes = {p.relative_to(output).as_posix(): sha256_file(p) for p in sorted(output.rglob("*")) if p.is_file()}
    (output / "checksums.json").write_text(json.dumps(hashes, indent=2) + "\n")
    return status


def upload(output, expected_head=None):
    from huggingface_hub import HfApi, CommitOperationAdd, hf_hub_download
    output = Path(output)
    api = HfApi()  # Uses HF_TOKEN or hf auth login. Never accepts/prints a token on the command line.
    identity = api.whoami()
    if identity.get("name") != "ahesh001":
        raise ValueError("Authenticated Hugging Face account is not ahesh001")
    info = api.model_info(REPO_ID)
    if info.private is not True:
        raise ValueError("Destination must already be private; visibility will not be changed automatically")
    remote = api.list_repo_files(REPO_ID)
    if not (output / "adapter_model.safetensors").exists() and any(
        name == "adapter_config.json" or ("/" not in name and name.endswith(".safetensors")) for name in remote
    ):
        raise ValueError("Destination already contains model files; inspect them before uploading a recipe-only card")
    files = sorted(p for p in output.rglob("*") if p.is_file())
    print(json.dumps({"repo_id": REPO_ID, "private": info.private, "head": info.sha,
        "files": [p.relative_to(output).as_posix() for p in files],
        "existing_files_to_update": [p.relative_to(output).as_posix() for p in files if p.relative_to(output).as_posix() in remote]}, indent=2))
    if not expected_head:
        print("Read-only preview. Review existing files; pass --expected-head with this SHA to commit.")
        return None
    require_commit(expected_head)
    if info.sha != expected_head:
        raise ValueError("Repository changed after review; inspect the new head before committing")
    expected = json.loads((output / "checksums.json").read_text())
    actual = {p.relative_to(output).as_posix(): sha256_file(p) for p in files if p.name != "checksums.json"}
    if actual != expected or any(p.is_symlink() for p in output.rglob("*")):
        raise ValueError("Package integrity check failed")
    commit = api.create_commit(repo_id=REPO_ID, repo_type="model", parent_commit=expected_head,
        commit_message="Add reproducible Marven training and evaluation package",
        operations=[CommitOperationAdd(path_in_repo=p.relative_to(output).as_posix(), path_or_fileobj=p) for p in files])
    verified = api.model_info(REPO_ID, revision=commit.oid)
    if verified.private is not True:
        raise RuntimeError("Post-upload privacy verification failed")
    for name, digest in expected.items():
        downloaded = hf_hub_download(REPO_ID, name, revision=commit.oid)
        if sha256_file(downloaded) != digest:
            raise RuntimeError(f"Remote checksum mismatch: {name}")
    print(json.dumps({"commit": commit.oid, "url": commit.commit_url, "private": True, "verified_files": len(expected)}))
    return commit.oid


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--adapter-dir", type=Path, help="Include only a real completed adapter")
    p.add_argument("--upload", action="store_true", help="Read-only remote preview unless expected-head is supplied")
    p.add_argument("--expected-head", help="Reviewed immutable remote SHA; authorizes one atomic commit")
    p.add_argument("--use-existing-package", action="store_true")
    args = p.parse_args()
    if args.use_existing_package and args.adapter_dir:
        p.error("adapter-dir is only used when preparing a new package")
    if args.expected_head and not args.upload:
        p.error("expected-head requires --upload")
    if not args.use_existing_package:
        print(json.dumps(prepare(args.output_dir, args.adapter_dir), indent=2))
    if args.upload:
        upload(args.output_dir, args.expected_head)


if __name__ == "__main__":
    main()
