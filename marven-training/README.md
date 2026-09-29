# Marven model training and private Hub release

This package produces a **candidate behavior adapter**, separate from Marven's
canonical memory and permission system. This training directory contains source;
the Hub root's `release_status.json` states whether actual weights are included.
The original 12-example public seed dataset is for pipeline validation only.

## What changed after the first training draft

- Pin the exact base-model revision, seed before LoRA initialization, and record code/template/data hashes.
- Validate text types, roles, duplicate questions, and train/evaluation overlap; keep `group_id` families together.
- Use the actual tokenizer to verify prompt-prefix masking, EOS, and sequence length; reject truncation.
- Align final-answer training and inference with Qwen3's `enable_thinking=False`.
- Keep the whole training model on one selected GPU instead of inference-style automatic offloading.
- Save/evaluate each epoch, keep the best validation-loss checkpoint, and preserve prior output directories.
- Replace ambiguous keyword evaluation with exact JSON contracts, error handling, and base/candidate comparisons.
- Package only approved public source files; verify actual Safetensors before packaging a future adapter.
- Preview and atomically upload to the existing private `ahesh001/marven` repository; verify uploaded hashes.

These are reliability improvements. No before/after model-quality claim has been measured.

## Environment and hardware

Use Python 3.11 in a dedicated environment on Linux or WSL2 with an NVIDIA driver.
The commands below use **Bash**, not PowerShell. From this directory (named
`marven-training` in GitHub, or `training` in the Hub package):

```bash
python3.11 -m venv .venv-training
source .venv-training/bin/activate
python -m pip install --upgrade pip
# Install a supported CUDA build first. This profile uses the PyTorch 2.x API.
python -m pip install 'torch==2.8.0' --index-url https://download.pytorch.org/whl/cu128
python -m pip install -r requirements.txt
python -m pip check
python -c "import torch; print(torch.__version__, torch.version.cuda, torch.cuda.is_available()); print(torch.cuda.get_device_name(0))"
```

PyTorch 2.7 introduced Blackwell support with CUDA 12.8 wheels; older generic
`torch>=2.4` guidance was insufficient for an RTX 5070. See the
[official announcement](https://pytorch.org/blog/pytorch-2-7/).
The 12 GB GPU is an intended experiment target, **not a verified fit guarantee**.
Begin at length 1536/batch 1. If GPU memory is insufficient, try 1024 or rank 8,
close other GPU consumers, or select a smaller base with its exact revision.
Do not silently swap in a quantized base from a different model or revision.

## Data

Each JSONL record contains a `prompt` message list and exactly one assistant
`completion`. An optional first system message may precede alternating user and
assistant turns; the prompt must end in a user message. Only string content is
supported by this profile. Tool payloads need a separately designed schema.

Optional `id`, `group_id`, and `category` describe the example. Give paraphrases,
variants from the same source, and episodes the same `group_id`. Explicit train
and validation files must also have disjoint IDs, groups, and normalized prompts.
This detects exact/normalized overlap, not all semantic duplicates.

See [data/README.md](data/README.md) for corpus scope and expansion criteria.
Do not train credentials, private conversations, canonical memories, current
project state, or changing user preferences into this adapter.

## Validate before training

```bash
python train_marven_lora.py --validate-only
python train_marven_lora.py --preflight-tokenizer
python -m unittest discover -s tests -v
```

The first command needs only Python. The second downloads the pinned tokenizer,
not model weights, and checks completion masks and lengths. It stops before training.
The trainer also verifies the real TRL collator's labels before taking any optimizer step.

An optional software integration check downloads only the pinned tokenizer and
runs one CPU optimizer step on a tiny randomly initialized Qwen3 architecture:

```bash
MARVEN_RUN_INTEGRATION=1 python -m unittest discover -s tests -p test_trainer_integration.py -v
```

It checks masking, trainable LoRA updates, and Safetensors serialization. Its
temporary weights are deleted and are not a Marven candidate or a quality test.

## Train on your GPU

```bash
python train_marven_lora.py --max-length 1536 --output-dir out/marven-qwen3-8b-run-001
```

For reviewed data, pass `--data path/to/train.jsonl --eval-data path/to/validation.jsonl`.
Keep an additional unseen test set separate from both training and validation.
Validation is used for checkpoint selection, so it cannot be the final unbiased test.
For a different base, supply `--base-model OWNER/MODEL --revision IMMUTABLE_40_CHAR_SHA`.

The completed candidate is in `out/marven-qwen3-8b-run-001/final/`:

- `adapter_model.safetensors` and `adapter_config.json`;
- tokenizer assets;
- `marven_training_manifest.json`, including real training metrics and artifact hashes.

`run_manifest.json` is also written before training, so an interrupted run is
not confused with a completed candidate. All completed adapters remain
`trained_unpromoted`; lower validation loss does not demonstrate improved behavior.
A seed run has very few optimizer updates and is only a pipeline experiment.

## Local 4-bit inference

```bash
python infer_adapter.py --adapter-dir out/marven-qwen3-8b-run-001/final --prompt 'Help me plan one measurable prototype test.'
```

This uses the base revision recorded in the manifest and the saved tokenizer.
It needs a GPU and only works after actual training. It has no connection to
canonical memory or tools by itself; the Marven runtime supplies those separately.

## vLLM and comparison

Use a separate serving environment. On hardware with enough VRAM for the full
base plus adapter and KV cache, a starting command is:

```bash
vllm serve Qwen/Qwen3-8B \
  --revision b968826d9c46dd6066d109eabc6255188de91218 \
  --port 8001 --max-model-len 1536 --enable-lora --max-lora-rank 16 \
  --lora-modules marven-qwen3-8b=out/marven-qwen3-8b-run-001/final
```

This full-precision command **does not fit an 8B base into 12 GB**; use the local
4-bit inference route above for the first workstation test. A quantized vLLM
configuration needs separate compatibility and memory validation.

The evaluation client passes `chat_template_kwargs={"enable_thinking": false}`.
The production client must pass the same setting. Before serving a custom base,
update both the base name and revision in the launch command.

```bash
python eval_behavior.py --model marven-qwen3-8b --baseline-model Qwen/Qwen3-8B --output out/behavior-comparison-001.json
```

If the server requires authentication, put its credential in
`MARVEN_EVAL_API_KEY`; it is never written to the report. The comparison uses
identical public cases, seed, and decoding parameters. See [eval/README.md](eval/README.md).

The existing Marven server can select `marven-*` through its vLLM path using
`VLLM_MODELS=marven-qwen3-8b` and `VLLM_BASE_URL=http://127.0.0.1:8001/v1`.
Its request construction still needs the matching non-thinking option verified
before adopting this adapter. No runtime deployment is implied by this package.

## Prepare and upload the private Hub package

Preparation is offline and uses a fixed file allowlist, excluding private corpora,
memories, credentials, checkpoints, and base weights:

```bash
python publish_hub.py --output-dir out/hub-recipe-001
```

Only after a completed GPU run, include real adapter files:

```bash
python publish_hub.py --adapter-dir out/marven-qwen3-8b-run-001/final --output-dir out/hub-candidate-001
```

Authenticate locally with `hf auth login`, or an appropriately scoped `HF_TOKEN`.
Never put a token in chat, source code, a model card, or a command-line argument.
Preview the existing private repository, review any same-name files, then use its
reported SHA for one atomic commit:

```bash
python publish_hub.py --output-dir out/hub-recipe-001 --use-existing-package --upload
python publish_hub.py --output-dir out/hub-recipe-001 --use-existing-package --upload --expected-head REVIEWED_40_CHAR_SHA
```

The helper targets only the already-existing private `ahesh001/marven`, preserves
unrelated files, refuses a recipe-only overwrite of an existing adapter, uses a
parent-commit guard, and verifies content hashes at the returned commit. It never
makes the repository public or creates a replacement repository.

## Release decision

Record a base-versus-adapter comparison, natural-language human review, independent
reasoning regressions, retrieval-grounded tasks, prompt-injection tests, and measured
latency/VRAM. Include source and dataset provenance. Version each candidate; preserve
a known-good adapter for rollback. Do not put rejected responses into ordinary SFT
completion targets; preference training requires a separate dataset and trainer.
