"""Opt-in CPU smoke test with a tiny random model, never a Marven weight release."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@unittest.skipUnless(os.environ.get("MARVEN_RUN_INTEGRATION") == "1", "Set MARVEN_RUN_INTEGRATION=1 with training dependencies installed")
class TrainerIntegrationTests(unittest.TestCase):
    def test_real_trl_masks_updates_and_saves_lora(self):
        import torch
        from datasets import Dataset
        from peft import LoraConfig, get_peft_model
        from safetensors import safe_open
        from transformers import AutoTokenizer, Qwen3Config, Qwen3ForCausalLM, set_seed
        from trl import SFTConfig, SFTTrainer
        from data_utils import tokenize_rows
        from train_marven_lora import DEFAULT_BASE_MODEL, DEFAULT_REVISION
        torch.set_num_threads(2)
        set_seed(42)
        tokenizer = AutoTokenizer.from_pretrained(DEFAULT_BASE_MODEL, revision=DEFAULT_REVISION)
        rows = [{"prompt": [{"role": "user", "content": "Say hello."}],
                 "completion": [{"role": "assistant", "content": "Hello."}]}]
        tokens = tokenize_rows(rows, tokenizer, 128)
        # Random miniature network. No pretrained base-model weights are downloaded.
        model = Qwen3ForCausalLM(Qwen3Config(vocab_size=len(tokenizer), hidden_size=32,
            intermediate_size=64, num_hidden_layers=1, num_attention_heads=4,
            num_key_value_heads=2, head_dim=8, max_position_embeddings=128,
            bos_token_id=tokenizer.bos_token_id, eos_token_id=tokenizer.eos_token_id))
        model.config.use_cache = False
        model = get_peft_model(model, LoraConfig(task_type="CAUSAL_LM", r=2, lora_alpha=4, target_modules="all-linear"))
        with tempfile.TemporaryDirectory() as temp:
            config = SFTConfig(output_dir=temp, use_cpu=True, bf16=False, fp16=False,
                max_steps=1, per_device_train_batch_size=1, per_device_eval_batch_size=1,
                gradient_accumulation_steps=1, learning_rate=1e-3, warmup_ratio=0,
                eval_strategy="epoch", save_strategy="epoch", save_total_limit=2,
                load_best_model_at_end=True, metric_for_best_model="eval_loss", greater_is_better=False,
                max_length=128, completion_only_loss=True, packing=False, report_to="none",
                gradient_checkpointing=True, gradient_checkpointing_kwargs={"use_reentrant": False},
                optim="adamw_torch", disable_tqdm=True)
            trainer = SFTTrainer(model=model, args=config, train_dataset=Dataset.from_list(tokens),
                eval_dataset=Dataset.from_list(tokens), processing_class=tokenizer)
            batch = trainer.data_collator([trainer.train_dataset[0]])
            expected = [t if m else -100 for t, m in zip(tokens[0]["input_ids"], tokens[0]["completion_mask"])]
            self.assertEqual(batch["labels"][0].tolist(), expected)
            result = trainer.train()
            self.assertTrue(torch.isfinite(torch.tensor(result.training_loss)))
            self.assertTrue(any(torch.count_nonzero(p).item() for n, p in model.named_parameters() if "lora_B" in n))
            destination = Path(temp) / "candidate"
            trainer.model.save_pretrained(destination, safe_serialization=True, save_embedding_layers=False)
            with safe_open(destination / "adapter_model.safetensors", framework="numpy") as tensors:
                self.assertTrue(any("lora_A" in k for k in tensors.keys()))
                self.assertTrue(any("lora_B" in k for k in tensors.keys()))


if __name__ == "__main__":
    unittest.main()
