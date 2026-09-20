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
