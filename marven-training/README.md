# Marven model training

This directory produces a **behavior adapter** for Marven. It is intentionally
separate from Marven's durable memory.

## What belongs in the weights

Good training targets are stable behaviors:

- Marven's clear, grounded, collaborative response style;
- honesty about uncertainty and missing memory;
- distinction between retrieval relevance and truth;
- respect for permission/tool boundaries;
- response-format discipline;
- constructive reflection without pretending to be conscious or clinically
  diagnosing a user;
- preference for measurable baselines before adding complexity.

Do **not** train private conversations, passwords, changing user preferences,
current project state, or canonical memories into the adapter. Those belong in
the governed memory system where they can be corrected, superseded, scoped, or
deleted.

## Recommended first base model

The default training profile uses:

`Qwen/Qwen3-8B`

The base model is configurable with `--base-model`. Treat the base model and
adapter as a coupled pair: a LoRA adapter should only be loaded on the same
base-model family/revision it was trained against.

The provided defaults target a single NVIDIA GPU with 4-bit QLoRA. On a
12 GB-class card, keep batch size at 1 and use gradient accumulation. If memory
pressure remains high, lower `--max-length` before lowering LoRA rank.

## Environment

Create a dedicated Python 3.11 environment. On Windows PowerShell:

```powershell
py -3.11 -m venv .venv-training
.\.venv-training\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r marven-training/requirements.txt
```

Install a CUDA-compatible PyTorch build appropriate for the workstation if the
generic package does not provide one.

## Dataset format

Each JSONL row uses conversational prompt/completion fields:

```json
{
  "prompt": [
    {"role": "system", "content": "stable Marven training instruction"},
    {"role": "user", "content": "user message"}
  ],
  "completion": [
    {"role": "assistant", "content": "ideal Marven response"}
  ]
}
```

The trainer uses completion-only loss, so user/system prompt tokens are context
rather than prediction targets.

`data/public_seed_v1.jsonl` is a deliberately small, non-private starter set.
It is enough to validate the pipeline, **not enough to call the resulting
adapter production-ready**.

For a serious v1 adapter, expand to several hundred carefully reviewed examples
covering ordinary conversation, technical work, uncertainty, memory behavior,
format adherence, refusals/redirects, contradiction handling, tool boundaries,
and adversarial prompts. Prefer fewer high-quality examples over large amounts
of synthetic repetition.

## Train

From the repository root:

```powershell
python marven-training/train_marven_lora.py
```

Useful overrides:

```powershell
python marven-training/train_marven_lora.py \
  --base-model Qwen/Qwen3-8B \
  --data marven-training/data/public_seed_v1.jsonl \
  --output-dir marven-training/out/marven-qwen3-8b-lora-v0.1 \
  --max-length 1536 \
  --batch-size 1 \
  --gradient-accumulation 8
```

The final adapter is written under `<output-dir>/final/` with
`marven_training_manifest.json`, which records dataset hashes, base model,
hyperparameters, package versions, hardware, and metrics.

Generated adapters and private JSONL files should remain local and uncommitted.

## Serve through vLLM

Marven already has an OpenAI-compatible vLLM path. A typical server launch is:

```powershell
vllm serve Qwen/Qwen3-8B \
  --enable-lora \
  --lora-modules marven-qwen3-8b=marven-training/out/marven-qwen3-8b-lora-v0.1/final
```

Then expose the adapter to Marven:

```powershell
$env:VLLM_MODELS="marven-qwen3-8b"
$env:VLLM_BASE_URL="http://127.0.0.1:8001/v1"
python server.py
```

Because Marven's server treats names beginning with `marven-` as vLLM models,
the adapter can be selected without merging it permanently into the base model.

## Smoke evaluation

With vLLM running:

```powershell
python marven-training/eval_behavior.py --model marven-qwen3-8b
```

The smoke checks are intentionally small. Before promoting a new adapter, also
evaluate:

1. held-out Marven conversation tasks;
2. memory abstention and stale/superseded-memory cases;
3. tool/prompt-injection boundary cases;
4. strict JSON/code/structured output tasks;
5. general reasoning regression against the unadapted base model;
6. latency and VRAM impact;
7. a blinded human preference set where the evaluator does not know which model
   generated which response.

Do not promote an adapter only because training loss decreased.

## Versioning

Suggested naming:

`marven-<base>-behavior-vMAJOR.MINOR`

Increment the dataset version separately. Never overwrite the only copy of a
known-good adapter. Keep:

- adapter weights;
- training manifest;
- immutable evaluation set;
- evaluation results;
- base-model revision;
- release notes describing intended behavior changes.

Model weights are one component of Marven. Canonical memory, retrieval,
permissions, audit, voice, presence, and tools remain external system layers.
