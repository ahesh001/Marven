# train_marven_lora.py
#
# Fine-tune a base model with LoRA to create a "Marven" adapter.
# Requires: torch (CUDA), transformers, datasets, peft, accelerate, bitsandbytes (optional for 4-bit).

import os
from dataclasses import dataclass, field
from typing import Dict, Any

import torch
from datasets import load_dataset
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    TrainingArguments,
    Trainer,
)
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training

# =========================
# CONFIG
# =========================

@dataclass
class MarvenConfig:
    # Base HF model (change if you want a different one)
    BASE_MODEL_NAME: str = "mistralai/Mistral-7B-Instruct-v0.3"

    # Path to your JSONL training file
    DATA_PATH: str = "data/marven_train.jsonl"

    # Where to save the LoRA adapter
    OUTPUT_DIR: str = "out/marven-lora"

    # Training hyperparams (tweak later)
    NUM_EPOCHS: int = 3
    BATCH_SIZE: int = 2
    GRADIENT_ACCUMULATION_STEPS: int = 4
    LEARNING_RATE: float = 2e-4
    MAX_SEQ_LEN: int = 2048

    # LoRA config
    LORA_R: int = 16
    LORA_ALPHA: int = 32
    LORA_DROPOUT: float = 0.05

    # Use 4-bit quantization (requires bitsandbytes and GPU with enough VRAM)
    USE_4BIT: bool = True

cfg = MarvenConfig()

os.makedirs(cfg.OUTPUT_DIR, exist_ok=True)


# =========================
# UTILITIES
# =========================

def print_device_info():
    print("==== Device Info ====")
    print("torch.cuda.is_available():", torch.cuda.is_available())
    if torch.cuda.is_available():
        print("CUDA device count:", torch.cuda.device_count())
        print("Current device:", torch.cuda.current_device())
        print("Device name:", torch.cuda.get_device_name(torch.cuda.current_device()))
    print("=====================")


def format_example(example: Dict[str, Any]) -> Dict[str, str]:
    """
    Convert {instruction, output} into a single text string.
    You can change the prompt style here later if you want.
    """
    instruction = example["instruction"].strip()
    output = example["output"].strip()

    text = (
        "### Instruction:\n"
        f"{instruction}\n\n"
        "### Response:\n"
        f"{output}"
    )

    return {"text": text}


# =========================
# MAIN
# =========================

def main():
    print_device_info()

    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is not available. Make sure you have an NVIDIA GPU and drivers installed.")

    # ---------- Load dataset ----------
    if not os.path.exists(cfg.DATA_PATH):
        raise FileNotFoundError(f"Training file not found at {cfg.DATA_PATH}")

    print(f"Loading dataset from {cfg.DATA_PATH} ...")
    dataset = load_dataset("json", data_files=cfg.DATA_PATH)["train"]
    dataset = dataset.map(format_example)

    # ---------- Load tokenizer ----------
    print(f"Loading tokenizer: {cfg.BASE_MODEL_NAME}")
    tokenizer = AutoTokenizer.from_pretrained(cfg.BASE_MODEL_NAME, use_fast=True)

    # Some models don't have a pad token; use eos_token as pad_token.
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "right"

    def tokenize_function(example):
        result = tokenizer(
            example["text"],
            truncation=True,
            max_length=cfg.MAX_SEQ_LEN,
            padding="max_length",
        )
        # Labels are just the input_ids for causal LM
        result["labels"] = result["input_ids"].copy()
        return result

    print("Tokenizing dataset ...")
    tokenized_dataset = dataset.map(
        tokenize_function,
        batched=True,
        remove_columns=dataset.column_names,
    )

    # ---------- Load base model ----------
    print(f"Loading base model: {cfg.BASE_MODEL_NAME}")
    if cfg.USE_4BIT:
        print("Using 4-bit quantization (QLoRA style)")
        from transformers import BitsAndBytesConfig

        bnb_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_use_double_quant=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
        )

        model = AutoModelForCausalLM.from_pretrained(
            cfg.BASE_MODEL_NAME,
            quantization_config=bnb_config,
            device_map="auto",
        )

        model = prepare_model_for_kbit_training(model)
    else:
        print("Using full-precision / fp16 model (requires more VRAM)")
        model = AutoModelForCausalLM.from_pretrained(
            cfg.BASE_MODEL_NAME,
            torch_dtype=torch.bfloat16,
            device_map="auto",
        )

    # ---------- LoRA setup ----------
    # Target modules depend on architecture; these are common for Mistral/LLaMA-based models.
    target_modules = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]

    lora_config = LoraConfig(
        r=cfg.LORA_R,
        lora_alpha=cfg.LORA_ALPHA,
        target_modules=target_modules,
        lora_dropout=cfg.LORA_DROPOUT,
        bias="none",
        task_type="CAUSAL_LM",
    )

    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()

    # ---------- Training arguments ----------
    training_args = TrainingArguments(
        output_dir=cfg.OUTPUT_DIR,
        num_train_epochs=cfg.NUM_EPOCHS,
        per_device_train_batch_size=cfg.BATCH_SIZE,
        gradient_accumulation_steps=cfg.GRADIENT_ACCUMULATION_STEPS,
        learning_rate=cfg.LEARNING_RATE,
        lr_scheduler_type="cosine",
        warmup_ratio=0.03,
        logging_steps=10,
        save_steps=200,
        save_total_limit=3,
        bf16=True,  # or fp16=True if your GPU prefers that
        optim="paged_adamw_8bit" if cfg.USE_4BIT else "adamw_torch",
        report_to="none",
    )

    # ---------- Trainer ----------
    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=tokenized_dataset,
    )

    print("Starting training...")
    trainer.train()
    print("Training complete.")

    # ---------- Save adapter ----------
    print(f"Saving LoRA adapter to {cfg.OUTPUT_DIR} ...")
    model.save_pretrained(cfg.OUTPUT_DIR)
    tokenizer.save_pretrained(cfg.OUTPUT_DIR)
    print("Done. Your Marven LoRA is ready!")


if __name__ == "__main__":
    main()

