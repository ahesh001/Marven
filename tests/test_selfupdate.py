import pathlib as pl
from marven_local.core.caps import CapabilityManager
from marven_local.core.selfupdate import SelfUpdater

def test_update_roundtrip(tmp_path: pl.Path):
    (tmp_path/"policy.yaml").write_text("capabilities:\n  fs.write:\n    enabled: true\n    allow_paths:\n      - ./\n  self.update:\n    enabled: true\n", encoding="utf-8")
    target = tmp_path/"t.py"
    target.write_text("x=1\n", encoding="utf-8")
    cm = CapabilityManager(tmp_path, "t")
    su = SelfUpdater(tmp_path, cm)
    p = su.propose(target, "1", "2", "d", "2025-01-01T00:00:00+00:00")
    su.sign_init()
    su.sign(p)
    assert su.apply_approved()
    assert target.read_text(encoding="utf-8").strip() == "x=2"
