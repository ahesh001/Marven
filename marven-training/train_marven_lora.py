#!/usr/bin/env python3
"""Train a text-only, non-thinking Marven behavior adapter on one CUDA GPU."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import warnings
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

from data_utils import load_splits, require_commit, sha256_file, tokenize_rows

ROOT = Path(__file__).resolve().parent
DEFAULT_BASE_MODEL = "Qwen/Qwen3-8B"
DEFAULT_REVISION = "b968826d9c46dd6066d109eabc6255188de91218"


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base-model", default=DEFAULT_BASE_MODEL)
    p.add_argument("--revision", help="Immutable base-model SHA; required for a different base model")
    p.add_argument("--data", default=str(ROOT / "data/public_seed_v1.jsonl"))
    p.add_argument("--eval-data")
    p.add_argument("--output-dir", default=str(ROOT / "out/marven-qwen3-8b-lora-v0.1"))
    p.add_argument("--max-length", type=int, default=1536)
    p.add_argument("--epochs", type=float, default=3.0)
    p.add_argument("--batch-size", type=int, default=1)
    p.add_argument("--gradient-accumulation", type=int, default=8)
    p.add_argument("--learning-rate", type=float, default=1e-4)
    p.add_argument("--eval-fraction", type=float, default=0.10)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--lora-r", type=int, default=16)
    p.add_argument("--lora-alpha", type=int, default=32)
    p.add_argument("--lora-dropout", type=float, default=0.05)
    p.add_argument("--no-4bit", action="store_true")
    p.add_argument("--no-gradient-checkpointing", action="store_true")
    p.add_argument("--validate-only", action="store_true", help="Validate data without ML packages or a GPU")
    p.add_argument("--preflight-tokenizer", action="store_true", help="Also check the real tokenizer; no model weights or GPU")
    args = p.parse_args(argv)
    args.revision = args.revision or (DEFAULT_REVISION if args.base_model == DEFAULT_BASE_MODEL else None)
    require_commit(args.revision)
    for key in ("max_length", "epochs", "batch_size", "gradient_accumulation", "learning_rate", "lora_r", "lora_alpha"):
        if getattr(args, key) <= 0:
            p.error(f"{key} must be positive")
    if not 0 <= args.lora_dropout < 1:
        p.error("lora-dropout must be in [0, 1)")
    if not 0 < args.eval_fraction < 1:
        p.error("eval-fraction must be in (0, 1)")
    return args


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main():
    args = parse_args()
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    train_rows, eval_rows, source_meta = load_splits(args)
    summary = {"train_examples": len(train_rows), "validation_examples": len(eval_rows), "data": source_meta}
    print(json.dumps(summary, indent=2))
    if len(train_rows) < 100:
        warnings.warn("Small dataset: use for pipeline experiments only; no quality claim or promotion is justified.")
    if args.validate_only and not args.preflight_tokenizer:
        return

    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(args.base_model, revision=args.revision, trust_remote_code=False)
    if not tokenizer.chat_template or tokenizer.eos_token_id is None:
        raise ValueError("A chat template and EOS token are required")
    if tokenizer.pad_token_id is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "right"
    train_tokens = tokenize_rows(train_rows, tokenizer, args.max_length)
    eval_tokens = tokenize_rows(eval_rows, tokenizer, args.max_length)
    summary["tokenization"] = {
        "maximum_length": max(len(r["input_ids"]) for r in train_tokens + eval_tokens),
        "train_supervised_tokens": sum(sum(r["completion_mask"]) for r in train_tokens),
        "validation_supervised_tokens": sum(sum(r["completion_mask"]) for r in eval_tokens),
        "enable_thinking": False, "truncation": "reject",
    }
    print(json.dumps(summary["tokenization"], indent=2))
    if args.preflight_tokenizer:
        return

    import torch
    from datasets import Dataset
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import AutoModelForCausalLM, BitsAndBytesConfig, set_seed
    from trl import SFTConfig, SFTTrainer

    if not torch.cuda.is_available():
        raise RuntimeError("This profile needs a CUDA GPU; no training has occurred")
    if int(os.environ.get("WORLD_SIZE", "1")) != 1:
        raise RuntimeError("This profile supports one process/GPU only; select it with CUDA_VISIBLE_DEVICES")
    output = Path(args.output_dir)
    if output.exists() and any(output.iterdir()):
        raise ValueError("Output directory is not empty; choose a new run directory to preserve earlier weights")
    output.mkdir(parents=True, exist_ok=True)
    set_seed(args.seed)  # Before model and LoRA initialization, not only Trainer construction.
    device = torch.cuda.current_device()
    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    use_4bit = not args.no_4bit
    checkpointing = not args.no_gradient_checkpointing
    model_kwargs = dict(revision=args.revision, trust_remote_code=False,
                        device_map={"": device}, torch_dtype=dtype, attn_implementation="sdpa")
    if use_4bit:
        model_kwargs["quantization_config"] = BitsAndBytesConfig(load_in_4bit=True,
            bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=dtype)
    model = AutoModelForCausalLM.from_pretrained(args.base_model, **model_kwargs)
    model.config.use_cache = False
    if use_4bit:
        model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=checkpointing,
                                                gradient_checkpointing_kwargs={"use_reentrant": False})
    peft_config = LoraConfig(r=args.lora_r, lora_alpha=args.lora_alpha, lora_dropout=args.lora_dropout,
                            bias="none", task_type="CAUSAL_LM", target_modules="all-linear",
                            base_model_name_or_path=args.base_model, revision=args.revision)
    model = get_peft_model(model, peft_config)
    model.print_trainable_parameters()
    config = SFTConfig(output_dir=str(output), num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size, per_device_eval_batch_size=1,
        gradient_accumulation_steps=args.gradient_accumulation, learning_rate=args.learning_rate,
        lr_scheduler_type="cosine", warmup_ratio=0.03, logging_steps=1,
        save_strategy="epoch", eval_strategy="epoch", save_total_limit=2,
        load_best_model_at_end=True, metric_for_best_model="eval_loss", greater_is_better=False,
        bf16=dtype == torch.bfloat16, fp16=dtype == torch.float16,
        gradient_checkpointing=checkpointing, gradient_checkpointing_kwargs={"use_reentrant": False},
        optim="paged_adamw_8bit" if use_4bit else "adamw_torch", report_to="none",
        seed=args.seed, data_seed=args.seed, max_length=args.max_length,
        completion_only_loss=True, packing=False)
    trainer = SFTTrainer(model=model, args=config, train_dataset=Dataset.from_list(train_tokens),
                         eval_dataset=Dataset.from_list(eval_tokens), processing_class=tokenizer)
    # Assert the *actual collator* masks the prompt and supervises the completion.
    batch = trainer.data_collator([trainer.train_dataset[0]])
    first = train_tokens[0]
    labels = batch["labels"][0].tolist()[:len(first["input_ids"])]
    expected = [token if mask else -100 for token, mask in zip(first["input_ids"], first["completion_mask"])]
    if labels != expected:
        raise RuntimeError("Completion-only loss check failed; refusing to train")

    manifest = {"format_version": 2, "status": "training", "base_model": args.base_model,
        "base_model_revision": args.revision, "dataset": source_meta,
        "examples": {"train": len(train_rows), "validation": len(eval_rows)},
        "tokenization": summary["tokenization"],
        "chat_template_sha256": hashlib.sha256(tokenizer.chat_template.encode()).hexdigest(),
        "source_files": {name: sha256_file(ROOT / name) for name in ("train_marven_lora.py", "data_utils.py", "requirements.txt")},
        "training": {k: v for k, v in vars(args).items() if k not in ("data", "eval_data", "output_dir")},
        "lora": {"target_modules": "all-linear", "rank": args.lora_r, "alpha": args.lora_alpha},
        "environment": {"python": os.sys.version.split()[0], "cuda": torch.version.cuda,
            "gpu": torch.cuda.get_device_name(device),
            "packages": {name: metadata.version(name) for name in
                         ("torch", "transformers", "peft", "trl", "datasets", "accelerate", "safetensors", "huggingface-hub")}},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "promotion_status": "not_evaluated", "quality_improvement_demonstrated": False}
    if use_4bit:
        manifest["environment"]["packages"]["bitsandbytes"] = metadata.version("bitsandbytes")
    write_json(output / "run_manifest.json", manifest)
    result = trainer.train()
    validation = trainer.evaluate()
    final = output / "final"
    trainer.model.save_pretrained(final, safe_serialization=True)
    tokenizer.save_pretrained(final)
    trainer.save_metrics("train", result.metrics)
    trainer.save_metrics("eval", validation)
    trainer.save_state()
    manifest.update(status="trained_unpromoted", metrics={"train": result.metrics, "validation": validation},
                    best_validation_loss=trainer.state.best_metric,
                    completed_at=datetime.now(timezone.utc).isoformat(),
                    artifacts={p.name: sha256_file(p) for p in final.iterdir() if p.is_file()})
    write_json(final / "marven_training_manifest.json", manifest)
    write_json(output / "run_manifest.json", manifest)
    print(f"Saved candidate adapter: {final}. Run held-out base/adapter comparisons before promotion.")


if __name__ == "__main__":
    main()
