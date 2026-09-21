import pathlib as pl
from marven_local.core.caps import CapabilityManager, CapabilityError

def test_fs_write_denied(tmp_path: pl.Path):
    (tmp_path/"policy.yaml").write_text("capabilities:\n  fs.write:\n    enabled: false\n", encoding="utf-8")
    cm = CapabilityManager(tmp_path, "tester")
    try:
        cm.fs_write(str(tmp_path/"x.txt"), "x")
        ok = False
    except CapabilityError:
        ok = True
    assert ok


def test_fs_operations_use_authorized_resolved_path(tmp_path: pl.Path):
    (tmp_path / "policy.yaml").write_text(
        "capabilities:\n"
        "  fs.read:\n"
        "    enabled: true\n"
        "    allow_paths:\n"
        "      - ./allowed\n"
        "  fs.write:\n"
        "    enabled: true\n"
        "    allow_paths:\n"
        "      - ./allowed\n",
        encoding="utf-8",
    )
    (tmp_path / "allowed").mkdir()
    cm = CapabilityManager(tmp_path, "tester")
    result = cm.fs_write("allowed/note.txt", "safe")
    assert pl.Path(result) == tmp_path / "allowed" / "note.txt"
    assert cm.fs_read("allowed/note.txt") == "safe"


def test_fs_read_rejects_parent_escape(tmp_path: pl.Path):
    (tmp_path / "policy.yaml").write_text(
        "capabilities:\n"
        "  fs.read:\n"
        "    enabled: true\n"
        "    allow_paths:\n"
        "      - ./\n",
        encoding="utf-8",
    )
    cm = CapabilityManager(tmp_path, "tester")
    try:
        cm.fs_read("../secret.txt")
        ok = False
    except CapabilityError:
        ok = True
    assert ok
