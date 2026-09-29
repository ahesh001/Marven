---
language:
- en
base_model: Qwen/Qwen3-8B
license: mit
tags:
- marven
- training-recipe
- peft
- qlora
---
# Marven behavior adapter

**Status: training recipe only. No trained adapter weights are included.**

This private repository holds the reproducible training, evaluation, and release
information for Marven, Heshware's local-first AI collaborator. Check
`release_status.json` for the exact contents and `checksums.json` for file hashes.
The source changes belong to [Marven PR #66](https://github.com/ahesh001/Marven/pull/66).

## What is available

- A single-GPU QLoRA trainer with explicit completion masking and real-tokenizer preflight.
- Twelve public synthetic seed examples, for testing the pipeline only.
- Twelve separate public constrained regression checks and base/adapter comparison reports.
- Local 4-bit inference and a private-repository upload helper.
- Data, training, evaluation, reproducibility, and release guidance in [training/README.md](training/README.md).

No Marven quality improvement or production readiness is claimed by this package.
A recipe-only checkout has no GPU training results and cannot be loaded as a PEFT
adapter. A completed candidate, when present, records its run in the training manifest.

## Model and training profile

| Setting | Value |
| --- | --- |
| Base | `Qwen/Qwen3-8B` |
| Immutable base revision | `b968826d9c46dd6066d109eabc6255188de91218` |
| Method | QLoRA; 4-bit NF4 with double quantization |
| Adapter targets | `all-linear`, rank 16, alpha 32, dropout 0.05 |
| Starting sequence length | 1536; oversized examples are rejected |
| Batch / accumulation | 1 / 8 |
| Learning rate / epochs | 0.0001 / 3; experimental starting values |
| Loss | Completion only; prompt and padding masked |
| Output style | Final answer, `enable_thinking=False` |
| Validation | Group-aware split, overlap checks, evaluate/save each epoch |
| Checkpoint selection | Lowest validation loss; not a behavioral promotion decision |
| Execution | One CUDA GPU; RTX 5070 12 GB fit has not been measured |

The base model and the adapter must use the same revision. The base checkpoint
is downloaded separately and remains subject to its Apache-2.0 license. The MIT
license here covers the repository's code and documentation; no base weights
are redistributed by this recipe.

## Architectural boundary

The adapter is intended to learn response habits: clear collaboration, uncertainty,
formatting, grounded answers, and careful use of supplied evidence. Current facts,
user memories, permissions, provenance, corrections, and deletion remain external.
A trained response habit cannot enforce authorization or make a memory true;
the Marven orchestrator must enforce those boundaries.

## Creating a candidate

Run data validation, tokenizer preflight, and then GPU training as documented in
[training/README.md](training/README.md). A completed run produces
`adapter_model.safetensors`, `adapter_config.json`, tokenizer files, and
`marven_training_manifest.json`. Keep the run's dataset hashes, exact package
versions, model revision, template hash, and evaluation reports with that candidate.

Before promotion, compare the candidate with the unadapted base on the same
inputs and decoding settings; then review natural conversation, technical accuracy,
memory grounding, authorization, reasoning regressions, and measured resource use.
The public smoke suite is not a hidden benchmark or proof of safety.

## Limitations

The seed corpus has only 12 examples. Its automatic split has just two validation
examples and is too small to estimate general quality. The dataset needs substantial,
reviewed expansion; grouped splitting reduces known leakage but cannot discover all
paraphrase or semantic overlap. Training reproducibility is improved by pinned inputs
and recorded settings, but bitwise determinism across hardware is not guaranteed.
Fine-tuning may cause regressions and must be evaluated before deployment.

## References

- [Qwen3-8B model card](https://huggingface.co/Qwen/Qwen3-8B)
- [TRL 0.24 SFT trainer](https://huggingface.co/docs/trl/v0.24.0/sft_trainer)
- [PEFT quantization](https://huggingface.co/docs/peft/developer_guides/quantization)
- [Hub uploads](https://huggingface.co/docs/huggingface_hub/guides/upload)
