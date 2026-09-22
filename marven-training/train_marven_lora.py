#!/usr/bin/env python3
"""Train a Marven LoRA/QLoRA adapter with reproducible metadata.

The adapter is for stable Marven behavior and response style. Durable user
memory, permissions, provenance, and changing facts stay in Marven's external
orchestrator and canonical memory store.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any

import torch
from datasets import Dataset, load_dataset
from peft import LoraConfig, prepare_model_for_kbit_training
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
from trl import SFTConfig, SFTTrainer


DEFAULT_BASE_MODEL = "Qwen/Qwen3-8B"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train Marven's behavior adapter.")
    parser.add_argument("--base-model", default=DEFAULT_BASE_MODEL)
    parser.add_argument("--data", default="marven-training/data/public_seed_v1.jsonl")
    parser.add_argument("--eval-data", default=None)
    parser.add_argument("--output-dir", default="marven-training/out/marven-qwen3-8b-lora-v0.1")
    parser.add_argument("--max-length", type=int, default=2048)
    parser.add_argument("--epochs", type=float, default=3.0)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--gradient-accumulation", type=int, default=8)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--eval-fraction", type=float, default=0.10)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--lora-r", type=int, default=16)
    parser.add_argument("--lora-alpha", type=int, default=32)
    parser.add_argument("--lora-dropout", type=float, default=0.05)
    parser.add_argument("--save-steps", type=int, default=25)
    parser.add_argument("--eval-steps", type=int, default=25)
    parser.add_argument("--no-4bit", action="store_true")
    parser.add_argument("--no-gradient-checkpointing", action="store_true")
    return parser.parse_args()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def package_version(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return "unknown"


def validate_rows(dataset: Dataset, label: str) -> None:
    if len(dataset) < 2:
        raise ValueError(f"{label} must contain at least 2 examples.")

    required_roles = {"system", "user", "assistant"}
    for idx, row in enumerate(dataset):
        prompt = row.get("prompt")
        completion = row.get("completion")
        if not isinstance(prompt, list) or not prompt:
            raise ValueError(f"{label} row {idx}: 'prompt' must be a non-empty message list.")
        if not isinstance(completion, list) or len(completion) != 1:
            raise ValueError(f"{label} row {idx}: 'completion' must contain exactly one assistant message.")

        prompt_roles = {str(message.get("role", "")) for message in prompt if isinstance(message, dict)}
        completion_role = str(completion[0].get("role", "")) if isinstance(completion[0], dict) else ""
        if "user" not in prompt_roles:
            raise ValueError(f"{label} row {idx}: prompt must contain a user message.")
        if not prompt_roles.issubset(required_roles):
            raise ValueError(f"{label} row {idx}: unsupported prompt role.")
        if completion_role != "assistant":
            raise ValueError(f"{label} row {idx}: completion role must be 'assistant'.")

        for message in [*prompt, *completion]:
            if not isinstance(message, dict) or not str(message.get("content", "")).strip():
                raise ValueError(f"{label} row {idx}: every message needs non-empty content.")


def load_splits(args: argparse.Namespace) -> tuple[Dataset, Dataset, dict[str, Any]]:
    data_path = Path(args.data)
    if not data_path.exists():
        raise FileNotFoundError(f"Training data not found: {data_path}")

    dataset = load_dataset("json", data_files=str(data_path), split="train")
    validate_rows(dataset, "training dataset")

    source_meta: dict[str, Any] = {
        "train_source": str(data_path),
        "train_sha256": sha256_file(data_path),
    }

    if args.eval_data:
        eval_path = Path(args.eval_data)
        if not eval_path.exists():
            raise FileNotFoundError(f"Evaluation data not found: {eval_path}")
        eval_dataset = load_dataset("json", data_files=str(eval_path), split="train")
        validate_rows(eval_dataset, "evaluation dataset")
        train_dataset = dataset
        source_meta.update(
            {
                "eval_source": str(eval_path),
                "eval_sha256": sha256_file(eval_path),
            }
        )
    else:
        if len(dataset) < 10:
            raise ValueError(
                "Need at least 10 examples to create a train/eval split. "
                "Provide --eval-data for a smaller smoke-test dataset."
            )
        split = dataset.train_test_split(test_size=args.eval_fraction, seed=args.seed, shuffle=True)
        train_dataset = split["train"]
        eval_dataset = split["test"]
        source_meta["eval_source"] = "deterministic split from training data"

    return train_dataset, eval_dataset, source_meta


def choose_dtype() -> torch.dtype:
    if torch.cuda.is_available() and torch.cuda.is_bf16_supported():
        return torch.bfloat16
    if torch.cuda.is_available():
        return torch.float16
    return torch.float32


def build_model_and_tokenizer(args: argparse.Namespace):
    if not torch.cuda.is_available():
        raise RuntimeError(
            "CUDA is required for this training profile. "
            "Run on the NVIDIA workstation or use a separate CPU/cloud profile."
        )

    dtype = choose_dtype()
    use_4bit = not args.no_4bit

    tokenizer = AutoTokenizer.from_pretrained(args.base_model, use_fast=True)
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "right"

    model_kwargs: dict[str, Any] = {
        "device_map": "auto",
        "torch_dtype": dtype,
    }

    if use_4bit:
        model_kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=dtype if dtype != torch.float32 else torch.float16,
        )

    model = AutoModelForCausalLM.from_pretrained(args.base_model, **model_kwargs)
    model.config.use_cache = False

    if use_4bit:
        model = prepare_model_for_kbit_training(
            model,
            use_gradient_checkpointing=not args.no_gradient_checkpointing,
        )

    return model, tokenizer, dtype, use_4bit


def main() -> None:
    args = parse_args()
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    train_dataset, eval_dataset, source_meta = load_splits(args)
    model, tokenizer, dtype, use_4bit = build_model_and_tokenizer(args)

    peft_config = LoraConfig(
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules="all-linear",
    )

    use_bf16 = dtype == torch.bfloat16
    use_fp16 = dtype == torch.float16

    training_args = SFTConfig(
        output_dir=str(output_dir),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=1,
        gradient_accumulation_steps=args.gradient_accumulation,
        learning_rate=args.learning_rate,
        lr_scheduler_type="cosine",
        warmup_ratio=0.03,
        logging_steps=5,
        save_strategy="steps",
        save_steps=args.save_steps,
        save_total_limit=2,
        eval_strategy="steps",
        eval_steps=args.eval_steps,
        bf16=use_bf16,
        fp16=use_fp16,
        gradient_checkpointing=not args.no_gradient_checkpointing,
        optim="paged_adamw_8bit" if use_4bit else "adamw_torch",
        report_to="none",
        seed=args.seed,
        data_seed=args.seed,
        max_length=args.max_length,
        completion_only_loss=True,
        packing=False,
    )

    trainer = SFTTrainer(
        model=model,
        args=training_args,
        train_dataset=train_dataset,
        eval_dataset=eval_dataset,
        processing_class=tokenizer,
        peft_config=peft_config,
    )

    print("==== Marven training configuration ====")
    print(f"Base model: {args.base_model}")
    print(f"Train examples: {len(train_dataset)}")
    print(f"Eval examples: {len(eval_dataset)}")
    print(f"QLoRA 4-bit: {use_4bit}")
    print(f"Compute dtype: {dtype}")
    print(f"LoRA rank: {args.lora_r}")
    print("Target modules: all-linear")
    if torch.cuda.is_available():
        print(f"GPU: {torch.cuda.get_device_name(torch.cuda.current_device())}")
    print("=======================================")

    train_result = trainer.train()
    eval_metrics = trainer.evaluate()

    final_dir = output_dir / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    trainer.model.save_pretrained(final_dir, safe_serialization=True)
    tokenizer.save_pretrained(final_dir)

    trainer.save_metrics("train", train_result.metrics)
    trainer.save_metrics("eval", eval_metrics)
    trainer.save_state()

    manifest = {
        "format_version": 1,
        "adapter_name": output_dir.name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "Stable Marven behavior/personality adapter; no durable user memory.",
        "base_model": args.base_model,
        "adapter_path": str(final_dir),
        "dataset": source_meta,
        "examples": {
            "train": len(train_dataset),
            "eval": len(eval_dataset),
        },
        "training": {
            "epochs": args.epochs,
            "batch_size": args.batch_size,
            "gradient_accumulation": args.gradient_accumulation,
            "learning_rate": args.learning_rate,
            "max_length": args.max_length,
            "seed": args.seed,
            "qlora_4bit": use_4bit,
            "quant_type": "nf4" if use_4bit else None,
            "lora_r": args.lora_r,
            "lora_alpha": args.lora_alpha,
            "lora_dropout": args.lora_dropout,
            "target_modules": "all-linear",
            "completion_only_loss": True,
        },
        "environment": {
            "python": os.sys.version.split()[0],
            "torch": torch.__version__,
            "transformers": package_version("transformers"),
            "trl": package_version("trl"),
            "peft": package_version("peft"),
            "datasets": package_version("datasets"),
            "bitsandbytes": package_version("bitsandbytes"),
            "gpu": torch.cuda.get_device_name(torch.cuda.current_device()) if torch.cuda.is_available() else None,
        },
        "metrics": {
            "train": train_result.metrics,
            "eval": eval_metrics,
        },
    }

    (final_dir / "marven_training_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    print(f"Saved Marven adapter to: {final_dir}")
    print("Keep the base model name and manifest with the adapter for reproducibility.")


if __name__ == "__main__":
    main()
