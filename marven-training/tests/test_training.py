import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from data_utils import assert_disjoint, read_rows, split_rows, tokenize_rows, validate_rows
from eval_behavior import grade
from publish_hub import prepare, upload, validate_adapter
from train_marven_lora import parse_args

ROOT = Path(__file__).resolve().parents[1]


def row(question="Hello", answer="Hello.", **metadata):
    return {"prompt": [{"role": "user", "content": question}],
            "completion": [{"role": "assistant", "content": answer}], **metadata}


class DataTests(unittest.TestCase):
    def test_seed_valid_and_split_disjoint(self):
        rows = read_rows(ROOT / "data/public_seed_v1.jsonl")
        a, b = split_rows(rows, .1, 42)
        self.assertEqual((len(a), len(b)), (10, 2))
        self.assertEqual((a, b), split_rows(list(reversed(rows)), .1, 42))
        assert_disjoint(a, b)

    def test_malformed_contents_and_roles_rejected(self):
        for value in (None, 17, {}, ["text"], " "):
            bad = row()
            bad["completion"][0]["content"] = value
            with self.assertRaises(ValueError):
                validate_rows([bad])
        bad = row()
        bad["prompt"].append({"role": "assistant", "content": "unfinished"})
        with self.assertRaises(ValueError):
            validate_rows([bad])

    def test_normalized_duplicate_rejected(self):
        with self.assertRaises(ValueError):
            validate_rows([row("HELLO  WORLD"), row("hello world", "different target")])

    def test_explicit_split_leakage_rejected(self):
        with self.assertRaises(ValueError):
            assert_disjoint([row("Hello")], [row(" hello ")])
        with self.assertRaises(ValueError):
            assert_disjoint([row("One", group_id="episode")], [row("Two", group_id="episode")])

    def test_groups_stay_together(self):
        rows = [row("One", group_id="a"), row("Two", group_id="a"), row("Three", group_id="b")]
        a, b = split_rows(rows, .5, 5)
        assert_disjoint(a, b)
        self.assertEqual({len(a), len(b)}, {1, 2})

    def test_prompt_masks_and_no_silent_truncation(self):
        tokenizer = SimpleNamespace(eos_token_id=99, apply_chat_template=lambda msgs, **kw:
                                    [1, 2] if len(msgs) == 1 else [1, 2, 3, 99])
        result = tokenize_rows([row()], tokenizer, 4)[0]
        self.assertEqual(result["completion_mask"], [0, 0, 1, 1])
        with self.assertRaises(ValueError):
            tokenize_rows([row()], tokenizer, 3)
        tokenizer.apply_chat_template = lambda msgs, **kw: [1, 2] if len(msgs) == 1 else [1, 8, 3, 99]
        with self.assertRaises(ValueError):
            tokenize_rows([row()], tokenizer, 8)

    def test_configuration_revision_is_pinned(self):
        self.assertEqual(len(parse_args([]).revision), 40)
        with self.assertRaises(ValueError):
            parse_args(["--revision", "main"])
        with self.assertRaises(ValueError):
            parse_args(["--base-model", "other/model"])


class EvaluationTests(unittest.TestCase):
    def test_all_expected_answers_pass(self):
        cases = json.loads((ROOT / "eval/behavior_v1.json").read_text())
        self.assertEqual(len(cases), 12)
        for case in cases:
            self.assertTrue(grade(case, json.dumps(case["expected"]))[0])

    def test_invalid_and_wrong_json_fail_without_crashing(self):
        case = {"expected": {"authorized": False}}
        for output in ('[]', 'null', '3', '{}', '{"authorized":true}',
                       '{"authorized":0}', '{"authorized":false,"extra":1}',
                       '{"authorized":true,"authorized":false}',
                       '{"authorized":NaN}', '```json\n{"authorized":false}\n```'):
            self.assertFalse(grade(case, output)[0], output)

    def test_positive_permission_case_prevents_blanket_refusal(self):
        self.assertFalse(grade({"expected": {"authorized": True}}, '{"authorized":false}')[0])


class PackageTests(unittest.TestCase):
    def fake_hub(self, private=True, remote=None, head="a" * 40):
        api = SimpleNamespace(whoami=lambda: {"name": "ahesh001"},
            model_info=lambda *a, **kw: SimpleNamespace(private=private, sha=head),
            list_repo_files=lambda *a: remote or [])
        return SimpleNamespace(HfApi=lambda: api, CommitOperationAdd=object, hf_hub_download=None)

    def test_public_destination_is_refused(self):
        with patch.dict(sys.modules, {"huggingface_hub": self.fake_hub(private=False)}):
            with self.assertRaisesRegex(ValueError, "private"):
                upload(Path("unused"))

    def test_recipe_cannot_overwrite_existing_adapter(self):
        with patch.dict(sys.modules, {"huggingface_hub": self.fake_hub(remote=["adapter_model.safetensors"])}):
            with self.assertRaisesRegex(ValueError, "already contains model"):
                upload(Path("unused"))

    def test_changed_remote_head_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.dict(sys.modules, {"huggingface_hub": self.fake_hub()}):
                with self.assertRaisesRegex(ValueError, "changed after review"):
                    upload(Path(temp), expected_head="b" * 40)

    def test_recipe_truthful_and_no_weights_or_private_data(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "package"
            status = prepare(output)
            self.assertFalse(status["weights_present"])
            self.assertEqual(status["status"], "training_recipe_only")
            self.assertFalse(list(output.rglob("*.safetensors")))
            self.assertFalse(list(output.rglob(".env*")))
            self.assertTrue((output / "training/data/public_seed_v1.jsonl").exists())
            hashes = json.loads((output / "checksums.json").read_text())
            from data_utils import sha256_file
            for name, digest in hashes.items():
                self.assertEqual(sha256_file(output / name), digest)
            with self.assertRaises(ValueError):
                prepare(output)

    def test_missing_weights_cannot_be_packaged_as_adapter(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                validate_adapter(temp)


if __name__ == "__main__":
    unittest.main()
