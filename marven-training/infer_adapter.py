#!/usr/bin/env python3
"""Local 4-bit inference from a completed Marven adapter and its pinned base."""
import argparse
from pathlib import Path

from publish_hub import validate_adapter


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--adapter-dir", type=Path, required=True)
    p.add_argument("--prompt", required=True)
    p.add_argument("--max-new-tokens", type=int, default=256)
    args = p.parse_args()
    if args.max_new_tokens < 1:
        p.error("max-new-tokens must be positive")
    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    if not torch.cuda.is_available():
        raise RuntimeError("This inference profile needs a CUDA GPU")
    manifest = validate_adapter(args.adapter_dir)
    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    tokenizer = AutoTokenizer.from_pretrained(args.adapter_dir, trust_remote_code=False)
    base = AutoModelForCausalLM.from_pretrained(manifest["base_model"],
        revision=manifest["base_model_revision"], trust_remote_code=False,
        device_map={"": torch.cuda.current_device()}, torch_dtype=dtype,
        quantization_config=BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
            bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=dtype))
    model = PeftModel.from_pretrained(base, args.adapter_dir, is_trainable=False)
    model.eval()
    model.config.use_cache = True
    text = tokenizer.apply_chat_template([{"role": "user", "content": args.prompt}],
        tokenize=False, add_generation_prompt=True, enable_thinking=False)
    inputs = tokenizer(text, return_tensors="pt", add_special_tokens=False).to(model.device)
    trained_length = manifest["training"]["max_length"]
    if inputs["input_ids"].shape[-1] + args.max_new_tokens > trained_length:
        raise ValueError("Prompt plus requested output exceeds this adapter's training length; shorten the request")
    with torch.inference_mode():
        output = model.generate(**inputs, max_new_tokens=args.max_new_tokens,
            do_sample=True, temperature=0.7, top_p=0.8, top_k=20,
            pad_token_id=tokenizer.pad_token_id, eos_token_id=tokenizer.eos_token_id)
    print(tokenizer.decode(output[0, inputs["input_ids"].shape[-1]:], skip_special_tokens=True))


if __name__ == "__main__":
    main()
