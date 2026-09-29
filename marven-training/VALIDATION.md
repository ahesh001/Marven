# Validation record — 2026-09-29 UTC

This record concerns software and the release package, not the quality of trained
Marven weights. No Qwen3-8B GPU fine-tuning was performed, and no Marven adapter is
included in the recipe package.

## Completed

- 15 dependency-free tests: schema rejection, normalized duplicates, split leakage,
  grouped partitioning, prompt masking, truncation rejection, immutable revision,
  strict JSON grading, positive permission behavior, package integrity, private
  destination enforcement, existing-adapter protection, and remote-head conflict.
- Real tokenizer preflight for all 12 seed examples at Qwen3-8B revision
  `b968826d9c46dd6066d109eabc6255188de91218`: 10 training / 2 validation examples;
  maximum sequence length 146; 447 training and 90 validation supervised tokens.
- One opt-in CPU integration test with a tiny randomly initialized Qwen3 model:
  real TRL collator masks, one optimizer step, nonzero LoRA updates, best-checkpoint
  reload, and valid Safetensors serialization. Temporary test weights were deleted.
- Python compilation, whitespace validation, and package dependency consistency.

CPU check environment: Python 3.12, torch 2.8.0+cpu, transformers 4.57.1,
TRL 0.24.0, PEFT 0.17.1, datasets 4.8.5, accelerate 1.15.0,
safetensors 0.6.2, huggingface-hub 0.36.2. Python 3.11 is the documented
workstation setup; it was not exercised in this environment.

## Still to measure

- RTX 5070 CUDA/Blackwell and bitsandbytes compatibility, actual VRAM, and throughput.
- A completed Qwen3-8B adapter training run on reviewed data.
- Base-versus-adapter smoke outputs, unseen evaluation, and human preference review.
- vLLM/runtime integration and actual deployment behavior.
- Hugging Face upload/privacy/content verification after authenticated access is available.

The 12 regression cases are prepared, but no live model responses were scored.
Their expected JSON fixtures were checked as part of the evaluator code tests.
